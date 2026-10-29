"""Public (Modal) deployment guards (M6 §5 + M6 fix 3).

``MMC_PUBLIC_MODE=replay`` (the Modal default; local dev leaves it unset
and is unaffected) turns the backend into a replay-only demo:

- only ``replay``/``upload`` sessions may be created;
- ``REPLAY_USE_CACHED_LLM`` is forced on (replay/rebuild spend $0);
- paid vision (keyframe/instrument), PDF spec extraction and video
  analysis answer 501 while the demo budget is off;
- Wonder stays off in public (reports ship with ``wonder_review: null``).

Optional judge live demo (OFF by default): set
``MMC_PUBLIC_LIVE_BUDGET_USD`` above 0 and public sessions may run **photo
keyframe analysis and instrument reading only**, billed to a separate
process-wide tracker capped at that amount, with a per-IP rate limit
(``PUBLIC_PAID_CALLS_PER_HOUR``, default 10/hour). The web budget badge
shows the public cap. Wonder, SAM video and video uploads stay off.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from fastapi import HTTPException

from mmc.meta.budget import BudgetTracker

PUBLIC_MODE_ENV = "MMC_PUBLIC_MODE"
PUBLIC_LIVE_BUDGET_ENV = "MMC_PUBLIC_LIVE_BUDGET_USD"
PUBLIC_RATE_ENV = "PUBLIC_PAID_CALLS_PER_HOUR"

PUBLIC_PAID_CALLS_PER_HOUR = 10
PUBLIC_MODES = ("live", "sim", "replay", "upload")


def is_public() -> bool:
    """True in the public replay-only deployment (Modal default)."""
    return os.environ.get(PUBLIC_MODE_ENV, "") == "replay"


def public_live_budget_usd() -> float:
    """Demo cap for photo keyframe/instrument calls (0 = demo off)."""
    try:
        return max(0.0, float(os.environ.get(PUBLIC_LIVE_BUDGET_ENV, "0") or 0))
    except ValueError:
        return 0.0


def public_rate_limit_per_hour() -> int:
    try:
        return max(1, int(os.environ.get(PUBLIC_RATE_ENV, "") or PUBLIC_PAID_CALLS_PER_HOUR))
    except ValueError:
        return PUBLIC_PAID_CALLS_PER_HOUR


def enforce_public_defaults() -> None:
    """Force cache-only LLM use in public (no-op locally). Called at startup."""
    if is_public():
        os.environ.setdefault("REPLAY_USE_CACHED_LLM", "true")


# -- separate public demo tracker (never the Meta/Baseten ledgers) --------------

_PUBLIC_TRACKER: BudgetTracker | None = None
_PUBLIC_TRACKER_LOCK = threading.Lock()


def get_public_tracker() -> BudgetTracker:
    """Process-wide demo tracker, capped at the live-demo budget."""
    global _PUBLIC_TRACKER
    with _PUBLIC_TRACKER_LOCK:
        if _PUBLIC_TRACKER is None:
            _PUBLIC_TRACKER = BudgetTracker(hard_stop_usd=public_live_budget_usd())
        # The cap is env-driven; follow it if the owner raises/lowers it.
        _PUBLIC_TRACKER.hard_stop_usd = public_live_budget_usd()
        return _PUBLIC_TRACKER


# -- per-IP rate limit for paid public calls -------------------------------------

_RATE: dict[str, deque[float]] = {}
_RATE_LOCK = threading.Lock()


def check_public_rate(ip: str, now: float | None = None) -> None:
    """Allow ``PUBLIC_PAID_CALLS_PER_HOUR`` paid calls/hour per IP (else 429)."""
    at = now if now is not None else time.time()
    window = 3600.0
    limit = public_rate_limit_per_hour()
    with _RATE_LOCK:
        calls = _RATE.setdefault(ip or "unknown", deque())
        while calls and calls[0] <= at - window:
            calls.popleft()
        if len(calls) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"public demo rate limit: {limit} paid calls/hour per IP",
            )
        calls.append(at)


# -- route guards -----------------------------------------------------------------

PUBLIC_SESSION_MODES = ("replay", "upload")


def require_public_session_mode(mode: str) -> None:
    """Reject non-demo session modes in public (local dev unaffected)."""
    if is_public() and mode not in PUBLIC_SESSION_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"public demo supports modes {list(PUBLIC_SESSION_MODES)} (not {mode!r})",
        )


def vision_allowance(*, live: bool, ip: str) -> BudgetTracker | None:
    """Public guard for photo keyframe/instrument routes.

    Returns None when the call proceeds on the default ledgers (local dev,
    or an offline fixture analyzer). Returns the public demo tracker when a
    real paid call is covered by the demo budget. Raises 501 while the demo
    is off and 429 past the per-IP rate.
    """
    if not is_public() or not live:
        return None
    cap = public_live_budget_usd()
    if cap <= 0:
        raise HTTPException(
            status_code=501,
            detail="paid vision is disabled in the public replay-only demo",
        )
    check_public_rate(ip or "unknown")
    return get_public_tracker()


def require_public_upload_paid() -> None:
    """PDF spec extraction and video analysis stay off in public."""
    if is_public():
        raise HTTPException(
            status_code=501,
            detail="paid upload processing is disabled in the public replay-only demo",
        )
