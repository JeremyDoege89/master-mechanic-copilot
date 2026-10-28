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


class FixtureAnalyzer:
    """Recorded-fixture backend: the live spike's captured streams replayed
    through the real SAM parser (battery found, air-filter-housing empty)."""

    def __init__(self) -> None:
        from mmc.meta import sam as sam_mod

        self.spark_record = json.loads((FIXTURES / "spark_components_01.json").read_text())
        self.sam_result = sam_mod.result_from_image(
            "battery",
            sam_mod.parse_image_events(
                sam_mod.load_stream_fixture(FIXTURES / "sam_stream_battery.jsonl")
            ),
        )
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


def test_instrument_reading_and_confirm_gate(client):
    sid = _create_session(client)
    blob = _upload(client, sid)
    r = client.post(f"/sessions/{sid}/vision/instrument", json={"blob_ref": blob})
    assert r.status_code == 200, r.text
    reading = r.json()
    assert reading["needs_retake"] is False
    assert reading["value"] == pytest.approx(12.42)

    # No Evidence exists before confirm.
    state = client.get(f"/sessions/{sid}/state").json()
    assert state["tests_log"] == []

    r = client.post(
        f"/sessions/{sid}/vision/instrument/confirm",
        json={"blob_ref": blob, "reading": reading, "note": "battery at rest"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["evidence_id"]


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
