"""Report evidence gate (M4 fixes): a report built before the engine stop
rule fires must say there is not enough evidence yet and list current
suspects — never present a "Most likely" conclusion."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app

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


def _create_session(client: TestClient) -> str:
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_report_with_zero_tests_says_not_enough_evidence(client):
    session_id = _create_session(client)
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["tests_log"] == []
    assert not sess.should_stop_from_state(state)

    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    report = client.get(f"/reports/{r.json()['report_id']}.json").json()
    _validate("report.json", report)

    assert "Not enough evidence yet" in report["conclusion"]
    assert "Most likely" not in report["conclusion"]
    assert "Current suspects" in report["conclusion"]
    for level in ("L1", "L2", "L3"):
        assert "Not enough evidence yet" in report["summary_by_level"][level]


def test_report_after_stop_rule_keeps_conclusion(client):
    session_id = _create_session(client)
    session = sess.get_registry().get(session_id)
    assert session is not None
    state = sess.compute_state(session)
    # Force a concluded state: runaway leader, no informative tests left.
    state = dict(state)
    state["hypotheses"] = [
        dict(h, current=0.96 if i == 0 else h["current"] * 0.1)
        for i, h in enumerate(state["hypotheses"])
    ]
    state["next_tests"] = []

    report = sess.build_report(session, state)
    _validate("report.json", report)
    assert report["conclusion"].startswith("Most likely:")
    assert "Not enough evidence" not in report["conclusion"]
