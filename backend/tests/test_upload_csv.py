"""M5U CSV tests (§3): golden logs reproduce .expected.json exactly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mmc.engine import evaluator
from mmc.kp.loader import PACKS_DIR, load_pack
from mmc.upload import csv_logs

FIXTURES = Path(__file__).parent / "fixtures" / "upload"
PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")

GOLDENS = [
    "torque_vacuum_leak",
    "car_scanner_healthy",
    "forscan_ect_stuck_open",
]


@pytest.mark.parametrize("name", GOLDENS)
def test_golden_csv_reproduces_expected(name):
    parsed = csv_logs.parse_csv((FIXTURES / f"{name}.csv").read_bytes())
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text())
    assert parsed.exporter == expected["exporter"]
    for series, median in expected["medians"].items():
        assert parsed.series[series] is not None
        assert csv_logs.series_median(parsed.series[series]) == pytest.approx(median)
    derived, skipped = csv_logs.derive_tests(parsed)
    outcomes = {}
    for item in derived:
        test = PACK.tests[item["test_ref"]]
        outcome, _ = evaluator.evaluate(test, item["measured"])
        outcomes[item["test_ref"]] = outcome
    assert outcomes == expected["outcomes"]
    assert sorted(s["test_ref"] for s in skipped) == sorted(expected["skipped"])


def test_vacuum_leak_yields_lean_idle_normal_cruise():
    parsed = csv_logs.parse_csv((FIXTURES / "torque_vacuum_leak.csv").read_bytes())
    derived, _ = csv_logs.derive_tests(parsed)
    trim = next(d for d in derived if d["test_ref"] == "trim_idle_vs_2500")
    outcome, _ = evaluator.evaluate(PACK.tests["trim_idle_vs_2500"], trim["measured"])
    assert outcome == "lean_idle_normal_cruise"


def test_malformed_csv_names_missing_column():
    with pytest.raises(ValueError, match="time/RPM"):
        csv_logs.parse_csv(b"foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="time/RPM"):
        csv_logs.parse_csv(b"")


def test_unknown_columns_ignored_and_reported():
    parsed = csv_logs.parse_csv((FIXTURES / "torque_vacuum_leak.csv").read_bytes())
    assert "Longitude" in parsed.ignored_columns
    assert "longitude" not in parsed.series


def test_unit_conversions():
    parsed = csv_logs.parse_csv((FIXTURES / "torque_vacuum_leak.csv").read_bytes())
    assert csv_logs.series_median(parsed.series["ect_c"]) == pytest.approx(90.5556, abs=1e-3)
    assert csv_logs.series_median(parsed.series["speed_kph"]) == pytest.approx(96.5604 / 2, abs=1e-3)


def test_bad_cell_drops_row_not_column():
    """Finding #4: one bad STFT cell must drop its ROW (keeping column
    alignment), not shift the STFT column against the RPM column.

    Idle trims [12, BAD, 8] with cruise [2, 2]: row-wise parsing drops the
    BAD row, so idle median is 10. Column-wise skipping pairs idle RPM
    with a cruise trim and reports 8.
    """
    data = (
        b"time,rpm,stft bank 1(%),ltft bank 1(%)\n"
        b"0,750,12.0,0.0\n"
        b"1,750,BAD,0.0\n"
        b"2,750,8.0,0.0\n"
        b"3,2500,2.0,0.0\n"
        b"4,2500,2.0,0.0\n"
    )
    parsed = csv_logs.parse_csv(data)
    assert parsed.dropped_rows == 1
    derived, _ = csv_logs.derive_tests(parsed)
    trim = next(d for d in derived if d["test_ref"] == "trim_idle_vs_2500")
    assert trim["measured"]["idle_trim_pct"] == 10.0
    assert trim["measured"]["cruise_trim_pct"] == 2.0


def test_empty_cell_null_marks_without_dropping_row():
    data = (
        b"rpm,stft bank 1(%)\n"
        b"750,10.0\n"
        b"750,\n"
        b"750,10.0\n"
    )
    parsed = csv_logs.parse_csv(data)
    assert parsed.dropped_rows == 0
    assert parsed.series["stft_b1_pct"] == [10.0, 10.0]
    assert parsed.series["rpm"] == [750.0, 750.0, 750.0]


def test_maf_derivation_needs_vehicle_displacement():
    """Finding #4: no 2.0 L assumption — without a parseable
    vehicle.engine displacement the MAF test is skipped with a reason."""
    data = (
        b"rpm,maf(g/s)\n"
        b"750,3.5\n"
        b"750,3.6\n"
        b"750,3.4\n"
    )
    parsed = csv_logs.parse_csv(data)
    derived, skipped = csv_logs.derive_tests(parsed)
    assert all(d["test_ref"] != "maf_pid_plausibility" for d in derived)
    reason = next(s["reason"] for s in skipped if s["test_ref"] == "maf_pid_plausibility")
    assert "displacement" in reason

    vehicle = {"make": "Ford", "model": "Focus", "year": 2012, "engine": "2.0L I4"}
    derived, skipped = csv_logs.derive_tests(parsed, vehicle)
    maf = next(d for d in derived if d["test_ref"] == "maf_pid_plausibility")
    assert maf["measured"]["disp_l"] == 2.0
    assert "maf_pid_plausibility" not in {s["test_ref"] for s in skipped}

    # A bigger engine judges the same MAF differently (no silent 2.0 L).
    big = dict(vehicle, engine="5.0L V8")
    derived_big, _ = csv_logs.derive_tests(parsed, big)
    maf_big = next(d for d in derived_big if d["test_ref"] == "maf_pid_plausibility")
    assert maf_big["measured"]["disp_l"] == 5.0
    outcome_small, _ = evaluator.evaluate(PACK.tests["maf_pid_plausibility"], maf["measured"])
    outcome_big, _ = evaluator.evaluate(PACK.tests["maf_pid_plausibility"], maf_big["measured"])
    assert outcome_small == "plausible"
    assert outcome_big == "under_reports"


def test_no_llm_client_constructed():
    import mmc.upload.csv_logs as mod

    assert not any(name in dir(mod) for name in ("MetaClient", "client"))

    class ExplodingClient:
        def __init__(self, *a, **k):
            raise AssertionError("CSV path must never construct an LLM client")

    parsed = csv_logs.parse_csv((FIXTURES / "car_scanner_healthy.csv").read_bytes())
    csv_logs.derive_tests(parsed)
    csv_logs.to_observations(parsed, "ev1")
    csv_logs.build_evidence("blobs/x", "x", parsed)
