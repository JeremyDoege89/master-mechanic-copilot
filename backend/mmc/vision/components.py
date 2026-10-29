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

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc._paths import schemas_dir

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
    for schema_path in sorted(schemas_dir().glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((schemas_dir() / "vision_components.json").read_text())
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


# -- second opinion (M5O §2) -------------------------------------------------
# Agreement only, never probabilities: the merge changes confidence display
# (max when corroborated) plus agreement/sources labels. Posteriors move only
# via test_result events, so compute_state numbers are bit-identical with or
# without a second opinion.

PRIMARY_SOURCE = "spark"
SECOND_SOURCE = "spark_open"

AGREEMENTS = ("corroborated", "single_source", "disputed")


def _concept_slug(comp: dict) -> str:
    return _slug(str(comp.get("sam_concept") or comp.get("name") or ""))


def _anomaly_key(anom: dict) -> tuple[str, str]:
    return (_slug(str(anom.get("type", ""))), _slug(str(anom.get("region_hint", ""))))


def merge_opinions(primary: dict, second: dict | None) -> tuple[dict, list[dict]]:
    """Merge two validated ``vision_components`` dicts on agreement.

    Components key on the normalised ``sam_concept`` slug: named by both
    sides → ``corroborated`` (confidence is the max); named by one side
    only → ``single_source``. Anomalies key on (type, region) slugs: an
    entry sharing a region slug with a different-typed entry on the other
    side → ``disputed``.

    Returns ``(merged, disagreements)`` where ``merged`` carries per-entry
    ``agreement`` + ``sources`` and ``disagreements`` is
    ``[{name, named_by}]`` for one-side-only components. ``second=None``
    (disabled/timed-out Open) marks every primary entry single-source.
    """
    prim_comps = list(primary.get("components", []))
    sec_comps = list((second or {}).get("components", []))
    sec_by_slug: dict[str, dict] = {}
    for comp in sec_comps:
        sec_by_slug.setdefault(_concept_slug(comp), comp)
    merged_comps: list[dict] = []
    disagreements: list[dict] = []
    for comp in prim_comps:
        slug = _concept_slug(comp)
        other = sec_by_slug.pop(slug, None)
        entry = dict(comp)
        if other is not None:
            entry["confidence"] = max(float(comp["confidence"]), float(other["confidence"]))
            entry["agreement"] = "corroborated"
            entry["sources"] = [PRIMARY_SOURCE, SECOND_SOURCE]
        else:
            entry["agreement"] = "single_source"
            entry["sources"] = [PRIMARY_SOURCE]
            disagreements.append({"name": str(comp.get("name", "")), "named_by": PRIMARY_SOURCE})
        merged_comps.append(entry)
    for slug, comp in sec_by_slug.items():
        entry = dict(comp)
        entry["agreement"] = "single_source"
        entry["sources"] = [SECOND_SOURCE]
        merged_comps.append(entry)
        disagreements.append({"name": str(comp.get("name", "")), "named_by": SECOND_SOURCE})

    prim_anoms = list(primary.get("anomalies", []))
    sec_anoms = list((second or {}).get("anomalies", []))
    sec_by_key: dict[tuple[str, str], dict] = {}
    for anom in sec_anoms:
        sec_by_key.setdefault(_anomaly_key(anom), anom)
    sec_regions: dict[str, list[dict]] = {}
    for anom in sec_anoms:
        sec_regions.setdefault(_anomaly_key(anom)[1], []).append(anom)
    prim_regions: dict[str, list[dict]] = {}
    for anom in prim_anoms:
        prim_regions.setdefault(_anomaly_key(anom)[1], []).append(anom)
    merged_anoms: list[dict] = []
    for anom in prim_anoms:
        key = _anomaly_key(anom)
        other = sec_by_key.pop(key, None)
        entry = dict(anom)
        if other is not None:
            entry["confidence"] = max(float(anom["confidence"]), float(other["confidence"]))
            entry["agreement"] = "corroborated"
            entry["sources"] = [PRIMARY_SOURCE, SECOND_SOURCE]
        elif any(
            _anomaly_key(o)[0] != key[0] for o in sec_regions.get(key[1], []) if key[1]
        ):
            entry["agreement"] = "disputed"
            entry["sources"] = [PRIMARY_SOURCE]
        else:
            entry["agreement"] = "single_source"
            entry["sources"] = [PRIMARY_SOURCE]
        merged_anoms.append(entry)
    for key, anom in sec_by_key.items():
        entry = dict(anom)
        if any(_anomaly_key(o)[0] != key[0] for o in prim_regions.get(key[1], []) if key[1]):
            entry["agreement"] = "disputed"
        else:
            entry["agreement"] = "single_source"
        entry["sources"] = [SECOND_SOURCE]
        merged_anoms.append(entry)
    return (
        {"components": merged_comps, "anomalies": merged_anoms},
        disagreements,
    )


def _single_source(parsed: dict, source: str = PRIMARY_SOURCE) -> dict:
    """Label every entry of one opinion ``single_source`` (Open unavailable)."""
    out = {"components": [], "anomalies": []}
    for comp in parsed.get("components", []):
        entry = dict(comp)
        entry["agreement"] = "single_source"
        entry["sources"] = [source]
        out["components"].append(entry)
    for anom in parsed.get("anomalies", []):
        entry = dict(anom)
        entry["agreement"] = "single_source"
        entry["sources"] = [source]
        out["anomalies"].append(entry)
    return out


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
        obs = {
            "observation_id": uuid.uuid4().hex,
            "feature": f"visual_component:{_slug(comp['name'])}",
            "value": 1.0,
            "confidence": float(comp["confidence"]),
            "extractor": EXTRACTOR,
            "evidence_ids": [evidence_id],
            "ts": ts,
        }
        if comp.get("agreement") in AGREEMENTS:
            obs["agreement"] = comp["agreement"]
            obs["sources"] = list(comp.get("sources") or [])
        observations.append(obs)
    for anom in parsed.get("anomalies", []):
        obs = {
            "observation_id": uuid.uuid4().hex,
            "feature": f"visual_anomaly:{anom['type']}",
            "value": anom.get("region_hint", ""),
            "confidence": float(anom["confidence"]),
            "extractor": EXTRACTOR,
            "evidence_ids": [evidence_id],
            "ts": ts,
        }
        if anom.get("agreement") in AGREEMENTS:
            obs["agreement"] = anom["agreement"]
            obs["sources"] = list(anom.get("sources") or [])
        observations.append(obs)
    return observations
