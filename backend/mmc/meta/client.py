"""M5 Meta Model API client (M5 spike item 1, finished in step 6).

One OpenAI-SDK Responses client for every model call: ``base_url`` is
``META_BASE_URL``, the key is ``MODEL_API_KEY`` (runtime only — never
printed, never stored in fixtures or the event log), model IDs are pinned
from env (``MODEL_SPARK`` / ``MODEL_SAM`` / ...; Standard mode only, never
``*-contributor``).

Behaviour per call:
- 429 retry with exponential backoff + jitter, honouring ``Retry-After``.
- Budget pre-check on the estimate, hard refusal past
  ``BUDGET_USD_HARD_STOP``; the spend ledger is updated from the ``usage``
  numbers the API returns, not from the estimate.
- Disk cache keyed by sha256(canonical request). With
  ``REPLAY_USE_CACHED_LLM=true`` a cache hit replays for $0 and a miss
  raises instead of spending.
- The ``openai`` package is imported lazily so the backend (and its tests)
  stay importable offline; only a real network call needs it. Tests inject
  a ``transport`` callable instead.
- Call records (cache entries, event payloads, spike fixtures) never
  contain request headers or the key — see :func:`call_record`.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetExceeded, BudgetTracker, actual_cost_from_usage, get_tracker

META_BASE_URL = "https://api.meta.ai/v1"

# Model-ID pins: env first, AGENTS.md Standard-mode IDs as defaults.
# Contributor-mode IDs are refused outright.
DEFAULT_MODELS = {
    "spark": "muse-spark-1.3",
    "wonder": "muse-wonder-1.0",
    "sam": "sam-3.1",
    "asr": "muse-voice-transcribe-1.0",
}
_MODEL_ENV_VARS = ("MODEL_SPARK", "MODEL_WONDER", "MODEL_SAM", "MODEL_ASR")

MAX_ATTEMPTS = 5
BASE_BACKOFF_S = 1.0


class NoCachedResponse(RuntimeError):
    """Replay mode (REPLAY_USE_CACHED_LLM=true) had no cached response."""


class MetaClientError(RuntimeError):
    """Non-retryable Meta API failure (auth, bad request, ...)."""


@dataclass
class LLMCallResult:
    model: str
    output_text: str
    output_json: Any = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    cached: bool = False
    request_sha: str = ""


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def request_sha256(request: dict) -> str:
    return hashlib.sha256(canonical_bytes(request)).hexdigest()


def _redact(obj: Any) -> Any:
    """Deep-strip anything that could carry credentials from a record."""
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k.lower() in ("authorization", "api-key", "x-api-key") else _redact(v))
            for k, v in obj.items()
            if k.lower() not in ("headers", "api_key", "apikey")
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def call_record(kind: str, model: str, request: dict, result: "LLMCallResult") -> dict:
    """Key-free record safe for cache files, event payloads and fixtures."""
    return {
        "kind": kind,  # "llm_call" | "sam_call"
        "model": model,
        "request_sha256": result.request_sha or request_sha256(request),
        "request": _redact(request),
        "output_text": result.output_text,
        "usage": dict(result.usage),
        "cost_usd": result.cost_usd,
        "cached": result.cached,
    }


def _is_contributor_model(model_id: str) -> bool:
    return model_id.strip().lower().endswith("-contributor")


# Finding #14: per-key Meta model-ID allowlist (mirrors the Baseten
# client's exact-pin enforcement). A foreign ID with a changed base URL
# (MODEL_SPARK=gpt-4o) must raise instead of being called.
MODEL_KEY_PREFIXES = {
    "spark": ("muse-spark-",),
    "wonder": ("muse-wonder-",),
    "sam": ("sam-",),
    "asr": ("muse-voice-",),
}


def _check_model_id(key: str, model_id: str) -> None:
    if not model_id:
        return  # missing pins are reported by the caller, not here
    if _is_contributor_model(model_id):
        raise MetaClientError(f"refusing contributor-mode model ID for {key}: {model_id!r}")
    prefixes = MODEL_KEY_PREFIXES.get(key)
    if prefixes is not None and not model_id.strip().lower().startswith(prefixes):
        raise MetaClientError(
            f"refusing non-Meta model ID for {key}: {model_id!r} (want prefix {prefixes!r})"
        )


def _event_to_dict(event: Any) -> dict:
    """Best-effort conversion of a Responses stream event to a plain dict."""
    if isinstance(event, dict):
        return dict(event)
    to_dict = getattr(event, "model_dump", None) or getattr(event, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass
    out: dict = {}
    for attr in ("type", "sequence_number", "delta", "text", "item_id",
                 "output_index", "content_index", "item", "part", "response"):
        try:
            value = getattr(event, attr, None)
        except Exception:
            continue
        if value is not None:
            out[attr] = value
    return out


def _ev_response_usage(event: dict) -> dict:
    response = event.get("response")
    if not isinstance(response, dict):
        return {}
    usage = response.get("usage")
    if isinstance(usage, dict):
        return dict(usage)
    to_dict = getattr(usage, "model_dump", None) or getattr(usage, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _stream_text_and_usage(events: list[dict]) -> tuple[str, dict]:
    """Accumulate output text and terminal usage from a Responses stream.

    Text comes from ``response.output_text.delta`` chunks; when a stream
    carries no deltas (the verified "nothing found" SAM outcome), the
    ``response.output_text.done`` text is used instead. Usage comes from
    the terminal ``response.completed`` event.
    """
    chunks: list[str] = []
    done_text: str | None = None
    usage: dict = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
        elif etype == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str):
                done_text = text
        elif etype == "response.content_part.done":
            part = event.get("part")
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    done_text = text
        elif etype == "response.completed":
            usage = _ev_response_usage(event)
    return ("".join(chunks) if chunks else (done_text or ""), usage)


class MetaClient:
    """Thin guard/cache/retry shell around the OpenAI Responses API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        models: dict[str, str] | None = None,
        budget: BudgetTracker | None = None,
        cache_dir: str | Path | None = None,
        replay_cached: bool | None = None,
        transport: Callable[[str, dict], Any] | None = None,
        on_call: Callable[[dict], None] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY", "")
        if not key:
            raise MetaClientError("MODEL_API_KEY is not set (runtime only; never commit it)")
        self._api_key = key
        self.base_url = base_url or os.environ.get("META_BASE_URL", META_BASE_URL)
        pins = dict(models or {})
        for var in _MODEL_ENV_VARS:
            short = var[len("MODEL_") :].lower()
            pins.setdefault(short, os.environ.get(var, "") or DEFAULT_MODELS[short])
        for name, model_id in pins.items():
            _check_model_id(name, model_id)
        self.models = pins
        # Process-wide ledger (finding #1): every default client shares
        # the $120 Meta tracker, so spend accumulates across bind() calls.
        self.budget = budget if budget is not None else get_tracker("meta")
        if cache_dir is None:
            cache_dir = Path(os.environ.get("DATA_DIR", "./data")) / "llm_cache"
        self.cache_dir = Path(cache_dir)
        if replay_cached is None:
            replay_cached = os.environ.get("REPLAY_USE_CACHED_LLM", "true").lower() == "true"
        self.replay_cached = replay_cached
        self._transport = transport
        self._on_call = on_call
        self._sleep = sleep or time.sleep
        self.timeout_s = float(timeout_s)

    def __repr__(self) -> str:  # never leaks the key
        return f"MetaClient(base_url={self.base_url!r}, models={self.models!r}, key=<redacted>)"

    # -- cache -----------------------------------------------------------
    def _cache_path(self, sha: str) -> Path:
        return self.cache_dir / f"{sha}.json"

    def _cache_read(self, sha: str) -> dict | None:
        path = self._cache_path(sha)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_write(self, sha: str, record: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(sha).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    # -- transport --------------------------------------------------------
    def _real_transport(self, model: str, kwargs: dict) -> Any:
        try:
            import openai  # lazy: offline imports must not require the SDK
        except ImportError as e:
            raise MetaClientError(
                "the openai package is not installed; add it (pyproject: openai) "
                "or inject a transport for offline use"
            ) from e
        client = openai.OpenAI(api_key=self._api_key, base_url=self.base_url, timeout=self.timeout_s)
        return client.responses.create(model=model, **kwargs)

    def _real_stream_transport(self, model: str, kwargs: dict) -> Any:
        """Yield plain-dict Responses events for a ``stream=True`` call."""
        try:
            import openai  # lazy: offline imports must not require the SDK
        except ImportError as e:
            raise MetaClientError(
                "the openai package is not installed; add it (pyproject: openai) "
                "or inject a transport for offline use"
            ) from e
        client = openai.OpenAI(api_key=self._api_key, base_url=self.base_url, timeout=self.timeout_s)
        kw = dict(kwargs)
        kw["stream"] = True
        stream = client.responses.create(model=model, **kw)
        for event in stream:
            yield _event_to_dict(event)

    # -- streaming ------------------------------------------------------
    def call_stream(
        self,
        *,
        kind: str,
        model_key: str,
        request: dict,
        estimate_usd: float,
        response_kwargs: dict | None = None,
        cache_key: dict | None = None,
    ) -> tuple[list[dict], str, dict, dict]:
        """One guarded + cached streaming Responses call (SAM 3.1).

        Returns ``(events, output_text, usage, key-free record)``. The
        transport returns an iterable of Responses events (plain dicts, or
        SDK objects on the real path); the full event list is cached so
        replays never touch the network. ``usage`` comes from the terminal
        ``response.completed`` event (SAM reports ``images_processed`` and
        no token counts); cost accounting uses it, falling back to the
        pre-call estimate only when the stream carries no usable counts.

        ``cache_key`` (findings #2/#9) overrides which fields feed the
        cache filename: media callers pass a stable subset keyed on the
        ORIGINAL input bytes, while ``request`` keeps the sent-bytes
        hash/scale as record metadata.
        """
        model = self.models.get(model_key, "")
        if not model:
            raise MetaClientError(f"no model pinned for {model_key!r} (env MODEL_{model_key.upper()})")
        _check_model_id(model_key, model)
        sha = request_sha256({"model": model, **(cache_key if cache_key is not None else request)})

        hit = self._cache_read(sha)
        if hit is not None and self.replay_cached:
            events = [dict(e) for e in hit.get("events", [])]
            result = LLMCallResult(
                model=model,
                output_text=hit.get("output_text", ""),
                usage=dict(hit.get("usage", {})),
                cost_usd=0.0,
                cached=True,
                request_sha=sha,
            )
            return events, result.output_text, result.usage, call_record(kind, model, request, result)
        if hit is None and self.replay_cached:
            raise NoCachedResponse(
                f"REPLAY_USE_CACHED_LLM=true and no cached response for {sha} ({kind}/{model})"
            )

        self.budget.check(estimate_usd)
        transport = self._transport or (lambda m, kw: self._real_stream_transport(m, kw))
        kwargs = dict(response_kwargs or {})

        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw_events = transport(model, kwargs)
                events = [_event_to_dict(e) for e in raw_events]
                break
            except Exception as exc:  # noqa: BLE001 - retry policy inspects it
                last = exc
                if not self._is_rate_limit(exc) or attempt == MAX_ATTEMPTS:
                    raise MetaClientError(f"Meta API stream failed ({kind}/{model}): {exc}") from exc
                self._sleep(self._retry_after(exc, attempt))
        else:  # pragma: no cover - loop always breaks or raises
            raise MetaClientError(f"Meta API stream failed ({kind}/{model}): {last}")

        text, usage = _stream_text_and_usage(events)
        actual = actual_cost_from_usage(model, usage)
        estimated = False
        if actual <= 0.0 and estimate_usd > 0.0:
            # SAM usage carries counts only for images; video/unknown shapes
            # fall back to the pre-call estimate as the ledger entry.
            actual = estimate_usd
            estimated = True
        self.budget.add(actual)
        result = LLMCallResult(
            model=model, output_text=text, usage=usage, cost_usd=actual, cached=False, request_sha=sha
        )
        record = call_record(kind, model, request, result)
        if estimated:
            record["cost_estimated"] = True
        record["events"] = events
        self._cache_write(sha, record)
        if self._on_call is not None:
            self._on_call(record)
        return events, text, usage, record

    @staticmethod
    def _response_text(response: Any) -> str:
        text = getattr(response, "output_text", None)
        if isinstance(text, str):
            return text
        # Fallback: concatenate text deltas from the content blocks.
        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for block in getattr(item, "content", []) or []:
                if getattr(block, "type", "") in ("output_text", "text") and getattr(block, "text", None):
                    chunks.append(block.text)
        if chunks:
            return "".join(chunks)
        to_dict = getattr(response, "to_dict", None) or getattr(response, "model_dump", None)
        if callable(to_dict):
            try:
                data = to_dict()
                return json.dumps(data)
            except Exception:
                pass
        return str(response)

    @staticmethod
    def _response_usage(response: Any) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        if isinstance(usage, dict):
            return dict(usage)
        to_dict = getattr(usage, "to_dict", None) or getattr(usage, "model_dump", None)
        if callable(to_dict):
            try:
                data = to_dict()
                return dict(data) if isinstance(data, dict) else {}
            except Exception:
                return {}
        out: dict = {}
        for attr in ("input_tokens", "output_tokens", "input_tokens_details"):
            value = getattr(usage, attr, None)
            if value is None:
                continue
            if attr == "input_tokens_details":
                details = getattr(value, "cached_tokens", None)
                out[attr] = {"cached_tokens": details} if details is not None else {}
            else:
                out[attr] = value
        return out

    def _retry_after(self, exc: Exception, attempt: int) -> float:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("Retry-After") or headers.get("retry-after")
        try:
            if raw is not None:
                return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
        return BASE_BACKOFF_S * (2.0 ** (attempt - 1)) + random.uniform(0, 0.5)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status == 429:
            return True
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        return "429" in str(type(exc).__name__) or "rate_limit" in str(exc).lower()

    # -- public -----------------------------------------------------------
    def call(
        self,
        *,
        kind: str,
        model_key: str,
        request: dict,
        estimate_usd: float,
        response_kwargs: dict | None = None,
        cache_key: dict | None = None,
    ) -> tuple[LLMCallResult, dict]:
        """One guarded + cached Responses call.

        Returns ``(result, key-free record)``. ``request`` is the cacheable,
        loggable description of the call (model, input, params); ``kind``
        is ``llm_call`` or ``sam_call``. ``cache_key`` optionally overrides
        the fields feeding the cache filename (findings #2/#9).
        """
        model = self.models.get(model_key, "")
        if not model:
            raise MetaClientError(f"no model pinned for {model_key!r} (env MODEL_{model_key.upper()})")
        _check_model_id(model_key, model)
        sha = request_sha256({"model": model, **(cache_key if cache_key is not None else request)})

        hit = self._cache_read(sha)
        if hit is not None and self.replay_cached:
            result = LLMCallResult(
                model=model,
                output_text=hit.get("output_text", ""),
                usage=dict(hit.get("usage", {})),
                cost_usd=0.0,
                cached=True,
                request_sha=sha,
            )
            try:
                result.output_json = json.loads(result.output_text)
            except ValueError:
                pass
            return result, call_record(kind, model, request, result)
        if hit is None and self.replay_cached:
            raise NoCachedResponse(
                f"REPLAY_USE_CACHED_LLM=true and no cached response for {sha} ({kind}/{model})"
            )
        if hit is not None and not self.replay_cached:
            pass  # live mode always re-calls; the entry is overwritten below.

        self.budget.check(estimate_usd)
        transport = self._transport or (lambda m, kw: self._real_transport(m, kw))
        kwargs = dict(response_kwargs or {})

        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = transport(model, kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - retry policy inspects it
                last = exc
                if not self._is_rate_limit(exc) or attempt == MAX_ATTEMPTS:
                    raise MetaClientError(f"Meta API call failed ({kind}/{model}): {exc}") from exc
                self._sleep(self._retry_after(exc, attempt))
        else:  # pragma: no cover - loop always breaks or raises
            raise MetaClientError(f"Meta API call failed ({kind}/{model}): {last}")

        text = self._response_text(response)
        usage = self._response_usage(response)
        actual = actual_cost_from_usage(model, usage)
        estimated = False
        if actual <= 0.0 and estimate_usd > 0.0:
            # Finding #3: any API/proxy shape drift that yields empty or
            # unknown usage must not make a paid call free in the ledger —
            # book the pre-call estimate (SAM bills per image/frame, so its
            # usage often carries no token counts at all).
            actual = estimate_usd
            estimated = True
        self.budget.add(actual)
        result = LLMCallResult(
            model=model, output_text=text, usage=usage, cost_usd=actual, cached=False, request_sha=sha
        )
        try:
            result.output_json = json.loads(text)
        except ValueError:
            pass
        record = call_record(kind, model, request, result)
        if estimated:
            record["cost_estimated"] = True
        self._cache_write(sha, record)
        if self._on_call is not None:
            self._on_call(record)
        return result, record
