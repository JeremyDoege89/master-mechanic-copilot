"""Sim parity: sim_scenarios.py reproduces the Kotlin snapshots within 1e-6."""

import json
from pathlib import Path

import pytest

from sim_scenarios import SCENARIOS, snapshot

SNAP_DIR = Path(__file__).resolve().parent / "fixtures" / "sim_snapshots"

FLOAT_KEYS = ("rpm", "ect_c", "maf_gs", "trim_total_pct", "iat_c")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_snapshot_matches_kotlin_sim(scenario):
    path = SNAP_DIR / f"{scenario}.json"
    assert path.exists(), f"missing {path}; run SimSnapshotTest first"
    saved = json.loads(path.read_text())
    expected = snapshot(scenario)
    assert saved["scenario"] == scenario
    assert saved["displacement_l"] == pytest.approx(expected["displacement_l"])
    for point in ("idle", "cruise2500"):
        for key in FLOAT_KEYS:
            assert saved["points"][point][key] == pytest.approx(
                expected["points"][point][key], abs=1e-6
            ), (scenario, point, key)
        assert saved["points"][point]["dtcs"] == expected["points"][point]["dtcs"]
        assert (
            saved["points"][point]["misfire_counts"]
            == expected["points"][point]["misfire_counts"]
        )
    assert len(saved["ect_series"]) == len(expected["ect_series"])
    for got, want in zip(saved["ect_series"], expected["ect_series"]):
        assert got["t_s"] == pytest.approx(want["t_s"])
        assert got["ect_c"] == pytest.approx(want["ect_c"], abs=1e-6)
