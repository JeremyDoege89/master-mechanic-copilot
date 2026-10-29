"""M5 meta budget + cache tests (all offline, fake transport)."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from mmc.meta.budget import (
    BudgetExceeded,
    BudgetTracker,
    actual_cost_from_usage,
    estimate_sam_images,
    estimate_sam_video,
    estimate_spark,
)
from mmc.meta.client import MetaClient, NoCachedResponse, call_record


def _resp(text: str, usage: dict | None = None):
    usage = usage or {"input_tokens": 2500, "output_tokens": 400}
    ns = SimpleNamespace(output_text=text, usage=usage)
    calls = {"n": 0}

    def transport(model: str, kwargs: dict):
        calls["n"] += 1
        return ns

    return transport, calls


def _client(tmp_path, transport, replay_cached=False, **kw):
    kw.setdefault("models", {"spark": "muse-spark-1.3", "sam": "sam-3.1"})
    kw.setdefault("budget", BudgetTracker(hard_stop_usd=120.0))
    return MetaClient(
        api_key="test-key-not-a-secret",
        cache_dir=tmp_path / "cache",
        replay_cached=replay_cached,
        transport=transport,
        sleep=lambda s: None,
        **kw,
    )


def test_spark_estimate_math():
    assert estimate_spark(1_000_000, 0) == pytest.approx(1.25)
    assert estimate_spark(1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.15)
    assert estimate_spark(0, 1_000_000) == pytest.approx(4.25)


def test_sam_pricing_and_video_minimum():
    assert estimate_sam_images(1000) == pytest.approx(2.50)
    assert estimate_sam_video(300) == pytest.approx(300 / 1000 * 0.20)
    # Minimum 50 frames per video request.
    assert estimate_sam_video(3) == pytest.approx(50 / 1000 * 0.20)


def test_sam_video_actual_cost_uses_frames_with_minimum():
    # Live SAM video usage carries video_frames_processed (no tokens):
    # 75 frames at $0.20/1k is $0.015, not the $0.01 estimate for 50.
    assert actual_cost_from_usage(
        "sam-3.1", {"video_frames_processed": 75}
    ) == pytest.approx(75 / 1000 * 0.20)
    # The 50-frame minimum is a floor (max), never a cap.
    assert actual_cost_from_usage(
        "sam-3.1", {"video_frames_processed": 3}
    ) == pytest.approx(50 / 1000 * 0.20)


def test_actual_cost_uses_returned_usage_not_estimate():
    usage = {"input_tokens": 2000, "output_tokens": 100,
             "input_tokens_details": {"cached_tokens": 1000}}
    assert actual_cost_from_usage("muse-spark-1.3", usage) == pytest.approx(
        1000 / 1e6 * 1.25 + 1000 / 1e6 * 0.15 + 100 / 1e6 * 4.25
    )


def test_live_call_spends_actual_and_caches(tmp_path):
    transport, calls = _resp('{"a": 1}')
    client = _client(tmp_path, transport)
    result, record = client.call(
        kind="llm_call", model_key="spark",
        request={"op": "t", "input": "hi"}, estimate_usd=0.01,
    )
    assert calls["n"] == 1
    assert client.budget.spend_usd > 0  # from returned usage
    assert (tmp_path / "cache").glob("*.json")
    assert record["cost_usd"] == pytest.approx(client.budget.spend_usd)


def test_replay_hit_costs_nothing(tmp_path):
    transport, calls = _resp('{"a": 1}')
    live = _client(tmp_path, transport)
    _, record = live.call(
        kind="llm_call", model_key="spark",
        request={"op": "t", "input": "hi"}, estimate_usd=0.01,
    )
    replay_transport, replay_calls = _resp('{"DIFFERENT": true}')
    replay = _client(tmp_path, replay_transport, replay_cached=True,
                     budget=BudgetTracker(hard_stop_usd=120.0))
    result, record2 = replay.call(
        kind="llm_call", model_key="spark",
        request={"op": "t", "input": "hi"}, estimate_usd=999.0,
    )
    assert replay_calls["n"] == 0  # no network
    assert result.cached is True
    assert result.cost_usd == 0.0
    assert replay.budget.spend_usd == 0.0
    assert record2["output_text"] == record["output_text"]


def test_replay_miss_raises_instead_of_spending(tmp_path):
    transport, _ = _resp("{}")
    client = _client(tmp_path, transport, replay_cached=True)
    with pytest.raises(NoCachedResponse):
        client.call(kind="llm_call", model_key="spark",
                    request={"op": "unseen"}, estimate_usd=0.01)
    assert client.budget.spend_usd == 0.0


def test_budget_hard_stop_refuses(tmp_path):
    transport, calls = _resp("{}")
    client = _client(tmp_path, transport,
                     budget=BudgetTracker(hard_stop_usd=0.0001))
    with pytest.raises(BudgetExceeded):
        client.call(kind="llm_call", model_key="spark",
                    request={"op": "t"}, estimate_usd=1.0)
    assert calls["n"] == 0


def test_contributor_model_refused(tmp_path):
    transport, _ = _resp("{}")
    with pytest.raises(Exception, match="[Cc]ontributor"):
        _client(tmp_path, transport, models={"spark": "muse-spark-9.9-contributor"})


def test_429_retries_then_succeeds(tmp_path):
    attempts = {"n": 0}

    class RateLimit(Exception):
        status_code = 429

    def transport(model: str, kwargs: dict):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimit("slow down")
        return SimpleNamespace(output_text="{}", usage={"input_tokens": 1, "output_tokens": 1})

    client = _client(tmp_path, transport)
    result, _ = client.call(kind="llm_call", model_key="spark",
                            request={"op": "t"}, estimate_usd=0.001)
    assert attempts["n"] == 3
    assert result.output_text == "{}"


def test_records_and_cache_carry_no_credentials(tmp_path):
    transport, _ = _resp("{}")
    client = _client(tmp_path, transport)
    _, record = client.call(
        kind="llm_call", model_key="spark",
        request={"op": "t", "input": "x",
                 "headers": {"Authorization": "Bearer SECRET"},
                 "api_key": "SECRET"},
        estimate_usd=0.001,
    )
    blob = json.dumps(record)
    assert "SECRET" not in blob and "Bearer" not in blob and "Authorization" not in blob
    cached_files = list((tmp_path / "cache").glob("*.json"))
    assert cached_files
    cached_blob = cached_files[0].read_text()
    assert "SECRET" not in cached_blob and "test-key" not in cached_blob


def test_repr_redacts_key(tmp_path):
    transport, _ = _resp("{}")
    assert "real-secret" not in repr(_client(tmp_path, transport))


def test_real_transport_passes_timeout_and_base_url(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    seen = {}

    class FakeResponses:
        def create(self, model, **kwargs):
            seen["model"] = model
            seen["kwargs"] = kwargs
            from types import SimpleNamespace

            return SimpleNamespace(output_text="{}", usage={"input_tokens": 1, "output_tokens": 1})

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None, timeout=None):
            seen["api_key"] = api_key
            seen["base_url"] = base_url
            seen["timeout"] = timeout
            self.responses = FakeResponses()

    fake = ModuleType("openai")
    fake.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake)
    client = _client(tmp_path, None, timeout_s=45.0)
    result, _ = client.call(
        kind="llm_call", model_key="spark",
        request={"op": "t"}, estimate_usd=0.001,
    )
    assert seen["timeout"] == 45.0
    assert seen["base_url"] == client.base_url
    assert seen["api_key"] == "test-key-not-a-secret"
    assert result.output_text == "{}"
