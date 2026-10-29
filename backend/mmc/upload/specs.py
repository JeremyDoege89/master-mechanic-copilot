"""PDF manual / scan-tool pages -> SpecRange -> expected-value source (M5U §2).

Spark transcribes the numeric rows; this module attaches the source blob
hash, validates the full ``spec_range`` schema, and builds the
``evidence_added`` payload (``evidence`` tolerates the extra
``spec_ranges`` key; no new event type). The expected-source wiring
resolves a ``spec_id`` from the session's ``manual_page`` evidence and
hands ``evaluator.evaluate`` a ``manual_spec`` so ``test_result.expected.
source.kind == "manual_page"`` wins over kp/physics per SPEC §5.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import jsonschema

from mmc._paths import schemas_dir

EXTRACTOR = "spark:spec_table_v1"


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((schemas_dir() / "spec_range.json").read_text())
    return jsonschema.Draft202012Validator(schema)


def attach_specs(
    ranges: list[dict],
    blob_ref: str,
    content_hash: str,
) -> list[dict]:
    """Fill extractor + blob hash and validate each range. Returns them."""
    validator = _validator()
    completed = []
    for item in ranges:
        full = dict(item)
        full["extractor"] = EXTRACTOR
        full["source_blob_sha256"] = content_hash
        validator.validate(full)
        completed.append(full)
    return completed


def build_evidence(blob_ref: str, content_hash: str, ranges: list[dict]) -> dict:
    """``evidence_added`` payload for a processed manual/scan PDF."""
    return {
        "evidence_id": uuid.uuid4().hex,
        "type": "manual_page",
        "blob_ref": blob_ref,
        "content_hash": content_hash,
        "spec_ranges": list(ranges),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def find_spec(session: Any, spec_id: str) -> dict | None:
    """Resolve ``spec_id`` from the session's ``manual_page`` evidence.

    Returns the completed range dict, or None when unknown.
    """
    for event in session.store.iter_replay():
        if event["type"] != "evidence_added":
            continue
        payload = event["payload"]
        if payload.get("type") != "manual_page":
            continue
        for item in payload.get("spec_ranges", []):
            if item.get("spec_id") == spec_id:
                return dict(item)
    return None


def manual_spec_for(spec: dict) -> dict:
    """Build the ``evaluator.evaluate`` manual_spec from a completed range."""
    out: dict = {
        "ref": f"p.{spec.get('page', '?')}",
        "content_hash": spec["source_blob_sha256"],
    }
    for key in ("min", "max", "units"):
        if spec.get(key) is not None:
            out[key] = spec[key]
    return out
