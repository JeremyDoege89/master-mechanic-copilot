"""M5U upload routes (plan §5, item 14).

Blobs first, processing second: a failed parse never loses the upload.
AGENTS.md contract style, beside ``routes_vision``:

- POST /sessions/{id}/uploads (multipart ``file`` + form ``kind``) ->
  {blob_ref, sha256, evidence_id, kind}
- POST /sessions/{id}/uploads/pdf/extract {blob_ref} -> {spec_ranges[], evidence_id}
- POST /sessions/{id}/uploads/csv/parse {blob_ref} -> {series[], derived_tests[], skipped[], evidence_id}
- POST /sessions/{id}/uploads/video/analyze {blob_ref} -> {events[], overlays[], evidence_id}

Photo needs no processing route: upload with kind=photo, then the existing
``POST .../vision/keyframe`` confirm flow.

The Meta-backed analysis lives behind a settable analyzer (mirrors
``routes_vision.set_analyzer``) so tests run offline on fixtures. All Meta
calls go through one ``MetaClient`` built with ``on_call`` appending
``llm_call``/``sam_call``; with ``REPLAY_USE_CACHED_LLM=true`` every call
replays from the sha256 disk cache at $0 and a cache miss raises
``NoCachedResponse`` (never spends). Upload routes accept ``live``/``upload``
sessions; replay sessions stay read-only for the processing routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from mmc import sessions as sess
from mmc.api.routes import MAX_BLOB_BYTES, read_upload_capped
from mmc.engine import evaluator as evaluator_mod
from mmc.meta.budget import BudgetExceeded
from mmc.meta.client import MetaClient
from mmc.meta.files import upload_file
from mmc.meta.spark import extract_spec_ranges
from mmc.store import blobs as blob_store
from mmc.upload import csv_logs
from mmc.upload import specs as specs_mod
from mmc.upload import video as video_mod

router = APIRouter()

UPLOAD_KINDS = ("manual_pdf", "scan_pdf", "csv", "photo", "video")

KIND_TO_EVIDENCE_TYPE = {
    "manual_pdf": "manual_page",
    "scan_pdf": "manual_page",
    "csv": "csv_log",
    "photo": "image",
    "video": "video",
}


class UploadAnalyzer(Protocol):
    def extract_pdf(self, data: bytes) -> list[dict]: ...
    def analyze_video(self, data: bytes, filename: str) -> dict: ...


class LiveUploadAnalyzer:
    """Meta-backed analyzer: Files API + Spark (+ SAM for video)."""

    def __init__(self, client: MetaClient | None = None) -> None:
        self._client = client

    def _client_or_default(self) -> MetaClient:
        return self._client or MetaClient()

    def bind(self, session: sess.Session) -> "LiveUploadAnalyzer":
        """Copy with a session-bound client: every Meta call appends its
        ``llm_call``/``sam_call`` record to this session's event log.

        The bound client keeps the analyzer's transport, budget, models,
        and replay flags — only the call-logging hook is added.
        """
        registry = sess.get_registry()

        def on_call(record: dict) -> None:
            registry.append(session, record["kind"], record)

        client = self._client_or_default()
        return LiveUploadAnalyzer(
            MetaClient(
                api_key=getattr(client, "_api_key", None),
                base_url=client.base_url,
                models=dict(client.models),
                budget=client.budget,
                cache_dir=client.cache_dir,
                replay_cached=client.replay_cached,
                transport=getattr(client, "_transport", None),
                on_call=on_call,
                sleep=getattr(client, "_sleep", None),
                timeout_s=client.timeout_s,
            )
        )

    def extract_pdf(self, data: bytes) -> list[dict]:
        client = self._client_or_default()
        file_id = upload_file(client, data, "manual.pdf")
        ranges, _rec = extract_spec_ranges(file_id, client)
        return ranges

    def analyze_video(self, data: bytes, filename: str, defer_masks: bool = False) -> dict:
        return video_mod.analyze_clip(
            data, filename, self._client_or_default(), defer_masks=defer_masks
        )


_ANALYZER: UploadAnalyzer = LiveUploadAnalyzer()


def set_analyzer(analyzer: UploadAnalyzer) -> None:
    global _ANALYZER
    _ANALYZER = analyzer


def get_analyzer() -> UploadAnalyzer:
    return _ANALYZER


class PdfExtractBody(BaseModel):
    blob_ref: str


class CsvParseBody(BaseModel):
    blob_ref: str


class VideoAnalyzeBody(BaseModel):
    blob_ref: str


def _require_session(session_id: str) -> sess.Session:
    session = sess.get_registry().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


def _require_writable(session: sess.Session) -> None:
    if session.meta.get("mode") == "replay":
        raise HTTPException(status_code=409, detail="replay sessions are read-only")


def _load_blob(blob_ref: str) -> tuple[bytes, str]:
    try:
        data = blob_store.get(blob_ref)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"unknown blob: {e}") from e
    return data, hashlib.sha256(data).hexdigest()


def _bound_analyzer(session: sess.Session) -> UploadAnalyzer:
    analyzer = get_analyzer()
    if isinstance(analyzer, LiveUploadAnalyzer):
        return analyzer.bind(session)
    return analyzer


@router.post("/sessions/{session_id}/uploads")
async def upload_file_route(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form(...),
):
    session = _require_session(session_id)
    if kind not in UPLOAD_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown upload kind {kind!r}")
    # M7 review #10: bounded read (never buffer past the cap).
    data = await read_upload_capped(file, request, MAX_BLOB_BYTES)
    blob_ref, sha = blob_store.put(data)
    # M7 review #8: server-side ownership — this session uploaded it.
    sess.record_session_blob(session.id, sha)
    evidence_id: str | None = None
    if session.meta.get("mode") != "replay":
        registry = sess.get_registry()
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "type": KIND_TO_EVIDENCE_TYPE[kind],
            "blob_ref": blob_ref,
            "content_hash": sha,
            "ts": sess._utc_now(),
        }
        registry.append(session, "evidence_added", evidence)
        evidence_id = evidence["evidence_id"]
    return {"blob_ref": blob_ref, "sha256": sha, "evidence_id": evidence_id, "kind": kind}


@router.post("/sessions/{session_id}/uploads/pdf/extract")
async def upload_pdf_extract(session_id: str, body: PdfExtractBody):
    session = _require_session(session_id)
    _require_writable(session)
    from mmc import public as public_mod

    public_mod.require_public_upload_paid()
    data, content_hash = _load_blob(body.blob_ref)
    if not data.lstrip().startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="blob is not a PDF")
    try:
        ranges = _bound_analyzer(session).extract_pdf(data)
    except BudgetExceeded as e:
        raise HTTPException(status_code=402, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"upload backend: {e}")
    try:
        completed = specs_mod.attach_specs(ranges, body.blob_ref, content_hash)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upload backend: bad spec ranges: {e}")
    registry = sess.get_registry()
    evidence = specs_mod.build_evidence(body.blob_ref, content_hash, completed)
    registry.append(session, "evidence_added", evidence)
    return {"spec_ranges": completed, "evidence_id": evidence["evidence_id"]}


@router.post("/sessions/{session_id}/uploads/csv/parse")
async def upload_csv_parse(session_id: str, body: CsvParseBody):
    session = _require_session(session_id)
    _require_writable(session)
    data, content_hash = _load_blob(body.blob_ref)
    try:
        parsed = csv_logs.parse_csv(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    registry = sess.get_registry()
    evidence = csv_logs.build_evidence(body.blob_ref, content_hash, parsed)
    registry.append(session, "evidence_added", evidence)
    for obs in csv_logs.to_observations(parsed, evidence["evidence_id"]):
        registry.append(session, "observation_added", obs)
    derived, skipped = csv_logs.derive_tests(parsed, session.meta.get("vehicle"))
    submitted: list[dict] = []
    skipped = list(skipped)
    # Finding #9: the derived check-then-submit loop holds the same
    # per-session lock as the result route, so a CSV parse racing a
    # manual submission of the same test cannot double-apply it.
    from mmc.api.routes import session_lock

    async with session_lock(session.id):
        state = sess.compute_state(session)
        performed = {e["test_ref"] for e in state["tests_log"]}
        runnable = {r["kp_test_ref"] for r in state["next_tests"]}
        for item in derived:
            ref = item["test_ref"]
            if ref in performed:
                skipped.append({"test_ref": ref, "reason": "already performed"})
                continue
            if ref not in runnable:
                skipped.append({"test_ref": ref, "reason": "not currently runnable"})
                continue
            submitted.append(_submit_derived(session, ref, item["measured"], [evidence["evidence_id"]]))
            performed.add(ref)
            state = sess.compute_state(session)
            runnable = {r["kp_test_ref"] for r in state["next_tests"]}
    return {
        "series": sorted(parsed.series),
        "derived_tests": submitted,
        "skipped": skipped,
        "evidence_id": evidence["evidence_id"],
    }


def _submit_derived(
    session: sess.Session, test_ref: str, measured: dict, evidence_ids: list[str]
) -> dict:
    """Submit one CSV-derived test through the same folds as the result route."""
    pack = sess.get_pack()
    test = pack.tests.get(test_ref)
    if test is None:  # pragma: no cover - derive_tests only emits known refs
        raise HTTPException(status_code=400, detail=f"unknown test {test_ref!r}")
    try:
        outcome, expected = evaluator_mod.evaluate(test, dict(measured))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"cannot evaluate: {e}")
    result_id = uuid.uuid4().hex
    test_result_payload = {
        "result_id": result_id,
        "test_id": test_ref,
        "expected": expected,
        "measured": {"value": dict(measured)},
        "outcome": outcome,
        "evaluator": test.evaluator,
        "evidence_ids": list(evidence_ids),
    }
    try:
        sess.validate("test_result", test_result_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad test_result: {e}")
    registry = sess.get_registry()
    performed_payload = {
        "performed_ts": sess._utc_now(),
        "test_id": result_id,
        "test_ref": test_ref,
        "by_level": session.meta["user_level"],
        "conditions": {},
        "safety_ack": test.safety_gate if test.safety_gate != "none" else None,
    }
    registry.append(session, "test_performed", performed_payload)
    registry.append(session, "test_result", test_result_payload)
    posterior_payload = {
        "result_id": result_id,
        "test_id": test_ref,
        "updates": [
            {"hypothesis_id": h["hypothesis_id"], "prior": h["prior"], "posterior": h["current"]}
            for h in sess.compute_state(session)["hypotheses"]
        ],
    }
    registry.append(session, "posterior_updated", posterior_payload)
    return {"test_ref": test_ref, "outcome": outcome}


@router.post("/sessions/{session_id}/uploads/video/analyze")
async def upload_video_analyze(session_id: str, body: VideoAnalyzeBody):
    import asyncio

    from mmc.api.routes import _broadcast as _push_state

    session = _require_session(session_id)
    _require_writable(session)
    from mmc import public as public_mod

    public_mod.require_public_upload_paid()
    data, content_hash = _load_blob(body.blob_ref)
    filename = Path(body.blob_ref).name
    if not filename.endswith((".mp4", ".mov")):
        filename = "upload.mp4"
    analyzer = _bound_analyzer(session)
    try:
        # Finding #8: ffprobe/ffmpeg/network leave the event loop.
        if isinstance(analyzer, LiveUploadAnalyzer):
            # Live path: boxes + events return right away; mask conversion
            # runs in the bounded pool and lands via the WS state push —
            # same pattern as the keyframe second-opinion diff.
            out = await asyncio.to_thread(
                analyzer.analyze_video, data, filename, defer_masks=True
            )
        else:
            out = await asyncio.to_thread(analyzer.analyze_video, data, filename)
    except BudgetExceeded as e:
        raise HTTPException(status_code=402, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    registry = sess.get_registry()
    evidence = video_mod.build_evidence(body.blob_ref, content_hash, out.get("events", []))
    registry.append(session, "evidence_added", evidence)
    for obs in video_mod.to_observations(out.get("events", []), evidence["evidence_id"]):
        registry.append(session, "observation_added", obs)
    pending = bool(out.get("masks_pending")) and bool(out.get("_mask_jobs"))
    if pending:
        loop = asyncio.get_running_loop()
        evidence_id = evidence["evidence_id"]
        future = video_mod.submit_mask_jobs(out["_mask_jobs"])
        overlays_now = out.get("overlays", [])

        def _deliver(done: Any) -> None:
            try:
                frame_results = done.result()
            except Exception:
                return
            try:
                finished = video_mod.merge_frame_results(
                    [dict(o) for o in overlays_now], frame_results
                )
            except Exception:
                return
            sess.note_video_overlays(
                session_id,
                evidence_id,
                {"overlays": finished, "ts": sess._utc_now()},
            )
            try:
                loop.call_soon_threadsafe(lambda: loop.create_task(_push_state(session_id)))
            except RuntimeError:
                pass

        future.add_done_callback(_deliver)
    return {
        "events": out.get("events", []),
        "boxes": out.get("boxes", []),
        "overlays": out.get("overlays", []),
        "overlays_pending": pending,
        "evidence_id": evidence["evidence_id"],
    }


__all__ = ["router", "set_analyzer", "get_analyzer", "LiveUploadAnalyzer"]
