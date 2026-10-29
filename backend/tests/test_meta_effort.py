"""Reasoning-effort tests: per-call-kind effort from env, in the request hash."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mmc.meta import spark as spark_mod
from mmc.meta.budget import BudgetTracker
from mmc.meta.client import MetaClient, NoCachedResponse, request_sha256


def _clear_effort_env(monkeypatch):
    for var in spark_mod.EFFORT_ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)


def test_effort_defaults():
    # Defaults without env: live medium, extract/agent high, wonder xhigh.
    import os

    saved = {var: os.environ.pop(var, None) for var in spark_mod.EFFORT_ENV_VARS.values()}
    try:
        assert spark_mod.reasoning_effort("live") == "medium"
        assert spark_mod.reasoning_effort("extract") == "high"
        assert spark_mod.reasoning_effort("agent") == "high"
        assert spark_mod.reasoning_effort("wonder") == "xhigh"
    finally:
        for var, value in saved.items():
            if value is not None:
                os.environ[var] = value


def test_effort_env_override_and_bad_value_falls_back(monkeypatch):
    _clear_effort_env(monkeypatch)
    monkeypatch.setenv("EFFORT_SPARK_LIVE", "low")
    assert spark_mod.reasoning_effort("live") == "low"
    monkeypatch.setenv("EFFORT_SPARK_EXTRACT", "XHIGH")
    assert spark_mod.reasoning_effort("extract") == "xhigh"
    monkeypatch.setenv("EFFORT_SPARK_LIVE", "turbo")
    assert spark_mod.reasoning_effort("live") == "medium"
    with pytest.raises(ValueError, match="unknown effort kind"):
        spark_mod.reasoning_effort("nope")


def _spark_client(tmp_path, seen: dict, output_text: str):
    def transport(model: str, kwargs: dict):
        seen["kwargs"] = dict(kwargs)
        seen["model"] = model
        return SimpleNamespace(output_text=output_text, usage={"input_tokens": 1, "output_tokens": 1})

    return MetaClient(
        api_key="test-key-not-a-secret",
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
        models={"spark": "muse-spark-1.3"},
        budget=BudgetTracker(hard_stop_usd=120.0),
    )


def test_live_effort_in_request_record_and_live_kwargs(tmp_path, monkeypatch):
    _clear_effort_env(monkeypatch)
    seen: dict = {}
    client = _spark_client(tmp_path, seen, '{"components": [], "anomalies": []}')
    _parsed, record = spark_mod.analyze_components(b"\xff\xd8fake", client)
    assert record["request"]["reasoning"] == {"effort": "medium"}
    assert seen["kwargs"]["reasoning"] == {"effort": "medium"}

    monkeypatch.setenv("EFFORT_SPARK_LIVE", "high")
    seen2: dict = {}
    client2 = _spark_client(tmp_path, seen2, '{"components": [], "anomalies": []}')
    _parsed2, record2 = spark_mod.analyze_components(b"\xff\xd8fake", client2)
    assert record2["request"]["reasoning"] == {"effort": "high"}
    assert seen2["kwargs"]["reasoning"] == {"effort": "high"}
    # Effort is part of the cache hash: same model+op, different effort.
    assert record["request_sha256"] != record2["request_sha256"]
    assert request_sha256({"model": record["model"], **record["request"]}) == record["request_sha256"]


def test_extract_effort_defaults_high_and_replay_deterministic(tmp_path, monkeypatch):
    _clear_effort_env(monkeypatch)
    seen: dict = {}
    client = _spark_client(tmp_path, seen, '{"events": []}')
    _data, record = spark_mod.summarize_video("file-abc", client)
    assert record["request"]["reasoning"] == {"effort": "high"}
    assert seen["kwargs"]["reasoning"] == {"effort": "high"}

    # Same effort replays from cache for $0; a different effort misses.
    replay = MetaClient(
        api_key="test-key-not-a-secret",
        cache_dir=tmp_path / "cache",
        replay_cached=True,
        transport=lambda m, kw: (_ for _ in ()).throw(AssertionError("no network on replay")),
        sleep=lambda s: None,
        models={"spark": "muse-spark-1.3"},
        budget=BudgetTracker(hard_stop_usd=120.0),
    )
    result, record2 = replay.call(
        kind="llm_call", model_key="spark", request=record["request"], estimate_usd=999.0
    )
    assert result.cached is True and result.cost_usd == 0.0
    altered = dict(record["request"])
    altered["reasoning"] = {"effort": "low"}
    with pytest.raises(NoCachedResponse):
        replay.call(kind="llm_call", model_key="spark", request=altered, estimate_usd=0.01)


def test_instrument_and_spec_extract_use_extract_effort(tmp_path, monkeypatch):
    _clear_effort_env(monkeypatch)
    seen: dict = {}
    client = _spark_client(
        tmp_path, seen, '{"value": 12.4, "units": "V", "confidence": 0.9}'
    )
    _reading, record = spark_mod.read_instrument(b"\xff\xd8fake", client)
    assert record["request"]["reasoning"] == {"effort": "high"}

    spec_text = json.dumps(
        {"spec_ranges": [{"spec_id": "x", "parameter": "y", "min": 1, "max": 2, "units": "V"}]}
    )
    seen2: dict = {}
    client2 = _spark_client(tmp_path, seen2, spec_text)
    _ranges, record2 = spark_mod.extract_spec_ranges("file-abc", client2)
    assert record2["request"]["reasoning"] == {"effort": "high"}
