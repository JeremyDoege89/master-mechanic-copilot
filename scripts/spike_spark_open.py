#!/usr/bin/env python3
"""M5O Spark Open spike (plan .agents/plans/M5O.md section 6).

Live-probes Muse Spark Open (`meta-models/muse-spark-open-1026`) over Baseten
Chat Completions and saves the raw KEY-FREE responses as fixtures under
backend/tests/fixtures/spark_open/ for $0 replay.

Usage (from repo root, OUTSIDE the sandbox — it needs network + a key):
    backend/.venv/bin/python scripts/spike_spark_open.py [--image PATH] [--audio PATH]

Probes: a fuel-trim text question, an engine-bay image
(backend/tests/fixtures/images/engine_bay_02.jpg), and a short WAV. Audio is
unavailable on Spark Open (Oct 29: `input_audio`/`audio_url` carry no audio
tokens, `file` parts are rejected with 400) — the audio probe documents that
outcome as data and does NOT fail the spike.

Key: BASETEN_API_KEY env, or ~/.config/baseten/api.env. The key is never
printed and never stored: fixtures hold only key-free records (no headers,
no key, image bytes replaced by a sha256+length stub).

Cost is mapped from the Chat usage the API returns (prompt/completion plus
reasoning/cached detail blocks) at the assumed ledger rates ($1.25/M in,
$4.25/M out). Exits non-zero without writing fixtures when the API is
unreachable or the key is missing.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mmc.meta.budget import actual_cost_from_usage  # noqa: E402
from mmc.meta.spark_open import DEFAULT_SPARK_OPEN_MODEL, SparkOpenClient  # noqa: E402

FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "spark_open"
DEFAULT_IMAGE = ROOT / "backend" / "tests" / "fixtures" / "images" / "engine_bay_02.jpg"


def load_key() -> str:
    key = os.environ.get("BASETEN_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config" / "baseten" / "api.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("BASETEN_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("spike: no key — set BASETEN_API_KEY or ~/.config/baseten/api.env")


def save(name: str, record: dict) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, sort_keys=True)
    for banned in ("Authorization", "api_key", "Bearer ", "sk-"):
        if banned in text:
            raise SystemExit(f"spike: refusing to write fixture containing {banned!r}")
    if "baseten" in text.lower() and "api.env" in text:
        raise SystemExit("spike: refusing to write fixture referencing the key file")
    path = FIXTURE_DIR / name
    path.write_text(text + "\n", encoding="utf-8")
    return path


def make_wav(seconds: float = 2.0, freq: float = 440.0, rate: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="M5O live Spark Open spike")
    ap.add_argument("--image", default=str(DEFAULT_IMAGE))
    ap.add_argument("--audio", default="")
    args = ap.parse_args()

    key = load_key()
    image = Path(args.image)
    if not image.exists():
        raise SystemExit(f"spike: image not found: {image}")

    tmp = Path(tempfile.mkdtemp(prefix="mmc-spike-open-"))
    os.environ["DATA_DIR"] = str(tmp)

    client = SparkOpenClient(api_key=key, replay_cached=False)
    print(f"spike: model={client.model} base={client.base_url}")
    assert client.model == DEFAULT_SPARK_OPEN_MODEL

    # 1. Text probe (fuel-trim question, reasons beside content).
    result_t, rec_t = client.chat_text(
        "You are a vehicle-diagnosis assistant. Answer in one short paragraph.",
        "Fuel trims read +18% at idle but +4% at 2500 rpm on a gasoline V6. "
        "What single test best separates a post-MAF vacuum leak from a dirty MAF sensor?",
    )
    p_t = save("text_01.json", rec_t)
    print(f"spike: text -> {p_t.name}: latency={rec_t.get('latency_s', -1):.1f}s "
          f"usage={rec_t.get('usage')} cost=${rec_t.get('cost_usd', 0.0):.6f}")
    print(f"spike: text reasoning_chars={len(rec_t.get('reasoning_content', ''))} "
          f"content_chars={len(result_t.output_text)}")

    # 2. Image probe (real engine-bay photo as an image_url data-URL).
    image_bytes = image.read_bytes()
    result_i, rec_i = client.chat_image(
        "You are the vision front-end of a vehicle-diagnosis copilot. Reply with JSON only.",
        "List the visible under-hood components as JSON "
        '{"components": [{"name": str, "confidence": 0-1}]}.',
        image_bytes,
    )
    p_i = save("image_01.json", rec_i)
    print(f"spike: image -> {p_i.name}: latency={rec_i.get('latency_s', -1):.1f}s "
          f"usage={rec_i.get('usage')} cost=${rec_i.get('cost_usd', 0.0):.6f}")

    # 3. Audio probe: documents unavailability, never fails the spike.
    wav = Path(args.audio).read_bytes() if args.audio else make_wav()
    try:
        client.chat_audio()
        audio_record = {
            "kind": "llm_call",
            "probe": "audio",
            "model": client.model,
            "provider": "baseten",
            "available": True,
            "audio_tokens": None,
            "note": "chat_audio unexpectedly succeeded; verify the Baseten audio shape",
        }
    except NotImplementedError as e:
        audio_record = {
            "kind": "llm_call",
            "probe": "audio",
            "model": client.model,
            "provider": "baseten",
            "available": False,
            "audio_tokens": 0,
            "wav_bytes": len(wav),
            "note": f"audio unavailable, recorded as data: {e}",
        }
    p_a = save("audio_01.json", audio_record)
    print(f"spike: audio -> {p_a.name}: available={audio_record['available']} "
          f"(wav {len(wav)} bytes, audio_tokens={audio_record['audio_tokens']})")

    total = 0.0
    for rec in (rec_t, rec_i):
        cost = actual_cost_from_usage(rec.get("model", ""), rec.get("usage", {}),
                                      provider="baseten")
        total += cost
    print(f"spike: total mapped spend: ${total:.6f} (ledger: ${client.budget.spend_usd:.6f})")
    print(f"spike: scratch DATA_DIR {tmp} (not committed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
