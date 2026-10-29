"""Evaluator unit tests (M3 step 4)."""

import pytest

from mmc.engine import evaluator
from mmc.kp.loader import PACKS_DIR, load_pack

PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")


def _eval(ref, measured, manual_spec=None):
    return evaluator.evaluate(PACK.tests[ref], measured, manual_spec)


def test_trim_routes_to_physics_source():
    outcome, expected = _eval("trim_idle_vs_2500", {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0})
    assert outcome == "lean_idle_normal_cruise"
    assert expected["source"]["kind"] == "physics"


def test_manual_spec_overrides_source():
    outcome, expected = _eval(
        "trim_idle_vs_2500",
        {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0},
        manual_spec={"ref": "manual p.42", "content_hash": "abc"},
    )
    assert outcome == "lean_idle_normal_cruise"
    assert expected["source"]["kind"] == "manual_page"


def test_maf_bands():
    kw = {"rpm": 750.0, "disp_l": 2.0}
    assert _eval("maf_pid_plausibility", {"maf_gs": 0.0, **kw})[0] == "reads_zero"
    assert _eval("maf_pid_plausibility", {"maf_gs": 1.0, **kw})[0] == "under_reports"
    assert _eval("maf_pid_plausibility", {"maf_gs": 3.5, **kw})[0] == "plausible"
    assert _eval("maf_pid_plausibility", {"maf_gs": 9.0, **kw})[0] == "over_reports"


def test_dtc_mapping():
    assert _eval("dtc_read", {"dtcs": ["P0102"]})[0] == "p0102"
    assert _eval("dtc_read", {"dtcs": ["P0113"]})[0] == "p0113"
    assert _eval("dtc_read", {"dtcs": ["P0128"]})[0] == "p0128"
    assert _eval("dtc_read", {"dtcs": ["P0303"]})[0] == "p030n"
    assert _eval("dtc_read", {"dtcs": ["P0420"]})[0] == "other_codes"
    assert _eval("dtc_read", {"dtcs": []})[0] == "none"


def test_iat_mapping():
    assert _eval("iat_plausibility", {"iat_c": -40.0})[0] == "minus40"
    assert _eval("iat_plausibility", {"iat_c": 25.0})[0] == "plausible"
    assert _eval("iat_plausibility", {"iat_c": 120.0})[0] == "biased"


def test_ect_mapping():
    assert _eval("ect_warmup_curve", {"ect_series_c": [20.0, 60.0, 90.0, 93.0, 94.0]})[0] == "plateau_normal"
    assert _eval("ect_warmup_curve", {"ect_series_c": [20.0, 55.0, 66.0, 68.5, 69.0]})[0] == "plateau_low"
    assert _eval("ect_warmup_curve", {"ect_series_c": []})[0] == "inconclusive"


def test_voltage_mapping():
    assert _eval("battery_voltage_rest_running", {"voltage_v": 12.5, "engine_on": False})[0] == "normal"
    assert _eval("battery_voltage_rest_running", {"voltage_v": 11.8, "engine_on": False})[0] == "low_rest"
    assert _eval("battery_voltage_rest_running", {"voltage_v": 12.0, "engine_on": True})[0] == "low_running"
    assert _eval("battery_voltage_rest_running", {"voltage_v": 14.2, "engine_on": True})[0] == "normal"
    assert _eval("battery_voltage_rest_running", {"voltage_v": 15.5, "engine_on": True})[0] == "overcharging"


def test_o2_mapping():
    ok, _ = _eval("o2_response_check", {"o2_samples_v": [0.1, 0.8, 0.15, 0.85, 0.12, 0.9]})
    assert ok == "switching_ok"
    lazy, _ = _eval("o2_response_check", {"o2_samples_v": [0.44, 0.45, 0.46, 0.45]})
    assert lazy == "lazy_stuck"


def test_mode06_from_counts():
    assert _eval("mode06_misfire_counts", {"misfire_counts": {1: 0, 2: 0, 3: 45, 4: 0}})[0] == "misfire_cyl_n"
    assert _eval("mode06_misfire_counts", {"misfire_counts": {1: 0, 2: 0, 3: 0, 4: 0}})[0] == "no_misfire"
    assert _eval("mode06_misfire_counts", {"misfire_counts": {}})[0] == "inconclusive"


def test_direct_passthrough_and_rejection():
    assert _eval("visual_intake_boot", {"outcome": "looks_ok"})[0] == "looks_ok"
    with pytest.raises(ValueError, match="not in"):
        _eval("visual_intake_boot", {"outcome": "exploded"})


def test_fuel_pressure_mapping():
    assert _eval("fuel_pressure_test", {"fuel_kpa": 150.0})[0] == "low_pressure"
    assert _eval("fuel_pressure_test", {"fuel_kpa": 350.0})[0] == "normal_pressure"


def _manual(min_v, max_v):
    return {"ref": "manual p.1", "content_hash": "abc", "min": min_v, "max": max_v,
            "units": "kPa"}


def test_manual_range_flips_fuel_pressure_outcome():
    """Finding #13 (SPEC §5: manual > kp > physics): a manual range decides
    the outcome, not just the citation. 350 kPa is normal against the
    hardcoded 280–420 kPa, but low against a manual 380–420 kPa range."""
    outcome, expected = _eval("fuel_pressure_test", {"fuel_kpa": 350.0},
                              manual_spec=_manual(380.0, 420.0))
    assert outcome == "low_pressure"
    assert expected["source"]["kind"] == "manual_page"
    assert expected["min"] == 380.0 and expected["max"] == 420.0


def test_manual_range_flips_voltage_outcome():
    """12.0 V at rest is low against the physics 12.4 V floor, but normal
    against a manual 11.5–12.6 V rest range (and still low against a
    manual 12.4–12.7 V range)."""
    outcome, _ = _eval("battery_voltage_rest_running",
                       {"voltage_v": 12.0, "engine_on": False},
                       manual_spec={"ref": "manual p.2", "content_hash": "abc",
                                    "min": 11.5, "max": 12.6, "units": "V"})
    assert outcome == "normal"
    outcome2, expected2 = _eval("battery_voltage_rest_running",
                                {"voltage_v": 12.0, "engine_on": False},
                                manual_spec={"ref": "manual p.2", "content_hash": "abc",
                                             "min": 12.4, "max": 12.7, "units": "V"})
    assert outcome2 == "low_rest"
    assert expected2["source"]["kind"] == "manual_page"


def test_unknown_evaluator_rejected():
    bad = PACK.tests["dtc_read"]
    import dataclasses

    altered = dataclasses.replace(bad, evaluator="nope.missing")
    with pytest.raises(ValueError, match="unknown evaluator"):
        evaluator.evaluate(altered, {"dtcs": []})
