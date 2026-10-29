#!/usr/bin/env python3
"""M7 voice ASR spike (plan .agents/plans/M7.md section 1).

Owner-run OUTSIDE the sandbox — it needs network + a key, like
spike_meta_upload.py. Probes the realtime Voice Transcribe endpoint and
saves KEY-FREE fixtures for the offline proxy build.

Usage (from repo root, OUTSIDE the sandbox):
    backend/.venv/bin/python scripts/spike_voice_asr.py [--wav PATH] [--seconds 5]

Key: MODEL_API_KEY env, or ~/.config/meta/model-api.env. The key is never
printed and never stored: fixtures hold only message shapes/sizes (no
headers, no key, no audio bytes — audio replaced by length stubs).

Calls, in order (each prints its own outcome; a failed call never stops the
later ones, but the exit is non-zero unless the session config round-trip
succeeds):
  1. Open ASR_REALTIME_URL (default wss://api.meta.ai/v1/asr/realtime) with
     Authorization: Bearer <key>, send one JSON session_config frame
     {model, sample_rate_hz, encoding, channels, keywords} and print the
     server's acknowledgement shape (field names verbatim).
  2. Stream a short PCM16 16 kHz mono clip as binary frames (~100 ms each),
     print each upstream message shape (partial/final events, usage /
     duration fields verbatim) with values truncated to 200 chars.
  3. Send a JSON {type: "stop"} frame, print the final + usage messages.

Test input is self-made only: a stdlib synth WAV ("next test", silence —
no real speech claims) written to /tmp when --wav is absent.

Fixture output (backend/tests/fixtures/asr/, provisional:true until the
owner flips them after a live run):
  session_config.json   — the exact config frame the spike sent
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
import os
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "asr"
DEFAULT_URL = os.environ.get("ASR_REALTIME_URL", "wss://api.meta.ai/v1/asr/realtime")
DEFAULT_MODEL = os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0")

SAMPLE_RATE_HZ = 16000
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
    """Deterministic PCM16 mono 16 kHz synth (440 Hz tone bursts, stdlib)."""
    n = int(SAMPLE_RATE_HZ * seconds)
    frames = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE_HZ
        on = (i % SAMPLE_RATE_HZ) < SAMPLE_RATE_HZ // 2
        v = 0.3 * __import__("math").sin(2 * __import__("math").pi * 440 * t) if on else 0.0
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


def session_config() -> dict:
    return {
        "type": "session_config",
        "model": DEFAULT_MODEL,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "encoding": "pcm16",
        "channels": 1,
        "keywords": KEYWORDS,
    }


def shape_of(msg) -> dict:
    if isinstance(msg, (bytes, bytearray)):
        return {"direction": "in", "type": "binary", "bytes": len(msg)}
    try:
        obj = json.loads(msg)
    except (ValueError, TypeError):
        text = str(msg)
        return {"direction": "in", "type": "text-unknown", "keys": [], "preview": text[:200]}
    if isinstance(obj, dict):
        return {
            "direction": "in",
            "type": str(obj.get("type", "json-unknown")),
            "keys": sorted(obj.keys()),
            "preview": json.dumps(obj, sort_keys=True)[:200],
        }
    return {"direction": "in", "type": "json-array", "keys": [], "preview": json.dumps(obj)[:200]}


async def run(url: str, pcm: bytes, seconds: float) -> int:
    try:
        import websockets  # type: ignore[import]
    except ImportError:
        print("spike: FAIL — no websocket client installed.")
        print("spike: install one to run live, e.g. `uv pip install websockets`,")
        print("spike: then re-run outside the sandbox. Fixtures not written.")
        print("spike: DECISIONS note needed if websockets becomes a backend dep.")
        return 1

    key = load_key()
    try:
        from mmc.meta import budget as budget_mod

        est = budget_mod.estimate_asr(seconds)
        print(f"spike: estimate_asr({seconds:.1f}s) = ${est:.6f} on the Meta $120 ledger")
    except ImportError:
        pass

    config = session_config()
    print(f"spike: connecting {url} model={DEFAULT_MODEL}")
    print(f"spike: session_config keys={sorted(config.keys())} keywords={len(KEYWORDS)}")
    shapes: list[dict] = [
        {"direction": "out", "type": "session_config", "keys": sorted(config.keys()),
         "preview": json.dumps(config, sort_keys=True)[:200], "provisional": True}
    ]
    ok = False
    try:
        async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
            await ws.send(json.dumps(config))
            print("spike: [1] session_config sent, waiting for ack (5 s)...")
            try:
                ack = await asyncio.wait_for(ws.recv(), timeout=5)
                sh = shape_of(ack)
                sh["provisional"] = True
                shapes.append(sh)
                print(f"spike: [1] ack shape type={sh['type']} keys={sh.get('keys')}")
                print(f"spike: [1] ack preview {sh.get('preview', '')[:200]}")
                ok = True
            except asyncio.TimeoutError:
                print("spike: [1] no ack in 5 s (endpoint may differ — shapes unknown)")
            # 2. Stream PCM as ~100 ms binary frames.
            frame = int(SAMPLE_RATE_HZ * 0.1) * 2
            chunks = [pcm[i : i + frame] for i in range(0, len(pcm), frame)]
            print(f"spike: [2] streaming {len(pcm)} PCM bytes in {len(chunks)} binary frames...")
            for i, chunk in enumerate(chunks):
                await ws.send(chunk)
                if i == 0:
                    print(f"spike: [2] first binary frame {len(chunk)} bytes sent")
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.2)
                    sh = shape_of(msg)
                    sh["provisional"] = True
                    shapes.append(sh)
                    print(f"spike: [2] upstream type={sh['type']} keys={sh.get('keys')}")
                    print(f"spike: [2] preview {sh.get('preview', '')[:200]}")
                except asyncio.TimeoutError:
                    pass
            # 3. Stop frame, drain finals + usage.
            await ws.send(json.dumps({"type": "stop"}))
            print("spike: [3] stop sent, draining (5 s)...")
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    sh = shape_of(msg)
                    sh["provisional"] = True
                    shapes.append(sh)
                    print(f"spike: [3] upstream type={sh['type']} keys={sh.get('keys')}")
                    print(f"spike: [3] preview {sh.get('preview', '')[:200]}")
                    if sh["type"] in ("usage", "session_end", "closed"):
                        break
            except asyncio.TimeoutError:
                print("spike: [3] drain timed out (no terminal usage message seen)")
    except Exception as e:  # noqa: BLE001 - spike reports, never raises
        print(f"spike: FAILED: {type(e).__name__}: {e}")
        return 1

    # Key-free fixtures (shapes + sizes only, never the key or audio).
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, obj in (
        ("session_config.json", {**config, "provisional": True,
                                 "captured": "spike_voice_asr.py owner run"}),
        ("pcm_stub.json", {"sample_rate_hz": SAMPLE_RATE_HZ, "encoding": "pcm16",
                           "channels": 1, "seconds": seconds, "bytes": len(pcm),
                           "provisional": True,
                           "captured": "spike_voice_asr.py owner run"}),
    ):
        text = json.dumps(obj, indent=2, sort_keys=True)
        for banned in ("Authorization", "api_key", "Bearer "):
            if banned in text:
                raise SystemExit(f"spike: refusing to write fixture containing {banned!r}")
        (FIXTURE_DIR / name).write_text(text + "\n", encoding="utf-8")
        print(f"spike: wrote {FIXTURE_DIR / name}")
    with (FIXTURE_DIR / "stream_shapes.jsonl").open("w", encoding="utf-8") as fh:
        for sh in shapes:
            fh.write(json.dumps({**sh, "captured": "spike_voice_asr.py owner run"},
                                sort_keys=True) + "\n")
    print(f"spike: wrote {FIXTURE_DIR / 'stream_shapes.jsonl'} ({len(shapes)} shapes)")
    print(f"spike: outcome session_config_ack={'OK' if ok else 'FAIL'}")
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
