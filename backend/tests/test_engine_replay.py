"""Replay determinism: rerunning an observation sequence reproduces posteriors.

Bitwise float equality after rounding to 1e-9 (M3 acceptance criterion).
"""

import pytest

from mmc.engine import bayes
from mmc.kp.loader import PACKS_DIR, load_pack

from sim_scenarios import PROFILES, SCENARIOS, run_planner_loop

PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")


def _replay(sequence):
    posteriors = {h: spec["prior"] for h, spec in PACK.hypotheses.items()}
    for test_ref, outcome in sequence:
        test = PACK.tests[test_ref]
        posteriors = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
    return posteriors


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("profile", ["L1", "L3"])
def test_replay_posteriors_bitwise_stable(scenario, profile):
    level, tools = PROFILES[profile]
    first, sequence = run_planner_loop(PACK, scenario, level, tools)
    second = _replay(sequence)
    for hyp in PACK.hypotheses:
        assert round(first[hyp], 9).hex() == round(second[hyp], 9).hex()
        assert first[hyp].hex() == second[hyp].hex()
