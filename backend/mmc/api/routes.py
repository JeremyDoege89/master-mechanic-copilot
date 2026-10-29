"""M4 HTTP + WebSocket API: sessions, events, blobs, test results, reports."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mmc import sessions as sess
from mmc.engine import evaluator as evaluator_mod
from mmc.store import blobs as blob_store
from mmc.store.event_store import EVENT_TYPES

router = APIRouter()

MAX_BLOB_BYTES = 32 * 1024 * 1024

_listeners: dict[str, set[WebSocket]] = {}


async def _broadcast(session_id: str) -> None:
    """Push the full recomputed state to every listener of this session."""
    sockets = _listeners.get(session_id)
    if not sockets:
        return
    registry = sess.get_registry()
    session = registry.get(session_id)
    if session is None:
        return
    state = sess.compute_state(session)
    for ws in list(sockets):
        try:
            await ws.send_json(state)
        except Exception:
            sockets.discard(ws)


async def _append_and_push(session: sess.Session, type: str, payload: dict) -> dict:
    registry = sess.get_registry()
    try:
        event = registry.append(session, type, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _broadcast(session.id)
    return event


def _require_session(session_id: str) -> sess.Session:
    session = sess.get_registry().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


class CreateSessionBody(BaseModel):
    vehicle: dict | None = None
    user_level: str = "L1"
    tools: list[str] = []
    # Session mode: live | sim | replay | upload (SPEC section 3 flow 8 —
    # upload sessions take manual pages / CSV logs / photos / video clips
    # instead of the live adapter strip).
    mode: str = "live"


class EventBody(BaseModel):
    type: str
    payload: dict


class ResultBody(BaseModel):
    measured: dict
    conditions: dict = {}
    evidence_ids: list[str] = []
    spec_id: str | None = None


class ClearRequestBody(BaseModel):
    # Pre-clear snapshot (client-read 03/07/0A DTCs, 02 freeze frame, Mode 06).
    # dtcs must be present (possibly empty): no snapshot, no token.
    dtcs: list[str] | None = None
    freeze_frame: dict | None = None
    mode06: dict | None = None


class ClearConfirmBody(BaseModel):
    token: str = ""
    typed_confirmation: str = ""
    engine_off_key_on: bool = False


@router.post("/sessions")
async def create_session(body: CreateSessionBody):
    from mmc import public as public_mod

    public_mod.require_public_session_mode(body.mode)
    registry = sess.get_registry()
    try:
        session = registry.create(
            user_level=body.user_level,
            tools=body.tools,
            mode=body.mode,
            vehicle=body.vehicle,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": session.id}


@router.post("/sessions/{session_id}/events")
async def post_event(session_id: str, body: EventBody):
    session = _require_session(session_id)
    if body.type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown event type: {body.type!r}")
    event = await _append_and_push(session, body.type, dict(body.payload))
    return {"seq": event["seq"], "hash": event["hash"]}


@router.get("/sessions/{session_id}/state")
async def get_state(session_id: str, upto_seq: int | None = None):
    session = _require_session(session_id)
    return sess.compute_state(session, upto_seq=upto_seq)


@router.get("/sessions/{session_id}/events")
async def get_events(session_id: str, after_seq: int = 0):
    """Raw event envelopes for the replay scrubber (seq-ordered)."""
    session = _require_session(session_id)
    return {
        "events": [
            e for e in session.store.iter_replay() if int(e.get("seq", 0)) > after_seq
        ]
    }


class ReplayBody(BaseModel):
    bundle_ref: str | None = None
    session_id: str | None = None
    fixture: str | None = None
    speed: float = 1.0


@router.post("/replay")
async def post_replay(body: ReplayBody):
    """Verify a bundle and fold it into a fresh read-only replay session."""
    from mmc.replay import mmrec, runner

    given = [k for k in ("bundle_ref", "session_id", "fixture") if getattr(body, k)]
    if len(given) != 1:
        raise HTTPException(
            status_code=400,
            detail="give exactly one of bundle_ref, session_id, fixture",
        )
    try:
        if body.bundle_ref:
            try:
                data = blob_store.get(body.bundle_ref)
            except (OSError, ValueError) as e:
                raise HTTPException(status_code=404, detail=f"unknown blob: {e}")
            try:
                session = runner.replay_bundle_bytes(data, label=body.bundle_ref)
            except mmrec.MmrecError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif body.session_id:
            try:
                session = runner.replay_session(body.session_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="unknown session")
        else:
            assert body.fixture is not None
            try:
                data = runner.fixture_bytes(body.fixture)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(status_code=404, detail=str(e))
            try:
                session = runner.replay_bundle_bytes(data, label=body.fixture)
            except mmrec.MmrecError as e:
                raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": session.id, "speed": body.speed}


@router.post("/sessions/{session_id}/blobs")
async def upload_blob(session_id: str, file: UploadFile = File(...)):
    _require_session(session_id)
    data = await file.read()
    if len(data) > MAX_BLOB_BYTES:
        raise HTTPException(status_code=413, detail="blob too large")
    blob_ref, sha = blob_store.put(data)
    return {"blob_ref": blob_ref, "sha256": sha}


@router.post("/sessions/{session_id}/tests/{test_ref}/result")
async def post_test_result(session_id: str, test_ref: str, body: ResultBody):
    session = _require_session(session_id)
    pack = sess.get_pack()
    test = pack.tests.get(test_ref)
    if test is None:
        raise HTTPException(status_code=404, detail=f"unknown test {test_ref!r}")
    state = sess.compute_state(session)
    if any(e["test_ref"] == test_ref for e in state["tests_log"]):
        raise HTTPException(status_code=409, detail=f"test {test_ref!r} already performed")
    runnable = {r["kp_test_ref"] for r in state["next_tests"]}
    if test_ref not in runnable:
        blocked = next(
            (b for b in state["blocked_tests"] if b["kp_test_ref"] == test_ref), None
        )
        reasons = blocked["blocked_reasons"] if blocked else ["not currently runnable"]
        raise HTTPException(status_code=409, detail=f"test not runnable: {'; '.join(reasons)}")
    manual_spec = None
    if body.spec_id is not None:
        from mmc.upload import specs as specs_mod

        spec = specs_mod.find_spec(session, body.spec_id)
        if spec is None:
            # Unknown spec_id: 400 with zero events appended (validated
            # before the first append below).
            raise HTTPException(status_code=400, detail=f"unknown spec_id {body.spec_id!r}")
        manual_spec = specs_mod.manual_spec_for(spec)
    try:
        outcome, expected = evaluator_mod.evaluate(test, dict(body.measured), manual_spec)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"cannot evaluate: {e}")

    result_id = uuid.uuid4().hex
    measured_doc = {"value": dict(body.measured)}
    if body.conditions:
        measured_doc["conditions"] = dict(body.conditions)
    test_result_payload = {
        "result_id": result_id,
        "test_id": test_ref,
        "expected": expected,
        "measured": measured_doc,
        "outcome": outcome,
        "evaluator": test.evaluator,
        "evidence_ids": list(body.evidence_ids),
    }
    try:
        sess.validate("test_result", test_result_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad test_result: {e}")

    performed_payload = {
        "performed_ts": sess._utc_now(),
        "test_id": result_id,
        "test_ref": test_ref,
        "by_level": session.meta["user_level"],
        "conditions": dict(body.conditions),
        "safety_ack": test.safety_gate if test.safety_gate != "none" else None,
    }
    # Atomicity: everything above is validated before the first append; the
    # three appends below are infallible given valid inputs (one fails = none
    # appended holds because no append has happened yet at any failure point).
    await _append_and_push(session, "test_performed", performed_payload)
    event = await _append_and_push(session, "test_result", test_result_payload)
    posterior_payload = {
        "result_id": result_id,
        "test_id": test_ref,
        "updates": [
            {"hypothesis_id": h["hypothesis_id"], "prior": h["prior"], "posterior": h["current"]}
            for h in sess.compute_state(session)["hypotheses"]
        ],
    }
    await _append_and_push(session, "posterior_updated", posterior_payload)
    return {"test_result": test_result_payload, "updates": posterior_payload["updates"],
            "seq": event["seq"], "hash": event["hash"]}


@router.get("/meta/levels")
async def meta_levels():
    """Level labels + tools questionnaire options (M6 §1, SPEC §3)."""
    from mmc import levels as levels_mod

    return {"levels": dict(levels_mod.LEVEL_LABELS), "tools": list(levels_mod.TOOL_OPTIONS)}


@router.get("/budget")
async def get_budget():
    """Spend ledgers (no keys): Meta $120, Baseten $80, public demo cap."""
    from mmc import public as public_mod
    from mmc.meta.budget import get_tracker

    def part(name: str) -> dict:
        tracker = get_tracker(name)
        return {
            "spend_usd": tracker.spend_usd,
            "hard_stop_usd": tracker.hard_stop_usd,
            "remaining_usd": max(0.0, tracker.hard_stop_usd - tracker.spend_usd),
        }

    body: dict = {
        "meta": part("meta"),
        "baseten": part("baseten"),
        "public_mode": public_mod.is_public(),
        "public_live": None,
    }
    if public_mod.is_public():
        demo = public_mod.get_public_tracker()
        body["public_live"] = {
            "spend_usd": demo.spend_usd,
            "cap_usd": public_mod.public_live_budget_usd(),
        }
    return body


@router.post("/sessions/{session_id}/clear-codes/request")
async def request_clear_codes(session_id: str, body: ClearRequestBody):
    """Snapshot DTCs/freeze-frame/Mode 06 as evidence, then issue a token.

    The backend never emits Mode 04: the confirmed token is handed to the
    Android driver's ClearToken for one driver-side Mode 04.
    """
    import json as _json

    from mmc import safety as safety_mod

    session = _require_session(session_id)
    if session.meta.get("mode") == "replay":
        raise HTTPException(status_code=409, detail="replay sessions are read-only")
    if body.dtcs is None:
        raise HTTPException(
            status_code=400,
            detail="pre-clear snapshot required: dtcs (Modes 03/07/0A), "
            "freeze_frame (02) and mode06 are saved as evidence first",
        )
    registry = sess.get_registry()
    evidence_ids: list[str] = []
    snapshots = [("dtc", {"dtcs": list(body.dtcs), "modes": ["03", "07", "0A"]})]
    if body.freeze_frame is not None:
        snapshots.append(("freeze_frame", dict(body.freeze_frame)))
    if body.mode06 is not None:
        snapshots.append(("mode06", dict(body.mode06)))
    for etype, doc in snapshots:
        blob = _json.dumps(doc, sort_keys=True).encode("utf-8")
        blob_ref, sha = blob_store.put(blob)
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "type": etype,
            "blob_ref": blob_ref,
            "content_hash": sha,
            "ts": sess._utc_now(),
        }
        registry.append(session, "evidence_added", evidence)
        evidence_ids.append(evidence["evidence_id"])
    token = safety_mod.issue_token(session.id)
    # The plaintext token is never logged — only its hash.
    await _append_and_push(
        session,
        "codes_clear_requested",
        {
            "token_sha256": safety_mod._token_hash(token),
            "evidence_ids": evidence_ids,
            "consequences": safety_mod.CONSEQUENCES_TEXT,
        },
    )
    return {"token": token, "consequences_text": safety_mod.CONSEQUENCES_TEXT}


@router.post("/sessions/{session_id}/clear-codes/confirm")
async def confirm_clear_codes(session_id: str, body: ClearConfirmBody):
    from mmc import safety as safety_mod

    session = _require_session(session_id)
    if session.meta.get("mode") == "replay":
        raise HTTPException(status_code=409, detail="replay sessions are read-only")
    if body.typed_confirmation != "CLEAR CODES":
        raise HTTPException(
            status_code=400, detail="type CLEAR CODES exactly to confirm"
        )
    if not body.engine_off_key_on:
        raise HTTPException(
            status_code=400,
            detail="engine-off/key-on attestation required",
        )
    try:
        token_sha = safety_mod.consume_token(session.id, body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad token: {e}")
    event = await _append_and_push(
        session, "codes_cleared", {"token_sha256": token_sha}
    )
    return {
        "cleared": True,
        "clear_token": body.token,
        "seq": event["seq"],
        "hash": event["hash"],
    }


@router.post("/sessions/{session_id}/report")
async def build_report(session_id: str):
    from mmc.meta import wonder as wonder_mod
    from mmc.meta.client import MetaClient, MetaClientError, NoCachedResponse

    session = _require_session(session_id)
    registry = sess.get_registry()
    state = sess.compute_state(session)
    report = sess.build_report(session, state)
    # One Wonder review per report; unavailable (no key / budget / API
    # error) ships the report with wonder_review null, never blocks it.
    # Wonder stays off in public replay mode (M6 fix 3).
    from mmc import public as public_mod

    if public_mod.is_public():
        report["wonder_review"] = None
        report["wonder_unavailable"] = "AI review unavailable in public replay mode."
        audit = None
    else:
        audit = wonder_mod.build_audit_bundle(report, state)
    if audit is not None:
        try:
            default_client = MetaClient(
                on_call=lambda record: registry.append(
                    session, record.get("kind", "llm_call"), record
                ),
            )
        except MetaClientError as e:
            report["wonder_review"] = None
            report["wonder_unavailable"] = f"AI review unavailable: {e}"
        else:
            # attach_review maps budget/API/cache failures to null + the
            # unavailable line itself; only unexpected errors escape.
            try:
                wonder_mod.attach_review(report, audit, default_client)
            except (MetaClientError, NoCachedResponse) as e:
                report["wonder_review"] = None
                report["wonder_unavailable"] = f"AI review unavailable: {e}"
    # M6B §1.2: where live Wonder is skipped or fails, serve the recorded
    # review folded in with a demo bundle ($0) instead of "unavailable".
    if report.get("wonder_review") is None:
        recorded = sess.find_recorded_wonder_review(session)
        if recorded is not None:
            report["wonder_review"] = recorded
            report["wonder_unavailable"] = None
    sess.save_report(registry, report)
    # The review rides in the event so replays rebuild it with no live call.
    registry.append(
        session,
        "report_built",
        {
            "report_id": report["report_id"],
            "wonder_review": report.get("wonder_review"),
            "wonder_unavailable": report.get("wonder_unavailable"),
        },
    )
    await _broadcast(session.id)
    return {"report_id": report["report_id"], "url": f"/reports/{report['report_id']}.json"}


@router.get("/reports/{report_id}.json")
async def get_report(report_id: str):
    report = sess.load_report(sess.get_registry(), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="unknown report")
    return JSONResponse(report)


@router.get("/reports/{report_id}.html")
async def get_report_html(report_id: str):
    from fastapi.responses import HTMLResponse

    from mmc.report import html as html_mod

    report = sess.load_report(sess.get_registry(), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="unknown report")
    return HTMLResponse(html_mod.render_html(report))


@router.get("/reports/{report_id}.pdf")
async def get_report_pdf(report_id: str):
    from fastapi.responses import Response

    from mmc.report import pdf as pdf_mod

    report = sess.load_report(sess.get_registry(), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="unknown report")
    return Response(pdf_mod.render_pdf(report), media_type="application/pdf")


@router.websocket("/sessions/{session_id}/ws")
async def session_ws(websocket: WebSocket, session_id: str):
    session = sess.get_registry().get(session_id)
    if session is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    _listeners.setdefault(session_id, set()).add(websocket)
    try:
        # Reconnecting clients get the current state immediately.
        await websocket.send_json(sess.compute_state(session))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _listeners.get(session_id, set()).discard(websocket)
