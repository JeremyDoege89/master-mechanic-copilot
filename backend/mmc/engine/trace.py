"""Explanation-trace entries per hypothesis.json (M3)."""

from __future__ import annotations

from mmc.engine.bayes import likelihood_ratio


def make_entry(
    seq: int,
    evidence_id: str,
    test_id: str | None,
    prior: float,
    posterior: float,
    outcome: str,
    likelihoods: dict[str, dict[str, float]],
    hyp: str,
    posteriors_before: dict[str, float],
    n_outcomes: int,
    lr_source: str,
    expected: str = "",
    measured: str = "",
    text_l1: str = "",
    text_l2: str = "",
    text_l3: str = "",
) -> dict:
    return {
        "seq": seq,
        "evidence_id": evidence_id,
        "test_id": test_id,
        "prior": prior,
        "posterior": posterior,
        "likelihood_ratio": likelihood_ratio(hyp, outcome, likelihoods, posteriors_before, n_outcomes),
        "lr_source": lr_source,
        "expected": expected,
        "measured": measured,
        "text_L1": text_l1,
        "text_L2": text_l2,
        "text_L3": text_l3,
    }
