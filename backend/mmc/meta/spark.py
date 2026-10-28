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


def _validators() -> dict[str, jsonschema.Draft202012Validator]:
    out = {}
    for name in ("vision_components", "vision_instrument"):
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
) -> tuple[dict, dict]:
    """Spark scene understanding -> validated ``vision_components`` dict.

    Returns ``(parsed, key-free record)``.
    """
    user_text = (
        f"Vehicle context: {vehicle_context}\n"
        "List the visible under-hood components and any visible anomalies."
        if vehicle_context
        else "List the visible under-hood components and any visible anomalies."
    )
    request = {
        "op": "spark.components.v1",
        "input": _input_payload(COMPONENTS_SYSTEM, user_text),
        "image": _image_part(image_bytes, mime),
    }
    result, record = client.call(
        kind="llm_call",
        model_key="spark",
        request=request,
        estimate_usd=estimate_spark(IMAGE_INPUT_TOKENS, JSON_OUTPUT_TOKENS),
        response_kwargs={"input": _live_input(COMPONENTS_SYSTEM, user_text, image_bytes, mime)},
    )
    return _validate("vision_components", result.output_text), record


def read_instrument(
    image_bytes: bytes,
    client: MetaClient,
    hint: str = "",
    mime: str = "image/jpeg",
) -> tuple[dict, dict]:
    """Spark meter reading -> validated ``vision_instrument`` dict."""
    user_text = f"What does this display read? {hint}".strip()
    request = {
        "op": "spark.instrument.v1",
        "input": _input_payload(INSTRUMENT_SYSTEM, user_text),
        "image": _image_part(image_bytes, mime),
    }
    result, record = client.call(
        kind="llm_call",
        model_key="spark",
        request=request,
        estimate_usd=estimate_spark(IMAGE_INPUT_TOKENS, 100),
        response_kwargs={"input": _live_input(INSTRUMENT_SYSTEM, user_text, image_bytes, mime)},
    )
    return _validate("vision_instrument", result.output_text), record


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
