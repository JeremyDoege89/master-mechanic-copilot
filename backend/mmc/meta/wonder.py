"""Muse Wonder senior review (M6 §3): one call per report, gaps only.

Input is the finished report JSON plus the audit bundle the user approved
for M6: per-hypothesis explanation traces, the missing-evidence list,
blocked tests with reasons, second-opinion disagreements and any Spark
Open fallback use. Output is validated against ``wonder_review.json``
(gaps may point by ``test_ref``/``evidence_id``); probability-named keys
are refused like every other LLM output. Wonder never changes posteriors:
the review rides on the report, the engine numbers are untouched (tested
byte-identical before/after).

Exactly one ``muse-wonder-1.0`` call per report at ``EFFORT_WONDER``
(xhigh) through the shared Meta ``BudgetTracker``. The result is cached
via the ``llm_call`` disk cache and stored on the report, so replay and
rebuild cost $0. On ``BudgetExceeded``/API error the report still ships
with ``wonder_review: null`` plus an explicit unavailable line. Wonder
never falls back to Spark Open.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from mmc._paths import schemas_dir
from mmc.meta import budget as budget_mod
from mmc.meta.budget import BudgetExceeded
from mmc.meta.client import MetaClient, MetaClientError, NoCachedResponse, canonical_bytes
from mmc.meta.spark import reasoning_effort

WONDER_SYSTEM = (
    "You are a senior diagnostic technician reviewing a finished vehicle "
    "diagnosis. The probabilities were computed by a deterministic Bayesian "
    "engine — you do not set, adjust, or restate any probability. Read the "
    "report and its reasoning audit, then list reasoning GAPS only: tests "
    "that should have run, evidence that is missing or over-read, and "
    "alternative explanations the evidence still allows. Point at gaps with "
    "the KP test_ref that would close them and the evidence_id involved. "
    "Reply with JSON only: {gaps: [{issue, test_ref?, evidence_id?, "
    "rationale?}], narrative, agrees_with_top}."
)

# Any of these keys anywhere in Wonder output means it tried to touch
# probabilities. Refuse loudly — only engine/ changes posteriors.
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

WONDER_OUTPUT_TOKENS = 800


def _scan_banned(obj: Any, path: str = "$") -> None:
    # Finding #3: keys are lower-cased before the ban check, so
    # {"Posterior": 0.92} cannot slip past the probability ban.
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in BANNED_KEYS:
                raise ValueError(f"Wonder output sets probability field {path}.{key}: refusing")
            _scan_banned(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_banned(value, f"{path}[{i}]")


def _registry() -> Registry:
    pairs = []
    for schema_path in sorted(schemas_dir().glob("*.json")):
        schema = json.loads(schema_path.read_text())
        pairs.append(
            (schema_path.name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((schemas_dir() / "wonder_review.json").read_text())
    return jsonschema.Draft202012Validator(schema, registry=_registry())


def build_audit_bundle(report: dict, state: dict) -> dict:
    """Audit inputs Wonder needs beyond the finished report JSON (M6 fix 1)."""
    traces = {
        h["hypothesis_id"]: h.get("trace", []) for h in state.get("hypotheses", [])
    }
    disagreements = []
    for view in state.get("vision_second_opinions", []):
        for item in view.get("disagreements", []) or []:
            disagreements.append({"evidence_id": view.get("evidence_id"), **item})
    return {
        "traces_per_hypothesis": traces,
        "missing_evidence": list(state.get("missing_evidence", [])),
        "blocked_tests": [
            {
                "kp_test_ref": b.get("kp_test_ref"),
                "blocked_reasons": list(b.get("blocked_reasons", [])),
            }
            for b in state.get("blocked_tests", [])
        ],
        "second_opinion_disagreements": disagreements,
        "fallback_used": bool(report.get("fallback_used", False)),
    }


def _estimate_usd(audit_bytes: bytes) -> float:
    # ~4 bytes/token heuristic for the JSON audit bundle.
    return budget_mod.estimate_wonder(max(1, len(audit_bytes) // 4), WONDER_OUTPUT_TOKENS)


def request_review(audit: dict, client: MetaClient) -> tuple[dict, dict]:
    """One Wonder call; returns ``(validated_review, key-free record)``.

    Raises ``ValueError`` on schema or probability-key failure,
    ``BudgetExceeded``/``MetaClientError``/``NoCachedResponse`` from the client.
    """
    effort = reasoning_effort("wonder")
    audit_json = json.dumps(audit, sort_keys=True)
    request = {
        "op": "wonder.review.v1",
        "input": [
            {"role": "system", "content": WONDER_SYSTEM},
            {"role": "user", "content": "AUDIT:\n" + audit_json},
        ],
        "reasoning": {"effort": effort},
    }
    estimate = _estimate_usd(canonical_bytes(request))
    result, record = client.call(
        kind="llm_call",
        model_key="wonder",
        request=request,
        estimate_usd=estimate,
        response_kwargs={
            "input": request["input"],
            "reasoning": {"effort": effort},
        },
    )
    try:
        parsed = json.loads(result.output_text)
    except ValueError as e:
        raise ValueError(f"Wonder output is not JSON: {e}") from e
    _scan_banned(parsed)
    _validator().validate(parsed)
    return parsed, record


def attach_review(report: dict, audit: dict, client: MetaClient) -> dict:
    """Attach a validated Wonder review, or null + unavailable line.

    Exactly one live call per report: the review is stored on the report,
    so rebuilding an already-reviewed report costs $0 without touching the
    client, and replay mode serves the ``llm_call`` disk-cache entry ($0).
    Posteriors are never touched.
    """
    if report.get("wonder_review") is not None:
        return report
    try:
        review, _record = request_review(audit, client)
    except (BudgetExceeded, MetaClientError, NoCachedResponse) as e:
        report["wonder_review"] = None
        report["wonder_unavailable"] = f"AI review unavailable: {e}"
        return report
    except (ValueError, jsonschema.ValidationError) as e:
        # Finding #3: a banned-key or schema-invalid Wonder reply maps to
        # null + the unavailable line — it never kills the report build.
        report["wonder_review"] = None
        report["wonder_unavailable"] = f"AI review unavailable: invalid review: {e}"
        return report
    report["wonder_review"] = review
    report["wonder_unavailable"] = None
    return report


__all__ = ["attach_review", "build_audit_bundle", "request_review"]
