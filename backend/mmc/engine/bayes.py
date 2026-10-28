"""Deterministic multi-hypothesis Bayes core (M3).

Only this module (never the LLM) changes posteriors. Naive-Bayes with one
update per discrete test outcome; correlated raw features (STFT+LTFT) are
merged upstream into a single outcome (see ``dependency_groups`` in the KP
pack and ``physics.trim_rpm_split``), so nothing is double-counted here.
"""

from __future__ import annotations


def update(
    posteriors: dict[str, float],
    outcome: str,
    likelihoods: dict[str, dict[str, float]],
    n_outcomes: int | None = None,
) -> dict[str, float]:
    """Return new posteriors after observing ``outcome``.

    ``likelihoods[h][outcome]`` is P(outcome | h). Hypotheses missing from
    ``likelihoods`` get the uniform default 1/n_outcomes, where n_outcomes
    is the outcome count of the test (inferred when omitted).
    """
    if n_outcomes is None:
        seen: set[str] = set()
        for row in likelihoods.values():
            seen.update(row)
        seen.add(outcome)
        n_outcomes = len(seen)
    uniform = 1.0 / n_outcomes
    unnorm = {}
    for hyp, prior in posteriors.items():
        row = likelihoods.get(hyp)
        lik = row.get(outcome, uniform) if row is not None else uniform
        unnorm[hyp] = prior * lik
    total = sum(unnorm.values())
    if total <= 0:
        raise ValueError("Bayes update has zero total probability")
    return {hyp: p / total for hyp, p in unnorm.items()}


def likelihood_ratio(
    hyp: str,
    outcome: str,
    likelihoods: dict[str, dict[str, float]],
    posteriors: dict[str, float],
    n_outcomes: int,
) -> float:
    """P(outcome | hyp) / P(outcome | others), for the explanation trace."""
    uniform = 1.0 / n_outcomes
    row = likelihoods.get(hyp)
    lik_h = row.get(outcome, uniform) if row is not None else uniform
    rest_prior = sum(p for h, p in posteriors.items() if h != hyp)
    if rest_prior <= 0:
        return float("inf") if lik_h > 0 else 1.0
    rest_lik = sum(
        posteriors[h]
        * ((likelihoods.get(h) or {}).get(outcome, uniform))
        for h in posteriors
        if h != hyp
    ) / rest_prior
    if rest_lik <= 0:
        return float("inf") if lik_h > 0 else 1.0
    return lik_h / rest_lik
