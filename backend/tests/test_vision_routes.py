"""M5 vision routes tests (all offline, fixture analyzer).

keyframe: recorded Spark+SAM fixtures -> components/anomalies/overlays
present and schema-valid, observations logged via the add_observation path.
instrument: readings incl. needs_retake; NO Evidence exists before the
explicit confirm call; confirm appends evidence_added + observation_added.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc.api import routes_vision as vision_routes
from mmc.main import app

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages" / "schemas"
FIXTURES = Path(__file__).parent / "fixtures" / "vision"


def _registry() -> Registry:
    pairs = []
    for schema_path in sorted(SCHEMAS.glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


REGISTRY = _registry()


_FIXTURE_SAM_RESULT = None


def _fixture_sam_result():
    """Parse the battery stream once per process: mask conversion costs
    seconds, and the fixture bytes are static."""
    global _FIXTURE_SAM_RESULT
    if _FIXTURE_SAM_RESULT is None:
        from mmc.meta import sam as sam_mod

        _FIXTURE_SAM_RESULT = sam_mod.result_from_image(
            "battery",
            sam_mod.parse_image_events(
                sam_mod.load_stream_fixture(FIXTURES / "sam_stream_battery.jsonl")
            ),
        )
    return _FIXTURE_SAM_RESULT


class FixtureAnalyzer:
    """Recorded-fixture backend: the live spike's captured streams replayed
    through the real SAM parser (battery found, air-filter-housing empty)."""

    def __init__(self) -> None:
        self.spark_record = json.loads((FIXTURES / "spark_components_01.json").read_text())
        self.sam_result = _fixture_sam_result()
        self.meter_record = json.loads((FIXTURES / "instrument_01.json").read_text())

    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]:
        from mmc.meta import sam as sam_mod
        from mmc.vision import components as comp_mod

        parsed = comp_mod.parse_components(json.loads(self.spark_record["output_text"]))
        overlays = []
        for comp in parsed["components"]:
            overlays.append(
                {
                    "concept": comp["sam_concept"],
                    "used_concept": "battery",
                    "found": self.sam_result.found,
                    "masks": sam_mod.overlays_for_result(self.sam_result),
                }
            )
        return parsed, overlays

    def analyze_instrument(self, image: bytes) -> dict:
        from mmc.vision import instruments as instr_mod

        return instr_mod.parse_instrument(json.loads(self.meter_record["output_text"]))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    vision_routes.set_analyzer(FixtureAnalyzer())
    yield TestClient(app)
    vision_routes.set_analyzer(vision_routes.LiveVisionAnalyzer())


def _create_session(client: TestClient) -> str:
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _upload(client: TestClient, session_id: str, data: bytes = b"fake-jpeg") -> str:
    r = client.post(
        f"/sessions/{session_id}/blobs",
        files={"file": ("frame.jpg", data, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    return r.json()["blob_ref"]


def _event_types(tmp_path, session_id: str) -> list[str]:
    import json as _json

    log = tmp_path / "sessions" / session_id / "events.jsonl"
    return [_json.loads(line)["type"] for line in log.read_text().splitlines() if line.strip()]


def _live_instrument_analyzer(tmp_path):
    """LiveVisionAnalyzer with a fake Meta transport (no Baseten key)."""
    from types import SimpleNamespace

    from mmc.api import routes_vision as rv
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    meter = json.loads((FIXTURES / "instrument_01.json").read_text())

    def transport(model, kwargs):
        return SimpleNamespace(
            output_text=meter["output_text"],
            usage={"input_tokens": 2500, "output_tokens": 100},
        )

    client = MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "meta",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    return rv.LiveVisionAnalyzer(client, None)


def test_keyframe_replay_session_is_read_only(client, tmp_path):
    """Finding #6: vision routes must reject replay sessions like the
    upload processing routes do — no spend, no log mutation."""
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "replay"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    # Blobs are global/content-addressed: upload via a writable session
    # (POST /blobs is 409 on replays per M6 finding #2) and reference it.
    live = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"})
    blob = _upload(client, live.json()["session_id"])
    r = client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": blob})
    assert r.status_code == 409, r.text
    assert _event_types(tmp_path, sid) == ["session_started"]


def test_instrument_replay_session_is_read_only(client, tmp_path):
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "replay"})
    sid = r.json()["session_id"]
    r = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": "nope"})
    assert r.status_code == 409, r.text
    assert _event_types(tmp_path, sid) == ["session_started"]


def test_instrument_confirm_replay_session_is_read_only(client, tmp_path):
    from mmc.api import routes_vision as vision_routes

    vision_routes.set_analyzer(FixtureAnalyzer())
    sid = _create_session(client)
    blob = _upload(client, sid)
    reading = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": blob}).json()
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "replay"})
    replay_sid = r.json()["session_id"]
    r = client.post(
        f"/sessions/{replay_sid}/vision/instrument/confirm",
        json={"blob_ref": blob, "reading": reading},
    )
    assert r.status_code == 409, r.text


def test_keyframe_budget_exceeded_maps_to_402(client, tmp_path):
    """Finding #6: BudgetExceeded from the vision path must surface as 402
    (like the upload routes), not 502/500."""
    from mmc.api import routes_vision as rv
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    def boom(model, kwargs):
        raise AssertionError("budget guard must fire before the transport")

    tiny = MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=0.0001),
        cache_dir=tmp_path / "meta402",
        replay_cached=False,
        transport=boom,
        sleep=lambda s: None,
    )
    rv.set_analyzer(rv.LiveVisionAnalyzer(tiny, None))
    try:
        sid = _create_session(client)
        blob = _upload(client, sid)
        r = client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": blob})
        assert r.status_code == 402, r.text
    finally:
        rv.set_analyzer(FixtureAnalyzer())


def test_keyframe_leaves_event_loop_responsive(client, tmp_path):
    """Finding #8: a slow analyzer must not stall the loop — a ticker
    sharing the loop with the keyframe handler must keep ticking."""
    import asyncio
    import time as _time

    from mmc.api import routes_vision as vision_routes

    class Slow(FixtureAnalyzer):
        def analyze_keyframe(self, image: bytes):
            _time.sleep(0.6)  # blocking work that must leave the loop
            return super().analyze_keyframe(image)

    vision_routes.set_analyzer(Slow())
    try:
        sid = _create_session(client)
        blob = _upload(client, sid)

        async def main():
            from mmc.api.routes_vision import KeyframeBody, vision_keyframe

            gaps: list[float] = []
            last = _time.monotonic()

            async def ticker():
                nonlocal last
                for _ in range(12):
                    await asyncio.sleep(0.05)
                    now = _time.monotonic()
                    gaps.append(now - last)
                    last = now

            await asyncio.gather(
                vision_keyframe(sid, KeyframeBody(blob_ref=blob)), ticker()
            )
            return gaps

        gaps = asyncio.run(main())
        assert max(gaps) < 0.4, gaps
    finally:
        vision_routes.set_analyzer(FixtureAnalyzer())


def test_instrument_logs_meta_call_to_audit_log(client, tmp_path):
    """Finding #7: the instrument reading's Meta llm_call record must reach
    the session event log (bound analyzer), without creating evidence."""
    from mmc.api import routes_vision as vision_routes

    vision_routes.set_analyzer(_live_instrument_analyzer(tmp_path))
    try:
        sid = _create_session(client)
        blob = _upload(client, sid)
        r = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": blob})
        assert r.status_code == 200, r.text
        types = _event_types(tmp_path, sid)
        assert types.count("llm_call") == 1
        assert "evidence_added" not in types
    finally:
        vision_routes.set_analyzer(FixtureAnalyzer())


def test_keyframe_returns_components_and_overlays(client):
    sid = _create_session(client)
    blob = _upload(client, sid)
    r = client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": blob})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["components"]) >= 1 and len(body["overlays"]) >= 1
    assert any(m["box"] is not None for o in body["overlays"] for m in o["masks"])
    comp_schema = json.loads((SCHEMAS / "vision_components.json").read_text())
    jsonschema.Draft202012Validator(comp_schema, registry=REGISTRY).validate(
        {"components": body["components"], "anomalies": body["anomalies"]}
    )


def test_keyframe_logs_observations_without_moving_posteriors(client):
    sid = _create_session(client)
    before = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    blob = _upload(client, sid)
    client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": blob})
    after = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    assert [h["hypothesis_id"] for h in before] == [h["hypothesis_id"] for h in after]
    for b, a in zip(before, after):
        assert b["current"] == pytest.approx(a["current"])


def test_instrument_reading_and_confirm_gate(client, tmp_path):
    sid = _create_session(client)
    blob = _upload(client, sid)
    r = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": blob})
    assert r.status_code == 200, r.text
    reading = r.json()
    assert reading["needs_retake"] is False
    assert reading["value"] == pytest.approx(12.42)

    # No Evidence exists before confirm: the event log (not just derived
    # state, which reports tests_log == [] regardless) must hold no
    # evidence_added event. Finding #15.
    assert "evidence_added" not in _event_types(tmp_path, sid)

    r = client.post(
        f"/sessions/{sid}/vision/instrument/confirm",
        json={"blob_ref": blob, "reading": reading, "note": "battery at rest"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["evidence_id"]
    assert "evidence_added" in _event_types(tmp_path, sid)


def test_confirm_refuses_retake_reading(client):
    sid = _create_session(client)
    blob = _upload(client, sid)
    bad = {"value": 1.0, "units": "V", "confidence": 0.2, "needs_retake": True}
    r = client.post(
        f"/sessions/{sid}/vision/instrument/confirm",
        json={"blob_ref": blob, "reading": bad},
    )
    assert r.status_code == 400


def test_instrument_needs_retake_case(client, monkeypatch):
    class Blurry(FixtureAnalyzer):
        def analyze_instrument(self, image: bytes) -> dict:
            from mmc.vision import instruments as instr_mod

            return instr_mod.parse_instrument({"value": 0.0, "units": "V", "confidence": 0.31})

    vision_routes.set_analyzer(Blurry())
    try:
        sid = _create_session(client)
        blob = _upload(client, sid)
        r = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": blob})
        assert r.status_code == 200
        assert r.json()["needs_retake"] is True
    finally:
        vision_routes.set_analyzer(FixtureAnalyzer())
