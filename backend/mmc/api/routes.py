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
    mode: str = "live"


class EventBody(BaseModel):
    type: str
    payload: dict


class ResultBody(BaseModel):
    measured: dict
    conditions: dict = {}
    evidence_ids: list[str] = []


@router.post("/sessions")
async def create_session(body: CreateSessionBody):
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
async def get_state(session_id: str):
    session = _require_session(session_id)
    return sess.compute_state(session)


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
    try:
        outcome, expected = evaluator_mod.evaluate(test, dict(body.measured))
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


@router.post("/sessions/{session_id}/report")
async def build_report(session_id: str):
    session = _require_session(session_id)
    registry = sess.get_registry()
    state = sess.compute_state(session)
    report = sess.build_report(session, state)
    sess.save_report(registry, report)
    registry.append(session, "report_built", {"report_id": report["report_id"]})
    await _broadcast(session.id)
    return {"report_id": report["report_id"], "url": f"/reports/{report['report_id']}.json"}


@router.get("/reports/{report_id}.json")
async def get_report(report_id: str):
    report = sess.load_report(sess.get_registry(), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="unknown report")
    return JSONResponse(report)


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
