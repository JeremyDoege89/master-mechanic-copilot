"""M4 session registry: session lifecycle over the hash-chained event store.

Sessions live under ``DATA_DIR/sessions/<session_id>/`` (``meta.json`` plus
``events.jsonl`` with its SQLite index). Reports live under
``DATA_DIR/reports/<report_id>.json``. VINs are indexed by sha256 in
``DATA_DIR/vin_index.json`` — plaintext VINs never touch disk.

Derived state (posteriors, rankings, traces) is recomputed from the event log
on every read, never stored, so replay always reproduces identical numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc import __version__ as ENGINE_VERSION
from mmc.engine import bayes, planner, stoprule
from mmc.engine import trace as trace_mod
from mmc.kp.loader import PACKS_DIR, KnowledgePack, load_pack
from mmc.store import blobs, event_store
from mmc.store.event_store import EventStore
from mmc import vin as vin_mod

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "packages" / "schemas"

LEVELS = ("L1", "L2", "L3")
MODES = ("live", "sim", "replay", "upload")

# -- second-opinion views (M5O §2) -------------------------------------------
# Async corroboration results keyed by session then evidence id. Display-only:
# compute_state folds only test_result events, so posteriors are bit-identical
# with or without these views. In-memory (single-process demo path); a fresh
# keyframe call recomputes the merge deterministically.
_VISION_SECOND_OPINIONS: dict[str, dict[str, dict]] = {}


def note_vision_second_opinion(session_id: str, evidence_id: str, view: dict) -> None:
    """Stash a merged second-opinion view for one keyframe's evidence."""
    _VISION_SECOND_OPINIONS.setdefault(session_id, {})[evidence_id] = dict(view)


def vision_second_opinions(session_id: str) -> list[dict]:
    return [
        {"evidence_id": eid, **view}
        for eid, view in _VISION_SECOND_OPINIONS.get(session_id, {}).items()
    ]


# Deferred video overlays keyed by session then evidence id. Display-only,
# like second opinions: compute_state folds only test_result events, so
# posteriors are bit-identical with or without them. The upload route
# returns boxes right away; pool-converted masks land here when ready and
# reach the UI through the next WS state push.
_VIDEO_OVERLAYS: dict[str, dict[str, dict]] = {}
_VIDEO_OVERLAYS_LOCK = threading.Lock()


def note_video_overlays(session_id: str, evidence_id: str, view: dict) -> None:
    """Stash finished video overlays (masks) for one clip's evidence."""
    with _VIDEO_OVERLAYS_LOCK:
        _VIDEO_OVERLAYS.setdefault(session_id, {})[evidence_id] = dict(view)


def video_overlays(session_id: str) -> list[dict]:
    return [
        {"evidence_id": eid, **view}
        for eid, view in _VIDEO_OVERLAYS.get(session_id, {}).items()
    ]


def _schema_registry() -> Registry:
    pairs = []
    for schema_path in sorted(SCHEMAS_DIR.glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


@lru_cache(maxsize=1)
def _validators() -> dict:
    registry = _schema_registry()

    def _v(name: str) -> jsonschema.Draft202012Validator:
        schema = json.loads((SCHEMAS_DIR / name).read_text())
        return jsonschema.Draft202012Validator(schema, registry=registry)

    return {
        "vehicle": _v("vehicle.json"),
        "observation": _v("observation.json"),
        "evidence": _v("evidence.json"),
        "test_result": _v("test_result.json"),
        "test_performed": _v("test_performed.json"),
        "hypothesis": _v("hypothesis.json"),
        "next_test": _v("next_test.json"),
        "report": _v("report.json"),
    }


def validate(schema_name: str, payload: dict) -> None:
    _validators()[schema_name].validate(payload)


@lru_cache(maxsize=1)
def get_pack() -> KnowledgePack:
    return load_pack(PACKS_DIR / "ford_gen_v1.yaml")


def data_root() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def model_pins() -> dict:
    return {
        "spark": os.environ.get("MODEL_SPARK", "muse-spark-1.3"),
        "wonder": os.environ.get("MODEL_WONDER", "muse-wonder-1.0"),
        "sam": os.environ.get("MODEL_SAM", "sam-3.1"),
        "asr": os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0"),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self, session_id: str, meta: dict, store: EventStore) -> None:
        self.id = session_id
        self.meta = meta
        self.store = store


class SessionRegistry:
    """Sessions rooted at one DATA_DIR; registries are cached per directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        (root / "reports").mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}

    # -- lookup ------------------------------------------------------------
    def get(self, session_id: str) -> Session | None:
        if session_id in self._sessions:
            return self._sessions[session_id]
        meta_path = self.root / "sessions" / session_id / "meta.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        store = EventStore(self.root / "sessions" / session_id / "events.jsonl")
        session = Session(session_id, meta, store)
        self._sessions[session_id] = session
        return session

    # -- creation ----------------------------------------------------------
    def create(
        self,
        user_level: str,
        tools: list[str],
        mode: str,
        vehicle: dict | None,
    ) -> Session:
        if user_level not in LEVELS:
            raise ValueError(f"bad user_level {user_level!r}")
        if mode not in MODES:
            raise ValueError(f"bad mode {mode!r}")
        pack = get_pack()
        session_id = uuid.uuid4().hex
        redacted_vehicle: dict | None = None
        vin_sha: str | None = None
        if vehicle:
            redacted_vehicle = {k: v for k, v in vehicle.items() if k != "vin"}
            if vehicle.get("vin"):
                full_vin = str(vehicle["vin"])
                redacted_vehicle["vin_encrypted"] = vin_mod.encrypt_vin(full_vin)
                redacted_vehicle["vin_last6"] = vin_mod.last6(full_vin)
                vin_sha = vin_mod.sha256_vin(full_vin)
            validate("vehicle", redacted_vehicle)
        meta = {
            "session_id": session_id,
            "created_ts": _utc_now(),
            "user_level": user_level,
            "tools": list(tools),
            "mode": mode,
            "model_pins": model_pins(),
            "kp_version": pack.version,
            "engine_version": ENGINE_VERSION,
            "vehicle": redacted_vehicle,
            "vin_sha256": vin_sha,
        }
        session_dir = self.root / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "meta.json").write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
        store = EventStore(session_dir / "events.jsonl")
        session = Session(session_id, meta, store)
        self._sessions[session_id] = session
        if vin_sha:
            self._index_vin(vin_sha, session_id)
        session_started_payload = {
            k: v for k, v in meta.items() if k not in ("vin_sha256",)
        }
        store.append("session_started", session_started_payload)
        return session

    def _index_vin(self, vin_sha: str, session_id: str) -> None:
        index_path = self.root / "vin_index.json"
        index: dict = {}
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        ids = index.setdefault(vin_sha, [])
        if session_id not in ids:
            ids.append(session_id)
        index_path.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")

    # -- events ------------------------------------------------------------
    def append(self, session: Session, type: str, payload: dict) -> dict:
        if type == "observation_added":
            validate("observation", payload)
        elif type == "evidence_added":
            validate("evidence", payload)
        return session.store.append(type, payload)

    def event_log_path(self, session: Session) -> Path:
        return self.root / "sessions" / session.id / "events.jsonl"


_REGISTRIES: dict[str, SessionRegistry] = {}


def get_registry() -> SessionRegistry:
    root = data_root()
    key = str(root.resolve()) if root.exists() else str(root)
    if key not in _REGISTRIES:
        _REGISTRIES[key] = SessionRegistry(root)
    return _REGISTRIES[key]


# -- derived state ----------------------------------------------------------

def _fmt_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def compute_state(session: Session) -> dict:
    """Fold the event log into hypotheses, rankings, and the test record."""
    pack = get_pack()
    meta = session.meta
    posteriors = {h: spec["prior"] for h, spec in pack.hypotheses.items()}
    histories: dict[str, list[dict]] = {h: [] for h in posteriors}
    traces: dict[str, list[dict]] = {h: [] for h in posteriors}
    trace_seq: dict[str, int] = {h: 0 for h in posteriors}
    acked: set[str] = set()
    results: list[dict] = []
    update_seq = 0

    for event in session.store.iter_replay():
        payload = event["payload"]
        if event["type"] == "safety_ack" and payload.get("gate"):
            acked.add(str(payload["gate"]))
        elif event["type"] == "test_result":
            test_ref = payload["test_id"]
            outcome = payload["outcome"]
            test = pack.tests.get(test_ref)
            if test is None:
                continue
            before = dict(posteriors)
            posteriors = bayes.update(posteriors, outcome, test.likelihoods, len(test.outcomes))
            update_seq += 1
            expected = payload.get("expected", {})
            measured = payload.get("measured", {})
            exp_str = str(expected.get("outcome", expected)) if isinstance(expected, dict) else str(expected)
            meas_value = measured.get("value", measured) if isinstance(measured, dict) else measured
            for hyp in posteriors:
                trace_seq[hyp] += 1
                traces[hyp].append(
                    trace_mod.make_entry(
                        seq=trace_seq[hyp],
                        evidence_id=str(payload.get("result_id", "")),
                        test_id=test_ref,
                        prior=before[hyp],
                        posterior=posteriors[hyp],
                        outcome=outcome,
                        likelihoods=test.likelihoods,
                        hyp=hyp,
                        posteriors_before=before,
                        n_outcomes=len(test.outcomes),
                        lr_source=test.evaluator or "kp",
                        expected=exp_str,
                        measured=_fmt_value(meas_value),
                        text_l1=f"{pack.hypotheses[hyp]['label']['L1']}: {outcome}",
                        text_l3=f"{pack.hypotheses[hyp]['label']['L3']}: {outcome}",
                    )
                )
                histories[hyp].append(
                    {"seq": update_seq, "p": posteriors[hyp], "cause_event": str(payload.get("result_id", ""))}
                )
            results.append(payload)

    performed = {str(r["test_id"]) for r in results}
    ranked, blocked = planner.rank_tests(
        pack, posteriors, meta["user_level"], list(meta["tools"]), acked, performed
    )
    next_tests = []
    for rec in ranked:
        entry = {
            "rank": rec["rank"],
            "kp_test_ref": rec["kp_test_ref"],
            "kind": pack.tests[rec["kp_test_ref"]].kind,
            "eig_bits": rec["eig_bits"],
            "cost": rec["cost"],
            "score": rec["score"],
            "why": rec["why"],
            "expected_outcomes": rec["expected_outcomes"],
            "required_tools": rec["required_tools"],
            "min_level": rec["min_level"],
            "safety_gate": rec["safety_gate"],
            "blocked_reasons": [],
        }
        next_tests.append(entry)

    hypotheses = []
    for hyp, current in sorted(posteriors.items(), key=lambda kv: kv[1], reverse=True):
        hypotheses.append(
            {
                "hypothesis_id": hyp,
                "label": pack.hypotheses[hyp]["label"],
                "origin": "kp",
                "prior": pack.hypotheses[hyp]["prior"],
                "current": current,
                "history": histories[hyp],
                "trace": traces[hyp],
            }
        )

    return {
        "session_id": session.id,
        "vehicle": session.meta.get("vehicle"),
        "vision_second_opinions": vision_second_opinions(session.id),
        "video_overlays": video_overlays(session.id),
        "hypotheses": hypotheses,
        "next_tests": next_tests,
        "blocked_tests": [
            {"kp_test_ref": b["kp_test_ref"], "blocked_reasons": b["blocked_reasons"]} for b in blocked
        ],
        "missing_evidence": planner.missing_evidence(pack, posteriors, performed),
        "tests_log": [
            {
                "test_ref": str(r["test_id"]),
                "outcome": str(r["outcome"]),
                "expected": r.get("expected", {}),
                "measured": r.get("measured", {}),
                "evaluator": str(r.get("evaluator", "")),
                "evidence_ids": list(r.get("evidence_ids", [])),
                "result_id": str(r.get("result_id", "")),
            }
            for r in results
        ],
    }


def should_stop_from_state(state: dict) -> bool:
    top_p = state["hypotheses"][0]["current"] if state["hypotheses"] else 0.0
    best_eig = state["next_tests"][0]["eig_bits"] if state["next_tests"] else None
    return stoprule.should_stop(top_p, best_eig)


# -- reports -----------------------------------------------------------------

def _prompts_sha() -> str:
    # M4 performs no LLM calls; pin the no-prompt marker for reproducibility.
    return hashlib.sha256(b"m4-no-llm-prompts").hexdigest()


def build_report(session: Session, state: dict) -> dict:
    top = state["hypotheses"][0]
    top_label_l1 = top["label"]["L1"]
    n_tests = len(state["tests_log"])
    concluded = should_stop_from_state(state)
    if concluded:
        conclusion = (
            f"Most likely: {top_label_l1} "
            f"({top['hypothesis_id']}, p={top['current']:.2f} after {n_tests} test(s))."
        )
    else:
        suspects = ", ".join(
            f"{h['label']['L1']} (p={h['current']:.2f})" for h in state["hypotheses"][:3]
        )
        conclusion = (
            f"Not enough evidence yet — no conclusion after {n_tests} test(s). "
            f"Current suspects: {suspects}. Run the recommended tests first."
        )
    evidence_index: list[str] = []
    for entry in state["tests_log"]:
        for eid in entry["evidence_ids"]:
            if eid not in evidence_index:
                evidence_index.append(eid)
    gates = sorted(
        {t.get("safety_gate", "none") for t in state["next_tests"]} |
        {b.get("safety_gate", "none") for b in state.get("blocked_tests", [])}
        - {"none"}
    )
    safety_notes = (
        [f"Safety gate acknowledged during session: {g}." for g in gates]
        or ["No safety-gated tests were run in this session."]
    )
    report = {
        "report_id": uuid.uuid4().hex,
        "session_id": session.id,
        "conclusion": conclusion,
        "summary_by_level": {
            "L1": conclusion,
            "L2": (
                f"{top_label_l1} suspected (p={top['current']:.2f}); "
                f"{n_tests} test(s) performed."
            ) if concluded else conclusion,
            "L3": (
                f"{top['hypothesis_id']} posterior {top['current']:.3f} "
                f"({top_label_l1} / {top['label']['L3']}) after {n_tests} test(s)."
            ) if concluded else conclusion,
        },
        "hypotheses_final": state["hypotheses"],
        "tests": [
            {"test_ref": e["test_ref"], "outcome": e["outcome"]} for e in state["tests_log"]
        ],
        "evidence_index": evidence_index,
        "safety_notes": safety_notes,
        "reproducibility": {
            "engine_version": ENGINE_VERSION,
            "kp_version": session.meta["kp_version"],
            "log_head_hash": session.store.head_hash(),
            "model_pins": session.meta["model_pins"],
            "prompts_sha256": _prompts_sha(),
        },
    }
    validate("report", report)
    return report


def save_report(registry: SessionRegistry, report: dict) -> Path:
    path = registry.root / "reports" / f"{report['report_id']}.json"
    path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return path


def load_report(registry: SessionRegistry, report_id: str) -> dict | None:
    path = registry.root / "reports" / f"{report_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["blobs"]
