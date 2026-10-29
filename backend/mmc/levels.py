"""L1/L2/L3 text styles — the only renderer (M6 §1).

Deterministic templating over KP text + engine numbers. No LLM: future
Spark narration (``agent/prompts/level_styles.md``) may only reword these
strings, never numbers or probabilities.
"""

from __future__ import annotations

import re

LEVELS = ("L1", "L2", "L3")

LEVEL_LABELS = {
    "L1": "I drive it \u2014 never opened the hood",
    "L2": "I do my own oil changes / basic repairs",
    "L3": "Professional technician",
}

# Canonical tools questionnaire (SPEC §3). "none" is the empty selection.
TOOL_OPTIONS = [
    "multimeter",
    "test light",
    "jack + stands",
    "vacuum gauge",
    "smoke machine",
    "fuel pressure gauge",
    "scope",
    "enhanced scan tool",
]

# Acronyms and trade terms a first-time driver cannot be expected to know.
# L1 hypothesis labels and L1 test instructions hard-fail on these
# (kp/loader.py). Tool names (multimeter, gauge) and plain words
# (sensor, volts, pressure) are deliberately NOT on this list.
JARGON_DENYLIST = [
    "maf",
    "stft",
    "ltft",
    "ect",
    "iat",
    "mil",
    "dtc",
    "dlc",
    "ecu",
    "pcm",
    "tcm",
    "pid",
    "obd",
    "tps",
    "vss",
    "o2",
    "ckp",
    "cmp",
    "evap",
    "egr",
    "stoichiometric",
    "lambda",
    "freeze frame",
    "fuel trim",
    "readiness",
    "mode 0",
    "mode0",
    "koeo",
    "koer",
    "bidirectional",
]

_JARGON_RES = [
    re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE) for term in JARGON_DENYLIST
]


def check_l1(text: str) -> list[str]:
    """Return the denylisted terms found in L1 ``text`` (empty = clean)."""
    return [term for term, rx in zip(JARGON_DENYLIST, _JARGON_RES) if rx.search(text or "")]


def _by_level(mapping: dict, level: str) -> str:
    """Level lookup with the KP fallback chain L2 → L1 → L3."""
    for key in (level, "L1", "L3"):
        value = (mapping or {}).get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def hypothesis_label(label: dict, level: str) -> str:
    """Hypothesis display label for ``level``."""
    return _by_level(label, level)


def instruction(instructions: dict, level: str) -> str:
    """Test instruction text for ``level``."""
    return _by_level(instructions, level)


def verbal_band(p: float) -> str:
    """Verbal band + natural frequency (SPEC §3), e.g. 'Likely (about 6 in 10)'."""
    # Finding #12: round half up, not Python banker's rounding (which sent
    # 0.85 to "8 in 10" against the adjacent band). The 1e-9 epsilon keeps
    # float artefacts such as 0.85*10 == 8.499999999999998 on the right side.
    import math

    n = max(0, min(10, int(math.floor(float(p) * 10 + 0.5 + 1e-9))))
    if p >= 0.85:
        band = "Very likely"
    elif p >= 0.6:
        band = "Likely"
    elif p >= 0.4:
        band = "Possible"
    elif p >= 0.2:
        band = "Unlikely"
    else:
        band = "Very unlikely"
    return f"{band} (about {n} in 10)"


# Finding #12: plain-language L1 paraphrases for every KP outcome slug,
# so generated trace text never leaks jargon (e.g. 'p030n') that the
# loader denylist cannot see. Unknown slugs fall back to "the test
# result" — never the raw slug. Every phrase is check_l1-clean.
OUTCOME_L1 = {
    "p0102": "the airflow reading dropping out",
    "p0113": "the intake temperature reading stuck impossibly cold",
    "p0128": "the engine staying too cool",
    "p030n": "one cylinder misfiring",
    "none": "no fault showing up",
    "other_codes": "a different fault showing up",
    "reads_zero": "the reading dropping to zero",
    "under_reports": "the reading coming in low",
    "plausible": "a believable reading",
    "over_reports": "the reading coming in high",
    "minus40": "the temperature stuck impossibly cold",
    "biased": "the reading shifted off",
    "lean_idle_normal_cruise": "the engine running lean at idle but fine when revved",
    "lean_both": "the engine running lean at idle and when revved",
    "normal": "a normal result",
    "rich": "the engine running rich",
    "cracked_disconnected": "a cracked or loose part",
    "looks_ok": "everything looking fine",
    "inconclusive": "an unclear result",
    "no_prime_no_start": "no fuel sound and no start",
    "primes_normally": "the normal priming sound",
    "weak_prime": "a weak priming sound",
    "plateau_low": "the temperature levelling off too low",
    "plateau_normal": "the temperature warming up normally",
    "misfire_cyl_n": "one cylinder misfiring",
    "no_misfire": "no misfire showing up",
    "low_rest": "low voltage with the engine off",
    "low_running": "low voltage with the engine running",
    "overcharging": "voltage reading high",
    "switching_ok": "the sensor switching normally",
    "lazy_stuck": "the sensor responding slowly",
    "leak_found": "a leak found",
    "no_leak": "no leak found",
    "low_pressure": "pressure reading low",
    "normal_pressure": "pressure reading normal",
    "misfire_dropout": "a stumble in the engine sound",
    "belt_bearing_tone": "a whine from a belt or pulley",
    "vacuum_hiss": "a hissing sound",
}


def outcome_l1(outcome: str) -> str:
    """Plain-language L1 phrase for a KP outcome slug (never the slug)."""
    return OUTCOME_L1.get(outcome, "the test result")


def trace_text(
    label: dict,
    outcome: str,
    prior: float,
    posterior: float,
    likelihood_ratio: float,
) -> dict[str, str]:
    """Per-level explanation of one posterior move (deterministic)."""
    direction = "more likely" if posterior >= prior else "less likely"
    band = verbal_band(posterior)
    return {
        "L1": (
            f"{_by_level(label, 'L1')}: {outcome_l1(outcome)} makes this "
            f"{direction} \u2014 {band}."
        ),
        "L2": (
            f"{_by_level(label, 'L2')}: outcome {outcome!r} moved this "
            f"from {prior:.2f} to {posterior:.2f} ({band})."
        ),
        "L3": (
            f"{_by_level(label, 'L3')}: {outcome} "
            f"LR {likelihood_ratio:.2f}, {prior:.3f}\u2192{posterior:.3f}."
        ),
    }


def summary(
    level: str,
    *,
    concluded: bool,
    conclusion: str,
    top_id: str,
    top_label: dict,
    top_p: float,
    n_tests: int,
    suspects: str,
) -> str:
    """Level-adapted report summary. All three levels differ pairwise."""
    if concluded:
        if level == "L1":
            return conclusion
        if level == "L2":
            return (
                f"{_by_level(top_label, 'L2')} suspected "
                f"({verbal_band(top_p)}, p={top_p:.2f}); "
                f"{n_tests} test(s) performed."
            )
        return (
            f"{top_id} posterior {top_p:.3f} "
            f"({_by_level(top_label, 'L1')} / {_by_level(top_label, 'L3')}) "
            f"after {n_tests} test(s)."
        )
    base = (
        f"Not enough evidence yet \u2014 no conclusion after {n_tests} test(s). "
        f"Current suspects: {suspects}."
    )
    if level == "L1":
        return base + " Run the recommended tests first."
    if level == "L2":
        return base + " Work the ranked test list below (DIY view)."
    return base + f" Leader {top_id} p={top_p:.3f}; rank by EIG/cost."
