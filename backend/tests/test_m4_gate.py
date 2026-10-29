"""M4 gate end-to-end (planner-driven, no phone).

For every sim scenario: create a session (L1, no tools), stream the
scenario's OBD observations as events, then repeatedly take the top runnable
test from GET /state and answer it from the scenario fixture via the result
route — until the engine stop rule fires or 4 tests. The final report must
name the true hypothesis #1 with p >= 0.8 and validate against
packages/schemas/report.json.

Also covers: WS pushes equal GET state after every event, WS reconnect gets
current state, and the .mmrec blob upload route.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc.api import routes as api_routes
from mmc.engine import stoprule
from mmc.main import app
from mmc.replay import mmrec
from mmc.store import blobs as blob_store

from sim_scenarios import (
    ALL_GATES,
    SCENARIOS,
    TRUE_HYPOTHESIS,
    ect_c,
    iat_c,
    maf_gs,
    measured_for_test,
    snapshot,
    total_trim_pct,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages" / "schemas"


def _registry() -> Registry:
    pairs = []
    for schema_path in sorted(SCHEMAS.glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


REGISTRY = _registry()


def _validate(name: str, payload: dict) -> None:
    schema = json.loads((SCHEMAS / name).read_text())
    jsonschema.Draft202012Validator(schema, registry=REGISTRY).validate(payload)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create_session(client: TestClient, **kwargs) -> str:
    body = {
        "user_level": "L1",
        "tools": [],
        "mode": "sim",
        "vehicle": {"make": "Ford", "model": "Focus", "year": 2012, "engine": "2.0L I4"},
    }
    body.update(kwargs)
    r = client.post("/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _state(client: TestClient, session_id: str) -> dict:
    r = client.get(f"/sessions/{session_id}/state")
    assert r.status_code == 200, r.text
    return r.json()


def _observations(scenario: str) -> list[dict]:
    """OBD observation payloads streamed live from the scenario snapshot."""
    snap = snapshot(scenario)
    out: list[dict] = []
    n = 0
    for point_name in ("idle", "cruise2500"):
        point = snap["points"][point_name]
        cond = {"rpm": point["rpm"], "ect_c": point["ect_c"]}
        for feature, value, units in (
            ("maf_gs", point["maf_gs"], "g/s"),
            ("iat_c", point["iat_c"], "degC"),
            ("trim_total_pct", point["trim_total_pct"], "pct"),
        ):
            n += 1
            out.append(
                {
                    "observation_id": f"obs_{point_name}_{feature}",
                    "feature": feature,
                    "value": value,
                    "units": units,
                    "conditions": cond,
                    "confidence": 0.9,
                    "extractor": "rule",
                    "evidence_ids": [],
                }
            )
    assert n == 6
    assert iat_c(scenario) is not None and maf_gs(scenario, 750.0) is not None
    assert ect_c(scenario, 1200.0) > 0 and total_trim_pct(scenario, 750.0) >= 0
    return out


def _drain_to_state(ws, client: TestClient, session_id: str, n: int) -> dict:
    """Read exactly n WS pushes; the last must equal current GET state."""
    push = None
    for _ in range(n):
        push = ws.receive_json()
    assert push == _state(client, session_id)
    return push


def run_planner_session(client: TestClient, scenario: str) -> tuple[str, dict]:
    """Full M4 gate flow for one scenario; returns (session_id, final state)."""
    session_id = _create_session(client)
    with client.websocket_connect(f"/sessions/{session_id}/ws") as ws:
        # Reconnect-prone clients get the current state on connect.
        assert ws.receive_json() == _state(client, session_id)

        # 1. Stream the scenario's OBD observations; every push == GET state.
        for obs in _observations(scenario):
            r = client.post(
                f"/sessions/{session_id}/events",
                json={"type": "observation_added", "payload": obs},
            )
            assert r.status_code == 200, r.text
            _drain_to_state(ws, client, session_id, 1)

        # 2. Acknowledge every safety gate (mirrors the M3 harness profile).
        for gate in sorted(ALL_GATES):
            r = client.post(
                f"/sessions/{session_id}/events",
                json={"type": "safety_ack", "payload": {"gate": gate}},
            )
            assert r.status_code == 200, r.text
            _drain_to_state(ws, client, session_id, 1)

        # 3. Planner-driven loop: top runnable test, answered from the fixture.
        for _ in range(4):
            state = _state(client, session_id)
            top_p = state["hypotheses"][0]["current"]
            best_eig = state["next_tests"][0]["eig_bits"] if state["next_tests"] else None
            if stoprule.should_stop(top_p, best_eig):
                break
            assert state["next_tests"], f"{scenario}: planner has no runnable test"
            ref = state["next_tests"][0]["kp_test_ref"]
            measured = measured_for_test(scenario, ref)
            r = client.post(
                f"/sessions/{session_id}/tests/{ref}/result",
                json={"measured": measured, "conditions": {}, "evidence_ids": []},
            )
            assert r.status_code == 200, (scenario, ref, r.text)
            # One push per appended event (performed/result/posterior).
            _drain_to_state(ws, client, session_id, 3)

    return session_id, _state(client, session_id)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_gate_converges_and_report_names_true_hypothesis(client, scenario):
    session_id, state = run_planner_session(client, scenario)
    assert len(state["tests_log"]) <= 4, (scenario, state["tests_log"])

    top = state["hypotheses"][0]
    assert top["hypothesis_id"] == TRUE_HYPOTHESIS[scenario], (scenario, top)
    assert top["current"] >= 0.8, (scenario, top["current"])
    for hyp in state["hypotheses"]:
        _validate("hypothesis.json", hyp)
    if state["next_tests"]:
        _validate("next_test.json", state["next_tests"][0])

    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    report_id = r.json()["report_id"]
    r = client.get(f"/reports/{report_id}.json")
    assert r.status_code == 200, r.text
    report = r.json()
    _validate("report.json", report)
    assert report["hypotheses_final"][0]["hypothesis_id"] == TRUE_HYPOTHESIS[scenario]
    assert report["hypotheses_final"][0]["current"] >= 0.8


def test_ws_reconnect_gets_current_state(client):
    session_id = _create_session(client)
    with client.websocket_connect(f"/sessions/{session_id}/ws") as ws:
        ws.receive_json()
    # Events appended while disconnected...
    r = client.post(
        f"/sessions/{session_id}/events",
        json={
            "type": "observation_added",
            "payload": {
                "observation_id": "obs_re",
                "feature": "maf_gs",
                "value": 3.0,
                "confidence": 0.9,
                "extractor": "rule",
            },
        },
    )
    assert r.status_code == 200
    # ...are reflected in the first push after reconnect.
    with client.websocket_connect(f"/sessions/{session_id}/ws") as ws2:
        assert ws2.receive_json() == _state(client, session_id)


def test_gated_result_rejected_until_ack(client):
    session_id = _create_session(client)
    r = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result",
        json={"measured": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}},
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}},
    )
    assert r.status_code == 200
    r = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result",
        json={"measured": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["test_result"]["outcome"] == "lean_idle_normal_cruise"


def test_blob_upload_roundtrip(client):
    session_id = _create_session(client)
    bundle = mmrec.create({"transcript.jsonl": b'{"t":0,"dir":"tx","data":"0100"}\n'}, {"vin": "x"})
    r = client.post(
        f"/sessions/{session_id}/blobs",
        files={"file": ("recording.mmrec", bundle, "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sha256"] == hashlib.sha256(bundle).hexdigest()
    assert body["blob_ref"] == f"blobs/{body['sha256']}"
    assert blob_store.get(body["blob_ref"]) == bundle
