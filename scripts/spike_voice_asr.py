#!/usr/bin/env python3
"""M7 voice ASR spike (plan .agents/plans/M7.md section 1).

Owner-run OUTSIDE the sandbox — it needs network + a key, like
spike_meta_upload.py. Probes the realtime Voice Transcribe endpoint per
docs/research/meta-voice/ (handshake as the first JSON text frame, key
inside the handshake, raw PCM binary frames, endStream, read to close
1000) and saves KEY-FREE fixtures for the offline proxy build.

Usage (from repo root, OUTSIDE the sandbox):
    backend/.venv/bin/python scripts/spike_voice_asr.py [--wav PATH] [--seconds 5]

Key: MODEL_API_KEY env, or ~/.config/meta/model-api.env. The key is never
printed and never stored: fixtures hold only message shapes/sizes (no
key, no audio bytes — audio replaced by length stubs). The handshake
fixture is the REDACTED frame (authorization removed entirely).

Calls, in order (each prints its own outcome; a failed call never stops the
later ones, but the exit is non-zero unless the handshake round-trip
succeeds):
  1. Open ASR_REALTIME_URL (default wss://api.meta.ai/v1/asr/realtime)
     with a ?sessionId= query param and NO Authorization header, send the
     documented handshake frame {authorization, audioEncoding, model,
     mode, keywords} as the first JSON text frame and print the server's
     acknowledgement shape (field names verbatim — the ack is the only
     server frame with no `type` field).
  2. Stream a short PCM16 24 kHz mono clip as binary frames (~80 ms each,
     paced at real time per the realtime spec — bursting ahead trips the
     backlog policy), printing each upstream event type (transcript /
     audioProgress / speech* / speaker / error verbatim) with values
     truncated to 200 chars.
  3. Send {"type": "endStream"}, keep reading until the server closes,
     print the close code/reason and any final + usage messages.

Test input is self-made only: a stdlib synth WAV ("next test", silence —
no real speech claims) written to /tmp when --wav is absent.

Fixture output (backend/tests/fixtures/asr/, provisional:true until the
owner flips them after a live run):
  session_config.json   — the redacted handshake frame the spike sent
                          (authorization removed; key never stored)
  stream_shapes.jsonl   — one JSON object per upstream message SHAPE
                          {direction, type, keys, truncated_preview}
  pcm_stub.json         — {sample_rate_hz, encoding, seconds, bytes}
All three carry "provisional": true plus "captured": "spike_voice_asr.py
owner run". backend tests must assert the flag so provisional data is never
mistaken for verified (M5U pattern).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import struct
import sys
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "asr"
DEFAULT_URL = os.environ.get("ASR_REALTIME_URL", "wss://api.meta.ai/v1/asr/realtime")
DEFAULT_MODEL = os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0")

# Documented engine-native rate: PCM_24KHZ = signed 16-bit LE mono 24 kHz.
SAMPLE_RATE_HZ = 24000
FRAME_BYTES = SAMPLE_RATE_HZ * 2 * 80 // 1000  # ~80 ms per binary frame
# Provisional keyword-biasing list (plan §1): test refs + hypothesis labels
# + outcome tokens + units. The proxy's session_config() builds the real one
# from the active pack; the spike pins the wire FIELD name only.
KEYWORDS = [
    "trim_idle_vs_2500",
    "battery_voltage_rest_running",
    "unmetered air leak",
    "volts",
    "next test",
]


def load_key() -> str:
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config" / "meta" / "model-api.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("MODEL_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("spike: no key — set MODEL_API_KEY or ~/.config/meta/model-api.env")


def synth_wav(seconds: float = 3.0) -> bytes:
    """Deterministic PCM16 mono 24 kHz synth (440 Hz tone bursts, stdlib)."""
    n = int(SAMPLE_RATE_HZ * seconds)
    frames = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE_HZ
        on = (i % SAMPLE_RATE_HZ) < SAMPLE_RATE_HZ // 2
        v = 0.3 * math.sin(2 * math.pi * 440 * t) if on else 0.0
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE_HZ)
        wav.writeframes(bytes(frames))
    return buf.getvalue()


def pcm_from_wav(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1, f"want mono wav, got {wav.getnchannels()}ch"
        assert wav.getsampwidth() == 2, "want PCM16 wav"
        assert wav.getframerate() == SAMPLE_RATE_HZ, f"want {SAMPLE_RATE_HZ} Hz"
        return wav.readframes(wav.getnframes())


def shape_of(msg, direction: str = "in") -> dict:
    if isinstance(msg, (bytes, bytearray)):
        return {"direction": direction, "type": "binary", "bytes": len(msg)}
    try:
        obj = json.loads(msg)
    except (ValueError, TypeError):
        text = str(msg)
        return {"direction": direction, "type": "text-unknown", "keys": [],
                "preview": text[:200]}
    if isinstance(obj, dict):
        return {
            "direction": direction,
            "type": str(obj.get("type", "json-unknown")),
            "keys": sorted(obj.keys()),
            "preview": json.dumps(obj, sort_keys=True)[:200],
        }
    return {"direction": direction, "type": "json-array", "keys": [],
            "preview": json.dumps(obj)[:200]}


async def run(url: str, pcm: bytes, seconds: float) -> int:
    try:
        import websockets  # type: ignore[import]
    except ImportError:
        print("spike: FAIL — no websocket client installed.")
        print("spike: install one to run live, e.g. `uv pip install websockets`,")
        print("spike: then re-run outside the sandbox. Fixtures not written.")
        print("spike: DECISIONS note needed if websockets becomes a backend dep.")
        return 1

    from mmc.meta import budget as budget_mod
    from mmc.meta import voice as voice_mod

    key = load_key()
    try:
        est = budget_mod.estimate_asr(seconds)
        print(f"spike: estimate_asr({seconds:.1f}s) = ${est:.6f} on the Meta $120 ledger")
    except ImportError:
        pass

    # Documented handshake: proxy's key-free config plus the in-frame
    # credential. The endpoint ignores the HTTP Authorization header, so
    # none is sent. Redacted copy below is what fixtures record.
    config = {**voice_mod.session_config(), "keywords": KEYWORDS}
    handshake = voice_mod.build_handshake(config, key)
    redacted = voice_mod.redacted_handshake(handshake)
    session_id = f"spike-{uuid.uuid4()}"
    sep = "&" if "?" in url else "?"
    full_url = url if "sessionId=" in url else f"{url}{sep}sessionId={session_id}"
    print(f"spike: connecting {full_url.split('?')[0]} model={DEFAULT_MODEL} "
          f"audioEncoding={config['audioEncoding']}")
    print(f"spike: handshake keys={sorted(handshake.keys())} keywords={len(KEYWORDS)} "
          f"(authorization in-frame, never logged)")
    shapes: list[dict] = [
        {"direction": "out", "type": "handshake", "keys": sorted(redacted.keys()),
         "preview": json.dumps(redacted, sort_keys=True)[:200], "provisional": True}
    ]
    ok = False
    try:
        async with websockets.connect(full_url, open_timeout=30) as ws:
            await ws.send(json.dumps(handshake))
            print("spike: [1] handshake sent, waiting for ack (10 s)...")
            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=10)
                sh = shape_of(ack)
                sh["provisional"] = True
                shapes.append(sh)
                print(f"spike: [1] ack shape type={sh['type']} keys={sh.get('keys')}")
                print(f"spike: [1] ack preview {sh.get('preview', '')[:200]}")
                ok = sh["type"] == "json-unknown" and "sessionId" in (sh.get("keys") or [])
                if not ok:
                    print("spike: [1] ack is not the documented sessionId frame "
                          "(it may be an error event — see preview)")
            except asyncio.TimeoutError:
                print("spike: [1] no ack in 10 s (handshake deadline is 10 s)")

            # 2. Stream PCM paced at real time; print every server event.
            events: list = []

            async def receive_events() -> None:
                try:
                    async for message in ws:
                        if isinstance(message, bytes):
                            continue
                        sh = shape_of(message)
                        sh["provisional"] = True
                        events.append(sh)
                        print(f"spike: [2] upstream type={sh['type']} keys={sh.get('keys')}")
                        print(f"spike: [2] preview {sh.get('preview', '')[:200]}")
                        if sh["type"] == "error":
                            print("spike: [2] fatal error event — server will close next")
                except Exception as e:  # noqa: BLE001 - close codes reported below
                    print(f"spike: [2] receiver ended: {type(e).__name__}: {e}")

            chunks = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
            print(f"spike: [2] streaming {len(pcm)} PCM bytes in {len(chunks)} "
                  f"binary frames (~80 ms each, real-time paced)...")
            receiver = asyncio.create_task(receive_events())
            try:
                for i, chunk in enumerate(chunks):
                    await ws.send(chunk)
                    if i == 0:
                        print(f"spike: [2] first binary frame {len(chunk)} bytes sent")
                    await asyncio.sleep(0.08)
            except Exception as e:  # noqa: BLE001 - spike reports, never raises
                print(f"spike: [2] send failed: {type(e).__name__}: {e}")
            # 3. Half-close input, keep reading until the server closes.
            try:
                await ws.send(json.dumps({"type": "endStream"}))
                print("spike: [3] endStream sent, reading until close (15 s)...")
            except Exception as e:  # noqa: BLE001 - spike reports, never raises
                print(f"spike: [3] endStream send failed: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(ws.wait_closed(), timeout=15)
            except asyncio.TimeoutError:
                print("spike: [3] close not seen in 15 s; cancelling receiver")
                receiver.cancel()
            try:
                await asyncio.wait_for(receiver, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            shapes.extend(events)
            print(f"spike: [3] close code={ws.close_code} reason={ws.close_reason!r}")
            if ws.close_code == 1000:
                print("spike: [3] normal completion")
    except Exception as e:  # noqa: BLE001 - spike reports, never raises
        print(f"spike: FAILED: {type(e).__name__}: {e}")
        return 1

    # Key-free fixtures (shapes + sizes only — never the key, whose value
    # and "Bearer " prefix are both refused below).
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    secret_markers = [key, "Bearer "]
    for name, obj in (
        ("session_config.json", {**redacted, "provisional": True,
                                 "captured": "spike_voice_asr.py owner run"}),
        ("pcm_stub.json", {"sample_rate_hz": SAMPLE_RATE_HZ,
                           "encoding": config["audioEncoding"],
                           "channels": 1, "seconds": seconds, "bytes": len(pcm),
                           "provisional": True,
                           "captured": "spike_voice_asr.py owner run"}),
    ):
        text = json.dumps(obj, indent=2, sort_keys=True)
        for banned in secret_markers:
            if banned and banned in text:
                raise SystemExit("spike: refusing to write fixture containing key material")
        (FIXTURE_DIR / name).write_text(text + "\n", encoding="utf-8")
        print(f"spike: wrote {FIXTURE_DIR / name}")
    with (FIXTURE_DIR / "stream_shapes.jsonl").open("w", encoding="utf-8") as fh:
        for sh in shapes:
            line = json.dumps({**sh, "captured": "spike_voice_asr.py owner run"},
                              sort_keys=True)
            for banned in secret_markers:
                if banned and banned in line:
                    raise SystemExit("spike: refusing to write fixture containing key material")
            fh.write(line + "\n")
    print(f"spike: wrote {FIXTURE_DIR / 'stream_shapes.jsonl'} ({len(shapes)} shapes)")
    print(f"spike: outcome handshake_ack={'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="M7 live ASR realtime spike")
    ap.add_argument("--wav", default="")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()
    if args.wav:
        pcm = pcm_from_wav(Path(args.wav).read_bytes())
        seconds = len(pcm) / 2 / SAMPLE_RATE_HZ
    else:
        pcm = pcm_from_wav(synth_wav(args.seconds))
        seconds = args.seconds
    return asyncio.run(run(args.url, pcm, seconds))


if __name__ == "__main__":
    raise SystemExit(main())
