"""M5O resilience fallback: text + image only (never SAM, never voice).

When a Meta Model API call fails AFTER the client's own 429 retries
(``BudgetExceeded`` from the pre-call guard, or ``MetaClientError`` for
5xx/timeout/auth-shape failures — non-429 errors already raise in
``meta/client.py``, so no client change was needed), retry the same job
once on Spark Open via Baseten. The returned record carries
``provider:"baseten"`` plus ``fallback:{from:"meta", reason, detail}`` in
the ``llm_call`` event so the audit trail says the answer came from the
fallback path.

SAM (``meta/sam.py`` streaming + parser grammar) and Voice Transcribe
(there is no ``meta/voice.py`` yet — nothing to substitute) get NO
fallback and keep raising.
"""

from __future__ import annotations

from typing import Any, Callable

from .budget import BudgetExceeded
from .client import MetaClientError

FALLBACK_KINDS = ("text", "image")


def call_text_or_image(
    primary_fn: Callable[[], tuple[Any, dict]],
    open_fn: Callable[[], tuple[Any, dict]],
    *,
    kind: str,
) -> tuple[Any, dict]:
    """Run ``primary_fn``; on Meta budget/error run ``open_fn`` once.

    Both callables return ``(value, key-free record)``. The fallback
    record is annotated with ``provider`` + ``fallback`` markers. Only
    ``BudgetExceeded`` (reason ``"budget"``) and ``MetaClientError``
    (reason ``"error"``) trigger the fallback; anything else (bad output
    shape, validation failure, programming error) propagates.
    """
    if kind not in FALLBACK_KINDS:
        raise ValueError(f"fallback kind must be one of {FALLBACK_KINDS}, not {kind!r}")
    try:
        return primary_fn()
    except BudgetExceeded as exc:
        reason = "budget"
        detail = exc
    except MetaClientError as exc:
        reason = "error"
        detail = exc
    value, record = open_fn()
    record = dict(record)
    record["provider"] = "baseten"
    marker = dict(record.get("fallback") or {})
    marker.update({"from": "meta", "reason": reason, "detail": str(detail)[:200]})
    record["fallback"] = marker
    return value, record
