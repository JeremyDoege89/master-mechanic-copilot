"""Report builder (M6 §3): reproducible JSON over the event log.

Moved out of ``sessions.py`` (which keeps a delegating ``build_report``
for compatibility). Every number comes from the engine fold; the LLM
(Wonder) only appends a labelled review and never touches posteriors.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc import __version__ as ENGINE_VERSION
from mmc import levels as levels_mod
from mmc._paths import prompts_dir, schemas_dir

AI_EXTRACTOR_PREFIXES = ("spark", "muse-")


def _registry() -> Registry:
    pairs = []
    for schema_path in sorted(schemas_dir().glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


def _validate_report(report: dict) -> None:
    schema = json.loads((schemas_dir() / "report.json").read_text())
    jsonschema.Draft202012Validator(schema, registry=_registry()).validate(report)


def prompts_sha256() -> str:
    """sha256 over the sorted pinned prompt files (replaces m4-no-llm-prompts)."""
    digest = hashlib.sha256()
    for path in sorted(prompts_dir().glob("*.md")):
        digest.update(path.name.encode("utf-8") + b"\x00" + path.read_bytes())
    return digest.hexdigest()


def model_pins() -> dict:
    import os

    return {
        "spark": os.environ.get("MODEL_SPARK", "muse-spark-1.3"),
        "wonder": os.environ.get("MODEL_WONDER", "muse-wonder-1.0"),
        "sam": os.environ.get("MODEL_SAM", "sam-3.1"),
        "asr": os.environ.get("MODEL_ASR", "muse-voice-transcribe-1.0"),
        "spark_open": os.environ.get("MODEL_SPARK_OPEN", "meta-models/muse-spark-open-1026"),
    }


def row_origin(evaluator: str) -> str:
    """How a test row was judged: DSP signal, human look, or determinism."""
    if evaluator == "dsp.engine_sound":
        return "signal_processing"
    if evaluator in ("visual.direct", "sensory.direct"):
        return "direct_observation"
    return "deterministic"


def build_report(session: Any, state: dict) -> dict:
    """Build the full report dict from a session and its folded state."""
    from mmc.engine import stoprule as stoprule_mod

    top = state["hypotheses"][0]
    top_label_l1 = top["label"]["L1"]
    n_tests = len(state["tests_log"])
    top_p = top["current"]
    best_eig = state["next_tests"][0]["eig_bits"] if state["next_tests"] else None
    concluded = stoprule_mod.should_stop(top_p, best_eig)
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
    suspects = ", ".join(
        f"{h['label']['L1']} (p={h['current']:.2f})" for h in state["hypotheses"][:3]
    )
    summary_kwargs = dict(
        concluded=concluded,
        conclusion=conclusion,
        top_id=top["hypothesis_id"],
        top_label=top["label"],
        top_p=top["current"],
        n_tests=n_tests,
        suspects=suspects,
    )

    evidence_index: list[str] = []
    for entry in state["tests_log"]:
        for eid in entry["evidence_ids"]:
            if eid not in evidence_index:
                evidence_index.append(eid)

    # Verbatim expected-vs-measured rows + evidence labelling from the log.
    test_results: list[dict] = []
    evidence_docs: dict[str, dict] = {}
    observation_docs: list[dict] = []
    llm_records: list[dict] = []
    for event in session.store.iter_replay():
        payload = event["payload"]
        if event["type"] == "test_result":
            test_results.append(payload)
        elif event["type"] == "evidence_added":
            evidence_docs[str(payload.get("evidence_id", ""))] = payload
        elif event["type"] == "observation_added":
            observation_docs.append(payload)
        elif event["type"] in ("llm_call", "sam_call"):
            llm_records.append(payload)

    expected_vs_measured = [
        {
            "test_ref": str(r.get("test_id", "")),
            "expected": r.get("expected", {}),
            "measured": r.get("measured", {}),
            "outcome": str(r.get("outcome", "")),
            "evaluator": str(r.get("evaluator", "")),
            "origin": row_origin(str(r.get("evaluator", ""))),
        }
        for r in test_results
    ]

    by_evidence: dict[str, list[dict]] = {}
    for obs in observation_docs:
        for eid in obs.get("evidence_ids", []) or []:
            by_evidence.setdefault(str(eid), []).append(obs)
    evidence_rows = []
    for eid, doc in evidence_docs.items():
        linked = by_evidence.get(eid, [])
        extractors = sorted({str(o.get("extractor", "")) for o in linked if o.get("extractor")})
        ai = any(e.startswith(AI_EXTRACTOR_PREFIXES) for e in extractors)
        agreements = sorted({str(o["agreement"]) for o in linked if o.get("agreement")})
        sources: list[str] = []
        for o in linked:
            for s in o.get("sources", []) or []:
                if s not in sources:
                    sources.append(str(s))
        row: dict[str, Any] = {
            "evidence_id": eid,
            "type": str(doc.get("type", "")),
            "ai_extracted": ai,
            "extractor": ";".join(extractors) if extractors else None,
            "agreement": agreements[0] if agreements else None,
            "sources": sources,
        }
        evidence_rows.append(row)
        if eid not in evidence_index:
            evidence_index.append(eid)

    fallback_used = any(
        isinstance(rec.get("fallback"), dict) and rec["fallback"].get("from") == "meta"
        for rec in llm_records
    )

    sam_overlays = [
        {
            "evidence_id": eid,
            "blob_ref": str(doc.get("blob_ref", "")),
            "kind": "image",
            "note": "SAM overlays for this keyframe are served with the vision response.",
        }
        for eid, doc in evidence_docs.items()
        if doc.get("type") == "image" and eid in by_evidence
    ]

    gates = sorted(
        {t.get("safety_gate", "none") for t in state["next_tests"]}
        | {b.get("safety_gate", "none") for b in state.get("blocked_tests", [])}
        - {"none"}
    )
    safety_notes = (
        [f"Safety gate acknowledged during session: {g}." for g in gates]
        or ["No safety-gated tests were run in this session."]
    )
    stop_triggers = list(state.get("stop_and_see_pro", {}).get("triggers", []))
    if stop_triggers:
        safety_notes = safety_notes + [
            "Stop-and-see-a-pro trigger fired: " + "; ".join(stop_triggers)
        ]

    vehicle = (session.meta.get("vehicle") or {}) if isinstance(session.meta, dict) else {}

    report = {
        "report_id": uuid.uuid4().hex,
        "session_id": session.id,
        "conclusion": conclusion,
        "summary_by_level": {
            level: levels_mod.summary(level, **summary_kwargs) for level in ("L1", "L2", "L3")
        },
        "hypotheses_final": state["hypotheses"],
        "tests": [
            {"test_ref": e["test_ref"], "outcome": e["outcome"]} for e in state["tests_log"]
        ],
        "expected_vs_measured": expected_vs_measured,
        "evidence": evidence_rows,
        "evidence_index": evidence_index,
        "fallback_used": fallback_used,
        "second_opinions": state.get("vision_second_opinions", []),
        "sam_overlays": sam_overlays,
        "stop_triggers": stop_triggers,
        "safety_notes": safety_notes,
        "vin_last6": vehicle.get("vin_last6"),
        "wonder_review": None,
        "wonder_unavailable": None,
        "reproducibility": {
            "engine_version": ENGINE_VERSION,
            "kp_version": session.meta["kp_version"],
            "log_head_hash": session.store.head_hash(),
            "model_pins": session.meta.get("model_pins") or model_pins(),
            "prompts_sha256": prompts_sha256(),
        },
    }
    _validate_report(report)
    return report


def iter_events(session: Any) -> Iterable[dict]:
    return session.store.iter_replay()
