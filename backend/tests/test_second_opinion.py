"""M5O second-opinion vision tests (all offline, fake transports)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from mmc.meta.budget import BudgetTracker
from mmc.meta.client import MetaClient
from mmc.meta.spark_open import SparkOpenClient
from mmc.vision import components as comp_mod

PRIMARY = {
    "components": [
        {"name": "Battery", "sam_concept": "battery", "confidence": 0.8},
        {"name": "Air filter box", "sam_concept": "air filter", "confidence": 0.7},
    ],
    "anomalies": [
        {"type": "corrosion", "region_hint": "battery terminal", "confidence": 0.6}
    ],
}
SECOND_AGREE = {
    "components": [
        {"name": "Battery terminal", "sam_concept": "Battery", "confidence": 0.9},
    ],
    "anomalies": [
        {"type": "corrosion", "region_hint": "battery terminal", "confidence": 0.5}
    ],
}
SECOND_EXTRA = {
    "components": [
        {"name": "Battery terminal", "sam_concept": "battery", "confidence": 0.9},
        {"name": "Radiator cap", "sam_concept": "radiator cap", "confidence": 0.75},
    ],
    "anomalies": [],
}
SECOND_DISPUTED = {
    "components": [],
    "anomalies": [
        {"type": "leak", "region_hint": "battery terminal", "confidence": 0.65}
    ],
}


def test_merge_agree_flags_and_confidence_rule():
    merged, disagreements = comp_mod.merge_opinions(PRIMARY, SECOND_AGREE)
    by_concept = {c["sam_concept"]: c for c in merged["components"]}
    battery = by_concept["battery"]
    assert battery["agreement"] == "corroborated"
    assert battery["sources"] == ["spark", "spark_open"]
    assert battery["confidence"] == pytest.approx(0.9)  # max(both)
    assert by_concept["air filter"]["agreement"] == "single_source"
    assert disagreements == [{"name": "Air filter box", "named_by": "spark"}]
    anom = merged["anomalies"][0]
    assert anom["agreement"] == "corroborated"
    assert anom["confidence"] == pytest.approx(0.6)


def test_merge_open_only_component_is_disagreement():
    merged, disagreements = comp_mod.merge_opinions(PRIMARY, SECOND_EXTRA)
    assert {d["name"] for d in disagreements} == {"Air filter box", "Radiator cap"}
    named_by = {d["name"]: d["named_by"] for d in disagreements}
    assert named_by["Radiator cap"] == "spark_open"
    assert named_by["Air filter box"] == "spark"


def test_merge_conflicting_anomaly_type_is_disputed():
    merged, _ = comp_mod.merge_opinions(PRIMARY, SECOND_DISPUTED)
    assert len(merged["anomalies"]) == 2
    assert {a["agreement"] for a in merged["anomalies"]} == {"disputed"}


def test_merge_none_is_all_single_source():
    merged, disagreements = comp_mod.merge_opinions(PRIMARY, None)
    assert all(c["agreement"] == "single_source" for c in merged["components"])
    assert len(disagreements) == 2


def test_to_observations_carries_agreement_and_sources():
    merged, _ = comp_mod.merge_opinions(PRIMARY, SECOND_AGREE)
    obs = comp_mod.to_observations(merged, "ev1")
    battery = next(o for o in obs if o["feature"] == "visual_component:battery")
    assert battery["confidence"] == pytest.approx(0.9)
    assert battery["agreement"] == "corroborated"
    assert battery["sources"] == ["spark", "spark_open"]
    filt = next(o for o in obs if o["feature"] == "visual_component:air_filter_box")
    assert filt["agreement"] == "single_source"


def _meta_client(tmp_path, payload):
    def transport(model, kwargs):
        return SimpleNamespace(output_text=json.dumps(payload),
                               usage={"input_tokens": 10, "output_tokens": 5})

    return MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "meta",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def _open_client(tmp_path, payload, delay_s=0.0):
    from mmc.meta.spark_open import SparkOpenClient as SOC

    def transport(model, kwargs):
        if delay_s:
            time.sleep(delay_s)
        msg = SimpleNamespace(content=json.dumps(payload), reasoning_content="r")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    return SOC(
        api_key="baseten-test",
        cache_dir=tmp_path / "baseten",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def test_analyzer_merges_fast_open(tmp_path):
    from mmc.api import routes_vision as rv

    analyzer = rv.LiveVisionAnalyzer(
        _meta_client(tmp_path, PRIMARY), _open_client(tmp_path, SECOND_AGREE)
    )
    merged, overlays, info = analyzer.analyze_keyframe_full(b"img")
    assert info["second_opinion_pending"] is False
    by_concept = {c["sam_concept"]: c for c in merged["components"]}
    assert by_concept["battery"]["agreement"] == "corroborated"
    assert info["disagreements"] == [{"name": "Air filter box", "named_by": "spark"}]
    assert info["primary_record"]["latency_s"] >= 0.0
    assert info["open_record"]["provider"] == "baseten"
    assert info["open_record"]["latency_s"] >= 0.0


def test_analyzer_open_timeout_returns_labels_on_time(tmp_path, monkeypatch):
    from mmc.api import routes_vision as rv

    monkeypatch.setattr(rv, "SYNC_BUDGET_S", 0.2)
    monkeypatch.setattr(rv, "OPEN_TIMEOUT_S", 1.0)
    seen = {}
    analyzer = rv.LiveVisionAnalyzer(
        _meta_client(tmp_path, PRIMARY),
        _open_client(tmp_path, SECOND_AGREE, delay_s=0.6),
        on_second_opinion=lambda sid, eid, view: seen.update(view),
    )
    t0 = time.time()
    merged, _overlays, info = analyzer.analyze_keyframe_full(
        b"img", session_id="s", evidence_id="e"
    )
    elapsed = time.time() - t0
    assert info["second_opinion_pending"] is True
    assert elapsed < 3.0  # labels never wait for the 6.5 s-scale Open call
    assert all(c["agreement"] == "single_source" for c in merged["components"])
    deadline = time.time() + 5.0
    while "components" not in seen and time.time() < deadline:
        time.sleep(0.05)
    assert [c["agreement"] for c in seen["components"] if c["sam_concept"] == "battery"] == [
        "corroborated"
    ]


def test_analyzer_open_error_degrades_to_single_source(tmp_path):
    from mmc.api import routes_vision as rv

    def boom(model, kwargs):
        raise RuntimeError("baseten 500")

    analyzer = rv.LiveVisionAnalyzer(
        _meta_client(tmp_path, PRIMARY),
        SparkOpenClient(api_key="k", cache_dir=tmp_path / "b",
                        replay_cached=False, transport=boom, sleep=lambda s: None),
    )
    merged, _overlays, info = analyzer.analyze_keyframe_full(b"img")
    assert info["second_opinion_pending"] is False
    assert all(c["agreement"] == "single_source" for c in merged["components"])


def test_posteriors_bit_identical_with_and_without_second_opinion(tmp_path, monkeypatch):
    from mmc import sessions as sess_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sess_mod.get_pack.cache_clear() if hasattr(sess_mod.get_pack, "cache_clear") else None
    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    before = sess_mod.compute_state(session)["hypotheses"]
    merged, _ = comp_mod.merge_opinions(PRIMARY, SECOND_AGREE)
    for obs in comp_mod.to_observations(merged, "ev-test"):
        registry.append(session, "observation_added", obs)
    after = sess_mod.compute_state(session)["hypotheses"]
    assert [h["hypothesis_id"] for h in before] == [h["hypothesis_id"] for h in after]
    for b, a in zip(before, after):
        assert b["current"] == a["current"]  # bitwise: observations never move posteriors


def test_keyframe_route_surfaces_agreement(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mmc.api import routes_vision as vision_routes
    from mmc.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    analyzer = vision_routes.LiveVisionAnalyzer(
        _meta_client(tmp_path, PRIMARY), _open_client(tmp_path, SECOND_AGREE)
    )
    vision_routes.set_analyzer(analyzer)
    try:
        client = TestClient(app)
        sid = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"}).json()[
            "session_id"
        ]
        blob = client.post(
            f"/sessions/{sid}/blobs", files={"file": ("frame.jpg", b"fake-jpeg", "image/jpeg")}
        ).json()["blob_ref"]
        r = client.post(f"/sessions/{sid}/vision/keyframe", json={"blob_ref": blob})
        assert r.status_code == 200, r.text
        body = r.json()
        by_concept = {c["sam_concept"]: c for c in body["components"]}
        assert by_concept["battery"]["agreement"] == "corroborated"
        assert body["disagreements"] == [{"name": "Air filter box", "named_by": "spark"}]
        assert body["second_opinion_pending"] is False
        assert body["evidence_id"]
        # Both opinions logged as llm_call events; posteriors unmoved.
        state = client.get(f"/sessions/{sid}/state").json()
        assert state["vision_second_opinions"] == []
        log = [e["type"] for e in client.get(f"/sessions/{sid}/state").json().get("tests_log", [])]
        assert log == []
    finally:
        vision_routes.set_analyzer(vision_routes.LiveVisionAnalyzer())
