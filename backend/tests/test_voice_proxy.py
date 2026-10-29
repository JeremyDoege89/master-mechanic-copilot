"""M7 §1 voice proxy: fake upstream, budget, guards, confirm-required."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess_mod
from mmc.api import routes as api_routes
from mmc.main import app
from mmc.meta import voice as voice_mod
from mmc.meta.budget import BudgetTracker, estimate_asr


class FakeUpstream:
    """Scripted upstream: recv yields finals, records everything sent."""

    def __init__(self, incoming: list):
        self.incoming = list(incoming)
        self.sent: list = []
        self.config = None

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self.incoming:
            raise StopAsyncIteration("upstream drained")
        item = self.incoming.pop(0)
        return item if isinstance(item, (bytes, bytearray)) else json.dumps(item)

    async def close(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_API_KEY", "test-key-never-logged")
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_MODE", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient, mode: str = "live") -> str:
    r = client.post("/sessions", json={"user_level": "L2", "tools": ["multimeter"], "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _tracker():
    return BudgetTracker(hard_stop_usd=120.0)


def test_session_config_pinned_shape_and_biasing():
    config = voice_mod.session_config()
    assert config["type"] == "session_config"
    assert config["model"] == "muse-voice-transcribe-1.0"
    assert config["sample_rate_hz"] == 16000
    assert config["encoding"] == "pcm16"
    assert "trim_idle_vs_2500" in config["keywords"]
    assert any("volts" in k for k in config["keywords"])


def test_final_appends_one_asr_segment_and_budgets(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_API_KEY", "test-key-never-logged")
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    tracker = _tracker()
    proxy = voice_mod.AsrProxy(
        upstream_factory=lambda config: FakeUpstream([]).__class__ and _immediate(config),
        tracker=tracker,
    )

    async def _immediate(config):
        return FakeUpstream([])

    appended: list[dict] = []

    def append_event(payload: dict):
        appended.append(payload)
        return sess_mod.get_registry().append(session, "asr_segment", payload)

    before = tracker.spend_usd
    payload = asyncio.run(
        proxy.handle_final({"type": "final", "text": "next test", "duration_s": 3.6}, session, append_event)
    )
    assert payload["text"] == "next test"
    assert payload["command"] == "next_test"
    assert len(appended) == 1
    assert tracker.spend_usd == pytest.approx(before + estimate_asr(3.6))
    # Key never reaches events, payloads, or the ledger.
    blob = json.dumps(appended) + json.dumps(payload)
    assert "test-key-never-logged" not in blob


def test_over_budget_final_raises_before_append(tmp_path, monkeypatch):
    import asyncio

    from mmc.meta.budget import BudgetExceeded

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    tracker = BudgetTracker(hard_stop_usd=0.0)
    proxy = voice_mod.AsrProxy(tracker=tracker)
    with pytest.raises(BudgetExceeded):
        asyncio.run(
            proxy.handle_final(
                {"type": "final", "text": "next test", "duration_s": 60.0},
                session,
                lambda payload: sess_mod.get_registry().append(session, "asr_segment", payload),
            )
        )
    types = [e["type"] for e in session.store.iter_replay()]
    assert "asr_segment" not in types


def test_asr_segment_alone_moves_no_posterior(client):
    sid = _create(client)
    before = client.get(f"/sessions/{sid}/state").json()
    top_before = before["hypotheses"][0]["current"]
    r = client.post(
        f"/sessions/{sid}/events", json={"type": "asr_segment", "payload": {"text": "next test"}}
    )
    assert r.status_code == 200, r.text
    after = client.get(f"/sessions/{sid}/state").json()
    assert after["hypotheses"][0]["current"] == pytest.approx(top_before)
    assert after["tests_log"] == []


def test_asr_socket_unknown_session_4404(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/sessions/nope/asr"):
            pass
    assert excinfo.value.code == 4404


def test_asr_socket_replay_4409(client):
    from starlette.websockets import WebSocketDisconnect

    sid = _create(client, mode="replay")
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/sessions/{sid}/asr"):
            pass
    assert excinfo.value.code == 4409


def test_asr_socket_public_4501(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("MMC_PUBLIC_MODE", "replay")
    sid = _create(client, mode="replay")
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/sessions/{sid}/asr"):
            pass
    assert excinfo.value.code == 4501
