"""Python port of Elm327Sim.kt vehicle math + per-scenario fixture outcomes (M3).

Mirrors the Kotlin simulator exactly (same constants and formulas) so pytest
needs no Android. The fixture answers EVERY KP test for EVERY scenario via
:func:`outcome_for_test` — the scenario loop always runs the planner's
top-ranked runnable test; a test the fixture cannot answer is a fixture gap.
"""

from __future__ import annotations

import math

from mmc.engine import evaluator
from mmc.kp.loader import KnowledgePack

AMBIENT_C = 20.0
WARM_TARGET_C = 95.0
STUCK_TARGET_C = 70.0
WARM_TAU_S = 300.0
IDLE_RPM = 750.0
CRUISE_RPM = 2500.0
DISPLACEMENT_L = 2.0
WARM_T_S = 1200.0
MISFIRE_CYL = 3

ECT_SERIES_T_S = [0.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1200.0]

SCENARIOS = [
    "healthy",
    "maf_unplugged",
    "iat_unplugged",
    "vacuum_leak",
    "fuel_fuse_removed",
    "coil_unplugged",
    "thermostat_stuck_open",
]

TRUE_HYPOTHESIS = {
    "healthy": "healthy",
    "maf_unplugged": "maf_circuit_open",
    "iat_unplugged": "iat_circuit_open",
    "vacuum_leak": "vacuum_leak_post_maf",
    "fuel_fuse_removed": "fuel_delivery_low",
    "coil_unplugged": "ignition_misfire_cyl",
    "thermostat_stuck_open": "thermostat_stuck_open",
}


def ect_c(scenario: str, t_s: float) -> float:
    target = STUCK_TARGET_C if scenario == "thermostat_stuck_open" else WARM_TARGET_C
    return target - (target - AMBIENT_C) * math.exp(-t_s / WARM_TAU_S)


def maf_gs(scenario: str, rpm: float) -> float:
    if scenario in ("fuel_fuse_removed", "maf_unplugged"):
        return 0.0
    if rpm <= 0:
        return 0.0
    return (rpm / 120.0) * DISPLACEMENT_L * 0.8 * 1.18 * 0.3


def total_trim_pct(scenario: str, rpm: float) -> float:
    if scenario == "vacuum_leak":
        return 18.0 * (IDLE_RPM / rpm) ** 1.2
    if scenario == "maf_unplugged":
        return 12.0
    if scenario == "fuel_fuse_removed":
        return 0.0
    return 1.5


def iat_c(scenario: str) -> float:
    return -40.0 if scenario == "iat_unplugged" else AMBIENT_C + 5.0


def dtcs(scenario: str) -> list[str]:
    return {
        "maf_unplugged": ["P0102"],
        "iat_unplugged": ["P0113"],
        "coil_unplugged": [f"P030{MISFIRE_CYL}"],
        "thermostat_stuck_open": ["P0128"],
    }.get(scenario, [])


def misfire_counts(scenario: str) -> dict[int, int]:
    return {
        cyl: (45 if scenario == "coil_unplugged" and cyl == MISFIRE_CYL else 0)
        for cyl in (1, 2, 3, 4)
    }


def ect_series(scenario: str) -> list[float]:
    return [ect_c(scenario, t) for t in ECT_SERIES_T_S]


def snapshot(scenario: str) -> dict:
    """Warm-idle + 2500 rpm PID/DTC/Mode 06 snapshot plus ECT series.

    This is the sim-parity contract: the Kotlin snapshot test writes these
    same values from ``Elm327Sim`` and ``test_sim_parity.py`` compares.
    """
    assert scenario in SCENARIOS, scenario
    rpm_points = {"idle": IDLE_RPM, "cruise2500": CRUISE_RPM}
    if scenario == "fuel_fuse_removed":
        rpm_points = {"idle": 0.0, "cruise2500": 0.0}
    points = {}
    for name, rpm in rpm_points.items():
        points[name] = {
            "rpm": rpm,
            "ect_c": ect_c(scenario, WARM_T_S),
            "maf_gs": maf_gs(scenario, rpm),
            "trim_total_pct": total_trim_pct(scenario, rpm) if rpm > 0 else 0.0,
            "iat_c": iat_c(scenario),
            "dtcs": dtcs(scenario),
            "misfire_counts": {str(k): v for k, v in misfire_counts(scenario).items()},
        }
    return {
        "scenario": scenario,
        "displacement_l": DISPLACEMENT_L,
        "points": points,
        "ect_series": [{"t_s": t, "ect_c": ect_c(scenario, t)} for t in ECT_SERIES_T_S],
    }


def measured_for_test(scenario: str, test_ref: str) -> dict:
    """Raw measured payload for any KP test in any scenario."""
    if test_ref == "dtc_read":
        return {"dtcs": dtcs(scenario)}
    if test_ref == "maf_pid_plausibility":
        rpm = 0.0 if scenario == "fuel_fuse_removed" else IDLE_RPM
        return {"maf_gs": maf_gs(scenario, rpm), "rpm": rpm, "disp_l": DISPLACEMENT_L}
    if test_ref == "iat_plausibility":
        return {"iat_c": iat_c(scenario)}
    if test_ref == "trim_idle_vs_2500":
        if scenario == "fuel_fuse_removed":
            return {"idle_trim_pct": 0.0, "cruise_trim_pct": 0.0}
        return {
            "idle_trim_pct": total_trim_pct(scenario, IDLE_RPM),
            "cruise_trim_pct": total_trim_pct(scenario, CRUISE_RPM),
        }
    if test_ref == "visual_intake_boot":
        return {"outcome": "cracked_disconnected" if scenario == "vacuum_leak" else "looks_ok"}
    if test_ref == "listen_fuel_pump_prime":
        return {
            "outcome": "no_prime_no_start"
            if scenario == "fuel_fuse_removed"
            else "primes_normally"
        }
    if test_ref == "ect_warmup_curve":
        return {"ect_series_c": ect_series(scenario)}
    if test_ref == "mode06_misfire_counts":
        return {"misfire_counts": misfire_counts(scenario)}
    if test_ref == "battery_voltage_rest_running":
        if scenario == "fuel_fuse_removed":
            return {"voltage_v": 12.5, "engine_on": False}
        return {"voltage_v": 14.2, "engine_on": True}
    if test_ref == "o2_response_check":
        return {"o2_samples_v": [0.12, 0.82, 0.15, 0.85, 0.11, 0.88, 0.14, 0.83]}
    if test_ref == "smoke_intake_test":
        return {"outcome": "leak_found" if scenario == "vacuum_leak" else "no_leak"}
    if test_ref == "fuel_pressure_test":
        return {"fuel_kpa": 150.0 if scenario == "fuel_fuse_removed" else 350.0}
    if test_ref == "engine_sound_screen":
        # M5O DSP screening: a dead cylinder drops firing pulses, a vacuum
        # leak hisses at idle, a no-start engine yields no usable clip.
        return {"outcome": {
            "coil_unplugged": "misfire_dropout",
            "vacuum_leak": "vacuum_hiss",
            "fuel_fuse_removed": "inconclusive",
        }.get(scenario, "normal")}
    raise KeyError(f"fixture has no measured payload for test {test_ref!r}")


ALL_GATES = {"engine_running_outdoors", "engine_off", "fuel_system"}

PROFILES = {
    "L1": ("L1", []),
    "L3": ("L3", ["multimeter", "smoke machine", "fuel pressure gauge", "vacuum gauge"]),
}


def run_planner_loop(
    pack: KnowledgePack,
    scenario: str,
    user_level: str,
    user_tools: list[str],
    max_steps: int = 6,
) -> tuple[dict[str, float], list[tuple[str, str]]]:
    """Planner-driven diagnosis loop for one scenario.

    Each step performs the planner's top-ranked runnable test and takes the
    outcome from the scenario fixture. Returns (posteriors, [(test, outcome)]).
    """
    from mmc.engine import bayes, planner

    posteriors = {h: spec["prior"] for h, spec in pack.hypotheses.items()}
    performed: set[str] = set()
    sequence: list[tuple[str, str]] = []
    for _ in range(max_steps):
        ranked, _ = planner.rank_tests(
            pack, posteriors, user_level, list(user_tools), ALL_GATES, performed
        )
        assert ranked, f"planner has no runnable test for {scenario}/{user_level}"
        top = ranked[0]["kp_test_ref"]
        outcome, _, _ = outcome_for_test(scenario, top, pack)
        test = pack.tests[top]
        posteriors = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
        performed.add(top)
        sequence.append((top, outcome))
    return posteriors, sequence


def outcome_for_test(scenario: str, test_ref: str, pack: KnowledgePack) -> tuple[str, dict, dict]:
    """Evaluate the KP test against the scenario model.

    Returns (outcome, measured, expected). Raises KeyError for a test the
    fixture cannot answer — that is a fixture gap, not a planner override.
    """
    measured = measured_for_test(scenario, test_ref)
    outcome, expected = evaluator.evaluate(pack.tests[test_ref], measured)
    return outcome, measured, expected
