"""M5 live opt-in test: Spark + SAM on the real engine-bay photo. Skipped in CI.

Run outside the sandbox with a key:
    MMC_LIVE_TEST=1 uv run pytest -q tests/test_vision_live.py -rs

Uses the real CC0 photo (engine_bay_02.jpg — the synthetic engine_bay_01.jpg
is useless for SAM). "battery" is the verified-working short concept; the
Spark concept goes through the once-only fallback retry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("MMC_LIVE_TEST"), reason="needs MMC_LIVE_TEST=1 + MODEL_API_KEY (live $$)"
)

from mmc.meta.client import MetaClient  # noqa: E402
from mmc.meta.sam import segment_image, segment_with_fallback  # noqa: E402
from mmc.meta.spark import analyze_components  # noqa: E402
from mmc.vision import components as comp_mod  # noqa: E402

IMAGE = Path(__file__).parent / "fixtures" / "images" / "engine_bay_02.jpg"


def test_live_spark_plus_sam():
    client = MetaClient(replay_cached=False)
    image_bytes = IMAGE.read_bytes()
    parsed, rec1 = analyze_components(image_bytes, client, "Ford gasoline vehicle")
    comp_mod.parse_components(parsed)  # schema + no-probability gates
    assert parsed["components"], "live Spark returned no components"
    for comp in parsed["components"]:
        assert "," not in comp["sam_concept"]
        assert len(comp["sam_concept"].split()) <= 2, comp["sam_concept"]

    # Verified-working short concept on this photo.
    direct, rec_direct = segment_image("battery", image_bytes, client)
    assert direct.found and direct.masks, "live SAM found no battery on engine_bay_02.jpg"

    # Spark's own concept through the fallback retry (empty is a normal
    # outcome here — the retry must simply behave).
    first = parsed["components"][0]
    retry, records, used = segment_with_fallback(
        first["sam_concept"], first.get("sam_fallback"), image_bytes, client
    )
    assert isinstance(retry.found, bool)
    assert client.budget.spend_usd > 0
    print(f"\nlive spend: ${client.budget.spend_usd:.6f} "
          f"(spark usage={rec1['usage']}, sam usage={rec_direct['usage']}, "
          f"retry used={used!r} found={retry.found}, calls={len(records) + 1})")
