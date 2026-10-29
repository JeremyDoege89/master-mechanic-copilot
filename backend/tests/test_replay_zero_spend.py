"""M6 §4: replay runner — bundled .mmrec replays at $0 with identical posteriors."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app
from mmc.meta.budget import get_tracker

FIXTURE = "sim-healthy.mmrec"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "true")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _spend() -> tuple[float, float]:
    return get_tracker("meta").spend_usd, get_tracker("baseten").spend_usd


def test_fixture_replay_is_free_and_matches_fresh_state(client):
    meta_before, baseten_before = _spend()
    r = client.post("/replay", json={"fixture": FIXTURE})
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]

    session = sess.get_registry().get(session_id)
    assert session is not None
    assert session.meta["mode"] == "replay"
    assert session.store.verify_chain()

    replayed = client.get(f"/sessions/{session_id}/state").json()
    fresh_id = client.post(
        "/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"}
    ).json()["session_id"]
    fresh = client.get(f"/sessions/{fresh_id}/state").json()
    assert [h["current"] for h in replayed["hypotheses"]] == [
        h["current"] for h in fresh["hypotheses"]
    ]

    events = client.get(f"/sessions/{session_id}/events").json()["events"]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[0]["type"] == "session_started"
    tail = client.get(f"/sessions/{session_id}/events?after_seq=1").json()["events"]
    assert all(e["seq"] > 1 for e in tail)
    assert len(tail) == len(events) - 1

    rep = client.post(f"/sessions/{session_id}/report")
    assert rep.status_code == 200, rep.text
    meta_after, baseten_after = _spend()
    assert meta_after == meta_before
    assert baseten_after == baseten_before


def test_session_copy_replay_preserves_posteriors_bitwise(client):
    live_id = client.post(
        "/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"}
    ).json()["session_id"]
    r = client.post(
        f"/sessions/{live_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text
    live_state = client.get(f"/sessions/{live_id}/state").json()

    r = client.post("/replay", json={"session_id": live_id})
    assert r.status_code == 200, r.text
    copy_id = r.json()["session_id"]
    copy_state = client.get(f"/sessions/{copy_id}/state").json()
    assert [h["current"] for h in copy_state["hypotheses"]] == [
        h["current"] for h in live_state["hypotheses"]
    ]
    assert copy_state["tests_log"] == live_state["tests_log"]


def test_state_upto_seq_scrubs_the_fold(client):
    live_id = client.post(
        "/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"}
    ).json()["session_id"]
    r = client.post(
        f"/sessions/{live_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text
    full = client.get(f"/sessions/{live_id}/state").json()
    assert full["tests_log"], "expected a folded test result"
    early = client.get(f"/sessions/{live_id}/state?upto_seq=1").json()
    assert early["tests_log"] == []
    mid = client.get(f"/sessions/{live_id}/state?upto_seq=2").json()
    assert len(mid["tests_log"]) <= len(full["tests_log"])


def test_replay_rejects_unknown_source(client):
    r = client.post("/replay", json={})
    assert r.status_code == 400, r.text
    r = client.post("/replay", json={"fixture": "no-such.mmrec"})
    assert r.status_code == 404, r.text
    r = client.post("/replay", json={"session_id": "nope"})
    assert r.status_code == 404, r.text
