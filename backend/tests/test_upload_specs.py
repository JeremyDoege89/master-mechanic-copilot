"""M5U PDF spec tests (§2): ranges, probability refusal, manual_page wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mmc.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "upload"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    yield TestClient(app)


def _create_session(client: TestClient) -> str:
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _recorded_ranges():
    from mmc.meta.spark import _validate_spec_ranges

    record = json.loads((FIXTURES / "spark_pdf_spec_01.json").read_text())
    return _validate_spec_ranges(record["output_text"])


def test_recorded_spark_json_becomes_validated_ranges_with_page_and_hash():
    import jsonschema

    from mmc.meta.spark import VALIDATORS
    from mmc.upload import specs as specs_mod

    completed = specs_mod.attach_specs(_recorded_ranges(), "blobs/abc", "abc" * 21 + "a")
    assert len(completed) >= 1
    full_schema = json.loads(
        (Path(__file__).resolve().parents[2] / "packages" / "schemas" / "spec_range.json").read_text()
    )
    validator = jsonschema.Draft202012Validator(full_schema)
    for item in completed:
        validator.validate(item)  # extractor + source_blob_sha256 present
        assert item["extractor"] == "spark:spec_table_v1"
    assert any(item.get("page") == 1 for item in completed)


def test_probability_key_output_rejected():
    from mmc.meta.spark import _validate_spec_ranges

    sneaky = json.dumps({"spec_ranges": [{"spec_id": "x", "parameter": "y", "posterior": 0.9}]})
    with pytest.raises(ValueError, match="probability"):
        _validate_spec_ranges(sneaky)


def test_result_with_spec_id_beats_kp_physics(client):
    from mmc.upload import specs as specs_mod

    sid = _create_session(client)
    completed = specs_mod.attach_specs(_recorded_ranges(), "blobs/abc", "abc123")
    evidence = specs_mod.build_evidence("blobs/abc", "abc123", completed)
    r = client.post(f"/sessions/{sid}/events", json={"type": "evidence_added", "payload": evidence})
    assert r.status_code == 200, r.text

    client.post(
        f"/sessions/{sid}/events",
        json={"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}},
    )
    spec_id = completed[0]["spec_id"]
    r = client.post(
        "/sessions/REPLACE/tests/trim_idle_vs_2500/result".replace("REPLACE", sid),
        json={
            "measured": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0},
            "spec_id": spec_id,
        },
    )
    assert r.status_code == 200, r.text
    expected = r.json()["test_result"]["expected"]
    assert expected["source"]["kind"] == "manual_page"
    assert expected["min"] == completed[0].get("min")
    assert expected["max"] == completed[0].get("max")


def test_unknown_spec_id_400_with_zero_events_appended(client):
    sid = _create_session(client)
    before = client.get(f"/sessions/{sid}/state").json()
    n_before = len(before["tests_log"])
    client.post(
        f"/sessions/{sid}/events",
        json={"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}},
    )
    # Use a gate-free test to isolate the spec_id failure from runnability.
    r = client.post(
        f"/sessions/{sid}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}, "spec_id": "nope_missing"},
    )
    assert r.status_code == 400, r.text
    after = client.get(f"/sessions/{sid}/state").json()
    assert len(after["tests_log"]) == n_before


def test_files_upload_cache_reuses_id_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from mmc.meta.client import NoCachedResponse
    from mmc.meta.files import upload_file

    class FakeClient:
        replay_cached = True

    with pytest.raises(NoCachedResponse):
        upload_file(FakeClient(), b"%PDF-1.4 fake", "x.pdf")

    class LiveClient:
        _api_key = "k"
        base_url = "http://x"
        replay_cached = False

    seen = {}

    def fake_transport(api_key, base_url, data, filename, purpose):
        seen["purpose"] = purpose
        return "file-123"

    fid = upload_file(LiveClient(), b"%PDF-1.4 fake", "x.pdf", transport=fake_transport)
    assert fid == "file-123"
    assert seen["purpose"] == "user_data"
    # Second call replays from the local cache with no transport.
    fid2 = upload_file(
        LiveClient(), b"%PDF-1.4 fake", "x.pdf", transport=None
    )
    assert fid2 == "file-123"
