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
