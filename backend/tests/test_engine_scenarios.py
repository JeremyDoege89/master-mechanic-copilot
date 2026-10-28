"""Scenario convergence tests (M3 acceptance criteria).

Each test runs the planner-driven loop: every step performs the planner's
top-ranked runnable test and takes the outcome from the scenario fixture.
No scripted test order. Both user profiles (L1 with OBD adapter only, L3
with a full toolset) must converge to the true hypothesis with p >= 0.8
within <= 4 tests.
"""

import pytest

from mmc.kp.loader import LEVELS, PACKS_DIR, load_pack

from sim_scenarios import PROFILES, SCENARIOS, TRUE_HYPOTHESIS, run_planner_loop

PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")

CASES = [(sc, profile) for sc in SCENARIOS for profile in ("L1", "L3")]


@pytest.mark.parametrize("scenario,profile", CASES)
def test_scenario_converges(scenario, profile):
    level, tools = PROFILES[profile]
    posteriors, sequence = run_planner_loop(PACK, scenario, level, tools)
    assert len(sequence) <= 6  # loop ran; convergence checked below step by step

    # Replay the recorded sequence and find the first step meeting criterion.
    from mmc.engine import bayes

    priors = {h: spec["prior"] for h, spec in PACK.hypotheses.items()}
    converged_at = None
    for i, (test_ref, outcome) in enumerate(sequence, start=1):
        test = PACK.tests[test_ref]
        # Planner guarantee: only runnable tests are ever performed.
        assert all(t in tools for t in test.tools), (scenario, profile, test_ref)
        assert LEVELS[test.min_level] <= LEVELS[level], (scenario, profile, test_ref)
        priors = bayes.update(priors, outcome, test.likelihoods, len(test.outcomes))
        top = max(priors, key=priors.get)
        if top == TRUE_HYPOTHESIS[scenario] and priors[top] >= 0.8 and converged_at is None:
            converged_at = i

    assert converged_at is not None, (
        f"{scenario}/{profile}: never converged; final={max(priors, key=priors.get)} "
        f"p={max(priors.values()):.3f} seq={sequence}"
    )
    assert converged_at <= 4, (
        f"{scenario}/{profile}: converged in {converged_at} tests {sequence}"
    )


def test_fixture_answers_every_test():
    # Fixture gap check: the planner may rank any test first; all must resolve.
    from sim_scenarios import outcome_for_test

    for scenario in SCENARIOS:
        for ref in PACK.tests:
            outcome, measured, expected = outcome_for_test(scenario, ref, PACK)
            assert outcome in PACK.tests[ref].outcomes, (scenario, ref)
            assert measured and expected
