"""Engine property tests: posteriors always valid probabilities."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from mmc.engine import bayes, planner
from mmc.kp.loader import PACKS_DIR, load_pack

PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")
TEST_IDS = sorted(PACK.tests)


def _random_posteriors(draw):
    weights = draw(
        st.lists(st.floats(min_value=0.01, max_value=1.0), min_size=3, max_size=6)
    )
    total = sum(weights)
    return {f"h{i}": w / total for i, w in enumerate(weights)}


@settings(max_examples=100)
@given(st.data())
def test_posteriors_sum_to_one(data):
    posteriors = _random_posteriors(data.draw)
    test = PACK.tests[data.draw(st.sampled_from(TEST_IDS))]
    outcome = data.draw(st.sampled_from(test.outcomes))
    post = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
    assert abs(math.fsum(post.values()) - 1.0) <= 1e-9


@settings(max_examples=100)
@given(st.data())
def test_no_negative_posteriors(data):
    posteriors = _random_posteriors(data.draw)
    test = PACK.tests[data.draw(st.sampled_from(TEST_IDS))]
    outcome = data.draw(st.sampled_from(test.outcomes))
    post = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
    assert all(p >= 0.0 for p in post.values())
    assert all(math.isfinite(p) for p in post.values())


@settings(max_examples=50)
@given(st.data())
def test_planner_scores_finite_and_ordered(data):
    priors = {h: spec["prior"] for h, spec in PACK.hypotheses.items()}
    level = data.draw(st.sampled_from(["L1", "L2", "L3"]))
    ranked, _ = planner.rank_tests(PACK, priors, level, [], set(), set())
    scores = [r["score"] for r in ranked]
    assert all(math.isfinite(s) and s >= 0 for s in scores)
    assert scores == sorted(scores, reverse=True)
