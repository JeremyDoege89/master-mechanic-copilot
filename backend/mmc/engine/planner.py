"""Next-best-test planner: EIG/cost ranking with hard safety filters (M3).

Score (SPEC §5): ``score(t) = EIG(t) / (1.0·minutes + 5.0·usd + 3.0·risk)``.
Hard filters (code, not prompts): required tools ⊆ user tools, min_level ≤
user level, safety gate acknowledged, not already performed. Filtered-out
tests are reported with ``blocked_reasons`` and never ranked runnable.
"""

from __future__ import annotations

import math

from mmc.engine import bayes
from mmc.kp.loader import LEVELS, KnowledgePack, KpTest

W_TIME = 1.0
W_MONEY = 5.0
W_RISK = 3.0


def entropy(posteriors: dict[str, float]) -> float:
    return -sum(p * math.log2(p) for p in posteriors.values() if p > 0)


def outcome_prob(posteriors: dict[str, float], test: KpTest, outcome: str) -> float:
    n = len(test.outcomes)
    uniform = 1.0 / n
    return sum(
        posteriors[h] * test.likelihoods.get(h, {}).get(outcome, uniform)
        for h in posteriors
    )


def eig_bits(posteriors: dict[str, float], test: KpTest) -> float:
    """Expected information gain of running ``test``, in bits."""
    base = entropy(posteriors)
    gain = base
    for outcome in test.outcomes:
        p_o = outcome_prob(posteriors, test, outcome)
        if p_o <= 0:
            continue
        post = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
        gain -= p_o * entropy(post)
    return max(gain, 0.0)


def cost_of(test: KpTest) -> dict:
    return {"minutes": test.minutes, "usd": test.usd, "risk": test.risk}


def score(test: KpTest, eig: float) -> float:
    denom = W_TIME * test.minutes + W_MONEY * test.usd + W_RISK * test.risk
    return eig / max(denom, 1e-6)


def blocked_reasons(
    test: KpTest,
    user_level: str,
    user_tools: list[str],
    acknowledged_gates: set[str],
    performed: set[str],
) -> list[str]:
    reasons = []
    if LEVELS[test.min_level] > LEVELS[user_level]:
        reasons.append(f"requires level {test.min_level} (user is {user_level})")
    missing_tools = [t for t in test.tools if t not in user_tools]
    if missing_tools:
        reasons.append(f"missing tools: {', '.join(missing_tools)}")
    if test.safety_gate != "none" and test.safety_gate not in acknowledged_gates:
        reasons.append(f"safety gate not acknowledged: {test.safety_gate}")
    if test.ref in performed:
        reasons.append("already performed")
    return reasons


def rank_tests(
    pack: KnowledgePack,
    posteriors: dict[str, float],
    user_level: str,
    user_tools: list[str],
    acknowledged_gates: set[str] | None = None,
    performed: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (ranked runnable recommendations, blocked entries)."""
    acknowledged_gates = acknowledged_gates or set()
    performed = performed or set()
    ranked: list[dict] = []
    blocked: list[dict] = []
    for ref, test in pack.tests.items():
        reasons = blocked_reasons(test, user_level, list(user_tools), acknowledged_gates, performed)
        if reasons:
            blocked.append({"kp_test_ref": ref, "blocked_reasons": reasons})
            continue
        eig = eig_bits(posteriors, test)
        effects = []
        for outcome in test.outcomes:
            p_o = outcome_prob(posteriors, test, outcome)
            post = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
            effects.append({"outcome": outcome, "p": p_o, "effect": dict(post)})
        top_outcome = max(effects, key=lambda e: e["p"])
        ranked.append(
            {
                "kp_test_ref": ref,
                "eig_bits": eig,
                "cost": cost_of(test),
                "score": score(test, eig),
                "why": (
                    f"most likely outcome {top_outcome['outcome']} "
                    f"(p={top_outcome['p']:.2f}); best separates "
                    f"{max(posteriors, key=posteriors.get)} from alternatives"
                ),
                "expected_outcomes": effects,
                "required_tools": list(test.tools),
                "min_level": test.min_level,
                "safety_gate": test.safety_gate,
                "blocked_reasons": [],
            }
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)
    for i, rec in enumerate(ranked, start=1):
        rec["rank"] = i
    return ranked, blocked


def missing_evidence(
    pack: KnowledgePack,
    posteriors: dict[str, float],
    performed: set[str] | None = None,
    k: int = 3,
) -> list[str]:
    """Top-3 hypotheses × unobserved near-zero-cost features, by EIG.

    Near-zero-cost means obtainable without tools, money or risk in about a
    minute or less (PIDs already streaming, a photo, listening).
    """
    performed = performed or set()
    top_hyps = sorted(posteriors, key=posteriors.get, reverse=True)[:3]
    candidates = []
    for ref, test in pack.tests.items():
        if ref in performed or test.tools or test.usd or test.risk or test.minutes > 1:
            continue
        candidates.append((eig_bits(posteriors, test), ref))
    candidates.sort(reverse=True)
    return [
        f"no {ref} reading yet; would separate {', '.join(top_hyps)} (EIG {eig:.2f} bits)"
        for eig, ref in candidates[:k]
    ]
