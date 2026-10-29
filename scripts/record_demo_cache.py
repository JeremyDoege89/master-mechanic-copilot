"""M6B demo-cache recorder (OWNER-RUN, live, outside the sandbox).

Makes ONLY the model calls the judge bundles cannot replay from existing
fixtures, and stores them under ``backend/tests/fixtures/demo_cache/``:

1. Spark Open second opinion on ``engine_bay_02.jpg`` (Baseten, ~$0.01).
2. Wonder senior review per bundle scenario, 2x (Meta, ~$0.09 each).
3. Optional: one SAM call for a concept (``--sam-concept``, ~$0.0025).

Expected one-time spend is about $0.20 Meta + $0.01 Baseten; the script
hard-refuses above $1.00 cumulative. Judge replay stays $0.

Keys come from ``~/.config/meta/model-api.env`` and
``~/.config/baseten/api.env`` (``KEY=VALUE`` lines) and are never printed.

Flow matters: the second opinion is recorded first (image-only, no
session needed); then each scenario's deterministic session is driven to
its finished report + Wonder audit via :mod:`make_demo_bundle` in
recording mode (same frozen clock/uuids and $0, so the audit matches the
offline build exactly) and exactly one live Wonder call runs against that
audit. Afterwards the owner runs ``scripts/make_demo_bundle.py`` (cache
mode, $0) and commits the bundles.

Entries already on disk are skipped, so a rerun after a partial recording
(such as the finished second opinion) only makes the missing calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures"
IMAGES_DIR = FIXTURES_DIR / "images"
CACHE_DIR = FIXTURES_DIR / "demo_cache"

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(FIXTURES_DIR))

import make_demo_bundle as gen

META_ENV = Path.home() / ".config" / "meta" / "model-api.env"
BASETEN_ENV = Path.home() / ".config" / "baseten" / "api.env"

HARD_REFUSE_USD = 1.00


def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines into the process environment (never printed)."""
    if not path.exists():
        raise SystemExit(f"record_demo_cache: missing key file {path}")
    loaded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value:
            os.environ.setdefault(key, value)
            loaded[key] = value
    return loaded


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def record_second_opinion() -> float:
    """One live Spark Open call on the demo photo. Returns actual cost."""
    from mmc.meta.spark_open import SparkOpenClient, analyze_components_open

    image = (IMAGES_DIR / gen.ENGINE_IMAGE).read_bytes()
    client = SparkOpenClient(replay_cached=False)
    parsed, record = analyze_components_open(
        image,
        client,
        vehicle_context="Ford gasoline vehicle, engine bay photo",
    )
    cost = float(record.get("cost_usd", 0.0))
    _write_json(
        CACHE_DIR / gen.SECOND_OPINION_FILE,
        {
            "captured": "record_demo_cache.py owner run",
            "captured_ts": _utc_now(),
            "cost_usd": cost,
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "model": record.get("model"),
            "record": record,
        },
    )
    print(f"second opinion: {len(parsed.get('components', []))} components, cost ${cost:.4f}")
    return cost


def record_wonder(scenario_key: str) -> float:
    """Build the recording context offline ($0), then one live Wonder call."""
    import sim_scenarios as sim_mod  # noqa: F401  (import parity check)

    from mmc.meta import wonder as wonder_mod
    from mmc.meta.client import MetaClient

    with tempfile.TemporaryDirectory(prefix="mmc-demo-record-") as tmp:
        data_dir = Path(tmp)
        context = gen.build_wonder_context(
            scenario_key, cache_dir=CACHE_DIR, data_dir=data_dir
        )
        client = MetaClient(
            replay_cached=False,
            cache_dir=str(data_dir / "llm_cache"),
        )
        _review, record = wonder_mod.request_review(context["audit"], client)
    cost = float(record.get("cost_usd", 0.0))
    _write_json(
        CACHE_DIR / gen.WONDER_FILES[scenario_key],
        {
            "captured": "record_demo_cache.py owner run",
            "captured_ts": _utc_now(),
            "cost_usd": cost,
            "audit_sha256": context.get("audit_sha256"),
            "record": record,
        },
    )
    print(f"wonder {scenario_key}: top={context['top_hypothesis']} cost ${cost:.4f}")
    return cost


def record_sam(concept: str) -> float:
    """One live SAM call for a concept, through the current downscale path."""
    from mmc.meta.client import MetaClient
    from mmc.meta import sam as sam_mod

    image = (IMAGES_DIR / gen.ENGINE_IMAGE).read_bytes()
    client = MetaClient(replay_cached=False, sleep=lambda s: None)
    _result, record = sam_mod.segment_image(concept, image, client)
    events = record.get("events", [])
    if not events:
        raise RuntimeError(f"SAM returned no stream events for {concept!r}")
    cost = float(record.get("cost_usd", 0.0))
    slug = "".join(c if (c.isalnum() or c == "_") else "_" for c in concept.strip().lower())
    path = CACHE_DIR / f"sam_{slug or 'concept'}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events) + "\n")
    _write_json(
        path.with_suffix(".meta.json"),
        {
            "captured": "record_demo_cache.py owner run",
            "captured_ts": _utc_now(),
            "concept": concept,
            "cost_usd": cost,
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "events": len(events),
        },
    )
    print(f"SAM {concept!r}: {len(events)} stream events, cost ${cost:.4f}")
    return cost


def _already_recorded(path: Path) -> bool:
    """A cache entry counts as recorded when it parses with a record inside."""
    if not path.exists():
        return False
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")).get("record"), dict)
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record M6B demo cache (live, ~$0.20).")
    parser.add_argument("--sam-concept", default=None, help="also record one SAM concept call")
    parser.add_argument(
        "--skip-wonder", action="store_true", help="record everything except the Wonder reviews"
    )
    args = parser.parse_args(argv)

    load_env_file(META_ENV)
    load_env_file(BASETEN_ENV)
    with tempfile.TemporaryDirectory(prefix="mmc-demo-cache-") as tmp:
        os.environ["DATA_DIR"] = tmp
        total = 0.0

        def charge(cost: float, label: str) -> None:
            nonlocal total
            total += cost
            print(f"  cumulative spend: ${total:.4f} (after {label})")
            if total > HARD_REFUSE_USD:
                raise SystemExit(
                    f"record_demo_cache: cumulative ${total:.4f} exceeds "
                    f"${HARD_REFUSE_USD:.2f}: refusing further live calls"
                )

        second_path = CACHE_DIR / gen.SECOND_OPINION_FILE
        if _already_recorded(second_path):
            print(f"second opinion: already recorded in {second_path.name}, skipping ($0)")
        else:
            charge(record_second_opinion(), "second opinion")
        if args.sam_concept:
            slug = "".join(
                c if (c.isalnum() or c == "_") else "_"
                for c in args.sam_concept.strip().lower()
            )
            sam_path = CACHE_DIR / f"sam_{slug or 'concept'}.jsonl"
            if sam_path.exists() and (sam_path.with_suffix(".meta.json").exists()):
                print(f"SAM {args.sam_concept!r}: already recorded in {sam_path.name}, skipping ($0)")
            else:
                charge(record_sam(args.sam_concept), f"SAM {args.sam_concept}")
        if not args.skip_wonder:
            for key in sorted(gen.SCENARIOS):
                wonder_path = CACHE_DIR / gen.WONDER_FILES[key]
                if _already_recorded(wonder_path):
                    print(f"wonder {key}: already recorded in {wonder_path.name}, skipping ($0)")
                    continue
                charge(record_wonder(key), f"wonder {key}")
    print(f"demo cache ready in {CACHE_DIR} (total about ${total:.4f})")
    print("next: run scripts/make_demo_bundle.py and commit the bundles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
