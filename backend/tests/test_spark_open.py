"""M5O Spark Open client tests (all offline, fake Chat transport)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mmc.meta.budget import BudgetTracker, estimate_spark_open
from mmc.meta.client import MetaClientError, NoCachedResponse
from mmc.meta.spark_open import DEFAULT_SPARK_OPEN_MODEL, SparkOpenClient


def _chat_resp(content="hello", reasoning="thinking...", usage=None):
    usage = usage or {"prompt_tokens": 1500, "completion_tokens": 400}
    msg = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=dict(usage))


def _client(tmp_path, transport, replay_cached=False, **kw):
    kw.setdefault("budget", BudgetTracker(hard_stop_usd=80.0))
    return SparkOpenClient(
        api_key="baseten-test-key",
        cache_dir=tmp_path / "baseten",
        replay_cached=replay_cached,
        transport=transport,
        sleep=lambda s: None,
        **kw,
    )


def test_chat_shape_stores_reasoning(tmp_path):
    def transport(model, kwargs):
        assert model == DEFAULT_SPARK_OPEN_MODEL
        assert kwargs["messages"][0]["role"] == "system"
        return _chat_resp("answer", "chain-of-thought")

    client = _client(tmp_path, transport)
    result, record = client.chat_text("sys", "fuel trim question")
    assert result.output_text == "answer"
    assert result.reasoning_content == "chain-of-thought"
    assert record["provider"] == "baseten"
    assert record["reasoning_content"] == "chain-of-thought"
    assert record["content"] == "answer"
    assert record["model"] == DEFAULT_SPARK_OPEN_MODEL


def test_usage_to_cost_with_reasoning_and_cached(tmp_path):
    usage = {
        "prompt_tokens": 2000,
        "completion_tokens": 500,
        "completion_tokens_details": {"reasoning_tokens": 300},
        "prompt_tokens_details": {"cached_tokens": 1000},
    }
    client = _client(tmp_path, lambda m, kw: _chat_resp("x", "r", usage))
    result, record = client.chat_text("s", "u")
    # Reasoning rides inside completion (no double bill); cached at input rate.
    assert result.cost_usd == pytest.approx(estimate_spark_open(2000, 500))
    assert record["usage"]["completion_tokens_details"]["reasoning_tokens"] == 300
    assert record["usage"]["prompt_tokens_details"]["cached_tokens"] == 1000


def test_wrong_model_refused(tmp_path):
    with pytest.raises(MetaClientError):
        _client(tmp_path, lambda m, kw: _chat_resp(), model="gpt-4")


def test_contributor_refused(tmp_path):
    with pytest.raises(MetaClientError, match="[Cc]ontributor"):
        _client(
            tmp_path,
            lambda m, kw: _chat_resp(),
            model="meta-models/muse-spark-open-1026-contributor",
        )


def test_missing_key_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(MetaClientError, match="BASETEN_API_KEY"):
        SparkOpenClient(api_key="", cache_dir=tmp_path / "c")


def test_baseten_stop_refuses_while_meta_tracker_untouched(tmp_path):
    from mmc.meta.budget import BudgetTracker as BT

    meta = BT(hard_stop_usd=120.0)
    baseten = BT(hard_stop_usd=0.0001)
    client = _client(tmp_path, lambda m, kw: _chat_resp(), budget=baseten)
    with pytest.raises(Exception, match="[Rr]efus"):
        client.chat_text("s", "u")
    assert baseten.spend_usd == 0.0
    assert meta.spend_usd == 0.0


def test_cache_hit_zero_and_replay_miss_raises(tmp_path):
    transport, calls = lambda m, kw: _chat_resp("live"), {"n": 0}

    def counting(m, kw):
        calls["n"] += 1
        return _chat_resp("live")

    live = _client(tmp_path, counting)
    _, record = live.chat_text("s", "same")
    assert calls["n"] == 1
    replay = _client(tmp_path, lambda m, kw: _chat_resp("DIFFERENT"), replay_cached=True,
                     budget=BudgetTracker(hard_stop_usd=80.0))
    result, record2 = replay.chat_text("s", "same")
    assert result.cached is True
    assert result.cost_usd == 0.0
    assert record2["output_text"] == record["output_text"]
    with pytest.raises(NoCachedResponse):
        replay.chat_text("s", "unseen prompt")
    assert replay.budget.spend_usd == 0.0


def test_429_then_success(tmp_path):
    attempts = {"n": 0}

    class RateLimit(Exception):
        status_code = 429

    def transport(model, kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimit("slow down")
        return _chat_resp("ok", "")

    client = _client(tmp_path, transport)
    result, _ = client.chat_text("s", "u")
    assert attempts["n"] == 3
    assert result.output_text == "ok"


def test_records_and_cache_carry_no_credentials(tmp_path):
    client = SparkOpenClient(
        api_key="sk-test-secret-key",
        cache_dir=tmp_path / "baseten",
        replay_cached=False,
        transport=lambda m, kw: _chat_resp("hi", "r"),
        sleep=lambda s: None,
    )
    _, record = client.chat_text("s", "u")
    blob = json.dumps(record)
    assert "sk-test-secret" not in blob
    assert "baseten-test" not in blob or "api_key" not in blob
    assert "Authorization" not in blob
    cached_files = list((tmp_path / "baseten").glob("*.json"))
    assert cached_files
    cached_blob = cached_files[0].read_text()
    assert "sk-test-secret" not in cached_blob


def test_image_uses_data_url_and_stub_cache(tmp_path):
    seen = {}

    def transport(model, kwargs):
        seen["kwargs"] = kwargs
        return _chat_resp("seen", "")

    client = _client(tmp_path, transport)
    _, record = client.chat_image("sys", "what?", b"\xff\xd8fake", "image/jpeg")
    url = seen["kwargs"]["messages"][1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    # Cached/logged request keeps a stub, not the bytes.
    assert record["request"]["image"]["_bytes_len"] == 6
    assert "fake" not in json.dumps(record["request"])


def test_audio_stub_off_by_default(tmp_path):
    client = _client(tmp_path, lambda m, kw: _chat_resp())
    with pytest.raises(NotImplementedError):
        client.chat_audio()
