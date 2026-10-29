"""M4 HTTP + WebSocket API: sessions, events, blobs, test results, reports."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from mmc import sessions as sess
from mmc.engine import evaluator as evaluator_mod
from mmc.store import blobs as blob_store
from mmc.store.event_store import EVENT_TYPES

router = APIRouter()

# Finding #1: the generic events endpoint is client-writeable only for
# observation-side types. Engine/privileged types (test_result,
# test_performed, posterior_updated, hypothesis_proposed, llm_call,
# sam_call, codes_clear_requested, codes_cleared, report_built,
# session_started, adapter_fingerprinted) are appended only by the
# server-side routes that own their validation, and are 403 here.
CLIENT_POSTABLE_EVENT_TYPES = frozenset(
    {"safety_ack", "observation_added", "evidence_added", "asr_segment", "test_requested"}
)


def _require_writable(session: sess.Session) -> None:
    """Finding #2: replay sessions stay read-only on the core routes too,
    mirroring the vision/upload guard (409 on mode == replay)."""
    if session.meta.get("mode") == "replay":
        raise HTTPException(status_code=409, detail="replay sessions are read-only")


# Finding #9: one asyncio lock per session serialises the
# check-then-append sequence in post_test_result (and the CSV derived
# loop), so two concurrent submissions of the same test cannot both pass
# the runnable/already-performed checks. The EventStore lock only makes
# single appends atomic, not the sequence.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def session_lock(session_id: str) -> asyncio.Lock:
    """Return the per-session check-then-append lock (shared with uploads)."""
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[session_id] = lock
        return lock

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
    _require_writable(session)
    if body.type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"unknown event type: {body.type!r}")
    if body.type not in CLIENT_POSTABLE_EVENT_TYPES:
        raise HTTPException(
            status_code=403,
            detail=f"event type {body.type!r} is engine/privileged: use its dedicated route",
        )
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


@router.get("/fixtures")
async def list_fixtures():
    """Judge-landing inventory: role=demo bundles only, real-vehicle first.

    Real-vehicle exports (``label == "real vehicle, real AI outputs"``)
    sort before simulated bundles; each entry carries its display label
    so the landing needs no hand editing when the owner drops a new
    ``real-*.mmrec`` into the fixtures dir (Modal reseeds on boot).
    Public-safe: bundle names + labels only, no session data.
    """
    from mmc.replay import runner

    return {"fixtures": runner.list_fixtures()}


async def read_upload_capped(
    file: UploadFile, request: Request, limit: int | None = None
) -> bytes:
    """Bounded upload read (M7 review #10).

    Rejects early on a declared Content-Length past the cap, then streams
    in 1 MiB chunks so a multi-GB body is never fully buffered before the
    413 can fire. ``limit`` defaults to this module's ``MAX_BLOB_BYTES``;
    the uploads route passes its own so its cap stays patchable per route.
    """
    cap = MAX_BLOB_BYTES if limit is None else limit
    try:
        declared = request.headers.get("content-length", "")
        if declared and int(declared) > cap:
            raise HTTPException(status_code=413, detail="blob too large")
    except ValueError:
        pass
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(status_code=413, detail="blob too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/sessions/{session_id}/blobs")
async def upload_blob(session_id: str, request: Request, file: UploadFile = File(...)):
    session = _require_session(session_id)
    _require_writable(session)
    data = await read_upload_capped(file, request)
    blob_ref, sha = blob_store.put(data)
    # M7 review #8: server-side ownership — this session uploaded it.
    sess.record_session_blob(session.id, sha)
    return {"blob_ref": blob_ref, "sha256": sha}


_BLOB_SHA_RE = re.compile(r"[0-9a-f]{64}")


def _session_blob_types(session: sess.Session) -> dict[str, str]:
    """Map referenced blob sha (lowercase) -> evidence type.

    Only blobs named by this session's own ``evidence_added`` payloads
    (``blob_ref`` suffix or ``content_hash``) are fetchable, so the
    public site cannot be used to pull arbitrary blobs.
    """
    index: dict[str, str] = {}
    for event in session.store.iter_replay():
        if event.get("type") != "evidence_added":
            continue
        payload = event.get("payload") or {}
        etype = str(payload.get("type", ""))
        for key in ("content_hash", "blob_ref"):
            raw = payload.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            digest = raw.split("/")[-1].lower()
            if _BLOB_SHA_RE.fullmatch(digest):
                index.setdefault(digest, etype)
    return index


def _blob_media_type(data: bytes, etype: str) -> str:
    """Content type for a session blob: magic bytes first, evidence type fallback."""
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if etype == "image":
        return "image/jpeg"
    if etype == "audio":
        return "audio/wav"
    if etype == "manual_page":
        return "application/pdf"
    stripped = data.strip()[:1]
    if stripped in (b"{", b"["):
        try:
            import json as _json

            _json.loads(data.decode("utf-8"))
            return "application/json"
        except (ValueError, UnicodeDecodeError):
            pass
    return "application/octet-stream"


@router.get("/sessions/{session_id}/blobs/{sha256}")
async def download_blob(session_id: str, sha256: str):
    """Stream one blob referenced by this session's event log.

    The digest must appear as an ``evidence_added`` payload's ``blob_ref``
    or ``content_hash`` for this session (replay sessions included);
    anything else is 404 so unreferenced blobs stay unfetchable.
    """
    session = _require_session(session_id)
    digest = sha256.lower()
    if not _BLOB_SHA_RE.fullmatch(digest):
        raise HTTPException(status_code=404, detail="unknown blob")
    # M7 review #8: serve only blobs this session itself produced or
    # uploaded. Ownership is recorded server-side at upload time — a
    # client-postable evidence record naming a foreign SHA is not enough.
    if not sess.session_owns_blob(session.id, digest):
        raise HTTPException(status_code=404, detail="unknown blob")
    etype = _session_blob_types(session).get(digest)
    if etype is None:
        raise HTTPException(status_code=404, detail="unknown blob")
    try:
        data = blob_store.get(f"blobs/{digest}")
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"unknown blob: {e}") from e
    if hashlib.sha256(data).hexdigest() != digest:
        raise HTTPException(status_code=404, detail="unknown blob")
    return Response(content=data, media_type=_blob_media_type(data, etype))


@router.post("/sessions/{session_id}/tests/{test_ref}/result")
async def post_test_result(session_id: str, test_ref: str, body: ResultBody):
    session = _require_session(session_id)
    _require_writable(session)
    pack = sess.get_pack()
    test = pack.tests.get(test_ref)
    if test is None:
        raise HTTPException(status_code=404, detail=f"unknown test {test_ref!r}")
    # Finding #9: the runnable/already-performed checks and the three
    # appends hold the per-session lock, so concurrent submissions of the
    # same test serialise (one 200, the other 409) instead of both
    # passing the checks and double-counting the likelihood update.
    async with session_lock(session.id):
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
        # M7 review #8: server-side ownership — the backend produced it.
        sess.record_session_blob(session.id, sha)
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
    import jsonschema

    from mmc.meta import wonder as wonder_mod
    from mmc.meta.client import MetaClient, MetaClientError, NoCachedResponse

    session = _require_session(session_id)
    registry = sess.get_registry()
    # M7 review #6: the check -> Wonder call -> append sequence holds the
    # per-session lock (like post_test_result), so two simultaneous builds
    # serialise: the second reuses the first build's stored review instead
    # of paying for its own Wonder call.
    async with session_lock(session.id):
        state = sess.compute_state(session)
        report = sess.build_report(session, state)
        # Finding #11: a stored review with no newer diagnostic event is
        # still current — reuse it instead of spending a new Wonder call.
        # This check runs before (and in) public mode, so demo-bundle
        # recorded reviews keep serving at $0 too.
        head_hash = report["reproducibility"]["log_head_hash"]
        stored = sess.find_current_report_review(session)
        if stored is not None:
            report["wonder_review"] = stored
            report["wonder_unavailable"] = None
            audit = None
        # One Wonder review per report; unavailable (no key / budget / API
        # error) ships the report with wonder_review null, never blocks it.
        # Wonder stays off in public replay mode (M6 fix 3).
        else:
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
                # attach_review maps budget/API/cache/invalid-review failures
                # to null + the unavailable line itself (finding #3); the
                # extra guard keeps an unexpected ValueError/ValidationError
                # from 500ing the build before save_report/report_built.
                try:
                    wonder_mod.attach_review(report, audit, default_client)
                except (MetaClientError, NoCachedResponse, ValueError, jsonschema.ValidationError) as e:
                    report["wonder_review"] = None
                    report["wonder_unavailable"] = f"AI review unavailable: {e}"
        # M6B §1.2: where live Wonder is skipped or fails, serve the recorded
        # review folded in with a demo bundle ($0) instead of "unavailable" —
        # but only while it is still current (M7 review #7): a newer
        # diagnostic event invalidates it and the build ships null.
        if report.get("wonder_review") is None:
            recorded = sess.find_recorded_wonder_review_if_current(session)
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
                "log_head_hash": head_hash,
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


@router.websocket("/sessions/{session_id}/asr")
async def session_asr(websocket: WebSocket, session_id: str):
    """M7 voice ASR proxy: PCM in, partials verbatim, finals to the log."""
    from mmc import public as public_mod
    from mmc.meta import voice as voice_mod

    session = sess.get_registry().get(session_id)
    if session is None:
        await websocket.close(code=4404)
        return
    if public_mod.is_public():
        # Paid live call, off on Modal (plan §1).
        await websocket.close(code=4501)
        return
    if session.meta.get("mode") == "replay":
        await websocket.close(code=4409)
        return
    await websocket.accept()
    proxy = voice_mod.AsrProxy()
    registry = sess.get_registry()

    async def append_event(payload: dict) -> dict:
        event = registry.append(session, "asr_segment", payload)
        await _broadcast(session.id)
        return event

    async def on_budget_exceeded() -> None:
        try:
            await websocket.send_json({"type": "error", "error": "asr budget exceeded"})
        except Exception:
            pass
        await websocket.close(code=4402)

    try:
        await proxy.run(
            websocket, session, append_event,
            send_to_client=websocket.send_text,
            on_budget_exceeded=on_budget_exceeded,
        )
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Server misconfigured for live ASR (no MODEL_API_KEY or no
        # websockets package): the proxy factory raises before dialling.
        try:
            await websocket.close(code=4500)
        except Exception:
            pass


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
