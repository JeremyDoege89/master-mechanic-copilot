"""M5 cost math for Meta Model API calls (prices per AGENTS.md).

Estimates gate calls *before* spend; actuals come from the ``usage`` numbers
the API returns on every response. Both flow through BudgetTracker, which
refuses new paid calls past BUDGET_USD_HARD_STOP.
"""

from __future__ import annotations

import os
import threading

# Prices (USD). Source: AGENTS.md "Meta client rules" / SPEC section 4.
SPARK_INPUT_PER_MTOK = 1.25
SPARK_CACHED_INPUT_PER_MTOK = 0.15
SPARK_OUTPUT_PER_MTOK = 4.25
WONDER_INPUT_PER_MTOK = 4.0
WONDER_OUTPUT_PER_MTOK = 20.0
SAM_IMAGE_PER_1K = 2.50
SAM_VIDEO_FRAME_PER_1K = 0.20
SAM_VIDEO_MIN_FRAMES = 50
ASR_PER_HOUR = 0.18
SEARCH_PER_1K = 2.50

DEFAULT_HARD_STOP_USD = 120.0


class BudgetExceeded(RuntimeError):
    """Raised when a new paid call would pass BUDGET_USD_HARD_STOP."""


def estimate_spark(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    fresh = max(0, input_tokens - cached_tokens)
    return (
        fresh / 1e6 * SPARK_INPUT_PER_MTOK
        + cached_tokens / 1e6 * SPARK_CACHED_INPUT_PER_MTOK
        + output_tokens / 1e6 * SPARK_OUTPUT_PER_MTOK
    )


def estimate_spark_open(input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    """Pre-call estimate for Spark Open via Baseten (Chat Completions).

    Same $1.25/M in / $4.25/M out ledger as Spark. Cached tokens bill at
    the full input rate until Baseten documents otherwise (see DECISIONS).
    """
    return (
        max(0, input_tokens) / 1e6 * SPARK_INPUT_PER_MTOK
        + max(0, output_tokens) / 1e6 * SPARK_OUTPUT_PER_MTOK
    )


def estimate_sam_images(n: int) -> float:
    return max(0, n) / 1000.0 * SAM_IMAGE_PER_1K


def estimate_sam_video(frames: int) -> float:
    return max(int(frames), SAM_VIDEO_MIN_FRAMES) / 1000.0 * SAM_VIDEO_FRAME_PER_1K


def estimate_asr(seconds: float) -> float:
    return max(0.0, seconds) / 3600.0 * ASR_PER_HOUR


def actual_cost_from_usage(model: str, usage: dict, provider: str = "meta") -> float:
    """Cost from the usage block the API returned (never from estimates).

    ``usage`` is the ``response.usage`` mapping: ``input_tokens``,
    ``output_tokens`` and (when the backend reports cache reuse)
    ``input_tokens_details.cached_tokens``. Unknown models cost 0.0 so a
    new model ID fails open on accounting, never on the spend guard: the
    guard always runs on the pre-call estimate.
    """
    model_l = (model or "").lower()
    if provider == "baseten" or "muse-spark-open" in model_l:
        # Baseten Chat Completions usage: prompt_tokens / completion_tokens
        # plus completion_tokens_details.reasoning_tokens and
        # prompt_tokens_details.cached_tokens. Reasoning tokens ride inside
        # completion_tokens (same output rate, never double-billed); cached
        # tokens bill at the full input rate until Baseten says otherwise.
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        if prompt == 0 and completion == 0:
            # Tolerate Responses-style keys on the same path.
            details = usage.get("input_tokens_details") or {}
            return estimate_spark_open(
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
                int(details.get("cached_tokens", 0) or 0),
            )
        return estimate_spark_open(prompt, completion)
    model = model_l
    if "wonder" in model:
        return (
            int(usage.get("input_tokens", 0)) / 1e6 * WONDER_INPUT_PER_MTOK
            + int(usage.get("output_tokens", 0)) / 1e6 * WONDER_OUTPUT_PER_MTOK
        )
    if "spark" in model:
        details = usage.get("input_tokens_details") or {}
        return estimate_spark(
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
            int(details.get("cached_tokens", 0) or 0),
        )
    if "sam" in model:
        # SAM bills per image / per frame, not per token. The verified
        # Responses usage block carries no token counts, only
        # ``images_processed`` (1 per image call in the live runs) or
        # ``video_frames_processed`` (75 on the live M5U clip), so cost
        # accounting uses those. Unknown shapes cost 0.0 and the caller
        # falls back to the pre-call estimate.
        images = usage.get("images_processed")
        if isinstance(images, (int, float)) and not isinstance(images, bool):
            return max(0.0, float(images)) / 1000.0 * SAM_IMAGE_PER_1K
        frames = usage.get("video_frames_processed")
        if isinstance(frames, (int, float)) and not isinstance(frames, bool):
            # 50-frame minimum is a floor (max), never a cap: 75 frames
            # bill 75, not 50.
            return estimate_sam_video(int(frames))
        return 0.0
    return 0.0


class BudgetTracker:
    """In-memory spend guard. One per process (or per test).

    Thread-safe: concurrent analyzer threads (fallback + second opinion)
    share one tracker, so ``check``/``add`` hold a lock — no torn
    read-modify-write on ``spend_usd``.
    """

    def __init__(self, hard_stop_usd: float = DEFAULT_HARD_STOP_USD, spend_usd: float = 0.0) -> None:
        self.hard_stop_usd = float(hard_stop_usd)
        self.spend_usd = float(spend_usd)
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, spend_usd: float = 0.0) -> "BudgetTracker":
        raw = os.environ.get("BUDGET_USD_HARD_STOP", str(DEFAULT_HARD_STOP_USD))
        try:
            hard_stop = float(raw)
        except ValueError:
            hard_stop = DEFAULT_HARD_STOP_USD
        return cls(hard_stop_usd=hard_stop, spend_usd=spend_usd)

    def check(self, estimate_usd: float) -> None:
        """Refuse *before* spending if this call would pass the hard stop."""
        with self._lock:
            if self.spend_usd + max(0.0, estimate_usd) > self.hard_stop_usd:
                raise BudgetExceeded(
                    f"refusing call: spend ${self.spend_usd:.4f} + estimate ${estimate_usd:.4f} "
                    f"exceeds BUDGET_USD_HARD_STOP=${self.hard_stop_usd:.2f}"
                )

    def add(self, actual_usd: float) -> float:
        with self._lock:
            self.spend_usd += max(0.0, actual_usd)
            return self.spend_usd


# -- process-wide registry (findings #1, M6 GET /budget) -----------------------
# One tracker per provider, shared by every client and bind(): Meta Model API
# spend ($120) and Baseten Spark Open spend ($80) accumulate across requests
# in this process, so the hard stops are reachable. Tests that need
# isolation pass an explicit BudgetTracker (the client constructors honour
# it); the registry is keyed read-only by provider name for M6's GET /budget.
_TRACKERS: dict[str, BudgetTracker] = {}
_TRACKERS_LOCK = threading.Lock()

_META_BUDGET_ENV = "BUDGET_USD_HARD_STOP"
_BASETEN_BUDGET_ENV = "BASETEN_BUDGET_USD_HARD_STOP"
_DEFAULT_BASETEN_HARD_STOP_USD = 80.0


def _tracker_hard_stop(name: str) -> float:
    if name == "baseten":
        raw = os.environ.get(_BASETEN_BUDGET_ENV, str(_DEFAULT_BASETEN_HARD_STOP_USD))
        default = _DEFAULT_BASETEN_HARD_STOP_USD
    else:
        raw = os.environ.get(_META_BUDGET_ENV, str(DEFAULT_HARD_STOP_USD))
        default = DEFAULT_HARD_STOP_USD
    try:
        return float(raw)
    except ValueError:
        return default


def get_tracker(name: str) -> BudgetTracker:
    """Return the process-wide tracker for provider ``name``.

    ``"meta"`` is the $120 Meta Model API ledger, ``"baseten"`` the $80
    Spark Open ledger. Unknown names raise ``ValueError``.
    """
    if name not in ("meta", "baseten"):
        raise ValueError(f"unknown budget tracker {name!r} (want 'meta' or 'baseten')")
    with _TRACKERS_LOCK:
        tracker = _TRACKERS.get(name)
        if tracker is None:
            tracker = BudgetTracker(hard_stop_usd=_tracker_hard_stop(name))
            _TRACKERS[name] = tracker
        return tracker
