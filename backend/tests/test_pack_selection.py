"""Pack selection per session vehicle (Mazda 3 owner vehicle).

Ford make -> ford-gen-v1; everything else and unknown make -> generic-gas-v1.
The pack id is recorded in session_started (kp_version) and drives the fold.
"""

from __future__ import annotations

from pathlib import Path

import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

import sim_scenarios as sim_mod
from mmc.engine import bayes
from mmc.kp.loader import PACKS_DIR, load_pack


def _make_registry(tmp_path, monkeypatch):
    from mmc import sessions as sess_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sess_mod.get_pack_by_version.cache_clear()  # type: ignore[attr-defined]
    if hasattr(sess_mod.get_pack, "cache_clear"):
        sess_mod.get_pack.cache_clear()  # type: ignore[attr-defined]
    return sess_mod


def test_ford_still_loads_ford_pack(tmp_path, monkeypatch):
    sess_mod = _make_registry(tmp_path, monkeypatch)
    registry = sess_mod.get_registry()
    session = registry.create(
        user_level="L1",
        tools=[],
        mode="live",
        vehicle={"make": "Ford", "model": "Focus", "year": 2012, "engine": "2.0L I4"},
    )
    assert session.meta["kp_version"] == "ford-gen-v1"
    assert sess_mod.pack_version_for_vehicle({"make": "Ford"}) == "ford-gen-v1"
    assert sess_mod.pack_version_for_vehicle({"make": "  FORD  "}) == "ford-gen-v1"
    pack = sess_mod.get_pack_for_session(session)
    assert pack.version == "ford-gen-v1"
    started = [e for e in session.store.iter_replay() if e["type"] == "session_started"][0]
    assert started["payload"]["kp_version"] == "ford-gen-v1"


def test_unknown_make_falls_back_to_generic(tmp_path, monkeypatch):
    sess_mod = _make_registry(tmp_path, monkeypatch)
    registry = sess_mod.get_registry()
    for vehicle in (None, {}, {"make": "", "model": "3", "year": 2018}, {"model": "3", "year": 2018}):
        assert sess_mod.pack_version_for_vehicle(vehicle) == "generic-gas-v1"
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    assert session.meta["kp_version"] == "generic-gas-v1"
    assert sess_mod.get_pack_for_session(session).version == "generic-gas-v1"


def test_mazda_vin_decoded_session_loads_generic_and_converges(tmp_path, monkeypatch):
    """Mazda 3 (as VIN-decoded) runs the generic pack and catches a vacuum leak."""
    sess_mod = _make_registry(tmp_path, monkeypatch)
    registry = sess_mod.get_registry()
    # As populated from a VIN decode: make/model/year plus vPIC DisplacementL.
    vehicle = {
        "make": "Mazda",
        "model": "3",
        "year": 2018,
        "engine": "2.0L I4",
        "DisplacementL": 2.0,
    }
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=vehicle)
    assert session.meta["kp_version"] == "generic-gas-v1"
    pack = sess_mod.get_pack_for_session(session)
    assert pack.version == "generic-gas-v1"
    # The generic pack carries the 9 planner tests with L1/L2/L3 text.
    for ref in (
        "trim_idle_vs_2500",
        "maf_pid_plausibility",
        "mode06_misfire_counts",
        "ect_warmup_curve",
        "battery_voltage_rest_running",
        "engine_sound_screen",
        "dtc_read",
        "listen_fuel_pump_prime",
        "visual_intake_boot",
    ):
        assert ref in pack.tests, ref
        instr = pack.tests[ref].instructions_by_level
        assert instr["L1"] and instr["L2"] and instr["L3"], ref

    # Planner-driven vacuum-leak loop on the generic pack converges <= 4.
    level, tools = sim_mod.PROFILES["L1"]
    posteriors, sequence = sim_mod.run_planner_loop(
        pack, "vacuum_leak", level, tools, vehicle=dict(vehicle)
    )
    priors = {h: spec["prior"] for h, spec in pack.hypotheses.items()}
    running = dict(priors)
    converged_at = None
    for i, (ref, outcome) in enumerate(sequence, start=1):
        test = pack.tests[ref]
        running = bayes.update(running, outcome, test.likelihoods, len(test.outcomes))
        top = max(running, key=running.get)
        if top == "vacuum_leak_post_maf" and running[top] >= 0.8 and converged_at is None:
            converged_at = i
    assert converged_at is not None, f"never converged: {sequence} {running}"
    assert converged_at <= 4, f"converged late in {converged_at}: {sequence}"

    # Same fold through the session state: submit the converging prefix
    # through the registry and check the top hypothesis.
    from mmc.engine import evaluator as evaluator_mod

    for ref, _outcome in sequence[:converged_at]:
        measured = sim_mod.measured_for_test("vacuum_leak", ref)
        test = pack.tests[ref]
        outcome, expected = evaluator_mod.evaluate(test, dict(measured))
        result_id = f"res-{ref}"
        registry.append(
            session,
            "test_performed",
            {
                "performed_ts": sess_mod._utc_now(),
                "test_id": result_id,
                "test_ref": ref,
                "by_level": "L1",
                "conditions": {},
                "safety_ack": test.safety_gate if test.safety_gate != "none" else None,
            },
        )
        registry.append(
            session,
            "test_result",
            {
                "result_id": result_id,
                "test_id": ref,
                "expected": expected,
                "measured": {"value": dict(measured)},
                "outcome": outcome,
                "evaluator": test.evaluator,
                "evidence_ids": [],
            },
        )
    state = sess_mod.compute_state(session)
    assert state["hypotheses"][0]["hypothesis_id"] == "vacuum_leak_post_maf"
    assert state["hypotheses"][0]["current"] >= 0.8


def test_displacement_prefers_vpic_field_and_skips_without_it():
    from mmc.upload import csv_logs

    assert csv_logs.displacement_l({"engine": "2.0L I4"}) == 2.0
    assert csv_logs.displacement_l({"DisplacementL": 2.5, "engine": "2.0L I4"}) == 2.5
    assert csv_logs.displacement_l({"DisplacementL": "2.5", "engine": ""}) == 2.5
    assert csv_logs.displacement_l({}) is None
    assert csv_logs.displacement_l(None) is None
    parsed = type("P", (), {"series": {"maf_gs": [3.5], "rpm": [750.0]}})()
    derived, skipped = csv_logs.derive_tests(parsed, None)
    assert any(s["test_ref"] == "maf_pid_plausibility" for s in skipped)
