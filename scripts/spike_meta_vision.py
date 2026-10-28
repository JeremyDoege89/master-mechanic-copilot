#!/usr/bin/env python3
"""M5 vision spike (plan .agents/plans/M5.md section 0).

Live-calls Muse Spark (scene JSON) + SAM 3.1 (concept mask, box-prompt mask)
through backend/mmc/meta/ and saves the raw KEY-FREE responses as fixtures
under backend/tests/fixtures/vision/.

Usage (from repo root, OUTSIDE the sandbox — it needs network + a key):
    backend/.venv/bin/python scripts/spike_meta_vision.py [--image PATH] [--instrument PATH]

Default image is the real CC0 engine-bay photo
(backend/tests/fixtures/images/engine_bay_02.jpg); the synthetic
engine_bay_01.jpg is useless for SAM.

Key: MODEL_API_KEY env, or ~/.config/meta/model-api.env. The key is never
printed and never stored: fixtures hold only call_record() output (no
headers, no key, image bytes replaced by a length stub).

Cost is logged from the usage numbers the API returns (SAM reports
images_processed, no tokens), not from estimates. Exits non-zero without
writing fixtures when the API is unreachable or the key is missing.

SAM notes (verified live 2026-10-28; a bare parts list is rejected
with 400): Responses stream=True, input is ONE user message wrapping
[input_text "<concept>", input_image data URL]. Empty completed streams
are a normal "not found" — the spike retries once with Spark's shorter
sam_fallback concept when one is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mmc.meta.budget import actual_cost_from_usage  # noqa: E402
from mmc.meta.client import MetaClient  # noqa: E402
from mmc.meta.sam import image_size, segment_image, segment_with_fallback  # noqa: E402
from mmc.meta.spark import analyze_components, read_instrument  # noqa: E402

FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "vision"
DEFAULT_IMAGE = ROOT / "backend" / "tests" / "fixtures" / "images" / "engine_bay_02.jpg"


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


def save(name: str, record: dict) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, sort_keys=True)
    for banned in ("Authorization", "api_key", "Bearer "):
        if banned in text:
            raise SystemExit(f"spike: refusing to write fixture containing {banned!r}")
    path = FIXTURE_DIR / name
    path.write_text(text + "\n", encoding="utf-8")
    return path


def slug(concept: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in concept.strip().lower())[:32] or "concept"


def main() -> int:
    ap = argparse.ArgumentParser(description="M5 live vision spike")
    ap.add_argument("--image", default=str(DEFAULT_IMAGE))
    ap.add_argument("--instrument", default="")
    ap.add_argument("--vehicle-context", default="Ford gasoline vehicle, engine bay photo")
    args = ap.parse_args()

    key = load_key()
    image = Path(args.image)
    if not image.exists():
        raise SystemExit(f"spike: image not found: {image}")

    # Scratch DATA_DIR: proves the llm_call/sam_call event path without
    # touching real session data.
    tmp = Path(tempfile.mkdtemp(prefix="mmc-spike-"))
    os.environ["DATA_DIR"] = str(tmp)
    import mmc.sessions as sess_mod

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    logged: list[str] = []

    def on_call(record: dict) -> None:
        event = registry.append(session, record["kind"], record)
        logged.append(f"{record['kind']} seq={event['seq']} sha={event['hash'][:12]}")

    client = MetaClient(api_key=key, replay_cached=False, on_call=on_call)
    print(f"spike: models spark={client.models.get('spark')} sam={client.models.get('sam')}")

    image_bytes = image.read_bytes()
    print(f"spike: image {image} ({len(image_bytes)} bytes, size={image_size(image_bytes)})")

    # 1. Spark scene understanding (short sam_concept nouns + fallbacks).
    components, rec1 = analyze_components(image_bytes, client, args.vehicle_context)
    p1 = save("spark_components_01.json", rec1)
    print(f"spike: spark components -> {p1.name}: "
          f"{len(components['components'])} components, {len(components['anomalies'])} anomalies")

    # 2. SAM: Spark's concept with the once-only fallback retry.
    first = components["components"][0] if components["components"] else {}
    concept = first.get("sam_concept", "battery")
    fallback = first.get("sam_fallback")
    result, records, used = segment_with_fallback(concept, fallback, image_bytes, client)
    attempted = [concept] + ([used] if len(records) > 1 else [])
    for i, rec in enumerate(records):
        p = save(f"sam_{slug(attempted[i])}_{i:02d}.json", rec)
        print(f"spike: sam attempt {i} ({attempted[i]!r}) -> {p.name}: "
              f"usage={rec.get('usage')} cost=${rec.get('cost_usd', 0.0):.6f}")
    print(f"spike: sam concept {concept!r} (fallback {fallback!r}) -> used {used!r}: "
          f"{len(result.masks)} masks, found={result.found}")

    # 3. SAM: same winning concept with a box prompt (pixel space). Uses the
    # first found mask's box, else a centre box.
    if result.masks and result.masks[0].box is not None:
        b = result.masks[0].box
        box = {"x_min": b.x_min, "y_min": b.y_min, "x_max": b.x_max - 1, "y_max": b.y_max - 1}
    else:
        w, h = image_size(image_bytes)
        box = {"x_min": w // 4, "y_min": h // 4, "x_max": 3 * w // 4, "y_max": 3 * h // 4}
    boxed, rec_box = segment_image(used, image_bytes, client, box=box)
    p_box = save(f"sam_{slug(used)}_box.json", rec_box)
    print(f"spike: sam box-prompt {box} -> {p_box.name}: {len(boxed.masks)} masks")

    # 4. Instrument reading (only with an explicit meter photo).
    if args.instrument:
        meter = Path(args.instrument).read_bytes()
        reading, rec4 = read_instrument(meter, client)
        p4 = save("instrument_01.json", rec4)
        print(f"spike: instrument -> {p4.name}: {reading}")
    else:
        print("spike: no --instrument given, skipping meter call")

    # Spend from returned usage, per call.
    total = 0.0
    for rec in [rec1, *records, rec_box]:
        usage = rec.get("usage", {})
        cost = actual_cost_from_usage(rec.get("model", ""), usage) or rec.get("cost_usd", 0.0)
        total += cost
        print(f"spike: {rec['kind']} model={rec.get('model')} usage={usage} cost=${cost:.6f}")
    print(f"spike: total spend from API usage: ${total:.6f} (ledger: ${client.budget.spend_usd:.6f})")
    print("spike: events: " + "; ".join(logged))
    print(f"spike: scratch session {session.id} under {tmp} (not committed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
