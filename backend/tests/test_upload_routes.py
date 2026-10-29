"""M5U upload route tests (§5): happy paths, errors, replay, budget."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mmc.api import routes_uploads
from mmc.api import routes_vision as vision_routes
from mmc.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "upload"


class FixtureUploadAnalyzer:
    """Offline backend: recorded Spark ranges + canned video events."""

    def extract_pdf(self, data: bytes) -> list[dict]:
        from mmc.meta.spark import _validate_spec_ranges

        record = json.loads((FIXTURES / "spark_pdf_spec_01.json").read_text())
        return _validate_spec_ranges(record["output_text"])

    def analyze_video(self, data: bytes, filename: str) -> dict:
        record = json.loads((FIXTURES / "spark_video_events_01.json").read_text())
        events = json.loads(record["output_text"])["events"]
        return {
            "events": events,
            "overlays": [
                {
                    "label": events[0]["label"],
                    "t_start_s": 0.0,
                    "t_end_s": 1.0,
                    "concept": events[0]["sam_concept"],
                    "found": False,
                    "masks": [],
                }
            ],
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    routes_uploads.set_analyzer(FixtureUploadAnalyzer())
    yield TestClient(app)
    routes_uploads.set_analyzer(routes_uploads.LiveUploadAnalyzer())
    vision_routes.set_analyzer(vision_routes.LiveVisionAnalyzer())


def _create_session(client: TestClient, mode: str = "upload") -> str:
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _upload(client: TestClient, sid: str, data: bytes, filename: str, kind: str) -> dict:
    r = client.post(
        f"/sessions/{sid}/uploads",
        files={"file": (filename, data, "application/octet-stream")},
        data={"kind": kind},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_then_pdf_extract_happy_path(client):
    sid = _create_session(client)
    pdf = (FIXTURES / "upload_spec_test.pdf").read_bytes()
    up = _upload(client, sid, pdf, "manual.pdf", "manual_pdf")
    assert up["kind"] == "manual_pdf" and up["evidence_id"]
    r = client.post(f"/sessions/{sid}/uploads/pdf/extract", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["spec_ranges"]) >= 1
    assert body["evidence_id"]
    assert all(s["extractor"] == "spark:spec_table_v1" for s in body["spec_ranges"])


def test_upload_then_csv_parse_submits_derived_tests(client):
    sid = _create_session(client)
    for gate in ("engine_running_outdoors", "engine_off"):
        client.post(f"/sessions/{sid}/events", json={"type": "safety_ack", "payload": {"gate": gate}})
    csv_bytes = (FIXTURES / "torque_vacuum_leak.csv").read_bytes()
    up = _upload(client, sid, csv_bytes, "log.csv", "csv")
    r = client.post(f"/sessions/{sid}/uploads/csv/parse", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rpm" in body["series"]
    trim = next(d for d in body["derived_tests"] if d["test_ref"] == "trim_idle_vs_2500")
    assert trim["outcome"] == "lean_idle_normal_cruise"
    # CSV-derived tests move posteriors through the normal test_result fold.
    state = client.get(f"/sessions/{sid}/state").json()
    assert any(e["test_ref"] == "trim_idle_vs_2500" for e in state["tests_log"])


def test_upload_then_video_analyze_without_moving_posteriors(client):
    sid = _create_session(client)
    before = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    clip = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
    up = _upload(client, sid, clip, "clip.mp4", "video")
    r = client.post(f"/sessions/{sid}/uploads/video/analyze", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 200, r.text
    assert len(r.json()["events"]) >= 1
    after = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    for b, a in zip(before, after):
        assert b["current"] == pytest.approx(a["current"])


def test_pdf_extract_alone_leaves_posteriors_untouched(client):
    sid = _create_session(client)
    before = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    pdf = (FIXTURES / "upload_spec_test.pdf").read_bytes()
    up = _upload(client, sid, pdf, "manual.pdf", "manual_pdf")
    client.post(f"/sessions/{sid}/uploads/pdf/extract", json={"blob_ref": up["blob_ref"]})
    after = client.get(f"/sessions/{sid}/state").json()["hypotheses"]
    for b, a in zip(before, after):
        assert b["current"] == pytest.approx(a["current"])


def test_photo_upload_routes_into_keyframe_confirm_flow(client):
    from test_vision_routes import FixtureAnalyzer as VisionFixtures

    vision_routes.set_analyzer(VisionFixtures())
    sid = _create_session(client)
    up = _upload(client, sid, b"fake-jpeg", "frame.jpg", "photo")
    assert up["kind"] == "photo"
    r = client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 200, r.text
    assert len(r.json()["components"]) >= 1


def test_unknown_session_and_blob_404(client):
    r = client.post(
        "/sessions/doesnotexist/uploads",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        data={"kind": "manual_pdf"},
    )
    assert r.status_code == 404
    sid = _create_session(client)
    r = client.post(f"/sessions/{sid}/uploads/pdf/extract", json={"blob_ref": "blobs/nope"})
    assert r.status_code == 404


def test_oversize_upload_413(client, monkeypatch):
    monkeypatch.setattr(routes_uploads, "MAX_BLOB_BYTES", 4)
    sid = _create_session(client)
    r = client.post(
        f"/sessions/{sid}/uploads",
        files={"file": ("big.bin", b"12345", "application/octet-stream")},
        data={"kind": "csv"},
    )
    assert r.status_code == 413


def test_replay_session_processing_routes_stay_readonly(client):
    sid = _create_session(client, mode="replay")
    pdf = (FIXTURES / "upload_spec_test.pdf").read_bytes()
    up = _upload(client, sid, pdf, "manual.pdf", "manual_pdf")
    assert up["evidence_id"] is None  # blob stored, nothing appended
    r = client.post(f"/sessions/{sid}/uploads/pdf/extract", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 409
    r = client.post(f"/sessions/{sid}/uploads/csv/parse", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 409


def test_replay_cache_miss_raises_instead_of_spending(tmp_path):
    from mmc.meta.client import MetaClient, NoCachedResponse

    def boom(model: str, kwargs: dict):
        raise AssertionError("replay must never touch the network")

    client = MetaClient(
        api_key="test-key-not-a-secret",
        cache_dir=tmp_path / "cache",
        replay_cached=True,
        transport=boom,
        sleep=lambda s: None,
    )
    analyzer = routes_uploads.LiveUploadAnalyzer(client)
    with pytest.raises(NoCachedResponse):
        analyzer.extract_pdf(b"%PDF-1.4 fake")
    assert client.budget.spend_usd == 0.0


def test_budget_refusal_past_hard_stop(client, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("BUDGET_USD_HARD_STOP", "0.0001")
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "false")
    routes_uploads.set_analyzer(routes_uploads.LiveUploadAnalyzer())
    sid = _create_session(client)
    pdf = (FIXTURES / "upload_spec_test.pdf").read_bytes()
    up = _upload(client, sid, pdf, "manual.pdf", "manual_pdf")
    # Prime the Files API cache so the refusal comes from the Spark budget
    # guard, not the network.
    sha = hashlib.sha256(pdf).hexdigest()
    cache = tmp_path / "llm_cache" / "files"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha}.json").write_text(json.dumps({"file_id": "file-x", "sha256": sha}))
    r = client.post(f"/sessions/{sid}/uploads/pdf/extract", json={"blob_ref": up["blob_ref"]})
    assert r.status_code == 402, r.text
