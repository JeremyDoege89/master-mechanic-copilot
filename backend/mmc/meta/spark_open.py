"""M5O Muse Spark Open client (Baseten Chat Completions, sibling — not subclass).

``MetaClient`` (``meta/client.py``) is Responses-shaped
(``client.responses.create``); Spark Open is Chat Completions
(``client.chat.completions.create``) at ``https://inference.baseten.co/v1``
with model exactly ``meta-models/muse-spark-open-1026``, key
``BASETEN_API_KEY`` (runtime only — never printed, never stored in
fixtures or the event log). This module reuses the MetaClient helpers
(``canonical_bytes`` / ``request_sha256`` / ``call_record`` / ``_redact``),
``BudgetTracker``, lazy ``openai`` import, the offline ``transport`` seam
``(model, kwargs) -> response``, and the 429 policy (5 tries, exp backoff
+ jitter, honour Retry-After; other errors raise).

Own ledger: ``BASETEN_BUDGET_USD_HARD_STOP=80`` (of the $100 credit, $20
buffer); Meta's $120 tracker is untouched.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetExceeded, BudgetTracker, actual_cost_from_usage, estimate_spark_open
from .client import (
    BASE_BACKOFF_S,
    MAX_ATTEMPTS,
    LLMCallResult,
    MetaClientError,
    NoCachedResponse,
    call_record,
    request_sha256,
)

BASETEN_BASE_URL = "https://inference.baseten.co/v1"
DEFAULT_SPARK_OPEN_MODEL = "meta-models/muse-spark-open-1026"
MODEL_ENV_VAR = "MODEL_SPARK_OPEN"
BASETEN_KEY_ENV = "BASETEN_API_KEY"
BASETEN_BUDGET_ENV = "BASETEN_BUDGET_USD_HARD_STOP"
DEFAULT_BASETEN_HARD_STOP_USD = 80.0

TEXT_INPUT_TOKENS = 1500
TEXT_OUTPUT_TOKENS = 400
IMAGE_INPUT_TOKENS = 2500
IMAGE_OUTPUT_TOKENS = 400


def _is_contributor_model(model_id: str) -> bool:
    return model_id.strip().lower().endswith("-contributor")


def _is_meta_model(model_id: str) -> bool:
    low = model_id.strip().lower()
    return ("muse" in low) or ("meta" in low) or ("sam" in low)


@dataclass
class SparkOpenResult:
    model: str
    output_text: str
    reasoning_content: str = ""
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    cached: bool = False
    request_sha: str = ""


def _image_stub(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime};base64,<{len(image_bytes)} bytes "
            f"sha256:{hashlib.sha256(image_bytes).hexdigest()[:16]}...>",
        },
        "_bytes_len": len(image_bytes),
        "_sha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def _response_content(response: Any) -> tuple[str, str]:
    """(content, reasoning_content) from a Chat Completions response."""
    try:
        msg = response.choices[0].message
    except (AttributeError, IndexError, TypeError) as e:
        raise MetaClientError(f"baseten chat response has no choices[0].message: {e}") from e
    content = getattr(msg, "content", "") or ""
    reasoning = getattr(msg, "reasoning_content", "") or ""
    if not isinstance(content, str):
        content = str(content)
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)
    return content, reasoning


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
            pass
    out: dict = {}
    for attr in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "completion_tokens_details",
        "prompt_tokens_details",
    ):
        try:
            value = getattr(usage, attr, None)
        except Exception:
            continue
        if value is None:
            continue
        conv = getattr(value, "model_dump", None) or getattr(value, "to_dict", None)
        if callable(conv):
            try:
                value = conv()
            except Exception:
                pass
        elif not isinstance(value, (dict, int, float, str)):
            value = str(value)
        out[attr] = dict(value) if isinstance(value, dict) else value
        if isinstance(out[attr], dict):
            for k, v in list(out[attr].items()):
                inner = getattr(v, "model_dump", None) or getattr(v, "to_dict", None)
                if callable(inner):
                    try:
                        out[attr][k] = inner()
                    except Exception:
                        out[attr][k] = str(v)
    # Nested detail objects may be SDK structs; stringify anything exotic.
    for key in ("completion_tokens_details", "prompt_tokens_details"):
        details = out.get(key)
        if isinstance(details, dict):
            for k, v in list(details.items()):
                if not isinstance(v, (dict, int, float, str, bool, type(None))):
                    details[k] = str(v)
    return out


def _open_record(
    model: str,
    request: dict,
    result: SparkOpenResult,
    kind: str = "llm_call",
    latency_s: float | None = None,
) -> dict:
    base = call_record(
        kind,
        model,
        request,
        LLMCallResult(
            model=result.model,
            output_text=result.output_text,
            usage=dict(result.usage),
            cost_usd=result.cost_usd,
            cached=result.cached,
            request_sha=result.request_sha,
        ),
    )
    base["provider"] = "baseten"
    base["reasoning_content"] = result.reasoning_content
    base["content"] = result.output_text
    if latency_s is not None:
        base["latency_s"] = latency_s
    return base


class SparkOpenClient:
    """Guard/cache/retry shell around Baseten Chat Completions (Spark Open)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        budget: BudgetTracker | None = None,
        cache_dir: str | Path | None = None,
        replay_cached: bool | None = None,
        transport: Callable[[str, dict], Any] | None = None,
        on_call: Callable[[dict], None] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(BASETEN_KEY_ENV, "")
        if not key:
            raise MetaClientError(
                f"{BASETEN_KEY_ENV} is not set (runtime only; never commit it)"
            )
        self._api_key = key
        self.base_url = base_url or os.environ.get("BASETEN_BASE_URL", BASETEN_BASE_URL)
        pinned = model or os.environ.get(MODEL_ENV_VAR, "") or DEFAULT_SPARK_OPEN_MODEL
        if _is_contributor_model(pinned):
            raise MetaClientError(f"refusing contributor-mode model ID: {pinned!r}")
        if not _is_meta_model(pinned):
            raise MetaClientError(f"refusing non-Meta model ID: {pinned!r}")
        if pinned != DEFAULT_SPARK_OPEN_MODEL:
            raise MetaClientError(
                f"refusing model pin {pinned!r}: Spark Open must be exactly "
                f"{DEFAULT_SPARK_OPEN_MODEL!r} (env {MODEL_ENV_VAR})"
            )
        self.model = pinned
        if budget is not None:
            self.budget = budget
        else:
            raw = os.environ.get(BASETEN_BUDGET_ENV, str(DEFAULT_BASETEN_HARD_STOP_USD))
            try:
                hard_stop = float(raw)
            except ValueError:
                hard_stop = DEFAULT_BASETEN_HARD_STOP_USD
            self.budget = BudgetTracker(hard_stop_usd=hard_stop)
        if cache_dir is None:
            cache_dir = Path(os.environ.get("DATA_DIR", "./data")) / "llm_cache" / "baseten"
        self.cache_dir = Path(cache_dir)
        if replay_cached is None:
            replay_cached = os.environ.get("REPLAY_USE_CACHED_LLM", "true").lower() == "true"
        self.replay_cached = replay_cached
        self._transport = transport
        self._on_call = on_call
        self._sleep = sleep or time.sleep
        self.timeout_s = float(timeout_s)

    def __repr__(self) -> str:  # never leaks the key
        return f"SparkOpenClient(base_url={self.base_url!r}, model={self.model!r}, key=<redacted>)"

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
        return client.chat.completions.create(model=model, **kwargs)

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status == 429:
            return True
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        return "429" in str(type(exc).__name__) or "rate_limit" in str(exc).lower()

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

    # -- core -------------------------------------------------------------
    def _chat(
        self,
        *,
        messages: list[dict],
        request: dict,
        estimate_usd: float,
        temperature: float = 0.0,
    ) -> tuple[SparkOpenResult, dict]:
        sha = request_sha256({"model": self.model, **request})
        hit = self._cache_read(sha)
        if hit is not None and self.replay_cached:
            result = SparkOpenResult(
                model=self.model,
                output_text=hit.get("output_text", ""),
                reasoning_content=hit.get("reasoning_content", ""),
                usage=dict(hit.get("usage", {})),
                cost_usd=0.0,
                cached=True,
                request_sha=sha,
            )
            return result, _open_record(self.model, request, result, latency_s=0.0)
        if hit is None and self.replay_cached:
            raise NoCachedResponse(
                f"REPLAY_USE_CACHED_LLM=true and no cached baseten response for {sha}"
            )
        self.budget.check(estimate_usd)
        transport = self._transport or (lambda m, kw: self._real_transport(m, kw))
        kwargs = {"messages": messages, "temperature": temperature}
        t_start = time.time()
        last: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = transport(self.model, kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - retry policy inspects it
                last = exc
                if not self._is_rate_limit(exc) or attempt == MAX_ATTEMPTS:
                    raise MetaClientError(
                        f"Baseten chat call failed ({self.model}): {exc}"
                    ) from exc
                self._sleep(self._retry_after(exc, attempt))
        else:  # pragma: no cover - loop always breaks or raises
            raise MetaClientError(f"Baseten chat call failed ({self.model}): {last}")
        content, reasoning = _response_content(response)
        usage = _response_usage(response)
        latency_s = time.time() - t_start
        actual = actual_cost_from_usage(self.model, usage, provider="baseten")
        self.budget.add(actual)
        result = SparkOpenResult(
            model=self.model,
            output_text=content,
            reasoning_content=reasoning,
            usage=usage,
            cost_usd=actual,
            cached=False,
            request_sha=sha,
        )
        record = _open_record(self.model, request, result, latency_s=latency_s)
        self._cache_write(sha, record)
        if self._on_call is not None:
            self._on_call(record)
        return result, record

    # -- public -----------------------------------------------------------
    def chat_text(self, system: str, user: str) -> tuple[SparkOpenResult, dict]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        request = {
            "op": "spark_open.text.v1",
            "messages": messages,
        }
        in_tokens = max(100, (len(system) + len(user)) // 4)
        estimate = estimate_spark_open(in_tokens + TEXT_INPUT_TOKENS // 4, TEXT_OUTPUT_TOKENS)
        return self._chat(messages=messages, request=request, estimate_usd=estimate)

    def chat_image(
        self,
        system: str,
        user: str,
        image_bytes: bytes,
        mime: str = "image/jpeg",
    ) -> tuple[SparkOpenResult, dict]:
        if not image_bytes:
            raise MetaClientError("Spark Open image call needs non-empty image bytes")
        b64 = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ]
        request = {
            "op": "spark_open.image.v1",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "image": _image_stub(image_bytes, mime),
        }
        estimate = estimate_spark_open(IMAGE_INPUT_TOKENS, IMAGE_OUTPUT_TOKENS)
        return self._chat(messages=messages, request=request, estimate_usd=estimate)

    def chat_audio(self, *args: Any, **kwargs: Any) -> Any:
        """Audio is unavailable on Spark Open (Oct 29 probe: audio → 0 tokens).

        Raises unless the owner explicitly opts in with SPARK_OPEN_AUDIO=true,
        and even then sends nothing until Baseten confirms a shape.
        """
        if os.environ.get("SPARK_OPEN_AUDIO", "false").lower() != "true":
            raise NotImplementedError(
                "Spark Open audio is unavailable (input_audio/audio_url carry no "
                "audio tokens); set SPARK_OPEN_AUDIO=true to experiment"
            )
        raise NotImplementedError(
            "SPARK_OPEN_AUDIO=true but no Baseten audio shape is confirmed; "
            "refusing to send audio"
        )


def analyze_components_open(
    image_bytes: bytes,
    client: "SparkOpenClient",
    vehicle_context: str = "",
    mime: str = "image/jpeg",
) -> tuple[dict, dict]:
    """Second-opinion scene understanding on Spark Open.

    Same ``COMPONENTS_SYSTEM`` prompt, same ``vision_components``
    validation and ``components.BANNED_KEYS`` scan as
    ``spark.analyze_components`` — only the transport (Baseten Chat
    Completions with an ``image_url`` data-URL part) differs, so the two
    opinions merge on agreement only (never probabilities).
    Returns ``(parsed, key-free record)``.
    """
    import json as _json

    import jsonschema as _jsonschema

    from .spark import COMPONENTS_SYSTEM

    try:
        from mmc.vision.components import BANNED_KEYS as _BANNED
    except ImportError:  # pragma: no cover - defensive mirror
        _BANNED = frozenset(
            {
                "prior", "current", "posterior", "probability", "probabilities",
                "likelihood", "likelihood_ratio", "posteriors",
            }
        )
    user_text = (
        f"Vehicle context: {vehicle_context}\n"
        "List the visible under-hood components and any visible anomalies."
        if vehicle_context
        else "List the visible under-hood components and any visible anomalies."
    )
    result, record = client.chat_image(COMPONENTS_SYSTEM, user_text, image_bytes, mime)
    try:
        data = _json.loads(result.output_text)
    except ValueError as e:
        raise ValueError(f"spark-open components: response is not JSON: {e}") from e
    from pathlib import Path as _Path

    schema = _json.loads(
        (_Path(__file__).resolve().parents[3] / "packages" / "schemas" / "vision_components.json").read_text()
    )
    _jsonschema.Draft202012Validator(schema).validate(data)

    def _scan(node: Any, path: str = "$") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _BANNED:
                    raise ValueError(
                        f"LLM output sets probability field {path}.{key}: refusing"
                    )
                _scan(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                _scan(value, f"{path}[{i}]")

    _scan(data)
    return data, record
