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


def test_handle_final_awaits_async_append(tmp_path, monkeypatch):
    """M7 review #1: the route passes an async append; it must be awaited.

    The production route's append_event is `async def` (it also
    broadcasts). handle_final called it without awaiting, so the
    coroutine was discarded and no asr_segment ever landed in the log.
    """
    import asyncio
    import warnings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    proxy = voice_mod.AsrProxy(tracker=_tracker())

    async def append_event(payload: dict):
        return sess_mod.get_registry().append(session, "asr_segment", payload)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        payload = asyncio.run(
            proxy.handle_final(
                {"type": "transcript", "transcript": "next test",
                 "final": True, "audioProcessedMs": 1200},
                session,
                append_event,
            )
        )
    assert payload["text"] == "next test"
    types = [e["type"] for e in session.store.iter_replay()]
    assert "asr_segment" in types


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


def test_asr_model_id_guard_rejects_contributor_and_foreign(monkeypatch):
    """M7 review #12: the voice path enforces the Meta/contributor guard."""
    from mmc.meta.client import MetaClientError

    monkeypatch.setenv("MODEL_ASR", "muse-voice-transcribe-1.0-contributor")
    with pytest.raises(MetaClientError):
        voice_mod.check_asr_model_id()
    with pytest.raises(MetaClientError):
        voice_mod.AsrProxy(tracker=_tracker())
    monkeypatch.setenv("MODEL_ASR", "gpt-4o-transcribe")
    with pytest.raises(MetaClientError):
        voice_mod.check_asr_model_id()
    with pytest.raises(MetaClientError):
        voice_mod.AsrProxy(tracker=_tracker())
    monkeypatch.setenv("MODEL_ASR", "muse-voice-transcribe-1.0")
    assert voice_mod.check_asr_model_id() == "muse-voice-transcribe-1.0"
    assert voice_mod.session_config()["model"] == "muse-voice-transcribe-1.0"


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


def test_final_without_reported_duration_bills_forwarded_audio(tmp_path, monkeypatch):
    """M7 review #3: no audioProcessedMs must not book $0.

    The proxy forwarded 48,000 PCM bytes (1.0 s at PCM_24KHZ) but the
    upstream final reports no duration. The ledger must bill the
    measured second, and the segment carries it.
    """
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    class GatedUpstream(FakeUpstream):
        """Yield the final only after the client audio arrived (like live)."""

        async def recv(self):
            for _ in range(5000):
                if self.sent:
                    break
                await asyncio.sleep(0.001)
            return await super().recv()

    fake = GatedUpstream([
        {"type": "transcript", "transcript": "next test", "final": True},
    ])
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    tracker = _tracker()
    proxy = voice_mod.AsrProxy(
        upstream_factory=lambda config: _immediate_fake(fake),
        tracker=tracker,
    )

    async def _immediate_fake(f):
        return f

    appended: list[dict] = []
    frames: list[str] = []
    script = [{"bytes": b"\x00" * 48000}]

    class FakeClient:
        async def receive(self):
            await asyncio.sleep(0)
            if script:
                return script.pop(0)
            # Hold the socket like a real client reading finals; the run
            # must still finish once the upstream final lands (#11).
            await asyncio.sleep(0.5)
            raise RuntimeError("client closed")

    async def send_to_client(text: str):
        frames.append(text)

    before = tracker.spend_usd
    asyncio.run(proxy.run(
        FakeClient(), session,
        lambda payload: appended.append(payload) or sess_mod.get_registry().append(
            session, "asr_segment", payload),
        send_to_client=send_to_client,
    ))
    assert len(appended) == 1
    assert appended[0]["duration_s"] == pytest.approx(1.0)
    assert tracker.spend_usd == pytest.approx(before + estimate_asr(1.0))
    assert tracker.spend_usd > before


def test_empty_final_appends_nothing_and_forwards_nothing(tmp_path, monkeypatch):
    """M7 review #3: a textless final (tone probe) is never a segment.

    A tone yields ``final ""`` (or a textless ``speechComplete``): the
    proxy must append no asr_segment and forward no final frame.
    """
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fake = FakeUpstream([
        {"type": "transcript", "transcript": "", "final": True, "audioProcessedMs": 500},
        {"type": "speechComplete"},
    ])
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    proxy = voice_mod.AsrProxy(
        upstream_factory=lambda config: _immediate_fake(fake),
        tracker=_tracker(),
    )

    async def _immediate_fake(f):
        return f

    appended: list[dict] = []
    frames: list[str] = []
    script = [{"text": json.dumps({"type": "stop"})}]

    class FakeClient:
        async def receive(self):
            await asyncio.sleep(0)
            if script:
                return script.pop(0)
            await asyncio.sleep(0.2)
            raise RuntimeError("client closed")

    async def send_to_client(text: str):
        frames.append(text)

    asyncio.run(proxy.run(
        FakeClient(), session,
        lambda payload: appended.append(payload) or sess_mod.get_registry().append(
            session, "asr_segment", payload),
        send_to_client=send_to_client,
    ))
    assert appended == []
    assert [f for f in frames if json.loads(f).get("type") == "final"] == []
    types = [e["type"] for e in session.store.iter_replay()]
    assert "asr_segment" not in types


def test_run_returns_after_upstream_finishes_with_client_holding_socket(
    tmp_path, monkeypatch
):
    """M7 review #11: run() must not hang once the upstream is done.

    Normal PUSH_TO_TALK completion ends the upstream while the client
    still holds its socket open. run() must notice the finished pump
    instead of blocking forever in client receive().
    """
    import asyncio

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fake = FakeUpstream([
        {"type": "transcript", "transcript": "next test",
         "final": True, "audioProcessedMs": 1000},
    ])
    closed = {"value": False}

    async def _close():
        closed["value"] = True

    fake.close = _close  # type: ignore[method-assign]
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    proxy = voice_mod.AsrProxy(
        upstream_factory=lambda config: _immediate_fake(fake),
        tracker=_tracker(),
    )

    async def _immediate_fake(f):
        return f

    appended: list[dict] = []

    class HoldingClient:
        async def receive(self):
            await asyncio.sleep(60)

    async def send_to_client(text: str):
        return None

    async def _main():
        await asyncio.wait_for(
            proxy.run(
                HoldingClient(), session,
                lambda payload: appended.append(payload) or sess_mod.get_registry().append(
                    session, "asr_segment", payload),
                send_to_client=send_to_client,
            ),
            timeout=5,
        )

    asyncio.run(_main())
    assert len(appended) == 1
    assert closed["value"] is True


def test_proxy_replays_live_spike_fixtures(tmp_path, monkeypatch):
    """The recorded live turn replays through the proxy verbatim.

    Upstream messages are rebuilt from ``stream_shapes.jsonl`` previews:
    every partial forwards byte-identically (as parsed content) and the
    final persists as the asr_segment with the reported duration.
    """
    import asyncio
    from pathlib import Path

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    base = Path(__file__).parent / "fixtures" / "asr"
    messages: list[dict] = []
    for line in (base / "stream_shapes.jsonl").read_text().splitlines():
        shape = json.loads(line)
        if shape["type"] not in ("transcript", "audioProgress"):
            continue
        msg = json.loads(shape["preview"])
        assert msg.get("type") == shape["type"]
        messages.append(msg)
    assert [m.get("transcript") for m in messages if m.get("type") == "transcript"] == [
        "one,", "one, three,", "one, three, five.", "one, three, five.",
    ]
    fake = FakeUpstream(messages)
    session = sess_mod.get_registry().create(user_level="L2", tools=[], mode="live", vehicle=None)
    proxy = voice_mod.AsrProxy(
        upstream_factory=lambda config: _immediate_fake(fake),
        tracker=_tracker(),
    )

    async def _immediate_fake(f):
        return f

    appended: list[dict] = []
    frames: list[str] = []
    script = [{"text": json.dumps({"type": "stop"})}]

    class FakeClient:
        async def receive(self):
            await asyncio.sleep(0)
            if script:
                return script.pop(0)
            await asyncio.sleep(0.5)
            raise RuntimeError("client closed")

    async def send_to_client(text: str):
        frames.append(text)

    asyncio.run(proxy.run(
        FakeClient(), session,
        lambda payload: appended.append(payload) or sess_mod.get_registry().append(
            session, "asr_segment", payload),
        send_to_client=send_to_client,
    ))
    assert len(appended) == 1
    assert appended[0]["text"] == "one, three, five."
    assert appended[0]["duration_s"] == pytest.approx(2.64)
    # Partials forward verbatim; audioProgress never reaches the client.
    partials = [json.loads(f) for f in frames]
    assert [m.get("transcript") for m in partials if m.get("type") == "transcript"] == [
        "one,", "one, three,", "one, three, five.",
    ]
    assert not [f for f in partials if f.get("type") == "audioProgress"]
    finals = [f for f in partials if f.get("type") == "final"]
    assert len(finals) == 1 and finals[0]["text"] == "one, three, five."


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
