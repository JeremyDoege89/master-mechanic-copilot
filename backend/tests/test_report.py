"""M6 §3: report JSON/HTML/PDF + Wonder review (extended audit input).

Engine-sound evidence lands in expected-vs-measured with its DSP numbers,
labelled signal processing, not AI. Wonder gets the report plus the audit
bundle (traces, missing evidence, blocked tests, disagreements, fallback
use) and never moves a posterior.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app
from mmc.meta.budget import BudgetTracker

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages" / "schemas"

VIN = "1FAFP40411F123456"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient) -> str:
    r = client.post(
        "/sessions",
        json={
            "user_level": "L1",
            "tools": [],
            "mode": "sim",
            "vehicle": {"make": "Ford", "model": "Focus", "year": 2012, "vin": VIN},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _ack(client: TestClient, session_id: str, gate: str = "engine_running_outdoors"):
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "safety_ack", "payload": {"gate": gate}},
    )
    assert r.status_code == 200, r.text


def _run_two_tests(client: TestClient, session_id: str):
    _ack(client, session_id)
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/sessions/{session_id}/tests/engine_sound_screen/result",
        json={
            "measured": {
                "outcome": "vacuum_hiss",
                "band_ratio": 0.62,
                "flatness": 0.71,
                "f_fire_hz": 40.0,
            }
        },
    )
    assert r.status_code == 200, r.text


def test_report_json_validates_and_has_expected_vs_measured(client):
    import jsonschema

    session_id = _create(client)
    _run_two_tests(client, session_id)
    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    report = client.get(f"/reports/{r.json()['report_id']}.json").json()

    schema = json.loads((SCHEMAS / "report.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(report)

    rows = {row["test_ref"]: row for row in report["expected_vs_measured"]}
    assert set(("dtc_read", "engine_sound_screen")) <= set(rows)
    trim_row = rows["dtc_read"]
    assert trim_row["outcome"] == "none"
    assert trim_row["expected"]["source"]["kind"] in ("kp", "physics", "manual_page", "mode06_limit")
    assert trim_row["measured"]["value"] == {"dtcs": []}

    sound_row = rows["engine_sound_screen"]
    assert sound_row["outcome"] == "vacuum_hiss"
    assert sound_row["measured"]["value"]["band_ratio"] == 0.62
    assert sound_row["measured"]["value"]["flatness"] == 0.71
    assert sound_row["origin"] == "signal_processing"

    assert report["reproducibility"]["prompts_sha256"] != (
        "m4-no-llm-prompts".encode().hex()
    )
    assert "spark_open" in report["reproducibility"]["model_pins"]
    assert report["vin_last6"] == VIN[-6:]
    dump = json.dumps(report)
    assert VIN not in dump
    assert VIN[:-6] not in dump

    summaries = report["summary_by_level"]
    assert len({summaries["L1"], summaries["L2"], summaries["L3"]}) == 3


def test_report_html_escapes_and_labels(client):
    session_id = _create(client)
    _ack(client, session_id)
    evil = {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0, "note": "<script>alert(1)</script>"}
    r = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result", json={"measured": evil}
    )
    assert r.status_code == 200, r.text
    report_id = client.post(f"/sessions/{session_id}/report").json()["report_id"]
    html = client.get(f"/reports/{report_id}.html")
    assert html.status_code == 200, html.text
    text = html.text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "physics.trim_rpm_split" in text
    assert "AI review" in text


def test_report_pdf_starts_pdf_and_embeds_conclusion(client):
    session_id = _create(client)
    _run_two_tests(client, session_id)
    report_id = client.post(f"/sessions/{session_id}/report").json()["report_id"]
    pdf = client.get(f"/reports/{report_id}.pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    report = client.get(f"/reports/{report_id}.json").json()
    first_line = report["conclusion"].split(".")[0]
    assert first_line.encode("latin-1", "ignore")[:20] in pdf.content


def _wonder_transport(calls: dict, review: dict):
    def transport(model, kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            output_text=json.dumps(review),
            usage={"input_tokens": 5000, "output_tokens": 400},
        )

    return transport


def _stub_client(tmp_path, transport, **kw):
    from mmc.meta.client import MetaClient

    kw.setdefault("budget", BudgetTracker(hard_stop_usd=120.0))
    return MetaClient(
        api_key="test-key",
        cache_dir=tmp_path / "llm_cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
        **kw,
    )


def _review(narrative="Looks thin on fuel delivery."):
    return {
        "gaps": [
            {
                "issue": "fuel pressure never checked",
                "test_ref": "fuel_pressure_test",
                "rationale": "would separate fuel_delivery_low",
            }
        ],
        "narrative": narrative,
        "agrees_with_top": True,
    }


def test_wonder_one_call_inert_and_cached(client, tmp_path):
    from mmc.meta import wonder as wonder_mod

    session_id = _create(client)
    _run_two_tests(client, session_id)
    session = sess.get_registry().get(session_id)
    assert session is not None
    before = [h["current"] for h in sess.compute_state(session)["hypotheses"]]

    calls: dict = {"n": 0}
    cache_dir = tmp_path / "llm_cache"
    client1 = _stub_client(tmp_path, _wonder_transport(calls, _review()))
    client1.cache_dir = cache_dir
    report = sess.build_report(session, sess.compute_state(session))
    audit = wonder_mod.build_audit_bundle(report, sess.compute_state(session))
    assert audit["missing_evidence"]
    assert audit["blocked_tests"]
    report = wonder_mod.attach_review(report, audit, client1)
    assert calls["n"] == 1
    assert report["wonder_review"]["narrative"].startswith("Looks thin")
    assert report["wonder_review"]["gaps"][0]["test_ref"] == "fuel_pressure_test"

    after = [h["current"] for h in sess.compute_state(session)["hypotheses"]]
    assert after == before
    assert [h["current"] for h in report["hypotheses_final"]] == before

    client2 = _stub_client(tmp_path, _wonder_transport(calls, _review("other")))
    client2.cache_dir = cache_dir
    report2 = wonder_mod.attach_review(report, audit, client2)
    assert calls["n"] == 1, "rebuild must reuse the stored review"
    assert report2["wonder_review"]["narrative"].startswith("Looks thin")

    # Replay mode serves the llm_call disk-cache entry at $0: a fresh
    # report with a cache-only client gets the review with no transport.
    from mmc.meta.client import MetaClient

    replay_client = MetaClient(
        api_key="test-key",
        cache_dir=cache_dir,
        replay_cached=True,
        transport=_wonder_transport(calls, _review("other")),
        sleep=lambda s: None,
        budget=BudgetTracker(hard_stop_usd=120.0),
    )
    fresh = sess.build_report(session, sess.compute_state(session))
    fresh = wonder_mod.attach_review(fresh, audit, replay_client)
    assert calls["n"] == 1, "replay must be a disk-cache hit"
    assert fresh["wonder_review"]["narrative"].startswith("Looks thin")


def test_wonder_over_budget_ships_null_with_unavailable_line(client, tmp_path):
    from mmc.meta import wonder as wonder_mod

    session_id = _create(client)
    _run_two_tests(client, session_id)
    session = sess.get_registry().get(session_id)
    assert session is not None
    calls: dict = {"n": 0}
    poor = _stub_client(
        tmp_path,
        _wonder_transport(calls, _review()),
        budget=BudgetTracker(hard_stop_usd=0.0),
    )
    report = sess.build_report(session, sess.compute_state(session))
    audit = wonder_mod.build_audit_bundle(report, sess.compute_state(session))
    report = wonder_mod.attach_review(report, audit, poor)
    assert calls["n"] == 0
    assert report["wonder_review"] is None
    assert "unavailable" in (report["wonder_unavailable"] or "").lower()

    report_id = client.post(f"/sessions/{session_id}/report").json()["report_id"]
    html = client.get(f"/reports/{report_id}.html").text
    assert "AI review unavailable" in html


def test_wonder_rejects_probability_keys(tmp_path):
    from mmc.meta import wonder as wonder_mod

    calls: dict = {"n": 0}
    bad = _review()
    bad["posterior"] = 0.9
    client = _stub_client(tmp_path, _wonder_transport(calls, bad))
    with pytest.raises(ValueError, match="[Pp]robability"):
        wonder_mod.request_review({"report": {}, "audit": "x"}, client)


def test_report_build_holds_session_lock_across_wonder_call(
    client, tmp_path, monkeypatch
):
    """M7 review #6: concurrent builds share one Wonder call.

    POST /report must hold the per-session lock across check -> Wonder
    call -> append, so a second simultaneous build waits and then reuses
    the first build's stored review instead of paying for its own call.
    The transport observes the lock state directly: unlocked means the
    critical section is unguarded and double spend is possible.
    """
    import asyncio

    from mmc.meta.client import MetaClient as RealMetaClient

    session_id = _create(client)
    _run_two_tests(client, session_id)

    class TrackingLock:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self.held_during_call: list = []

        async def __aenter__(self):
            await self._lock.acquire()
            return self

        async def __aexit__(self, *exc):
            self._lock.release()
            return False

    lock = TrackingLock()
    monkeypatch.setattr(api_routes, "session_lock", lambda sid: lock)

    def transport(model, kwargs):
        lock.held_during_call.append(lock._lock.locked())
        return SimpleNamespace(
            output_text=json.dumps(_review()),
            usage={"input_tokens": 5000, "output_tokens": 400},
        )

    def factory(**kw):
        return RealMetaClient(
            api_key="test-key",
            cache_dir=tmp_path / "llm_cache",
            replay_cached=False,
            transport=transport,
            sleep=lambda s: None,
            budget=BudgetTracker(hard_stop_usd=120.0),
            on_call=kw.get("on_call"),
        )

    monkeypatch.setattr("mmc.meta.client.MetaClient", factory)
    r = client.post(f"/sessions/{session_id}/report", json={})
    assert r.status_code == 200, r.text
    assert lock.held_during_call == [True]


def test_stale_recorded_review_not_served_after_new_events(client):
    """M7 review #7: a recorded review dies with the head it reviewed.

    A demo-bundle recorded review serves keyless while current, but once
    a newer diagnostic event (here a safety_ack) lands, the next build
    must ship wonder_review null — never the senior review of a state
    that no longer exists.
    """
    session_id = _create(client)
    _run_two_tests(client, session_id)
    session = sess.get_registry().get(session_id)
    assert session is not None
    recorded = _review("Recorded senior review.")
    sess.get_registry().append(
        session,
        "report_built",
        {
            "report_id": "r-recorded",
            "log_head_hash": "head-1",
            "wonder_review": recorded,
            "wonder_unavailable": None,
        },
    )
    first = client.post(f"/sessions/{session_id}/report", json={})
    assert first.status_code == 200, first.text
    served = client.get(first.json()["url"]).json()
    assert served["wonder_review"] == recorded

    _ack(client, session_id, gate="engine_off")
    second = client.post(f"/sessions/{session_id}/report", json={})
    assert second.status_code == 200, second.text
    rebuilt = client.get(second.json()["url"]).json()
    assert rebuilt["wonder_review"] is None
    assert rebuilt["wonder_review"] != recorded


def test_pdf_writer_is_stdlib_only():
    src = (ROOT / "backend" / "mmc" / "report" / "pdf.py").read_text()
    for banned in ("jinja2", "reportlab", "fpdf", "weasyprint", "numpy"):
        assert banned not in src
    import mmc.report.pdf as pdf_mod

    assert not hasattr(pdf_mod, "jinja2")
