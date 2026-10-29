"""Real-vehicle judge exports (owner Ford baseline -> judge demo, $0 replay).

Covers the task's test list:
- export of a finished session round-trips (export -> replay -> identical
  state and report),
- missing-blob refusal names the blob,
- VIN truncation (vin_last6 only, full VIN nowhere),
- landing ordering (real-vehicle bundles first with their label).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
while not (REPO_ROOT / "scripts").exists() and REPO_ROOT != REPO_ROOT.parent:
    REPO_ROOT = REPO_ROOT.parent

from mmc.api import routes as api_routes
from mmc.main import app

VIN = "1FAHP3F28CL123456"
LAST6 = "123456"
LABEL = "Ford F-150 2.7L, MAF unplugged"


def _load_exporter():
    path = REPO_ROOT / "scripts" / "export_session_bundle.py"
    spec = importlib.util.spec_from_file_location("export_session_bundle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_session_bundle"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "true")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_MODE", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _finished_session(client: TestClient, tmp_path: Path, vin: str | None = VIN) -> str:
    """Minimal finished live session: 1 blob, 1 test, 1 cached LLM, 1 reviewed report."""
    from mmc import sessions as sess_mod

    vehicle: dict | None = {"make": "Ford", "model": "F-150", "year": 2015, "engine": "2.7L V6"}
    if vin is not None:
        vehicle["vin"] = vin
    r = client.post("/sessions", json={"vehicle": vehicle, "user_level": "L1", "tools": [], "mode": "live"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    # One referenced blob (photo stand-in) via the real upload route.
    up = client.post(f"/sessions/{sid}/blobs", files={"file": ("photo.jpg", b"fake-photo-bytes", "image/jpeg")})
    assert up.status_code == 200, up.text
    blob_ref = up.json()["blob_ref"]
    sha = up.json()["sha256"]
    evid = {
        "evidence_id": uuid.uuid4().hex,
        "type": "image",
        "blob_ref": blob_ref,
        "content_hash": sha,
        "ts": sess_mod._utc_now(),
    }
    r = client.post(f"/sessions/{sid}/events", json={"type": "evidence_added", "payload": evid})
    assert r.status_code == 200, r.text

    # One engine fold so state actually moves.
    r = client.post(f"/sessions/{sid}/tests/dtc_read/result", json={"measured": {"dtcs": []}})
    assert r.status_code == 200, r.text

    # One cached model response the session used (registry: POST /events 403s llm_call).
    session = sess_mod.get_registry().get(sid)
    assert session is not None
    fake_sha = hashlib.sha256(b"fake-llm-request").hexdigest()
    record = {
        "kind": "llm_call",
        "model": "muse-spark-1.3",
        "request_sha256": fake_sha,
        "request": {"op": "test"},
        "output_text": "{}",
        "usage": {},
        "cost_usd": 0.0,
        "cached": False,
    }
    (tmp_path / "llm_cache").mkdir(parents=True, exist_ok=True)
    (tmp_path / "llm_cache" / f"{fake_sha}.json").write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    sess_mod.get_registry().append(session, "llm_call", record)

    # Finished report with a Wonder review (registry + report file).
    state = sess_mod.compute_state(session)
    provisional = sess_mod.build_report(session, state)
    review = {"agrees_with_top": True, "gaps": [{"issue": "test gap"}], "narrative": "Test review."}
    provisional["wonder_review"] = review
    provisional["wonder_unavailable"] = None
    sess_mod.save_report(sess_mod.get_registry(), provisional)
    sess_mod.get_registry().append(
        session,
        "report_built",
        {
            "report_id": provisional["report_id"],
            "log_head_hash": provisional["reproducibility"]["log_head_hash"],
            "wonder_review": review,
            "wonder_unavailable": None,
        },
    )
    return sid


def test_export_round_trips_state_and_report(client, tmp_path, monkeypatch):
    from mmc import sessions as sess_mod
    from mmc.replay import mmrec, runner

    sid = _finished_session(client, tmp_path)
    session = sess_mod.get_registry().get(sid)
    assert session is not None
    before_state = sess_mod.compute_state(session)
    report_id = [e for e in session.store.iter_replay() if e["type"] == "report_built"][-1]["payload"]["report_id"]
    before_report = json.loads((tmp_path / "reports" / f"{report_id}.json").read_text(encoding="utf-8"))

    exporter = _load_exporter()
    out = tmp_path / "real-test.mmrec"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert exporter.main([sid, "--label", LABEL, "--out", str(out)]) == 0
    assert out.is_file()
    members = mmrec.loads(out.read_bytes())
    mmrec.verify_members(members, str(out))
    meta = json.loads(members["meta.json"].decode("utf-8"))
    assert meta["label"] == mmrec.REAL_LABEL
    assert meta["role"] == mmrec.ROLE_DEMO
    assert meta["display_label"] == LABEL
    assert meta["vehicle"].get("vin_last6") == LAST6
    assert "vin_encrypted" not in (meta.get("vehicle") or {})

    # Replay in a clean workspace: blobs + cache ride along, $0, no keys.
    replay_root = tmp_path / "replay-data"
    replay_root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(replay_root))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    session2 = runner.replay_bundle_bytes(out.read_bytes(), label="test")
    assert session2.store.verify_chain()
    after_state = sess_mod.compute_state(session2)
    assert [h["hypothesis_id"] for h in after_state["hypotheses"]] == [
        h["hypothesis_id"] for h in before_state["hypotheses"]
    ]
    for a, b in zip(after_state["hypotheses"], before_state["hypotheses"]):
        assert a["current"] == pytest.approx(b["current"], abs=1e-9)
    assert after_state["tests_log"] == before_state["tests_log"]

    rebuilt_id = [e for e in session2.store.iter_replay() if e["type"] == "report_built"][-1]["payload"]["report_id"]
    rebuilt = client.get(f"/reports/{rebuilt_id}.json")
    # The replay session serves through the original DATA_DIR registry; read
    # the rebuilt file from the replay workspace instead.
    monkeypatch.setenv("DATA_DIR", str(replay_root))
    after_report = json.loads((replay_root / "reports" / f"{rebuilt_id}.json").read_text(encoding="utf-8"))
    assert after_report["wonder_review"] == before_report["wonder_review"]
    assert after_report["hypotheses_final"][0]["hypothesis_id"] == before_report["hypotheses_final"][0]["hypothesis_id"]
    # Cached response rode along and was seeded for the $0 replay.
    cache_names = [n for n in members if n.startswith("llm_cache/")]
    assert cache_names, "export must embed the cached model response"
    assert next((replay_root / "llm_cache").glob("*.json"), None) is not None


def test_export_refuses_missing_blob(client, tmp_path, monkeypatch, capsys):
    from mmc import sessions as sess_mod

    exporter = _load_exporter()
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    sid = r.json()["session_id"]
    session = sess_mod.get_registry().get(sid)
    assert session is not None
    missing = "b" * 64
    sess_mod.get_registry().append(
        session,
        "evidence_added",
        {
            "evidence_id": uuid.uuid4().hex,
            "type": "image",
            "blob_ref": f"blobs/{missing}",
            "content_hash": missing,
            "ts": sess_mod._utc_now(),
        },
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert exporter.main([sid, "--out", str(tmp_path / "x.mmrec")]) == 1
    assert missing in capsys.readouterr().err


def test_export_refuses_missing_cached_response(client, tmp_path, monkeypatch, capsys):
    from mmc import sessions as sess_mod

    exporter = _load_exporter()
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    sid = r.json()["session_id"]
    session = sess_mod.get_registry().get(sid)
    assert session is not None
    fake_sha = hashlib.sha256(b"missing-cache-request").hexdigest()
    sess_mod.get_registry().append(
        session,
        "llm_call",
        {
            "kind": "llm_call",
            "model": "muse-spark-1.3",
            "request_sha256": fake_sha,
            "request": {"op": "test"},
            "output_text": "{}",
            "usage": {},
            "cost_usd": 0.0,
            "cached": False,
        },
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert exporter.main([sid, "--out", str(tmp_path / "y.mmrec")]) == 1
    assert fake_sha in capsys.readouterr().err


def test_export_truncates_vin(client, tmp_path, monkeypatch):
    from mmc import sessions as sess_mod
    from mmc.replay import mmrec

    sid = _finished_session(client, tmp_path, vin=VIN)
    exporter = _load_exporter()
    out = tmp_path / "real-vin.mmrec"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert exporter.main([sid, "--out", str(out)]) == 0
    members = mmrec.loads(out.read_bytes())
    meta = json.loads(members["meta.json"].decode("utf-8"))
    assert meta["vehicle"].get("vin_last6") == LAST6
    assert "vin_encrypted" not in meta["vehicle"]
    assert VIN not in out.read_bytes().decode("utf-8", errors="ignore")
    # Chain intact: the encrypted token stays in events, plaintext never does.
    assert VIN not in members["events.jsonl"].decode("utf-8")
    session = sess_mod.get_registry().get(sid)
    assert session is not None and session.store.verify_chain()


def test_fixture_listing_orders_real_first(client, tmp_path, monkeypatch):
    from mmc.replay import mmrec, runner

    dest = tmp_path / "fixtures" / "sessions"
    dest.mkdir(parents=True)
    sim_meta = {"role": "demo", "label": mmrec.SIM_LABEL, "display_label": "Z sim demo", "scenario": "vacuum_leak"}
    ci_meta = {"role": "ci", "scenario": "healthy"}
    roleless_meta = {"scenario": "healthy"}
    real_meta = {"role": "demo", "label": mmrec.REAL_LABEL, "display_label": LABEL, "origin": "real", "scenario": "real-vehicle"}
    (dest / "sim-a.mmrec").write_bytes(mmrec.create({"events.jsonl": b""}, sim_meta))
    (dest / "sim-healthy.mmrec").write_bytes(mmrec.create({"events.jsonl": b""}, ci_meta))
    (dest / "legacy.mmrec").write_bytes(mmrec.create({"events.jsonl": b""}, roleless_meta))
    (dest / "real-x.mmrec").write_bytes(mmrec.create({"events.jsonl": b""}, real_meta))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    listed = runner.list_fixtures()
    names = [e["name"] for e in listed]
    # CI fixtures (and role-less legacy bundles, which default to ci) never
    # reach the judge landing — the sim-healthy regression.
    assert "sim-healthy.mmrec" not in names, names
    assert "legacy.mmrec" not in names, names
    seeded_names = [n for n in names if n in ("sim-a.mmrec", "real-x.mmrec")]
    assert seeded_names and seeded_names[0] == "real-x.mmrec", names
    assert listed[0]["origin"] == "real"
    assert listed[0]["display_label"] == LABEL

    r = client.get("/fixtures")
    assert r.status_code == 200, r.text
    api_names = [e["name"] for e in r.json()["fixtures"]]
    assert "sim-healthy.mmrec" not in api_names, api_names
    assert "legacy.mmrec" not in api_names, api_names
    api_seeded = [n for n in api_names if n in ("sim-a.mmrec", "real-x.mmrec")]
    assert api_seeded and api_seeded[0] == "real-x.mmrec", api_names


def test_fixture_default_is_vacuum_leak_until_real_bundle_exists(tmp_path, monkeypatch):
    """The landing auto-plays the first /fixtures entry: sim-vacuum-leak
    with no real bundle, the real bundle once the owner exports one."""
    from mmc.replay import mmrec, runner

    dest = tmp_path / "fixtures" / "sessions"
    dest.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    first = runner.list_fixtures()[0]["name"]
    assert first == "sim-vacuum-leak.mmrec", first
    real_meta = {"role": "demo", "label": mmrec.REAL_LABEL, "display_label": LABEL, "origin": "real", "scenario": "real-vehicle"}
    (dest / "real-ford.mmrec").write_bytes(mmrec.create({"events.jsonl": b""}, real_meta))
    first = runner.list_fixtures()[0]["name"]
    assert first == "real-ford.mmrec", first
