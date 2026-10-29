"""M6 review findings #1-#12: regression tests (one per finding).

Each test reproduces the review's probe scenario and pins the fixed
behaviour. Findings #4 and #8 are Android-side (ClearTokenTest.kt,
SafetyGates ack-path test); everything else is covered here.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient, level: str = "L1", tools: list | None = None) -> str:
    r = client.post(
        "/sessions", json={"user_level": level, "tools": tools or [], "mode": "sim"}
    )
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _priors(client: TestClient, session_id: str) -> dict[str, float]:
    state = client.get(f"/sessions/{session_id}/state").json()
    return {h["hypothesis_id"]: h["current"] for h in state["hypotheses"]}


# -- finding #1: generic event endpoint must reject engine/privileged types --

FORGED_TEST_RESULT = {
    "result_id": "forged1",
    "test_id": "trim_idle_vs_2500",
    "expected": {"source": {"kind": "kp"}},
    "measured": {"value": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}},
    "outcome": "lean_idle_normal_cruise",
    "evaluator": "physics.trim_rpm_split",
    "evidence_ids": [],
}


def test_f1_events_route_rejects_forged_test_result(client):
    """Probe: POST test_result with no ack/evaluator must be 403, priors kept."""
    session_id = _create(client)
    before = _priors(client, session_id)
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "test_result", "payload": dict(FORGED_TEST_RESULT)},
    )
    assert r.status_code == 403, r.text
    assert _priors(client, session_id) == before
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["tests_log"] == []


def test_f1_events_route_rejects_privileged_types(client):
    session_id = _create(client)
    before = _priors(client, session_id)
    forged = {
        "test_performed": {"test_id": "x", "test_ref": "dtc_read"},
        "posterior_updated": {"result_id": "x", "test_id": "dtc_read", "updates": []},
        "hypothesis_proposed": {"hypothesis_id": "evil"},
        "llm_call": {"kind": "llm_call"},
        "sam_call": {"kind": "sam_call"},
        "codes_clear_requested": {"token_sha256": "fake"},
        "codes_cleared": {"token_sha256": "fake"},
        "report_built": {"report_id": "fake"},
        "session_started": {"session_id": session_id},
    }
    for type, payload in forged.items():
        r = client.post(
            f"/sessions/{session_id}/events", json={"type": type, "payload": payload}
        )
        assert r.status_code == 403, (type, r.text)
    assert _priors(client, session_id) == before


def test_f1_events_route_still_accepts_client_types(client):
    session_id = _create(client)
    ok = [
        ("safety_ack", {"gate": "engine_running_outdoors"}),
        (
            "observation_added",
            {
                "observation_id": "obs1",
                "feature": "rpm",
                "value": 750.0,
                "confidence": 0.9,
                "extractor": "rule",
            },
        ),
        ("asr_segment", {"text": "log idle trim"}),
        ("test_requested", {"test_ref": "dtc_read"}),
    ]
    for type, payload in ok:
        r = client.post(
            f"/sessions/{session_id}/events", json={"type": type, "payload": payload}
        )
        assert r.status_code == 200, (type, r.text)


# -- finding #2: replay sessions are read-only on the core routes --


def _replay_session(client: TestClient) -> str:
    r = client.post("/replay", json={"fixture": "sim-healthy.mmrec"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_f2_replay_session_rejects_events_results_and_blobs(client):
    session_id = _replay_session(client)
    r = client.post(
        f"/sessions/{session_id}/events",
        json={
            "type": "observation_added",
            "payload": {
                "observation_id": "obs-evil",
                "feature": "rpm",
                "value": 1.0,
                "confidence": 0.9,
                "extractor": "rule",
            },
        },
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"/sessions/{session_id}/blobs", files={"file": ("x.jpg", b"fake-jpeg")}
    )
    assert r.status_code == 409, r.text
    session = sess.get_registry().get(session_id)
    assert session is not None
    assert session.store.verify_chain()


# -- finding #3: bad Wonder replies must not 500 the report --


def _wonder_review(**kw) -> dict:
    review = {
        "gaps": [{"issue": "fuel pressure never checked", "test_ref": "fuel_pressure_test"}],
        "narrative": "Thin on fuel delivery evidence.",
        "agrees_with_top": True,
    }
    review.update(kw)
    return review


def _wonder_client(tmp_path, transport, **kw):
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    kw.setdefault("budget", BudgetTracker(hard_stop_usd=120.0))
    return MetaClient(
        api_key="test-key",
        cache_dir=tmp_path / "wonder-cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
        **kw,
    )


def _transport_for(calls: dict, payload: dict):
    from types import SimpleNamespace

    def transport(model, kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            output_text=json.dumps(payload),
            usage={"input_tokens": 5000, "output_tokens": 400},
        )

    return transport


def test_f3_wonder_banned_key_case_insensitive(tmp_path):
    from mmc.meta import wonder as wonder_mod

    calls: dict = {"n": 0}
    bad = _wonder_review(Posterior=0.92)
    client = _wonder_client(tmp_path, _transport_for(calls, bad))
    with pytest.raises(ValueError, match="[Pp]robability"):
        wonder_mod.request_review({"report": {}, "audit": "x"}, client)


def test_f3_wonder_bad_reply_ships_null_instead_of_raising(tmp_path):
    from mmc.meta import wonder as wonder_mod

    audit = {"report": {}, "audit": "x"}
    # Banned key reply.
    calls: dict = {"n": 0}
    report = {"wonder_review": None}
    wonder_mod.attach_review(
        report, audit, _wonder_client(tmp_path, _transport_for(calls, _wonder_review(posterior=0.5)))
    )
    assert report["wonder_review"] is None
    assert "unavailable" in (report["wonder_unavailable"] or "").lower()
    # Schema-invalid reply (missing required narrative/agrees_with_top).
    report2 = {"wonder_review": None}
    wonder_mod.attach_review(
        report2, audit, _wonder_client(tmp_path, _transport_for(calls, {"gaps": []}))
    )
    assert report2["wonder_review"] is None
    assert "unavailable" in (report2["wonder_unavailable"] or "").lower()
    # Extra top-level key (additionalProperties:false).
    report3 = {"wonder_review": None}
    wonder_mod.attach_review(
        report3,
        audit,
        _wonder_client(tmp_path, _transport_for(calls, _wonder_review(extra_key=1))),
    )
    assert report3["wonder_review"] is None
    assert "unavailable" in (report3["wonder_unavailable"] or "").lower()


def test_f3_report_route_survives_wonder_value_error(client, tmp_path, monkeypatch):
    from mmc.meta import wonder as wonder_mod

    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text

    def boom(report, audit, client):
        raise ValueError("Wonder output sets probability field $.Posterior: refusing")

    monkeypatch.setattr(wonder_mod, "attach_review", boom)
    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    report = client.get(f"/reports/{r.json()['report_id']}.json").json()
    assert report["wonder_review"] is None
    assert report["wonder_unavailable"] is not None


# -- finding #5: public web bundle-upload sequence (upload-mode session) --


def test_f5_public_upload_session_bundle_flow(tmp_path, monkeypatch):
    """Drive the web uploadBundle sequence against the public backend.

    The web client creates a throwaway session, uploads the .mmrec bytes,
    then replays from the blob. In public mode that session must be
    ``mode:"upload"`` (``sim`` is rejected): create -> blobs -> replay.
    """
    from pathlib import Path

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MMC_PUBLIC_MODE", "replay")
    monkeypatch.delenv("MMC_PUBLIC_LIVE_BUDGET_USD", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    try:
        with TestClient(app) as pub:
            sim = pub.post(
                "/sessions", json={"user_level": "L1", "tools": [], "mode": "sim"}
            )
            assert sim.status_code == 400, sim.text
            r = pub.post(
                "/sessions", json={"user_level": "L1", "tools": [], "mode": "upload"}
            )
            assert r.status_code == 200, r.text
            throwaway = r.json()["session_id"]
            fixture = (
                Path(__file__).parent / "fixtures" / "sessions" / "sim-healthy.mmrec"
            ).read_bytes()
            up = pub.post(
                f"/sessions/{throwaway}/blobs",
                files={"file": ("sim-healthy.mmrec", fixture)},
            )
            assert up.status_code == 200, up.text
            rep = pub.post(
                "/replay", json={"bundle_ref": up.json()["blob_ref"]}
            )
            assert rep.status_code == 200, rep.text
            state = pub.get(f"/sessions/{rep.json()['session_id']}/state")
            assert state.status_code == 200, state.text
            assert state.json()["hypotheses"]
    finally:
        api_routes._listeners.clear()


# -- finding #6: bundle replay keeps context and verifies the chain --


def _ack(client: TestClient, session_id: str, gate: str) -> None:
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "safety_ack", "payload": {"gate": gate}},
    )
    assert r.status_code == 200, r.text


def test_f6_tampered_bundle_chain_rejected(client, tmp_path):
    """A bundle with a valid manifest but a broken inner hash chain is 400."""
    from pathlib import Path

    from mmc.replay import mmrec

    committed = (
        Path(__file__).parent / "fixtures" / "sessions" / "sim-vacuum-leak.mmrec"
    ).read_bytes()
    members = mmrec.loads(committed)
    rows = mmrec.event_rows(members)
    assert any("payload_sha256" in row for row in rows), "need enveloped rows"
    tampered = []
    touched = False
    for row in rows:
        row = dict(row)
        if not touched and row.get("type") == "test_result":
            payload = dict(row["payload"])
            payload["outcome"] = "normal"
            row["payload"] = payload
            touched = True
        tampered.append(row)
    assert touched, "expected a test_result row to tamper"
    events_raw = ("\n".join(json.dumps(r, sort_keys=True) for r in tampered) + "\n").encode()
    meta = json.loads(members["meta.json"].decode("utf-8"))
    bad = mmrec.create({"transcript.jsonl": b"", "events.jsonl": events_raw}, meta)
    up = client.post(
        f"/sessions/{_create(client)}/blobs", files={"file": ("bad.mmrec", bad)}
    )
    assert up.status_code == 200, up.text
    r = client.post("/replay", json={"bundle_ref": up.json()["blob_ref"]})
    assert r.status_code == 400, r.text


def test_f6_bundle_replay_restores_full_state(client):
    """L2+tools session with >=2 results: replay matches full state, not priors."""
    from mmc.replay import mmrec, runner

    session_id = _create(client, "L2", ["multimeter"])
    _ack(client, session_id, "engine_running_outdoors")
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/sessions/{session_id}/tests/trim_idle_vs_2500/result",
        json={"measured": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}},
    )
    assert r.status_code == 200, r.text
    live_state = client.get(f"/sessions/{session_id}/state").json()
    assert len(live_state["tests_log"]) == 2

    session = sess.get_registry().get(session_id)
    assert session is not None
    events_raw = (
        sess.get_registry().root / "sessions" / session_id / "events.jsonl"
    ).read_bytes()
    bundle = mmrec.create(
        {"transcript.jsonl": b"", "events.jsonl": events_raw},
        {
            "user_level": session.meta["user_level"],
            "tools": list(session.meta["tools"]),
            "vehicle": dict(session.meta.get("vehicle") or {}),
        },
    )
    replayed = runner.replay_bundle_bytes(bundle, label="test")
    assert replayed.meta["user_level"] == "L2"
    assert replayed.meta["tools"] == ["multimeter"]
    assert replayed.store.verify_chain()
    copy_state = sess.compute_state(replayed)
    assert [h["current"] for h in copy_state["hypotheses"]] == [
        h["current"] for h in live_state["hypotheses"]
    ]
    assert copy_state["tests_log"] == live_state["tests_log"]
    assert [t["kp_test_ref"] for t in copy_state["next_tests"]] == [
        t["kp_test_ref"] for t in live_state["next_tests"]
    ]
    assert [b["kp_test_ref"] for b in copy_state["blocked_tests"]] == [
        b["kp_test_ref"] for b in live_state["blocked_tests"]
    ]
    assert copy_state["missing_evidence"] == live_state["missing_evidence"]


# -- finding #7: report safety notes must reflect actual acks --


def _report(client: TestClient, session_id: str) -> dict:
    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    return client.get(f"/reports/{r.json()['report_id']}.json").json()


def test_f7_empty_session_report_names_no_acknowledged_gates(client):
    report = _report(client, _create(client))
    assert report["safety_notes"] == ["No safety-gated tests were run in this session."]


def test_f7_report_lists_only_actually_acked_gates(client):
    session_id = _create(client)
    _ack(client, session_id, "engine_running_outdoors")
    report = _report(client, session_id)
    assert report["safety_notes"] == [
        "Safety gate acknowledged during session: engine_running_outdoors."
    ]


# -- finding #9: concurrent duplicate submissions apply once --


def test_f9_concurrent_duplicate_test_result_applies_once(client):
    import threading

    session_id = _create(client)
    _ack(client, session_id, "engine_running_outdoors")
    body = {"measured": {"idle_trim_pct": 18.0, "cruise_trim_pct": 4.0}}
    barrier = threading.Barrier(2)
    outcomes: dict[int, int] = {}

    def submit(slot: int) -> None:
        barrier.wait()
        try:
            r = client.post(
                f"/sessions/{session_id}/tests/trim_idle_vs_2500/result", json=body
            )
            outcomes[slot] = r.status_code
        except Exception:  # noqa: BLE001 - a transport error is also a failure
            outcomes[slot] = -1

    with client.websocket_connect(f"/sessions/{session_id}/ws") as ws:
        ws.receive_json()  # current state on connect; also arms broadcast yields
        threads = [threading.Thread(target=submit, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
    assert sorted(outcomes.values()) == [200, 409], outcomes
    state = client.get(f"/sessions/{session_id}/state").json()
    trims = [e for e in state["tests_log"] if e["test_ref"] == "trim_idle_vs_2500"]
    assert len(trims) == 1


# -- finding #10: only SRS body codes stop-and-see-a-pro --


def test_f10_generic_body_code_does_not_trigger_pro_referral(client):
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": ["B1234"]}},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["stop_and_see_pro"]["triggers"] == []
    assert state["next_tests"], "diagnosis must continue on a generic body code"


def test_f10_srs_code_and_flag_still_trigger(client):
    from mmc import safety as safety_mod

    assert safety_mod.stop_triggers_for(["B0012"])
    assert safety_mod.stop_triggers_for(["B0100"])
    assert not safety_mod.stop_triggers_for(["B1234"])
    session_id = _create(client)
    payload = {
        "observation_id": "obs-srs",
        "feature": "safety:flag",
        "value": "srs",
        "confidence": 0.9,
        "extractor": "human",
        "evidence_ids": [],
    }
    r = client.post(
        f"/sessions/{session_id}/events",
        json={"type": "observation_added", "payload": payload},
    )
    assert r.status_code == 200, r.text
    state = client.get(f"/sessions/{session_id}/state").json()
    assert state["next_tests"] == []
    assert any("SRS" in t or "airbag" in t.lower() for t in state["stop_and_see_pro"]["triggers"])


# -- finding #11: rebuilding a report must not re-spend Wonder --


def test_f11_second_report_build_makes_zero_wonder_calls(client, tmp_path, monkeypatch):
    from mmc.meta import wonder as wonder_mod

    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    session_id = _create(client)
    r = client.post(
        f"/sessions/{session_id}/tests/dtc_read/result",
        json={"measured": {"dtcs": []}},
    )
    assert r.status_code == 200, r.text

    calls: dict = {"n": 0}

    def counting_review(audit, client):
        calls["n"] += 1
        return (
            _wonder_review(),
            {"kind": "llm_call", "model": "muse-wonder-1.0"},
        )

    real_request = wonder_mod.request_review
    monkeypatch.setattr(wonder_mod, "request_review", counting_review)
    try:
        first = client.post(f"/sessions/{session_id}/report")
        assert first.status_code == 200, first.text
        assert calls["n"] == 1
        second = client.post(f"/sessions/{session_id}/report")
        assert second.status_code == 200, second.text
        assert calls["n"] == 1, "rebuild with no new events must reuse the stored review"
        first_body = client.get(f"/reports/{first.json()['report_id']}.json").json()
        second_body = client.get(f"/reports/{second.json()['report_id']}.json").json()
        assert second_body["wonder_review"] == first_body["wonder_review"]
        # New diagnostic content invalidates the stored review: one fresh call.
        _ack(client, session_id, "engine_running_outdoors")
        r = client.post(
            f"/sessions/{session_id}/tests/engine_sound_screen/result",
            json={
                "measured": {
                    "outcome": "normal",
                    "band_ratio": 0.3,
                    "flatness": 0.4,
                    "f_fire_hz": 40.0,
                }
            },
        )
        assert r.status_code == 200, r.text
        third = client.post(f"/sessions/{session_id}/report")
        assert third.status_code == 200, third.text
        assert calls["n"] == 2, "new evidence must trigger a fresh review"
    finally:
        monkeypatch.setattr(wonder_mod, "request_review", real_request)


# -- finding #12: L1 trace text and natural-frequency rounding --


def test_f12_verbal_band_rounds_half_up():
    from mmc import levels

    assert "9 in 10" in levels.verbal_band(0.85)
    assert levels.verbal_band(0.85).startswith("Very likely")
    assert "7 in 10" in levels.verbal_band(0.65)
    assert levels.verbal_band(0.65).startswith("Likely")
    assert "3 in 10" in levels.verbal_band(0.25)
    assert "6 in 10" in levels.verbal_band(0.62)
    assert "9 in 10" in levels.verbal_band(0.9)


def test_f12_l1_trace_text_is_jargon_free_for_every_kp_outcome():
    from mmc import levels
    from mmc.kp.loader import PACKS_DIR, load_pack

    outcomes: set[str] = set()
    for pack_name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / pack_name)
        for test in pack.tests.values():
            outcomes.update(test.outcomes)
    assert len(outcomes) >= 20, outcomes
    label = {"L1": "Test label", "L2": "middle label", "L3": "technical label"}
    for outcome in sorted(outcomes):
        texts = levels.trace_text(label, outcome, 0.2, 0.7, 3.0)
        assert levels.check_l1(texts["L1"]) == [], (outcome, texts["L1"])
        # Slug-like outcomes (underscores / code prefixes) must never
        # appear verbatim; plain words like "normal" may.
        if "_" in outcome or outcome.startswith("p0"):
            assert outcome not in texts["L1"], (outcome, texts["L1"])
