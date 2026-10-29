"""M5O fallback tests (all offline, fake transports)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mmc.meta.budget import BudgetExceeded, BudgetTracker
from mmc.meta.client import MetaClient, MetaClientError
from mmc.meta import fallback as fallback_mod
from mmc.meta.spark_open import SparkOpenClient

COMP_JSON = json.dumps({
    "components": [{"name": "Battery", "sam_concept": "battery", "confidence": 0.8}],
    "anomalies": [],
})
METER_JSON = json.dumps({"value": 12.42, "units": "V", "confidence": 0.9})


def _meta_client(tmp_path, behavior):
    def transport(model, kwargs):
        if behavior == "ok":
            return SimpleNamespace(output_text=COMP_JSON,
                                   usage={"input_tokens": 10, "output_tokens": 5})
        raise RuntimeError("500 Internal Server Error")

    # A real budget refusal comes from the pre-call guard (tiny stop), not
    # from the transport: client.call wraps transport errors as
    # MetaClientError after its 429 retries.
    budget = BudgetTracker(hard_stop_usd=0.0001 if behavior == "budget"
                           else 120.0)
    return MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=budget,
        cache_dir=tmp_path / "meta",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def _open_client(tmp_path, payload_text, calls):
    def transport(model, kwargs):
        calls["n"] += 1
        msg = SimpleNamespace(content=payload_text, reasoning_content="r")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    return SparkOpenClient(
        api_key="baseten-test",
        cache_dir=tmp_path / "baseten",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def test_fallback_on_budget_exceeded(tmp_path):
    from mmc.meta import spark as spark_mod

    calls = {"n": 0}
    parsed, record = spark_mod.analyze_components(
        b"img", _meta_client(tmp_path, "budget"),
        open_client=_open_client(tmp_path, COMP_JSON, calls),
    )
    assert parsed["components"][0]["sam_concept"] == "battery"
    assert calls["n"] == 1
    assert record["provider"] == "baseten"
    assert record["fallback"]["from"] == "meta"
    assert record["fallback"]["reason"] == "budget"
    assert "refusing call" in record["fallback"]["detail"]


def test_fallback_on_5xx_error(tmp_path):
    from mmc.meta import spark as spark_mod

    calls = {"n": 0}
    parsed, record = spark_mod.analyze_components(
        b"img", _meta_client(tmp_path, "boom"),
        open_client=_open_client(tmp_path, COMP_JSON, calls),
    )
    assert parsed["components"][0]["name"] == "Battery"
    assert record["fallback"]["reason"] == "error"
    assert "500" in record["fallback"]["detail"]


def test_healthy_primary_zero_baseten_calls(tmp_path):
    from mmc.meta import spark as spark_mod

    calls = {"n": 0}
    parsed, record = spark_mod.analyze_components(
        b"img", _meta_client(tmp_path, "ok"),
        open_client=_open_client(tmp_path, COMP_JSON, calls),
    )
    assert calls["n"] == 0
    assert "fallback" not in record
    assert record.get("provider", "meta") != "baseten"


def test_fallback_detail_truncated():
    def primary():
        raise MetaClientError("x" * 500)

    _, record = fallback_mod.call_text_or_image(
        primary, lambda: ("v", {"kind": "llm_call"}), kind="text"
    )
    assert len(record["fallback"]["detail"]) == 200


def test_fallback_rejects_bad_kind():
    with pytest.raises(ValueError):
        fallback_mod.call_text_or_image(lambda: (1, {}), lambda: (1, {}), kind="video")


def test_fallback_non_meta_errors_propagate():
    calls = {"n": 0}

    def primary():
        raise ValueError("bad output shape")

    def open_fn():
        calls["n"] += 1
        return "v", {"kind": "llm_call"}

    with pytest.raises(ValueError, match="bad output shape"):
        fallback_mod.call_text_or_image(primary, open_fn, kind="text")
    assert calls["n"] == 0


def test_instrument_fallback_keeps_retake_and_confirm_gates(tmp_path):
    from mmc.meta import spark as spark_mod
    from mmc.vision import instruments as instr_mod

    calls = {"n": 0}
    low = json.dumps({"value": 0.0, "units": "V", "confidence": 0.31})
    raw, record = spark_mod.read_instrument(
        b"img", _meta_client(tmp_path, "boom"),
        open_client=_open_client(tmp_path, low, calls),
    )
    assert record["fallback"]["reason"] == "error"
    reading = instr_mod.parse_instrument(raw)
    assert reading["needs_retake"] is True
    with pytest.raises(ValueError, match="[Rr]etake"):
        instr_mod.confirm_reading(reading, "blob", "sha")

    calls2 = {"n": 0}
    raw2, _ = spark_mod.read_instrument(
        b"img", _meta_client(tmp_path, "budget"),
        open_client=_open_client(tmp_path, METER_JSON, calls2),
    )
    good = instr_mod.parse_instrument(raw2)
    assert good["needs_retake"] is False
    evidence, _obs = instr_mod.confirm_reading(good, "blob", "sha")
    assert evidence["evidence_id"]


def test_sam_and_voice_have_no_fallback():
    # SAM streaming + parser grammar and Voice Transcribe keep raising:
    # the helper must be unreachable from those sites (import graph).
    meta_dir = Path(__file__).resolve().parents[1] / "mmc" / "meta"
    sam_src = (meta_dir / "sam.py").read_text()
    # SAM has its own "segment_with_fallback" (short-concept retry); what
    # must be absent is any wire to the Meta->Baseten fallback helper.
    assert "meta.fallback" not in sam_src
    assert "call_text_or_image" not in sam_src
    assert not (meta_dir / "voice.py").exists()  # no substitute to wire
    import mmc.meta.sam as sam_mod

    assert not hasattr(sam_mod, "fallback")
    assert not hasattr(sam_mod, "call_text_or_image")
