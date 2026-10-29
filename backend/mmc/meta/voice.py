"""M7 ASR realtime proxy (plan .agents/plans/M7.md §1).

Android streams PCM to the backend WS; the backend adds the Meta key
upstream only and forwards partials verbatim. Each upstream final appends
one hash-chained ``asr_segment`` event; partials are never events. Cost
flows through the shared Meta tracker (``estimate_asr``); over-budget
closes 4402. No Spark Open fallback for voice; transcripts never set
probabilities (only ``test_result`` folds into posteriors).

Upstream wire format follows ``docs/research/meta-voice/`` (Voice
Transcribe realtime): the handshake is the first JSON text frame on
``wss://api.meta.ai/v1/asr/realtime`` — camelCase ``audioEncoding``
(``PCM_24KHZ``, the engine-native 16-bit mono rate the Android client
captures), ``model``, ``mode`` (``PUSH_TO_TALK``: single voice-command
turn), and ``keywords`` vocabulary biasing — with the credential inside
``authorization.accessToken`` (``"Bearer <key>"``; the endpoint ignores
the HTTP ``Authorization`` header). Audio goes as raw PCM binary frames
paced at real time; ``{"type": "endStream"}`` ends input and the server
is read until close 1000. Server events dispatch on ``type``
(``transcript`` with a ``final`` flag, ``audioProgress``, ``speech*``,
``speaker``, ``error``); unknown types are ignored.

Key hygiene: ``session_config()`` is key-free (safe to log and fixture).
``build_handshake()`` adds ``authorization`` only for the single upstream
send; the key never reaches logs, fixtures, ``asr_segment`` events, or
client frames (pinned by ``test_handshake_key_never_leaks`` with a
frame-recording fake upstream).

Live upstream fixtures under ``backend/tests/fixtures/asr/`` are
``provisional:false`` since the owner reran ``scripts/spike_voice_asr.py``
live on real speech ("one, three, five."); a fixture test pins the flag
plus the recorded partial/final transcripts, and the proxy suite replays
them.
"""

from __future__ import annotations

import inspect
import json
import os
import uuid
from typing import Any, Awaitable, Callable

from mmc.meta.budget import estimate_asr, get_tracker

ASR_REALTIME_URL = os.environ.get("ASR_REALTIME_URL", "wss://api.meta.ai/v1/asr/realtime")
ASR_MODEL = os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0")


def check_asr_model_id(model_id: str | None = None) -> str:
    """Validate the ASR model pin (M7 review #12).

    Same rule as ``MetaClient`` (client.py): contributor-mode IDs and
    non-Meta IDs are refused instead of being sent upstream and echoed
    into session ``model_pins`` as if pinned.
    """
    from mmc.meta.client import MODEL_KEY_PREFIXES, MetaClientError
    from mmc.meta.client import _is_contributor_model

    model = model_id if model_id is not None else os.environ.get(
        "MODEL_ASR", "muse-voice-transcribe-1.0"
    )
    if _is_contributor_model(model):
        raise MetaClientError(f"refusing contributor-mode model ID for asr: {model!r}")
    prefixes = MODEL_KEY_PREFIXES.get("asr", ())
    if not model.strip().lower().startswith(prefixes):
        raise MetaClientError(
            f"refusing non-Meta model ID for asr: {model!r} (want prefix {prefixes!r})"
        )
    return model
# Documented engine-native encoding (signed 16-bit little-endian mono at
# 24 kHz = 48,000 bytes/s). Must match the Android capture rate
# (VoiceAudioSpec.SAMPLE_RATE_HZ).
AUDIO_ENCODING = "PCM_24KHZ"
SAMPLE_RATE_HZ = 24000
# Single-turn voice commands ("next test", "log 12.4 volts"): the client
# delimits the turn with endStream; completion is a transcript final:true.
ASR_MODE = "PUSH_TO_TALK"


def build_biasing_list(pack: Any = None) -> list[str]:
    """Keyword-biasing list from the active pack (one session_config fn).

    Test refs in spoken form, hypothesis L1–L3 labels, outcome tokens and
    units — e.g. ``trim_idle_vs_2500``, "unmetered air leak", ``volts``.
    """
    if pack is None:
        try:
            from mmc import sessions as sess_mod

            pack = sess_mod.get_pack()
        except Exception:
            pack = None
    keywords: list[str] = []
    if pack is None:
        return ["next test", "volts"]
    for ref in getattr(pack, "tests", {}):
        keywords.append(str(ref))
    for _hyp, spec in (getattr(pack, "hypotheses", {}) or {}).items():
        label = (spec or {}).get("label") or {}
        for level in ("L1", "L2", "L3"):
            text = label.get(level)
            if text:
                keywords.append(str(text))
    for _ref, test in (getattr(pack, "tests", {}) or {}).items():
        for outcome in getattr(test, "outcomes", []) or []:
            keywords.append(str(outcome))
    keywords += ["volts", "next test", "trim"]
    # De-duplicate, keep order.
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw and kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def session_config(pack: Any = None) -> dict:
    """Key-free handshake fields (safe to log, fixture, and shape-pin).

    ``build_handshake`` adds ``authorization`` for the single upstream
    send; this frame never carries the key. The model pin is validated
    (M7 review #12), so a bad ``MODEL_ASR`` fails here, not upstream.
    """
    return {
        "audioEncoding": AUDIO_ENCODING,
        "model": check_asr_model_id(),
        "mode": ASR_MODE,
        "keywords": build_biasing_list(pack),
    }


def build_handshake(config: dict, api_key: str) -> dict:
    """Full first-frame handshake: config plus the in-frame credential."""
    return {"authorization": {"accessToken": f"Bearer {api_key}"}, **config}


def redacted_handshake(frame: dict) -> dict:
    """Handshake with the credential removed (logging / fixtures)."""
    return {k: v for k, v in frame.items() if k != "authorization"}


def load_api_key() -> str:
    """Runtime Meta key (never committed, never logged, never fixtured)."""
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set (runtime only; never commit it)")
    return key


def _final_text(msg: dict) -> str:
    for key in ("transcript", "text", "utterance"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _final_duration_s(msg: dict) -> float:
    # Documented progress unit is milliseconds (audioProcessedMs).
    try:
        ms = msg.get("audioProcessedMs", None)
        if ms is not None:
            return max(0.0, float(ms) / 1000.0)
    except (TypeError, ValueError):
        pass
    for key in ("duration_s", "duration", "audio_duration_s"):
        try:
            return max(0.0, float(msg.get(key, 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_final(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    kind = str(msg.get("type", ""))
    if kind == "transcript":
        return msg.get("final") is True
    # Turn-completion event (ENDPOINTING/DIARIZATION modes); tolerated so a
    # mode change still closes the turn instead of dropping its text.
    if kind == "speechComplete":
        return True
    return kind.lower() in ("final", "utterance_end", "transcript_final")


def _is_partial(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    kind = str(msg.get("type", ""))
    if kind == "transcript":
        return msg.get("final") is not True
    return kind.lower() in ("partial", "transcript_partial")


UpstreamFactory = Callable[[dict], Awaitable[Any]]


class _WsUpstream:
    """Thin send/recv/close wrapper over a live Meta realtime socket."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send(self, data: Any) -> None:
        await self._ws.send(data)

    async def recv(self) -> Any:
        return await self._ws.recv()

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


async def default_upstream_factory(config: dict) -> Any:
    """Dial Meta realtime: handshake first frame, ack before audio.

    Sends ``build_handshake(config, key)`` as the first JSON text frame
    (within the 10 s handshake deadline) and waits for the acknowledgement
    — the only server frame with no ``type`` field, carrying ``sessionId``.
    An ``error``-typed first frame raises with the server message.
    """
    # M7 review #12: never dial with a contributor/foreign model ID.
    check_asr_model_id()
    try:
        import websockets  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "live ASR upstream unavailable: install the 'websockets' "
            "dependency (see backend/pyproject.toml)"
        ) from exc
    import asyncio as _asyncio

    key = load_api_key()
    session_id = f"mmc-{uuid.uuid4()}"
    url = ASR_REALTIME_URL
    if "?sessionId=" not in url and "&sessionId=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sessionId={session_id}"
    ws = await websockets.connect(url, open_timeout=30)
    try:
        await ws.send(json.dumps(build_handshake(config, key)))
        raw = await _asyncio.wait_for(ws.recv(), timeout=10)
        ack = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(ack, dict):
            raise RuntimeError(f"ASR handshake failed: unexpected ack {ack!r:.200}")
        if ack.get("type") == "error":
            raise RuntimeError(f"ASR handshake failed: {ack.get('message', ack)!r:.200}")
        if "sessionId" not in ack:
            raise RuntimeError(f"ASR handshake failed: {ack!r:.200}")
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
        raise
    return _WsUpstream(ws)


class AsrProxy:
    """PCM in, partials verbatim out, finals to the event log.

    ``upstream_factory`` receives the key-free ``session_config`` frame and
    returns an object with async ``send``/``recv``/``close`` (``send`` takes
    str|bytes, ``recv`` returns str|bytes). ``append_event`` persists one
    asr_segment; ``send_to_client`` forwards text frames; ``tracker``
    defaults to the shared Meta ledger.
    """

    # 16-bit mono PCM at 24 kHz: 48,000 forwarded bytes = 1 s of audio.
    PCM_BYTES_PER_SECOND = SAMPLE_RATE_HZ * 2

    def __init__(
        self,
        upstream_factory: UpstreamFactory | None = None,
        tracker: Any = None,
    ) -> None:
        # M7 review #12: refuse contributor/foreign model IDs at startup.
        self._asr_model = check_asr_model_id()
        self._factory = upstream_factory or default_upstream_factory
        self._tracker = tracker
        # PCM bytes forwarded upstream in the current turn (M7 review #3:
        # the only duration the proxy measures itself).
        self._pcm_bytes = 0

    @property
    def tracker(self) -> Any:
        return self._tracker if self._tracker is not None else get_tracker("meta")

    async def handle_final(
        self,
        msg: dict,
        session: Any,
        append_event: Callable[[dict], Any],
    ) -> dict | None:
        """Persist one upstream final as asr_segment; return the event payload.

        Returns ``None`` (appending and forwarding nothing) for a textless
        final — a tone probe's ``final ""`` or an empty ``speechComplete``
        is not a transcript. Billing always uses the maximum of the
        upstream-reported and the proxy-measured (forwarded PCM) duration
        so a missing ``audioProcessedMs`` can never book $0 (M7 review #3).
        """
        from mmc import voice_commands as vc_mod

        text = _final_text(msg)
        reported_s = _final_duration_s(msg)
        measured_s = max(0.0, self._pcm_bytes / self.PCM_BYTES_PER_SECOND)
        duration_s = max(reported_s, measured_s)
        # Budget: pre-check the estimate, then ledger the actual seconds.
        self.tracker.check(estimate_asr(duration_s or 1.0))
        self._pcm_bytes = 0
        if not text:
            self.tracker.add(estimate_asr(duration_s))
            return None
        payload = {
            "text": text,
            "duration_s": duration_s,
            "command": vc_mod.parse(text).get("intent", "unknown"),
        }
        # M7 review #1: the route's append is `async def` (append +
        # broadcast) while tests inject sync appends — await whatever is
        # awaitable so production finals actually land in the event log.
        result = append_event(payload)
        if inspect.isawaitable(result):
            await result
        self.tracker.add(estimate_asr(duration_s))
        return payload

    async def run(
        self,
        client_ws: Any,
        session: Any,
        append_event: Callable[[dict], Any],
        send_to_client: Callable[[str], Awaitable[None]] | None = None,
        on_budget_exceeded: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Bridge one client socket to one upstream connection.

        Control frames from the client are JSON text (start/stop); binary
        frames are PCM (``AUDIO_ENCODING``) forwarded verbatim. Upstream
        transcript partials forward verbatim; finals persist + forward.
        ``stop`` half-closes upstream input via ``endStream`` while pending
        results are still read. Over-budget calls ``on_budget_exceeded``
        (the route closes 4402) instead of appending.
        """
        from mmc.meta.budget import BudgetExceeded

        config = session_config()
        upstream = await self._factory(config)

        async def _send(text: str) -> None:
            if send_to_client is not None:
                await send_to_client(text)
            else:
                await client_ws.send_text(text)

        async def _pump_upstream() -> None:
            try:
                while True:
                    raw = await upstream.recv()
                    msg = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    if not isinstance(msg, dict):
                        try:
                            msg = json.loads(str(msg))
                        except ValueError:
                            continue
                    if _is_partial(msg):
                        await _send(json.dumps(msg, sort_keys=True))
                    elif _is_final(msg):
                        try:
                            payload = await self.handle_final(msg, session, append_event)
                        except BudgetExceeded:
                            if on_budget_exceeded is not None:
                                await on_budget_exceeded()
                            return
                        # A textless final returns None: billed, but neither
                        # appended nor forwarded (M7 review #3).
                        if payload is not None:
                            await _send(json.dumps(
                                {"type": "final", **payload}, sort_keys=True
                            ))
                    elif isinstance(msg, dict) and msg.get("type") == "error":
                        # Fatal server event (client-safe message only):
                        # forward it, then stop — the server closes next.
                        try:
                            await _send(json.dumps(
                                {"type": "error",
                                 "message": str(msg.get("message", "ASR error"))},
                                sort_keys=True,
                            ))
                        except Exception:
                            pass
                        return
                    # audioProgress / speechStart / speechEnd / speaker /
                    # unknown: progress, boundary and speaker labels stay
                    # upstream — the client renders transcript partials and
                    # finals. Unknown types are ignored per the realtime
                    # spec. (speechComplete takes the _is_final path above.)
            except Exception:
                return

        import asyncio as _asyncio

        async def _forward(incoming: Any) -> bool:
            """Forward one client message upstream; False = connection dead."""
            # Starlette TestClient/WS shapes: dict, str, or bytes.
            if isinstance(incoming, dict):
                text = incoming.get("text")
                data = incoming.get("bytes")
                if isinstance(data, (bytes, bytearray)):
                    # M7 review #3: measure the PCM the proxy forwards.
                    self._pcm_bytes += len(bytes(data))
                    try:
                        await upstream.send(bytes(data))
                    except Exception:
                        return False
                elif isinstance(text, str):
                    try:
                        ctl = json.loads(text)
                    except ValueError:
                        return True
                    if isinstance(ctl, dict) and ctl.get("type") == "stop":
                        try:
                            await upstream.send(json.dumps({"type": "endStream"}))
                        except Exception:
                            return False
                    # "start" needs no upstream frame beyond the handshake.
            elif isinstance(incoming, (bytes, bytearray)):
                self._pcm_bytes += len(bytes(incoming))
                try:
                    await upstream.send(bytes(incoming))
                except Exception:
                    return False
            elif isinstance(incoming, str):
                try:
                    ctl = json.loads(incoming)
                except ValueError:
                    return True
                if isinstance(ctl, dict) and ctl.get("type") == "stop":
                    try:
                        await upstream.send(json.dumps({"type": "endStream"}))
                    except Exception:
                        return False
            return True

        pump = _asyncio.ensure_future(_pump_upstream())
        recv_task: Any = None
        try:
            while True:
                # M7 review #11: race the client receive against pump
                # completion. The normal PUSH_TO_TALK ending finishes the
                # upstream while the client still holds its socket open —
                # blocking unconditionally in receive() would hang here
                # forever and never close the upstream. A client message
                # that already completed is still forwarded first, so a
                # queued stop/PCM burst is never dropped when the pump
                # wins the race.
                if recv_task is None:
                    recv_task = _asyncio.ensure_future(client_ws.receive())
                await _asyncio.wait(
                    {recv_task, pump}, return_when=_asyncio.FIRST_COMPLETED
                )
                if recv_task.done():
                    try:
                        incoming = recv_task.result()
                    except Exception:
                        break
                    recv_task = None
                    if not await _forward(incoming):
                        break
                if pump.done():
                    if recv_task is not None and not recv_task.done():
                        recv_task.cancel()
                    break
        finally:
            if recv_task is not None and not recv_task.done():
                recv_task.cancel()
            if not pump.done():
                pump.cancel()
            try:
                await upstream.close()
            except Exception:
                pass
