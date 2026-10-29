"""M7 ASR realtime proxy (plan .agents/plans/M7.md §1).

Android streams PCM16 to the backend WS; the backend adds the Meta key
upstream only and forwards partials verbatim. Each upstream final appends
one hash-chained ``asr_segment`` event; partials are never events. Cost
flows through the shared Meta tracker (``estimate_asr``); over-budget
closes 4402. No Spark Open fallback for voice; transcripts never set
probabilities (only ``test_result`` folds into posteriors).

Live upstream wire format is PROVISIONAL until scripts/spike_voice_asr.py
runs outside the sandbox (fixtures carry provisional:true). Every test
below runs on provisional fixtures + a fake upstream; a fixture test
asserts the flag so provisional data is never mistaken for verified.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

from mmc.meta.budget import estimate_asr, get_tracker

ASR_REALTIME_URL = os.environ.get("ASR_REALTIME_URL", "wss://api.meta.ai/v1/asr/realtime")
ASR_MODEL = os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0")
SAMPLE_RATE_HZ = 16000


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
    """Exact session_config frame the proxy sends upstream (spike pins it)."""
    return {
        "type": "session_config",
        "model": ASR_MODEL,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "encoding": "pcm16",
        "channels": 1,
        "keywords": build_biasing_list(pack),
    }


def _final_text(msg: dict) -> str:
    for key in ("text", "transcript", "utterance"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _final_duration(msg: dict) -> float:
    for key in ("duration_s", "duration", "audio_duration_s"):
        try:
            return max(0.0, float(msg.get(key, 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_final(msg: Any) -> bool:
    return isinstance(msg, dict) and str(msg.get("type", "")).lower() in (
        "final", "utterance_end", "transcript_final",
    )


def _is_partial(msg: Any) -> bool:
    return isinstance(msg, dict) and str(msg.get("type", "")).lower() in (
        "partial", "transcript_partial",
    )


UpstreamFactory = Callable[[dict], Awaitable[Any]]


def default_upstream_factory(config: dict) -> Any:
    """Production upstream dialer (pending owner spike).

    Raises until the spike pins the WS client library; DECISIONS records
    the choice per AGENTS.md §8 when it lands. Tests always inject a fake.
    """
    raise RuntimeError(
        "live ASR upstream unavailable: no WS client chosen until "
        "scripts/spike_voice_asr.py runs (see docs/DECISIONS.md M7)"
    )


class AsrProxy:
    """PCM in, partials verbatim out, finals to the event log.

    ``upstream_factory`` receives the session_config frame and returns an
    object with async ``send``/``recv``/``close`` (``send`` takes str|bytes,
    ``recv`` returns str|bytes). ``append_event`` persists one asr_segment;
    ``send_to_client`` forwards text frames; ``tracker`` defaults to the
    shared Meta ledger.
    """

    def __init__(
        self,
        upstream_factory: UpstreamFactory | None = None,
        tracker: Any = None,
    ) -> None:
        self._factory = upstream_factory or default_upstream_factory
        self._tracker = tracker

    @property
    def tracker(self) -> Any:
        return self._tracker if self._tracker is not None else get_tracker("meta")

    async def handle_final(
        self,
        msg: dict,
        session: Any,
        append_event: Callable[[dict], Any],
    ) -> dict:
        """Persist one upstream final as asr_segment; return the event payload."""
        from mmc import voice_commands as vc_mod

        text = _final_text(msg)
        duration_s = _final_duration(msg)
        # Budget: pre-check the estimate, then ledger the actual seconds.
        self.tracker.check(estimate_asr(duration_s or 1.0))
        payload = {
            "text": text,
            "duration_s": duration_s,
            "command": vc_mod.parse(text).get("intent", "unknown"),
        }
        append_event(payload)
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
        frames are PCM16 forwarded verbatim. Upstream partials forward
        verbatim; finals persist + forward. Over-budget calls
        ``on_budget_exceeded`` (the route closes 4402) instead of appending.
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
                        await _send(json.dumps(
                            {"type": "final", **payload}, sort_keys=True
                        ))
                    elif str(msg.get("type", "")).lower() in ("usage", "session_end"):
                        await _send(json.dumps(msg, sort_keys=True))
                        return
            except Exception:
                return

        import asyncio as _asyncio

        pump = _asyncio.ensure_future(_pump_upstream())
        try:
            while True:
                try:
                    incoming = await client_ws.receive()
                except Exception:
                    break
                # Starlette TestClient/WS shapes: dict, str, or bytes.
                if isinstance(incoming, dict):
                    text = incoming.get("text")
                    data = incoming.get("bytes")
                    if isinstance(data, (bytes, bytearray)):
                        try:
                            await upstream.send(bytes(data))
                        except Exception:
                            break
                    elif isinstance(text, str):
                        try:
                            ctl = json.loads(text)
                        except ValueError:
                            continue
                        if isinstance(ctl, dict) and ctl.get("type") == "stop":
                            try:
                                await upstream.send(text)
                            except Exception:
                                break
                        # "start" needs no upstream frame beyond session_config.
                elif isinstance(incoming, (bytes, bytearray)):
                    try:
                        await upstream.send(bytes(incoming))
                    except Exception:
                        break
                elif isinstance(incoming, str):
                    try:
                        ctl = json.loads(incoming)
                    except ValueError:
                        continue
                    if isinstance(ctl, dict) and ctl.get("type") == "stop":
                        try:
                            await upstream.send(incoming)
                        except Exception:
                            break
                if pump.done():
                    break
        finally:
            if not pump.done():
                pump.cancel()
            try:
                await upstream.close()
            except Exception:
                pass
