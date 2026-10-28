"""M5 Spark component JSON -> typed Observations (plan step 7).

The LLM proposes, the engine decides: this module schema-validates the
Spark output, strips anything resembling a probability (defence in depth —
the engine ignores observations for posteriors anyway, see
``mmc.sessions.compute_state``), and emits ``observation``-schema dicts
through the normal ``observation_added`` event path.
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

# Any of these keys anywhere in LLM output means it tried to set/compare
# probabilities. Reject loudly — only engine/ changes posteriors.
BANNED_KEYS = frozenset(
    {
        "prior",
        "current",
        "posterior",
        "probability",
        "probabilities",
        "likelihood",
        "likelihood_ratio",
        "posteriors",
    }
)

EXTRACTOR = "spark:components_v1"


def _registry() -> Registry:
    pairs = []
    for schema_path in sorted(SCHEMAS_DIR.glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / "vision_components.json").read_text())
    return jsonschema.Draft202012Validator(schema, registry=_registry())


def _scan_banned(obj: object, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in BANNED_KEYS:
                raise ValueError(f"LLM output sets probability field {path}.{key}: refusing")
            _scan_banned(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_banned(value, f"{path}[{i}]")


def _slug(name: str) -> str:
    slug = "".join(c if (c.isalnum() or c == "_") else "_" for c in name.strip().lower())
    return "_".join(s for s in slug.split("_") if s) or "component"


def parse_components(raw: dict) -> dict:
    """Validate Spark output and reject probability fields. Returns it unchanged."""
    _validator().validate(raw)
    _scan_banned(raw)
    return raw


def to_observations(parsed: dict, evidence_id: str) -> list[dict]:
    """Map validated components/anomalies to ``observation``-schema dicts.

    Feature vocabulary (provisional KP extension, reconciled after the live
    spike): ``visual_component:<slug>`` with value 1.0 (seen), and
    ``visual_anomaly:<type>`` with the anomaly type as value. Confidence
    passes through from Spark. No posteriors are touched.
    """
    ts = datetime.now(timezone.utc).isoformat()
    observations: list[dict] = []
    for comp in parsed.get("components", []):
        observations.append(
            {
                "observation_id": uuid.uuid4().hex,
                "feature": f"visual_component:{_slug(comp['name'])}",
                "value": 1.0,
                "confidence": float(comp["confidence"]),
                "extractor": EXTRACTOR,
                "evidence_ids": [evidence_id],
                "ts": ts,
            }
        )
    for anom in parsed.get("anomalies", []):
        observations.append(
            {
                "observation_id": uuid.uuid4().hex,
                "feature": f"visual_anomaly:{anom['type']}",
                "value": anom.get("region_hint", ""),
                "confidence": float(anom["confidence"]),
                "extractor": EXTRACTOR,
                "evidence_ids": [evidence_id],
                "ts": ts,
            }
        )
    return observations
