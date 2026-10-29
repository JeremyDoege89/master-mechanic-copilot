"""M5 Muse Spark image-understanding calls (spike item 2, finished in step 6).

Two jobs: engine-bay component/anomaly JSON (``vision_components`` schema)
and meter/gauge reading (``vision_instrument`` schema). Outputs are
JSON-Schema-validated before use; probabilities never flow through here
(the engine owns posteriors — see ``mmc/vision/components.py``).

The caller downscales images to ~1024 px on the long side before sending
(Android does this in ``KeyframeSelector``); this module caps the token
estimate accordingly.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import jsonschema

from .budget import estimate_spark
from .client import LLMCallResult, MetaClient

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "packages" / "schemas"

# Rough planning estimate for a ~1024px image + prompt in / ~400 JSON tokens out.
IMAGE_INPUT_TOKENS = 2500
JSON_OUTPUT_TOKENS = 400

# Upload mode (M5U): PDF spec extraction (~6k in / 0.8k out) and video
# understanding (token cost unverified; budget pre-check caps it).
PDF_INPUT_TOKENS = 6000
PDF_OUTPUT_TOKENS = 800
VIDEO_INPUT_TOKENS = 8000
VIDEO_OUTPUT_TOKENS = 500

# Reasoning effort per call kind (verified live: the Responses API accepts
# ``reasoning={"effort": ...}`` on muse-spark-1.3 and muse-wonder-1.0).
# Live keyframe "what am I looking at" stays fast (medium, <3 s target);
# extraction (PDF tables, video events, instruments) and agent
# orchestration/explanations use high; Wonder report review uses xhigh.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
EFFORT_DEFAULTS = {"live": "medium", "extract": "high", "agent": "high", "wonder": "xhigh"}
EFFORT_ENV_VARS = {
    "live": "EFFORT_SPARK_LIVE",
    "extract": "EFFORT_SPARK_EXTRACT",
    "agent": "EFFORT_SPARK_AGENT",
    "wonder": "EFFORT_WONDER",
}


def reasoning_effort(kind: str) -> str:
    """Effort level for one call kind (``live`` | ``extract`` | ``agent`` | ``wonder``).

    From env (``EFFORT_SPARK_LIVE`` / ``EFFORT_SPARK_EXTRACT`` /
    ``EFFORT_SPARK_AGENT`` / ``EFFORT_WONDER``) with the defaults above.
    Unknown values fall back to the default so a typo never blocks a call.
    The returned value goes both into the cached/logged ``request`` record
    (so the response-cache hash stays deterministic per effort) and into
    the live Responses ``reasoning`` kwarg.
    """
    import os

    if kind not in EFFORT_DEFAULTS:
        raise ValueError(f"unknown effort kind {kind!r} (want one of {sorted(EFFORT_DEFAULTS)})")
    raw = (os.environ.get(EFFORT_ENV_VARS[kind], "") or "").strip().lower()
    if raw in EFFORT_LEVELS:
        return raw
    return EFFORT_DEFAULTS[kind]


def _validators() -> dict[str, jsonschema.Draft202012Validator]:
    out = {}
    for name in ("vision_components", "vision_instrument", "spec_range", "video_events"):
        schema = json.loads((SCHEMAS_DIR / f"{name}.json").read_text())
        out[name] = jsonschema.Draft202012Validator(schema)
    return out


VALIDATORS = _validators()

COMPONENTS_SYSTEM = (
    "You are the vision front-end of a vehicle-diagnosis copilot. "
    "Describe only what you see in this engine-bay photo. Reply with JSON only, "
    "exactly: {\"components\": [{\"name\": str, \"sam_concept\": str, "
    "\"sam_fallback\": str (optional), \"confidence\": 0-1}], "
    "\"anomalies\": [{\"type\": one of leak|corrosion|frayed_wire|belt_glazing|cracked_hose, "
    "\"region_hint\": str, \"confidence\": 0-1}]}. "
    "Each sam_concept is ONE short common noun for a segmentation model, "
    "1-2 words (e.g. \"battery\", \"air filter\", \"radiator cap\"); longer "
    "phrases segment badly, so never a comma list and never 3+ words. "
    "When the part has an even shorter everyday name, also give it as "
    "sam_fallback (used only if the first concept finds nothing). "
    "Never output probabilities, priors, posteriors or diagnoses."
)

INSTRUMENT_SYSTEM = (
    "You read a meter, gauge or scan-tool screen. Reply with JSON only, exactly: "
    "{\"value\": number, \"units\": str, \"confidence\": 0-1, "
    "\"display_text\": str}. Units are SI-style (V, ohm, kPa, degC, rpm, pct). "
    "Transcribe the digits literally; never guess a value you cannot see "
    "(use low confidence instead). Never output diagnoses."
)


def _image_part(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{mime};base64,{b64[:32]}...<{(len(image_bytes))} bytes>",
        "_bytes_len": len(image_bytes),
    }


def _input_payload(system: str, user_text: str) -> list[dict]:
    # The real Responses payload (with the full data URL) is built by the
    # transport layer; the cached/logged request keeps a redacted stub so
    # fixtures stay small and key-free.
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]


def _validate(name: str, raw_text: str) -> dict:
    try:
        data = json.loads(raw_text)
    except ValueError as e:
        raise ValueError(f"spark {name}: response is not JSON: {e}") from e
    VALIDATORS[name].validate(data)
    return data


def analyze_components(
    image_bytes: bytes,
    client: MetaClient,
    vehicle_context: str = "",
    mime: str = "image/jpeg",
    open_client: Any = None,
) -> tuple[dict, dict]:
    """Spark scene understanding -> validated ``vision_components`` dict.

    Live keyframe path: reasoning effort from ``EFFORT_SPARK_LIVE``
    (default ``medium`` for the <3 s keyframe->labels target). The effort
    is part of the cached/logged ``request`` record, so replays stay
    deterministic. Returns ``(parsed, key-free record)``.

    M5O §3: with ``open_client`` (a ``SparkOpenClient``), a Meta
    budget/error failure retries once on Spark Open; the record then
    carries ``provider:"baseten"`` + the ``fallback`` marker.
    """
    user_text = (
        f"Vehicle context: {vehicle_context}\n"
        "List the visible under-hood components and any visible anomalies."
        if vehicle_context
        else "List the visible under-hood components and any visible anomalies."
    )
    effort = reasoning_effort("live")
    request = {
        "op": "spark.components.v1",
        "input": _input_payload(COMPONENTS_SYSTEM, user_text),
        "image": _image_part(image_bytes, mime),
        "reasoning": {"effort": effort},
    }

    def primary() -> tuple[dict, dict]:
        result, record = client.call(
            kind="llm_call",
            model_key="spark",
            request=request,
            estimate_usd=estimate_spark(IMAGE_INPUT_TOKENS, JSON_OUTPUT_TOKENS),
            response_kwargs={
                "input": _live_input(COMPONENTS_SYSTEM, user_text, image_bytes, mime),
                "reasoning": {"effort": effort},
            },
        )
        return _validate("vision_components", result.output_text), record

    if open_client is None:
        return primary()
    from mmc.meta import fallback as fallback_mod
    from mmc.meta import spark_open as spark_open_mod

    return fallback_mod.call_text_or_image(
        primary,
        lambda: spark_open_mod.analyze_components_open(
            image_bytes, open_client, vehicle_context, mime
        ),
        kind="image",
    )


def read_instrument(
    image_bytes: bytes,
    client: MetaClient,
    hint: str = "",
    mime: str = "image/jpeg",
    open_client: Any = None,
) -> tuple[dict, dict]:
    """Spark meter reading -> validated ``vision_instrument`` dict.

    Extraction path: reasoning effort from ``EFFORT_SPARK_EXTRACT``
    (default ``high``). Part of the cached/logged ``request`` record.

    M5O §3: with ``open_client``, a Meta budget/error failure retries
    once on Spark Open (same <0.8 retake + user-confirm gates apply to
    the fallback reading — it returns the identical validated shape).
    """
    user_text = f"What does this display read? {hint}".strip()
    effort = reasoning_effort("extract")
    request = {
        "op": "spark.instrument.v1",
        "input": _input_payload(INSTRUMENT_SYSTEM, user_text),
        "image": _image_part(image_bytes, mime),
        "reasoning": {"effort": effort},
    }

    def primary() -> tuple[dict, dict]:
        result, record = client.call(
            kind="llm_call",
            model_key="spark",
            request=request,
            estimate_usd=estimate_spark(IMAGE_INPUT_TOKENS, 100),
            response_kwargs={
                "input": _live_input(INSTRUMENT_SYSTEM, user_text, image_bytes, mime),
                "reasoning": {"effort": effort},
            },
        )
        return _validate("vision_instrument", result.output_text), record

    if open_client is None:
        return primary()
    from mmc.meta import fallback as fallback_mod

    def open_read() -> tuple[dict, dict]:
        import json as _json

        result, record = open_client.chat_image(INSTRUMENT_SYSTEM, user_text, image_bytes, mime)
        try:
            data = _json.loads(result.output_text)
        except ValueError as e:
            raise ValueError(f"spark-open instrument: response is not JSON: {e}") from e
        VALIDATORS["vision_instrument"].validate(data)
        return data, record

    return fallback_mod.call_text_or_image(primary, open_read, kind="image")


def _live_input(system: str, user_text: str, image_bytes: bytes, mime: str) -> Any:
    """Full Responses ``input`` for the real transport (never cached/logged)."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": f"{system}\n\n{user_text}"},
                {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"},
            ],
        }
    ]


def parsed_json(result: LLMCallResult) -> Any:
    if result.output_json is None:
        raise ValueError("spark response is not JSON")
    return result.output_json


SPEC_SYSTEM = (
    "You are the spec-table front-end of a vehicle-diagnosis copilot. "
    "Read the uploaded manual or scan-tool page (PDF) and transcribe every "
    "numeric specification table row literally. Reply with JSON only, exactly: "
    "{\"spec_ranges\": [{\"spec_id\": str, \"parameter\": str, "
    "\"min\": number, \"max\": number, \"units\": str, \"page\": int}]}. "
    "spec_id is a stable slug like \"fuel_pressure_kpa\"; parameter is the "
    "canonical name like \"fuel_pressure\" (a non-table numeric readout uses "
    "\"scan_readout:<slug>\" with min==max only when the page states a "
    "value, never invented). Transcribe digits literally; never guess a "
    "value you cannot see (omit the row instead). "
    "Never output probabilities, priors, posteriors, likelihoods or diagnoses."
)

VIDEO_SYSTEM = (
    "You are the video front-end of a vehicle-diagnosis copilot. Watch the "
    "uploaded clip and list timestamped observable events. Reply with JSON "
    "only, exactly: {\"events\": [{\"t_start_s\": number, \"t_end_s\": number, "
    "\"label\": str, \"sam_concept\": str}]}. Each sam_concept is ONE short "
    "common noun for a segmentation model, 1-2 words, never a comma list. "
    "Never output probabilities, priors, posteriors or diagnoses."
)


def _live_file_input(system: str, user_text: str, file_id: str) -> Any:
    """Full Responses ``input`` referencing an uploaded file (never cached)."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": f"{system}\n\n{user_text}"},
                {"type": "input_file", "file_id": file_id},
            ],
        }
    ]


def _scan_banned(obj: Any) -> None:
    """Refuse LLM output that sets probabilities (defence in depth)."""
    from mmc.vision.components import BANNED_KEYS

    def walk(node: Any, path: str = "$") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in BANNED_KEYS:
                    raise ValueError(f"LLM output sets probability field {path}.{key}: refusing")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(obj)


def _llm_spec_validator() -> jsonschema.Draft202012Validator:
    """Spec-range shape the LLM itself supplies.

    ``extractor`` and ``source_blob_sha256`` are attached by the caller
    (``upload/specs.py`` owns the blob hash — the LLM never supplies
    them), so the LLM shape requires only ``spec_id`` + ``parameter``.
    """
    schema = json.loads((SCHEMAS_DIR / "spec_range.json").read_text())
    schema = dict(schema)
    schema["required"] = ["spec_id", "parameter"]
    return jsonschema.Draft202012Validator(schema)


def _validate_spec_ranges(raw_text: str) -> list[dict]:
    try:
        data = json.loads(raw_text)
    except ValueError as e:
        raise ValueError(f"spark spec_ranges: response is not JSON: {e}") from e
    ranges = data.get("spec_ranges") if isinstance(data, dict) else None
    if not isinstance(ranges, list):
        raise ValueError("spark spec_ranges: response needs a 'spec_ranges' array")
    validator = _llm_spec_validator()
    for item in ranges:
        validator.validate(item)
    _scan_banned(data)
    return ranges


def extract_spec_ranges(
    pdf_file_id: str,
    client: MetaClient,
    page_hint: str = "",
) -> tuple[list[dict], dict]:
    """Spark spec-table extraction -> validated ``spec_range`` dicts.

    Extraction path: reasoning effort from ``EFFORT_SPARK_EXTRACT``
    (default ``high``). Returns ``(ranges, key-free record)``.
    ``source_blob_sha256`` and ``extractor`` are attached by the caller
    (``upload/specs.py``), which owns the blob hash — the LLM never
    supplies them.
    """
    user_text = (
        f"Extract every numeric spec row from this manual page. {page_hint}".strip()
    )
    effort = reasoning_effort("extract")
    request = {
        "op": "spark.spec_table.v1",
        "input": _input_payload(SPEC_SYSTEM, user_text),
        "file_id": pdf_file_id,
        "reasoning": {"effort": effort},
    }
    result, record = client.call(
        kind="llm_call",
        model_key="spark",
        request=request,
        estimate_usd=estimate_spark(PDF_INPUT_TOKENS, PDF_OUTPUT_TOKENS),
        response_kwargs={
            "input": _live_file_input(SPEC_SYSTEM, user_text, pdf_file_id),
            "reasoning": {"effort": effort},
        },
    )
    return _validate_spec_ranges(result.output_text), record


def summarize_video(file_id: str, client: MetaClient) -> tuple[dict, dict]:
    """Spark video understanding -> validated ``video_events`` dict.

    Extraction path: reasoning effort from ``EFFORT_SPARK_EXTRACT``
    (default ``high``). Part of the cached/logged ``request`` record.
    """
    user_text = "List the timestamped observable events in this clip."
    effort = reasoning_effort("extract")
    request = {
        "op": "spark.video_events.v1",
        "input": _input_payload(VIDEO_SYSTEM, user_text),
        "file_id": file_id,
        "reasoning": {"effort": effort},
    }
    result, record = client.call(
        kind="llm_call",
        model_key="spark",
        request=request,
        estimate_usd=estimate_spark(VIDEO_INPUT_TOKENS, VIDEO_OUTPUT_TOKENS),
        response_kwargs={
            "input": _live_file_input(VIDEO_SYSTEM, user_text, file_id),
            "reasoning": {"effort": effort},
        },
    )
    try:
        data = json.loads(result.output_text)
    except ValueError as e:
        raise ValueError(f"spark video_events: response is not JSON: {e}") from e
    VALIDATORS["video_events"].validate(data)
    _scan_banned(data)
    return data, record
