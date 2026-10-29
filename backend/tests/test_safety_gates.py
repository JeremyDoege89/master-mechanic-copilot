"""M6 §2: safety gates as code, stop-and-see-a-pro triggers, gated clear-codes flow."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient, level: str = "L1", tools: list | None = None) -> str:
    r = client.post(
        "/sessions", json={"user_level": level, "tools": tools or [], "mode": "sim"}
    )
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _event_types(session_id: str) -> list[str]:
    session = sess.get_registry().get(session_id)
    assert session is not None
    return [e["type"] for e in session.store.iter_replay()]


TRIM_MEASURED = {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}


def test_gated_result_409_before_ack_200_after(client):
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result",
        json={"measured": TRIM_MEASURED},
    )
    assert r.status_code == 409, r.text
    assert "engine_running_outdoors" in r.text

    ack = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}},
    )
    assert ack.status_code == 200, ack.text

    r2 = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result",
        json={"measured": TRIM_MEASURED},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["test_result"]["outcome"] == "lean_idle_normal_cruise"


def test_gate_checklist_served_from_safety_module(client):
    from mmc import safety as safety_mod

    session_id = _create(client)
    state = client.get(f"/sessions/{session_id}/state").json()
    gated = [
        t
        for t in state["next_tests"] + state["blocked_tests"]
        if t.get("safety_gate", "none") != "none"
    ]
    assert gated, "expected gated tests in a fresh session"
    for entry in gated:
        checklist = entry.get("gate_checklist")
        assert checklist, entry["kp_test_ref"]
        assert checklist["title"] and len(checklist["items"]) >= 2
        gate = entry["safety_gate"]
        assert checklist == safety_mod.gate_checklist(gate), gate


def test_srs_code_triggers_pro_referral_and_suppresses_cards(client):
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": ["B0100"]}},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["next_tests"] == []
    banner = state["stop_and_see_pro"]
    assert banner["banner"], "expected a pro-referral banner"
    assert any("B0100" in t for t in banner["triggers"])


def test_user_safety_flag_triggers_pro_referral(client):
    import uuid
    from datetime import datetime, timezone

    session_id = _create(client)
    payload = {
        "observation_id": uuid.uuid4().hex,
        "feature": "safety:flag",
        "value": "fuel_smell",
        "confidence": 0.9,
        "extractor": "human",
        "evidence_ids": [],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "observation_added", "payload": payload},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["next_tests"] == []
    assert any("fuel" in t.lower() for t in state["stop_and_see_pro"]["triggers"])


def test_clear_codes_request_needs_snapshot(client):
    session_id = _create(client)
    r = client.post(f"/sessions/{session_id}/clear-codes/request", json={})
    assert r.status_code == 400, r.text
    assert "codes_clear_requested" not in _event_types(session_id)


def test_clear_codes_happy_path_single_use(client):
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/clear-codes/request",
        json={"dtcs": ["P0303"], "freeze_frame": {"ect_c": 90}, "mode06": {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] and len(body["token"]) >= 32
    assert "readiness" in body["consequences_text"].lower()
    assert "freeze" in body["consequences_text"].lower()
    assert "codes_clear_requested" in _event_types(session_id)

    bad = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={"token": body["token"], "typed_confirmation": "clear codes"},
    )
    assert bad.status_code == 400, bad.text
    assert "codes_cleared" not in _event_types(session_id)

    no_attest = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={
            "token": body["token"],
            "typed_confirmation": "CLEAR CODES",
            "engine_off_key_on": False,
        },
    )
    assert no_attest.status_code == 400, no_attest.text
    assert "codes_cleared" not in _event_types(session_id)

    ok = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={
            "token": body["token"],
            "typed_confirmation": "CLEAR CODES",
            "engine_off_key_on": True,
        },
    )
    assert ok.status_code == 200, ok.text
    types = _event_types(session_id)
    assert "codes_clear_requested" in types and "codes_cleared" in types

    reuse = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={
            "token": body["token"],
            "typed_confirmation": "CLEAR CODES",
            "engine_off_key_on": True,
        },
    )
    assert reuse.status_code == 400, reuse.text
    assert types.count("codes_cleared") == 1


def test_clear_codes_unknown_and_expired_tokens_rejected(client):
    import time

    from mmc import safety as safety_mod

    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={
            "token": "nope-not-a-token",
            "typed_confirmation": "CLEAR CODES",
            "engine_off_key_on": True,
        },
    )
    assert r.status_code == 400, r.text
    assert "codes_cleared" not in _event_types(session_id)

    r2 = client.post(
        f"/sessions/{session_id}/clear-codes/request", json={"dtcs": []}
    )
    assert r2.status_code == 200, r2.text
    token = r2.json()["token"]
    safety_mod.backdate_token_for_test(token, time.time() - 10.0)
    expired = client.post(
        f"/sessions/{session_id}/clear-codes/confirm",
        json={
            "token": token,
            "typed_confirmation": "CLEAR CODES",
            "engine_off_key_on": True,
        },
    )
    assert expired.status_code == 400, expired.text
    assert "codes_cleared" not in _event_types(session_id)
