"""M5 instrument (meter/gauge) reading (plan step 8).

Spark returns ``{value, units, confidence, needs_retake?}``; this module
validates it and sets ``needs_retake`` authoritatively: retake when
confidence < 0.8 (the model's own flag is advisory and may be overridden).

A reading becomes Evidence ONLY after explicit user confirmation —
:func:`confirm_reading` builds the ``evidence_added`` + ``observation_added``
payloads, and the route appends them solely from the confirm endpoint.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "packages" / "schemas"

RETAKE_CONFIDENCE = 0.8
EXTRACTOR = "spark:instrument_v1"


def _validator() -> jsonschema.Draft202012Validator:
    pairs = []
    for schema_path in sorted(SCHEMAS_DIR.glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    registry = Registry().with_resources(pairs)
    schema = json.loads((SCHEMAS_DIR / "vision_instrument.json").read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry)


def parse_instrument(raw: dict) -> dict:
    """Validate a Spark meter reading; set needs_retake from confidence."""
    _validator().validate(raw)
    confidence = float(raw["confidence"])
    parsed = {
        "value": raw["value"],
        "units": str(raw["units"]),
        "confidence": confidence,
        "needs_retake": confidence < RETAKE_CONFIDENCE,
    }
    if "display_text" in raw:
        parsed["display_text"] = str(raw["display_text"])
    return parsed


def confirm_reading(
    reading: dict, blob_ref: str, content_hash: str, note: str = ""
) -> tuple[dict, dict]:
    """Build (evidence, observation) payloads for a user-confirmed reading.

    Raises if the reading needs a retake — confirm only sharp readings.
    """
    if reading.get("needs_retake"):
        raise ValueError("reading needs a retake; cannot confirm")
    ts = datetime.now(timezone.utc).isoformat()
    evidence_id = uuid.uuid4().hex
    evidence = {
        "evidence_id": evidence_id,
        "type": "measurement",
        "blob_ref": blob_ref,
        "content_hash": content_hash,
        "ts": ts,
    }
    if note:
        evidence["note"] = note
    observation = {
        "observation_id": uuid.uuid4().hex,
        "feature": "measurement:meter_reading",
        "value": reading["value"],
        "units": reading["units"],
        "confidence": float(reading["confidence"]),
        "extractor": EXTRACTOR,
        "evidence_ids": [evidence_id],
        "ts": ts,
    }
    return evidence, observation
