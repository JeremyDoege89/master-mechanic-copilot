"""M5O docs/env pins + spike-fixture replay tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "spark_open"

PIN = "meta-models/muse-spark-open-1026"


def test_pins_present_everywhere():
    for rel in ("AGENTS.md", "docs/SPEC.md", ".env.example", "README.md"):
        text = (ROOT / rel).read_text()
        assert PIN in text, f"{rel} must pin {PIN}"


def test_baseten_key_blank_in_example():
    for line in (ROOT / ".env.example").read_text().splitlines():
        head = line.split("#", 1)[0].strip()
        if head.startswith("BASETEN_API_KEY="):
            assert head == "BASETEN_API_KEY=", "Baseten key must stay blank in the repo"
            return
    raise AssertionError("BASETEN_API_KEY missing from .env.example")


def test_baseten_env_defaults():
    text = (ROOT / ".env.example").read_text()
    assert "BASETEN_BUDGET_USD_HARD_STOP=80" in text
    assert "SPARK_OPEN_AUDIO=false" in text
    assert "BASETEN_BASE_URL=https://inference.baseten.co/v1" in text


def test_agents_states_both_meta_paths():
    text = (ROOT / "AGENTS.md").read_text()
    assert "OR Meta open-weight models via Baseten" in text


def _replay(record: dict, tmp_path) -> tuple:
    from mmc.meta.spark_open import SparkOpenClient

    cache_dir = tmp_path / "baseten"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{record['request_sha256']}.json").write_text(json.dumps(record))

    def boom(model, kwargs):
        raise AssertionError("replay must not touch the network")

    client = SparkOpenClient(
        api_key="replay-key",
        cache_dir=cache_dir,
        replay_cached=True,
        transport=boom,
        sleep=lambda s: None,
    )
    return client._chat(messages=[], request=dict(record["request"]), estimate_usd=999.0)


@pytest.mark.parametrize("name", ["text_01.json", "image_01.json"])
def test_spike_fixtures_replay_at_zero_cost(name, tmp_path):
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"owner spike has not run yet ({name} missing)")
    record = json.loads(path.read_text())
    assert record.get("provider") == "baseten"
    assert "sk-" not in json.dumps(record)
    result, replayed = _replay(record, tmp_path)
    assert result.cached is True
    assert result.cost_usd == 0.0
    assert replayed["output_text"] == record["output_text"]
    assert replayed["reasoning_content"] == record.get("reasoning_content", "")


def test_spike_audio_fixture_documents_unavailability():
    path = FIXTURES / "audio_01.json"
    if not path.exists():
        pytest.skip("owner spike has not run yet (audio_01.json missing)")
    record = json.loads(path.read_text())
    assert record["available"] is False
    assert record["audio_tokens"] == 0
