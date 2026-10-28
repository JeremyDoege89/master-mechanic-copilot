"""Planner + stop-rule tests (M3 step 5)."""

import pytest

from mmc.engine import planner, stoprule
from mmc.kp.loader import PACKS_DIR, load_pack

PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")
ALL_GATES = {"engine_running_outdoors", "engine_off", "fuel_system"}


def _priors():
    return {h: spec["prior"] for h, spec in PACK.hypotheses.items()}


def test_tool_filter_excludes_multimeter():
    ranked, blocked = planner.rank_tests(
        PACK, _priors(), "L3", [], ALL_GATES, set()
    )
    refs = [r["kp_test_ref"] for r in ranked]
    assert "battery_voltage_rest_running" not in refs
    entry = next(b for b in blocked if b["kp_test_ref"] == "battery_voltage_rest_running")
    assert any("multimeter" in r for r in entry["blocked_reasons"])


def test_tools_subset_allowed():
    ranked, _ = planner.rank_tests(
        PACK, _priors(), "L3", ["multimeter"], ALL_GATES, set()
    )
    assert "battery_voltage_rest_running" in [r["kp_test_ref"] for r in ranked]


def test_level_filter_excludes_L2_for_L1_user():
    ranked, blocked = planner.rank_tests(PACK, _priors(), "L1", [], ALL_GATES, set())
    refs = [r["kp_test_ref"] for r in ranked]
    assert "mode06_misfire_counts" not in refs
    assert "battery_voltage_rest_running" not in refs
    entry = next(b for b in blocked if b["kp_test_ref"] == "mode06_misfire_counts")
    assert any("L2" in r for r in entry["blocked_reasons"])


def test_gated_test_blocked_until_ack():
    ranked, blocked = planner.rank_tests(PACK, _priors(), "L1", [], set(), set())
    refs = [r["kp_test_ref"] for r in ranked]
    assert "trim_idle_vs_2500" not in refs  # gate engine_running_outdoors unacked
    assert "dtc_read" in refs  # gate none always runnable
    ranked2, _ = planner.rank_tests(PACK, _priors(), "L1", [], ALL_GATES, set())
    assert "trim_idle_vs_2500" in [r["kp_test_ref"] for r in ranked2]


def test_performed_test_never_reranked():
    ranked, _ = planner.rank_tests(PACK, _priors(), "L1", [], ALL_GATES, {"dtc_read"})
    assert "dtc_read" not in [r["kp_test_ref"] for r in ranked]


def test_eig_perfect_test_beats_useless_test():
    priors = {"a": 0.5, "b": 0.5}
    perfect = PACK.tests["dtc_read"]
    assert planner.eig_bits(_priors(), perfect) > 0.1
    # A test whose outcomes are independent of the hypothesis has ~0 EIG.
    from mmc.kp.loader import KpTest

    useless = KpTest(
        ref="coin", kind="sensory", min_level="L1", outcomes=["heads", "tails"],
        evaluator="sensory.direct",
        likelihoods={"a": {"heads": 0.5, "tails": 0.5}, "b": {"heads": 0.5, "tails": 0.5}},
    )
    assert planner.eig_bits(priors, useless) == pytest.approx(0.0)


def test_score_penalizes_cost():
    priors = _priors()
    cheap = PACK.tests["dtc_read"]  # 1 min
    pricey = PACK.tests["ect_warmup_curve"]  # 15 min
    assert planner.score(cheap, 0.5) > planner.score(pricey, 0.5)


def test_rank_is_sorted_by_score():
    ranked, _ = planner.rank_tests(PACK, _priors(), "L3",
                                  ["multimeter", "smoke machine", "fuel pressure gauge"],
                                  ALL_GATES, set())
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))


def test_missing_evidence_lists_unobserved():
    missing = planner.missing_evidence(PACK, _priors(), set())
    assert 1 <= len(missing) <= 3
    assert any("dtc_read" in m for m in missing)
    missing2 = planner.missing_evidence(PACK, _priors(), {"dtc_read", "iat_plausibility",
                                                         "listen_fuel_pump_prime"})
    assert not any("dtc_read" in m for m in missing2)


def test_stop_rule():
    assert stoprule.should_stop(0.9, 0.01) is True
    assert stoprule.should_stop(0.9, 0.5) is False
    assert stoprule.should_stop(0.5, 0.01) is False
    assert stoprule.should_stop(0.9, None) is True
    assert stoprule.should_stop(0.9, 0.0) is True
