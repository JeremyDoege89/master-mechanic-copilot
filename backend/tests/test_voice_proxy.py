"""M7 §1 voice proxy: fake upstream, budget, guards, confirm-required."""

from __future__ import annotations

import asyncio
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
        # Yield like a real socket so the pump and client tasks interleave.
        await asyncio.sleep(0)
        self.sent.append(data)

    async def recv(self):
        await asyncio.sleep(0)
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
    # Documented RealtimeHandshakeRequest fields (key-free part only).
    config = voice_mod.session_config()
    assert config["audioEncoding"] == "PCM_24KHZ"
    assert config["model"] == "muse-voice-transcribe-1.0"
    assert config["mode"] == "PUSH_TO_TALK"
    assert "trim_idle_vs_2500" in config["keywords"]
    assert any("volts" in k for k in config["keywords"])
    # The credential travels only in build_handshake(), never here.
    assert "authorization" not in config
    assert "accessToken" not in json.dumps(config)
    assert set(config) == {"audioEncoding", "model", "mode", "keywords"}


def test_handshake_build_and_redact():
    config = voice_mod.session_config()
    full = voice_mod.build_handshake(config, "SENTINEL-KEY-NEVER-LOGGED")
    assert full["authorization"] == {"accessToken": "Bearer SENTINEL-KEY-NEVER-LOGGED"}
    assert full["audioEncoding"] == "PCM_24KHZ"
    redacted = voice_mod.redacted_handshake(full)
    assert "authorization" not in redacted
    blob = json.dumps(redacted) + json.dumps(config)
    assert "SENTINEL-KEY-NEVER-LOGGED" not in blob
    assert "Bearer " not in blob


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
        proxy.handle_final(
            {"type": "transcript", "transcript": "next test",
             "final": True, "audioProcessedMs": 3600},
            session, append_event,
        )
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
                {"type": "transcript", "transcript": "next test",
                 "final": True, "audioProcessedMs": 60000},
                session,
                lambda payload: sess_mod.get_registry().append(session, "asr_segment", payload),
            )
        )
    types = [e["type"] for e in session.store.iter_replay()]
    assert "asr_segment" not in types


def test_handshake_key_never_leaks(tmp_path, monkeypatch, caplog):
    """Key in the handshake send only — never in events, client frames, logs."""
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sentinel = "SENTINEL-KEY-NEVER-LOGGED"

    seen_fakes: list = []

    async def factory(config):
        fake = FakeUpstream([
            {"type": "transcript", "transcript": "next",
             "final": False, "audioProcessedMs": 800},
            {"type": "audioProgress", "audioProcessedMs": 800},
            {"type": "transcript", "transcript": "next test",
             "final": True, "audioProcessedMs": 1200},
        ])
        # Production order: the full handshake (with the in-frame
        # credential) is the first frame the proxy sends upstream.
        await fake.send(json.dumps(voice_mod.build_handshake(config, sentinel)))
        seen_fakes.append(fake)
        return fake

    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    proxy = voice_mod.AsrProxy(upstream_factory=factory, tracker=_tracker())
    appended: list[dict] = []
    client_frames: list[str] = []

    def append_event(payload: dict):
        appended.append(payload)
        return sess_mod.get_registry().append(session, "asr_segment", payload)

    script = [
        {"bytes": b"\x00" * 4800},
        {"text": json.dumps({"type": "stop"})},
    ]

    class FakeClient:
        async def receive(self):
            await asyncio.sleep(0)
            if script:
                return script.pop(0)
            # Stay connected like a real client reading finals; close
            # once the upstream final has landed in the event log.
            for _ in range(500):
                if appended:
                    break
                await asyncio.sleep(0.01)
            raise RuntimeError("client closed")

        async def send_text(self, text: str):
            raise AssertionError("proxy must use send_to_client")

    async def send_to_client(text: str):
        client_frames.append(text)

    with caplog.at_level("INFO"):
        asyncio.run(proxy.run(FakeClient(), session, append_event,
                              send_to_client=send_to_client))

    assert len(seen_fakes) == 1
    recorded = seen_fakes[0].sent
    # The credential travels in the handshake frame only: first frame
    # sent, and no frame after it carries the key.
    first = json.loads(recorded[0])
    assert first["authorization"] == {"accessToken": "Bearer " + sentinel}
    assert not [f for f in recorded[1:] if isinstance(f, str) and sentinel in f]
    assert len(appended) == 1
    assert appended[0]["text"] == "next test"
    assert appended[0]["command"] == "next_test"
    assert appended[0]["duration_s"] == pytest.approx(1.2)
    # Partials forward verbatim; the final forwards as the client frame.
    partials = [json.loads(f) for f in client_frames]
    assert {"type": "transcript", "transcript": "next",
            "final": False, "audioProcessedMs": 800} in partials
    finals = [f for f in partials if f.get("type") == "final"]
    assert len(finals) == 1 and finals[0]["text"] == "next test"
    # audioProgress never reaches the client.
    assert not [f for f in partials if f.get("type") == "audioProgress"]
    # The sentinel key appears nowhere but the handshake send: not in
    # events, client frames, configs, or captured logs.
    leak_surfaces = (
        json.dumps(appended) + json.dumps(client_frames)
        + json.dumps(voice_mod.session_config())
        + json.dumps(voice_mod.redacted_handshake(
            voice_mod.build_handshake(voice_mod.session_config(), sentinel)))
        + caplog.text
    )
    assert sentinel not in leak_surfaces


def test_proxy_maps_stop_to_endstream(tmp_path, monkeypatch):
    """Client stop half-closes upstream input via endStream (never 'stop')."""
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fake = FakeUpstream([
        {"type": "transcript", "transcript": "next test",
         "final": True, "audioProcessedMs": 1000},
    ])
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)

    async def _factory(config):
        return fake

    async def _send_to_client(text: str):
        return None

    proxy = voice_mod.AsrProxy(upstream_factory=_factory, tracker=_tracker())
    script = [{"text": json.dumps({"type": "stop"})}]

    class FakeClient:
        async def receive(self):
            await asyncio.sleep(0)
            if not script:
                raise RuntimeError("client closed")
            return script.pop(0)

    asyncio.run(proxy.run(FakeClient(), session, lambda payload: payload,
                          send_to_client=_send_to_client))

    texts = [s for s in fake.sent if isinstance(s, str)]
    assert json.dumps({"type": "endStream"}) in texts
    assert not [s for s in texts if json.loads(s).get("type") == "stop"]


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
