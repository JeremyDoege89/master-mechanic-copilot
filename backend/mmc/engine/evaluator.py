"""Deterministic test evaluators (M3).

Maps (expected, measured) to a discrete KP outcome token. Source priority
for the expectation: manual_page (caller-supplied spec) > kp > physics.
Physics-derived expectations must be labelled "generic" in the UI.
"""

from __future__ import annotations

from mmc.engine import physics
from mmc.kp.loader import KpTest

MISFIRE_COUNT_THRESHOLD = 10
IAT_OPEN_CIRCUIT_C = -35.0
IAT_PLAUSIBLE_LO, IAT_PLAUSIBLE_HI = -30.0, 60.0
MAF_ZERO_GPS = 0.5
MAF_UNDER_RATIO = 0.7
MAF_OVER_RATIO = 1.3
FUEL_KPA_MIN, FUEL_KPA_MAX = 280.0, 420.0


def _expected(kind: str, ref: str, detail: str = "") -> dict:
    return {"source": {"kind": kind, "ref": ref}, "outcome": detail}


def evaluate(
    test: KpTest,
    measured: dict,
    manual_spec: dict | None = None,
) -> tuple[str, dict]:
    """Return (outcome, expected). ``manual_spec`` overrides the expectation source."""
    fn = _EVALUATORS.get(test.evaluator)
    if fn is None:
        raise ValueError(f"unknown evaluator {test.evaluator!r} for test {test.ref}")
    outcome, expected = fn(test, measured)
    if manual_spec is not None:
        source = {"kind": "manual_page", **manual_spec}
        expected = {"source": source, "outcome": expected.get("outcome", "")}
        # A manual page states the expectation numerically: its min/max/units
        # ride on the expected value itself (test_result schema), not just
        # inside the source citation.
        for key in ("min", "max", "units"):
            if manual_spec.get(key) is not None:
                expected[key] = manual_spec[key]
    if outcome not in test.outcomes:
        raise ValueError(f"evaluator {test.evaluator} produced {outcome!r}, not in {test.outcomes}")
    return outcome, expected


def _trim(test: KpTest, measured: dict) -> tuple[str, dict]:
    outcome = physics.trim_rpm_split(measured["idle_trim_pct"], measured["cruise_trim_pct"])
    return outcome, _expected("physics", "physics.trim_rpm_split", outcome)


def _maf(test: KpTest, measured: dict) -> tuple[str, dict]:
    maf_gs = float(measured["maf_gs"])
    exp = physics.maf_expected(
        float(measured["rpm"]),
        float(measured.get("disp_l", 2.0)),
        float(measured.get("map_kpa", 30.0)),
        float(measured.get("baro_kpa", 100.0)),
    )
    if maf_gs < MAF_ZERO_GPS:
        outcome = "reads_zero"
    elif exp <= 0:
        outcome = "reads_zero"
    elif maf_gs < MAF_UNDER_RATIO * exp:
        outcome = "under_reports"
    elif maf_gs > MAF_OVER_RATIO * exp:
        outcome = "over_reports"
    else:
        outcome = "plausible"
    return outcome, _expected("physics", "physics.maf_expected", f"{exp:.2f} g/s (generic)")


def _ect(test: KpTest, measured: dict) -> tuple[str, dict]:
    series = list(measured["ect_series_c"])
    if not series:
        return "inconclusive", _expected("physics", "physics.ect_plateau", "no data")
    outcome = "plateau_normal" if physics.ect_plateau_ok(series) else "plateau_low"
    return outcome, _expected("physics", "physics.ect_plateau", outcome)


def _voltage(test: KpTest, measured: dict) -> tuple[str, dict]:
    v = float(measured["voltage_v"])
    engine_on = bool(measured.get("engine_on", False))
    if engine_on:
        if v < physics.VOLT_RUN_MIN:
            outcome = "low_running"
        elif v > physics.VOLT_RUN_MAX:
            outcome = "overcharging"
        else:
            outcome = "normal"
    else:
        if v < physics.VOLT_REST_MIN:
            outcome = "low_rest"
        elif v > 12.9:
            outcome = "overcharging"
        else:
            outcome = "normal"
    return outcome, _expected("physics", "physics.voltage", outcome)


def _o2(test: KpTest, measured: dict) -> tuple[str, dict]:
    outcome = "switching_ok" if physics.o2_switching_ok(list(measured["o2_samples_v"])) else "lazy_stuck"
    return outcome, _expected("physics", "physics.o2_switching", outcome)


def _dtc(test: KpTest, measured: dict) -> tuple[str, dict]:
    dtcs = [d.upper() for d in measured.get("dtcs", [])]
    for prefix, outcome in (("P0102", "p0102"), ("P0113", "p0113"), ("P0128", "p0128")):
        if prefix in dtcs:
            return outcome, _expected("kp", "kp:dtc_match", outcome)
    if any(d.startswith("P030") for d in dtcs):
        return "p030n", _expected("kp", "kp:dtc_match", "p030n")
    if dtcs:
        return "other_codes", _expected("kp", "kp:dtc_match", "other_codes")
    return "none", _expected("kp", "kp:dtc_match", "none")


def _iat(test: KpTest, measured: dict) -> tuple[str, dict]:
    iat = float(measured["iat_c"])
    if iat <= IAT_OPEN_CIRCUIT_C:
        outcome = "minus40"
    elif IAT_PLAUSIBLE_LO <= iat <= IAT_PLAUSIBLE_HI:
        outcome = "plausible"
    else:
        outcome = "biased"
    return outcome, _expected("kp", "kp:iat_range", outcome)


def _fuel_pressure(test: KpTest, measured: dict) -> tuple[str, dict]:
    lo = float(measured.get("spec_min_kpa", FUEL_KPA_MIN))
    hi = float(measured.get("spec_max_kpa", FUEL_KPA_MAX))
    v = float(measured["fuel_kpa"])
    if v < lo:
        outcome = "low_pressure"
    elif v <= hi:
        outcome = "normal_pressure"
    else:
        outcome = "inconclusive"
    return outcome, _expected("kp", "kp:fuel_pressure_spec", outcome)


def _mode06(test: KpTest, measured: dict) -> tuple[str, dict]:
    if "outcome" in measured:
        outcome = measured["outcome"]
        return outcome, _expected("mode06_limit", "ecu:mode06", outcome)
    counts = measured.get("misfire_counts", {})
    if not counts:
        return "inconclusive", _expected("mode06_limit", "ecu:mode06", "no data")
    threshold = int(measured.get("threshold", MISFIRE_COUNT_THRESHOLD))
    if any(int(c) >= threshold for c in counts.values()):
        return "misfire_cyl_n", _expected("mode06_limit", "ecu:mode06", "misfire_cyl_n")
    return "no_misfire", _expected("mode06_limit", "ecu:mode06", "no_misfire")


def _direct(test: KpTest, measured: dict) -> tuple[str, dict]:
    # Human/vision-judged tests: the outcome arrives already discrete.
    return measured["outcome"], _expected("kp", f"kp:{test.ref}", measured["outcome"])


def _engine_sound(test: KpTest, measured: dict) -> tuple[str, dict]:
    # DSP-judged test: the outcome arrives discrete from the engine_sound
    # analysis endpoint (stdlib DSP, no LLM). Membership in the KP outcome
    # list is enforced by evaluate(); posteriors move only here.
    return measured["outcome"], _expected("kp", f"kp:{test.ref}", measured["outcome"])


_EVALUATORS = {
    "physics.trim_rpm_split": _trim,
    "physics.maf_expected": _maf,
    "physics.ect_plateau": _ect,
    "physics.voltage": _voltage,
    "physics.o2_switching": _o2,
    "rule.dtc_match": _dtc,
    "rule.iat": _iat,
    "rule.fuel_pressure": _fuel_pressure,
    "mode06.misfire": _mode06,
    "visual.direct": _direct,
    "sensory.direct": _direct,
    "dsp.engine_sound": _engine_sound,
}
