"""M5 vision routes (plan step 10).

- POST /sessions/{id}/vision/keyframe   {blob_ref} -> {components[], anomalies[], overlays[]}
- POST /sessions/{id}/vision/instrument {blob_ref} -> {value, units, confidence, needs_retake}
- POST /sessions/{id}/vision/instrument/confirm {blob_ref, reading, note?} -> {evidence_id}

Blobs arrive via the existing POST .../blobs route. The keyframe route
appends evidence_added + observation_added events (the add_observation
path); the instrument route appends nothing — a reading becomes Evidence
only through the confirm route after explicit user confirmation.

The Meta-backed analysis lives behind a settable analyzer so tests run
offline on recorded fixtures: ``set_analyzer(FixtureAnalyzer(...))``.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mmc import sessions as sess
from mmc.meta import sam as sam_mod
from mmc.meta import spark as spark_mod
from mmc.meta.client import MetaClient
from mmc.store import blobs as blob_store
from mmc.vision import components as comp_mod
from mmc.vision import instruments as instr_mod

router = APIRouter()

MAX_OVERLAY_COMPONENTS = 3


class VisionAnalyzer(Protocol):
    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]: ...
    def analyze_instrument(self, image: bytes) -> dict: ...


class LiveVisionAnalyzer:
    """Meta-backed analyzer: Spark scene JSON + SAM masks for top components."""

    def __init__(self, client: MetaClient | None = None) -> None:
        self._client = client

    def _client_or_default(self) -> MetaClient:
        return self._client or MetaClient()

    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]:
        client = self._client_or_default()
        parsed, _rec1 = spark_mod.analyze_components(image, client)
        parsed = comp_mod.parse_components(parsed)
        overlays: list[dict] = []
        for comp in parsed.get("components", [])[:MAX_OVERLAY_COMPONENTS]:
            # An empty SAM result is a normal "not found": retry once with
            # the shorter fallback concept when Spark gave one.
            try:
                result, _recs, used = sam_mod.segment_with_fallback(
                    comp["sam_concept"], comp.get("sam_fallback"), image, client
                )
            except Exception:
                continue  # a failed overlay never blocks the components answer
            overlays.append(
                {
                    "concept": comp["sam_concept"],
                    "used_concept": used,
                    "found": result.found,
                    "masks": sam_mod.overlays_for_result(result),
                }
            )
        return parsed, overlays

    def analyze_instrument(self, image: bytes) -> dict:
        client = self._client_or_default()
        raw, _rec = spark_mod.read_instrument(image, client)
        return instr_mod.parse_instrument(raw)


_ANALYZER: VisionAnalyzer = LiveVisionAnalyzer()


def set_analyzer(analyzer: VisionAnalyzer) -> None:
    global _ANALYZER
    _ANALYZER = analyzer


def get_analyzer() -> VisionAnalyzer:
    return _ANALYZER


class KeyframeBody(BaseModel):
    blob_ref: str


class InstrumentBody(BaseModel):
    blob_ref: str


class ConfirmBody(BaseModel):
    blob_ref: str
    reading: dict
    note: str = ""


def _require_session(session_id: str) -> sess.Session:
    session = sess.get_registry().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


def _load_blob(blob_ref: str) -> tuple[bytes, str]:
    try:
        data = blob_store.get(blob_ref)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"unknown blob: {e}") from e
    import hashlib

    return data, hashlib.sha256(data).hexdigest()


@router.post("/sessions/{session_id}/vision/keyframe")
async def vision_keyframe(session_id: str, body: KeyframeBody):
    session = _require_session(session_id)
    image, content_hash = _load_blob(body.blob_ref)
    try:
        parsed, overlays = get_analyzer().analyze_keyframe(image)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"vision backend: {e}")
    registry = sess.get_registry()
    evidence = {
        "evidence_id": uuid.uuid4().hex,
        "type": "image",
        "blob_ref": body.blob_ref,
        "content_hash": content_hash,
        "ts": sess._utc_now(),
    }
    registry.append(session, "evidence_added", evidence)
    for obs in comp_mod.to_observations(parsed, evidence["evidence_id"]):
        registry.append(session, "observation_added", obs)
    return {
        "components": parsed["components"],
        "anomalies": parsed["anomalies"],
        "overlays": overlays,
    }


@router.post("/sessions/{session_id}/vision/instrument")
async def vision_instrument(session_id: str, body: InstrumentBody):
    _require_session(session_id)
    image, _sha = _load_blob(body.blob_ref)
    try:
        reading = get_analyzer().analyze_instrument(image)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"vision backend: {e}")
    return reading


@router.post("/sessions/{session_id}/vision/instrument/confirm")
async def vision_instrument_confirm(session_id: str, body: ConfirmBody):
    session = _require_session(session_id)
    _image, content_hash = _load_blob(body.blob_ref)
    try:
        reading = instr_mod.parse_instrument(dict(body.reading))
        evidence, observation = instr_mod.confirm_reading(
            reading, body.blob_ref, content_hash, body.note
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    registry = sess.get_registry()
    registry.append(session, "evidence_added", evidence)
    registry.append(session, "observation_added", observation)
    return {"evidence_id": evidence["evidence_id"]}
