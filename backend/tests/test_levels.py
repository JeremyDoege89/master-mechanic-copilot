"""M6 §1: L1/L2/L3 text styles from one source of truth (levels.py).

Same session must render different instructions for L1 vs L3; blocked
tests are shown with reasons at every level; the planner still filters on
the tools questionnaire.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.kp.loader import PACKS_DIR, load_pack
from mmc.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient, level: str, tools: list) -> str:
    r = client.post(
        "/sessions",
        json={"user_level": level, "tools": tools, "mode": "sim"},
    )
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_pack_labels_have_three_distinct_levels():
    from mmc import levels

    for pack_name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / pack_name)
        assert pack.hypotheses, pack_name
        for hyp, spec in pack.hypotheses.items():
            label = spec["label"]
            assert set(("L1", "L2", "L3")) <= set(label), (pack_name, hyp)
            assert len({label["L1"], label["L2"], label["L3"]}) == 3, (pack_name, hyp)
            assert levels.check_l1(label["L1"]) == [], (pack_name, hyp)


def test_pack_test_instructions_three_levels_and_l1_jargon_free():
    from mmc import levels

    for pack_name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / pack_name)
        assert pack.tests, pack_name
        for ref, test in pack.tests.items():
            instr = test.instructions_by_level
            assert set(("L1", "L2", "L3")) <= set(instr), (pack_name, ref)
            assert len({instr["L1"], instr["L2"], instr["L3"]}) == 3, (pack_name, ref)
            assert levels.check_l1(instr["L1"]) == [], (pack_name, ref)


def test_loader_rejects_l1_jargon(tmp_path):
    from mmc.kp import loader as loader_mod

    bad = (
        "version: bad-v1\n"
        "hypotheses:\n"
        "  h1: {prior: 0.5, label: {L1: plain one, L2: middle, L3: tech}}\n"
        "  h2: {prior: 0.5, label: {L1: plain two, L2: middle, L3: tech}}\n"
        "tests:\n"
        "  t1:\n"
        "    kind: visual\n"
        "    min_level: L1\n"
        "    tools: []\n"
        "    minutes: 1\n"
        "    risk: 0\n"
        "    safety_gate: none\n"
        "    outcomes: [yes, no]\n"
        "    evaluator: visual.direct\n"
        "    instructions_by_level: {L1: 'check the MAF reading', L2: middle, L3: tech}\n"
        "    likelihoods:\n"
        "      h1: {yes: 0.8, no: 0.2}\n"
        "      h2: {yes: 0.2, no: 0.8}\n"
    )
    path = tmp_path / "bad.yaml"
    path.write_text(bad)
    with pytest.raises(ValueError):
        loader_mod.load_pack(path)


def test_state_surfaces_differ_pairwise_across_levels(client):
    session_id = _create(client, "L1", [])
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/sessions/{session_id}/state").json()

    assert state["next_tests"], "expected runnable tests after dtc_read"
    first = state["next_tests"][0]
    instr = first["instructions_by_level"]
    assert len({instr["L1"], instr["L2"], instr["L3"]}) == 3

    top = state["hypotheses"][0]
    assert len({top["label"]["L1"], top["label"]["L2"], top["label"]["L3"]}) == 3
    assert top["trace"], "dtc_read must leave a trace entry"
    entry = top["trace"][0]
    assert len({entry["text_L1"], entry["text_L2"], entry["text_L3"]}) == 3


def test_blocked_tests_carry_reasons_and_metadata_at_every_level(client):
    from mmc import levels

    for level in ("L1", "L2", "L3"):
        session_id = _create(client, level, [])
        state = client.get(f"/sessions/{session_id}/state").json()
        assert state["blocked_tests"], level
        for blocked in state["blocked_tests"]:
            assert blocked["blocked_reasons"], (level, blocked["kp_test_ref"])
            for key in ("min_level", "required_tools", "safety_gate"):
                assert key in blocked, (level, blocked["kp_test_ref"], key)
        for rec in state["next_tests"]:
            assert rec["blocked_reasons"] == []
            assert set(("L1", "L2", "L3")) <= set(rec["instructions_by_level"])
    assert len(levels.TOOL_OPTIONS) == 8


def test_tools_questionnaire_filters_battery_test(client):
    bare = _create(client, "L2", [])
    state = client.get(f"/sessions/{bare}/state").json()
    ranked = {t["kp_test_ref"] for t in state["next_tests"]}
    assert "battery_voltage_rest_running" not in ranked
    blocked = {b["kp_test_ref"]: b for b in state["blocked_tests"]}
    assert "battery_voltage_rest_running" in blocked
    assert any("multimeter" in reason for reason in blocked["battery_voltage_rest_running"]["blocked_reasons"])

    equipped = _create(client, "L2", ["multimeter"])
    state2 = client.get(f"/sessions/{equipped}/state").json()
    ranked2 = {t["kp_test_ref"] for t in state2["next_tests"]}
    assert "battery_voltage_rest_running" in ranked2


def test_meta_levels_endpoint_lists_levels_and_tools(client):
    r = client.get("/meta/levels")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(("L1", "L2", "L3")) <= set(body["levels"])
    assert body["levels"]["L1"] != body["levels"]["L3"]
    assert "multimeter" in body["tools"]
    assert len(body["tools"]) == 8


def test_trace_l1_reads_direction_then_new_band():
    """Judge fix M7 §2.3b: direction of the update, then the new band."""
    from mmc import levels

    label = {"L1": "Air leak", "L2": "Intake leak", "L3": "Leak"}
    texts = levels.trace_text(label, "none", 0.17, 0.24, 1.5)
    assert texts["L1"] == "Air leak: no fault showing up makes this more likely; now unlikely (about 2 in 10)."


def test_verbal_band_uses_natural_frequencies():
    from mmc import levels

    assert "6 in 10" in levels.verbal_band(0.62)
    assert "9 in 10" in levels.verbal_band(0.9)
    assert levels.verbal_band(0.9) != levels.verbal_band(0.1)
