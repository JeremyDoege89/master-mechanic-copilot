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

import asyncio
import threading
import time
import uuid
from typing import Any, Callable, Protocol

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mmc import sessions as sess
from mmc.meta import sam as sam_mod
from mmc.meta import spark as spark_mod
from mmc.meta.client import MetaClient, MetaClientError
from mmc.store import blobs as blob_store
from mmc.vision import components as comp_mod
from mmc.vision import instruments as instr_mod

router = APIRouter()

MAX_OVERLAY_COMPONENTS = 3

# M5O §2 latency budget: Spark 1.3 answers the keyframe target (<3 s, SPEC
# §7); Spark Open measured 6.5 s on an image probe, so it joins the sync
# merge only inside the remaining budget. Per-model hard timeout 10 s; an
# Open timeout degrades to single_source and never blocks the labels.
PRIMARY_TIMEOUT_S = 10.0
OPEN_TIMEOUT_S = 10.0
SYNC_BUDGET_S = 3.0

_UNSET: Any = object()


class VisionAnalyzer(Protocol):
    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]: ...
    def analyze_instrument(self, image: bytes) -> dict: ...


def _overlays_for(parsed: dict, image: bytes, client: MetaClient) -> list[dict]:
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
    return overlays


class LiveVisionAnalyzer:
    """Meta-backed analyzer: Spark scene JSON + SAM masks for top components.

    M5O §2 runs ``spark.analyze_components`` (Spark 1.3) plus
    ``spark_open.analyze_components_open`` concurrently and merges on
    agreement only (never probabilities). SAM overlays always come from
    the served primary concepts.
    """

    def __init__(
        self,
        client: MetaClient | None = None,
        open_client: Any = _UNSET,
        on_second_opinion: Callable[[str, str, dict], None] | None = None,
    ) -> None:
        self._client = client
        self._open_client = open_client
        self._on_second_opinion = on_second_opinion

    def _client_or_default(self) -> MetaClient:
        return self._client or MetaClient()

    def _open_or_none(self) -> Any | None:
        if self._open_client is _UNSET:
            try:
                from mmc.meta import spark_open as spark_open_mod

                # Resolved once so the fallback use (§3) and the second
                # opinion (§2) inside one analyzer share a single $80
                # ledger instead of each minting a fresh tracker.
                self._open_client = spark_open_mod.SparkOpenClient()
            except (MetaClientError, ValueError, Exception):
                self._open_client = None  # no Baseten key: Open disabled
        return self._open_client

    def bind(self, session: sess.Session) -> "LiveVisionAnalyzer":
        """Copy with session-bound call logging: every Meta call appends its
        key-free ``llm_call`` record to this session's event log."""
        registry = sess.get_registry()

        def on_call(record: dict) -> None:
            try:
                registry.append(session, record.get("kind", "llm_call"), record)
            except Exception:
                pass

        client = self._client_or_default()
        bound_primary = MetaClient(
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
        return LiveVisionAnalyzer(bound_primary, self._open_or_none(), self._on_second_opinion)

    def _run_primary(self, image: bytes, open_client: Any = _UNSET) -> tuple[dict, dict]:
        client = self._client_or_default()
        if open_client is _UNSET:
            open_client = self._open_or_none()
        t0 = time.time()
        # M5O §3: the served primary may come from the Spark Open
        # fallback (record carries provider baseten + fallback marker);
        # SAM overlays follow those served concepts.
        parsed, record = spark_mod.analyze_components(image, client, open_client=open_client)
        record = dict(record)
        record.setdefault("latency_s", time.time() - t0)
        return comp_mod.parse_components(parsed), record

    def _run_open(self, image: bytes, open_client: Any = _UNSET) -> tuple[dict, dict]:
        from mmc.meta import spark_open as spark_open_mod

        client = self._open_or_none() if open_client is _UNSET else open_client
        if client is None:
            raise MetaClientError("spark-open second opinion disabled (no Baseten key)")
        return spark_open_mod.analyze_components_open(image, client)

    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]:
        merged, overlays, _info = self.analyze_keyframe_full(image)
        return merged, overlays

    def analyze_keyframe_full(
        self,
        image: bytes,
        *,
        session_id: str = "",
        evidence_id: str = "",
    ) -> tuple[dict, list[dict], dict]:
        """Concurrent primary + second opinion with progressive return.

        Returns ``(merged, overlays, info)`` where ``merged`` components
        carry ``agreement``/``sources`` and ``info`` holds
        ``disagreements``, ``second_opinion_pending``, both latencies and
        both call records. Spark 1.3 labels never wait past the sync
        budget for Open: when Open is still running, the primary ships
        immediately as ``single_source`` with ``pending=True`` and the
        merge lands later through ``on_second_opinion`` (the route notes
        it into session state and pushes a WS refresh; posteriors are
        bit-identical either way).
        """
        client = self._client_or_default()
        open_client = self._open_or_none()
        primary_box: dict = {}
        open_box: dict = {}

        def run_primary() -> None:
            try:
                parsed, record = self._run_primary(image, open_client)
                primary_box["parsed"] = parsed
                primary_box["record"] = record
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                primary_box["error"] = exc

        def run_open() -> None:
            try:
                parsed, record = self._run_open(image, open_client)
                open_box["parsed"] = parsed
                open_box["record"] = record
            except Exception as exc:  # noqa: BLE001 - degrade, never block
                open_box["error"] = exc

        t0 = time.time()
        tp = threading.Thread(target=run_primary, daemon=True)
        tp.start()
        to: threading.Thread | None = None
        if open_client is not None:
            to = threading.Thread(target=run_open, daemon=True)
            to.start()
        tp.join(timeout=PRIMARY_TIMEOUT_S)
        primary_latency = time.time() - t0
        if "parsed" not in primary_box:
            err = primary_box.get("error")
            raise MetaClientError(f"primary vision failed: {err}") from (
                err if isinstance(err, Exception) else None
            )
        primary = primary_box["parsed"]
        primary_record = primary_box["record"]
        overlays = _overlays_for(primary, image, client)

        info: dict = {
            "disagreements": [],
            "second_opinion_pending": False,
            "primary_latency_s": primary_latency,
            "open_latency_s": None,
            "primary_record": primary_record,
            "open_record": None,
        }
        if to is None:
            merged = comp_mod._single_source(primary)
            info["disagreements"] = [
                {"name": str(c.get("name", "")), "named_by": comp_mod.PRIMARY_SOURCE}
                for c in primary.get("components", [])
            ]
            return merged, overlays, info
        sync_wait = max(0.0, SYNC_BUDGET_S - (time.time() - t0))
        to.join(timeout=sync_wait)
        if "parsed" in open_box:
            merged, disagreements = comp_mod.merge_opinions(primary, open_box["parsed"])
            info["disagreements"] = disagreements
            info["open_latency_s"] = self._record_latency(open_box["record"])
            info["open_record"] = open_box["record"]
            return merged, overlays, info
        if "error" in open_box:
            merged = comp_mod._single_source(primary)
            info["disagreements"] = [
                {"name": str(c.get("name", "")), "named_by": comp_mod.PRIMARY_SOURCE}
                for c in primary.get("components", [])
            ]
            info["open_record"] = open_box.get("record")
            return merged, overlays, info
        # Still running past the sync budget: ship primary now, merge later.
        merged = comp_mod._single_source(primary)
        info["disagreements"] = [
            {"name": str(c.get("name", "")), "named_by": comp_mod.PRIMARY_SOURCE}
            for c in primary.get("components", [])
        ]
        info["second_opinion_pending"] = True
        deadline = t0 + OPEN_TIMEOUT_S
        callback = self._on_second_opinion

        def waiter() -> None:
            remaining = max(0.0, deadline - time.time())
            to.join(timeout=remaining)
            if "parsed" not in open_box or callback is None:
                return
            late, disagreements = comp_mod.merge_opinions(primary, open_box["parsed"])
            view = {
                "open_record": open_box.get("record"),
                "components": [
                    {
                        "name": c.get("name", ""),
                        "sam_concept": c.get("sam_concept", ""),
                        "confidence": c.get("confidence", 0.0),
                        "agreement": c.get("agreement", "single_source"),
                        "sources": c.get("sources", []),
                    }
                    for c in late.get("components", [])
                ],
                "disagreements": disagreements,
                "open_latency_s": self._record_latency(open_box["record"]),
                "ts": sess._utc_now(),
            }
            try:
                callback(session_id, evidence_id, view)
            except Exception:
                pass

        threading.Thread(target=waiter, daemon=True).start()
        return merged, overlays, info

    @staticmethod
    def _record_latency(record: Any) -> float | None:
        try:
            value = record.get("latency_s")
            return float(value) if value is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def analyze_instrument(self, image: bytes) -> dict:
        reading, _record = self.analyze_instrument_full(image)
        return reading

    def analyze_instrument_full(
        self, image: bytes, hint: str = ""
    ) -> tuple[dict, dict | None]:
        """Meter reading with its call record (M5O §3 fallback inside).

        Returns ``(reading, record)``; the reading is the identical
        validated shape whether Meta or the fallback served it, so the
        <0.8 retake + user-confirm gates apply unchanged.
        """
        client = self._client_or_default()
        raw, record = spark_mod.read_instrument(
            image, client, hint=hint, open_client=self._open_or_none()
        )
        return instr_mod.parse_instrument(raw), record


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
    from mmc.api.routes import _broadcast as _push_state

    session = _require_session(session_id)
    image, content_hash = _load_blob(body.blob_ref)
    registry = sess.get_registry()
    evidence_id = uuid.uuid4().hex
    analyzer = get_analyzer()
    full = getattr(analyzer, "analyze_keyframe_full", None)
    if full is None:
        try:
            parsed, overlays = analyzer.analyze_keyframe(image)
        except (ValueError, MetaClientError) as e:
            raise HTTPException(status_code=502, detail=f"vision backend: {e}")
        info: dict = {"disagreements": [], "second_opinion_pending": False,
                      "open_record": None}
    else:
        assert isinstance(analyzer, LiveVisionAnalyzer)
        loop = asyncio.get_running_loop()
        try:
            bound = analyzer.bind(session)
        except (ValueError, MetaClientError) as e:
            raise HTTPException(status_code=502, detail=f"vision backend: {e}")

        def on_late(late_session_id: str, late_evidence_id: str, view: dict) -> None:
            if late_session_id != session_id or late_evidence_id != evidence_id:
                return

            def deliver() -> None:
                try:
                    late_record = view.pop("open_record", None)
                    if isinstance(late_record, dict):
                        registry.append(session, late_record.get("kind", "llm_call"), late_record)
                    if view.get("components"):
                        sess.note_vision_second_opinion(session_id, evidence_id, view)
                    loop.create_task(_push_state(session_id))
                except Exception:
                    pass

            try:
                loop.call_soon_threadsafe(deliver)
            except RuntimeError:
                pass

        bound._on_second_opinion = on_late
        try:
            parsed, overlays, info = bound.analyze_keyframe_full(
                image, session_id=session_id, evidence_id=evidence_id
            )
        except (ValueError, MetaClientError) as e:
            raise HTTPException(status_code=502, detail=f"vision backend: {e}")
        # Meta-provider records already hit the log via the bound client's
        # on_call; Baseten records (second opinion, or a §3 fallback that
        # served the primary) are appended here. Pending-path late records
        # arrive through on_late above.
        if not info.get("second_opinion_pending"):
            for record in (info.get("primary_record"), info.get("open_record")):
                if isinstance(record, dict) and record.get("provider") == "baseten":
                    try:
                        registry.append(session, record.get("kind", "llm_call"), record)
                    except Exception:
                        pass
    evidence = {
        "evidence_id": evidence_id,
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
        "disagreements": info.get("disagreements", []),
        "second_opinion_pending": bool(info.get("second_opinion_pending", False)),
        "evidence_id": evidence_id,
    }


@router.post("/sessions/{session_id}/vision/instrument")
async def vision_instrument(session_id: str, body: InstrumentBody):
    session = _require_session(session_id)
    image, _sha = _load_blob(body.blob_ref)
    analyzer = get_analyzer()
    full = getattr(analyzer, "analyze_instrument_full", None)
    try:
        if full is None:
            reading = analyzer.analyze_instrument(image)
            record = None
        else:
            reading, record = full(image)
    except (ValueError, MetaClientError) as e:
        raise HTTPException(status_code=502, detail=f"vision backend: {e}")
    if isinstance(record, dict) and record.get("provider") == "baseten":
        try:
            sess.get_registry().append(session, record.get("kind", "llm_call"), record)
        except Exception:
            pass
    return reading


class EngineSoundBody(BaseModel):
    blob_ref: str
    rpm: float
    cylinders: int | None = None
    blob_ref_cruise: str | None = None
    rpm_cruise: float | None = None


@router.post("/sessions/{session_id}/vision/engine-sound")
async def vision_engine_sound(session_id: str, body: EngineSoundBody):
    """M5O §4: stdlib DSP screening of a phone engine recording.

    The WAV blob (5–8 s from the app, 3–10 s accepted) plus the live OBD
    rpm and the cylinder count (explicit, else parsed from the session
    vehicle — never guessed) runs the firing-dropout / narrowband-tone
    / broadband-hiss cascade. Appends ``evidence_added`` (type ``audio``)
    + ``observation_added``; the outcome reaches posteriors only when the
    caller submits it through the normal test-result route for
    ``engine_sound_screen``.
    """
    from mmc.vision import engine_sound as engine_sound_mod

    session = _require_session(session_id)
    data, content_hash = _load_blob(body.blob_ref)
    cruise: tuple[bytes, float] | None = None
    if body.blob_ref_cruise is not None and body.rpm_cruise:
        cruise_data, _cruise_hash = _load_blob(body.blob_ref_cruise)
        cruise = (cruise_data, float(body.rpm_cruise))
    cylinders = body.cylinders
    if cylinders is None:
        cylinders = engine_sound_mod.cylinders_from_vehicle(session.meta.get("vehicle"))
    try:
        result = engine_sound_mod.analyze(
            data,
            float(body.rpm),
            cylinders,
            cruise_wav=cruise[0] if cruise else None,
            cruise_rpm=cruise[1] if cruise else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"cannot analyze audio: {e}")
    registry = sess.get_registry()
    evidence = {
        "evidence_id": uuid.uuid4().hex,
        "type": "audio",
        "blob_ref": body.blob_ref,
        "content_hash": content_hash,
        "note": f"engine sound {result['outcome']} at {body.rpm:g} rpm",
        "ts": sess._utc_now(),
    }
    registry.append(session, "evidence_added", evidence)
    observation = engine_sound_mod.to_observation(result, evidence["evidence_id"])
    registry.append(session, "observation_added", observation)
    return {
        "evidence_id": evidence["evidence_id"],
        "outcome": result["outcome"],
        "confidence": result["confidence"],
        "details": result["details"],
        "observation": observation,
    }


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
