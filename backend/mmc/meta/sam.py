"""M5 SAM 3.1 segmentation calls (reconciled with the live API 2026-10-28).

Verified request shape (live run 2026-10-28: concept "battery" on
``engine_bay_02.jpg``; captured streams in
``backend/tests/fixtures/vision/sam_stream_*.jsonl``): Responses API with
``stream=True`` and ``input`` as ONE user message wrapping the parts —
``[{"role": "user", "content": [{"type": "input_text", "text": "<concept>"},
{"type": "input_image", "image_url": "data:<mime>;base64,..."}]}]``. A bare
``[input_text, input_image]`` list is REJECTED with
``400 input[0] did not match any supported type``. Video wraps the same way
with an ``input_file`` part for the clip. SAM does NOT return JSON: it
streams ``output_text``
in its own grammar, one line per frame, e.g.
``<0f>0<|box;x1=963;y1=690;x2=1130;y2=830;w=1280;h=960|><|mask;x=0;y=0;data=141,168,...|>``.
Boxes are inclusive pixel edges in the source-media space (``w``/``h`` of
the token); the official parser reports them half-open
(``right = x2 + 1``).

All parsing goes through the official ``meta-sam-parser`` package:
:func:`parse_responses_stream` with :func:`image_segmentation_format` /
:func:`video_segmentation_format` over the raw event stream,
:func:`decode_mask_to_rle` / :func:`decode_mask_to_svg_path` for overlays,
and :func:`build_object_prompt` for box/point prompts.

An empty result (``status: completed`` with empty text, as in
``sam_stream_air_filter_housing.jsonl``) is a NORMAL "not found" outcome,
not an error — longer phrases miss while short common nouns hit
("battery" found 3 objects on the same photo). Callers should use
:func:`segment_with_fallback` to retry once with a shorter concept.

Cost: SAM usage carries no tokens, only ``images_processed`` (1 per image
call); the ledger entry comes from that via
``budget.actual_cost_from_usage``.
"""

from __future__ import annotations

import asyncio
import base64
import struct
from dataclasses import dataclass, field
from typing import Any, Literal

from meta_sam_parser import (
    build_object_prompt,
    decode_mask_to_rle,
    decode_mask_to_svg_path,
    image_segmentation_format,
    parse_responses_stream,
    video_segmentation_format,
)
from meta_sam_parser import (
    FramePrompt,
    PromptBox,
    PromptObject,
    PromptPoint,
)

from .budget import estimate_sam_images, estimate_sam_video
from .client import MetaClient

MAX_TRACKED_OBJECTS = 3
VIDEO_MIN_FRAMES = 50


@dataclass
class SamBox:
    """One detected box in source-media pixels (half-open: x_max/y_max exclusive)."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int
    score: float | None = None


@dataclass
class SamMask:
    object_id: str
    box: SamBox | None = None
    svg_path: str = ""
    rle: dict = field(default_factory=dict)
    score: float | None = None


@dataclass
class SamResult:
    concept: str
    masks: list[SamMask] = field(default_factory=list)
    raw_output: str = ""
    outcome: str = "completed"

    @property
    def found(self) -> bool:
        """An empty result is a normal "not found", not an error."""
        return len(self.masks) > 0


def _check_concept(concept: str) -> str:
    concept = concept.strip()
    if not concept:
        raise ValueError("SAM needs exactly one noun-phrase concept")
    if "," in concept:
        raise ValueError(
            f"one concept per SAM request; comma lists return zero matches: {concept!r}"
        )
    return concept


def _check_image_bytes(image_bytes: bytes) -> bytes:
    if not image_bytes:
        raise ValueError("SAM needs non-empty image bytes")
    return image_bytes


def load_stream_fixture(path: Any) -> list[dict]:
    """Read captured Responses events (one JSON object per line) for replay."""
    import json
    from pathlib import Path

    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


async def _parse_async(
    events: list[dict],
    media: Literal["image", "video"],
) -> Any:
    fmt = image_segmentation_format() if media == "image" else video_segmentation_format()

    async def gen() -> Any:
        for event in events:
            yield event

    return await parse_responses_stream(gen(), fmt).final_result()


def parse_image_events(events: list[dict]) -> Any:
    """Parse a captured/live image stream into an ImageSegmentationResult."""
    return asyncio.run(_parse_async(events, "image"))


def parse_video_events(events: list[dict]) -> Any:
    """Parse a captured/live video stream into a VideoSegmentationResult."""
    return asyncio.run(_parse_async(events, "video"))


def result_from_image(concept: str, seg: Any) -> SamResult:
    """Convert an ImageSegmentationResult into overlay-ready masks."""
    result = SamResult(
        concept=concept,
        raw_output=seg.raw_output,
        outcome=getattr(seg.outcome, "status", "completed"),
    )
    boxes: dict[str, Any] = {}
    for record in seg.records:
        if record.kind == "box":
            boxes.setdefault(record.object_id, record)
    for record in seg.records:
        if record.kind != "mask":
            continue
        box_rec = boxes.get(record.object_id)
        box = None
        if box_rec is not None:
            box = SamBox(
                x_min=int(box_rec.left),
                y_min=int(box_rec.top),
                x_max=int(box_rec.right),
                y_max=int(box_rec.bottom),
                score=box_rec.confidence,
            )
        rle_obj = decode_mask_to_rle(record.mask)
        result.masks.append(
            SamMask(
                object_id=record.object_id,
                box=box,
                svg_path=decode_mask_to_svg_path(record.mask),
                rle={"size": list(rle_obj.size), "counts": rle_obj.counts},
                score=record.confidence,
            )
        )
    result.masks.sort(key=lambda m: m.object_id)
    return result


def overlays_for_result(result: SamResult) -> list[dict]:
    """JSON-ready overlay masks (boxes + SVG paths) for the keyframe route."""
    return [
        {
            "object_id": m.object_id,
            "box": (
                {
                    "x_min": m.box.x_min,
                    "y_min": m.box.y_min,
                    "x_max": m.box.x_max,
                    "y_max": m.box.y_max,
                }
                if m.box
                else None
            ),
            "svg_path": m.svg_path,
            "score": m.score,
        }
        for m in result.masks
    ]


def image_size(image_bytes: bytes) -> tuple[int, int]:
    """(width, height) of a JPEG/PNG image from its header (stdlib only)."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", image_bytes[16:24])
        return (int(w), int(h))
    if image_bytes[:2] == b"\xff\xd8":
        pos = 2
        while pos + 4 <= len(image_bytes):
            if image_bytes[pos] != 0xFF:
                break
            marker = image_bytes[pos + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", image_bytes[pos + 5 : pos + 9])
                return (int(w), int(h))
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
                pos += 2
                continue
            seg_len = struct.unpack(">H", image_bytes[pos + 2 : pos + 4])[0]
            pos += 2 + seg_len
    raise ValueError("SAM box prompts need a JPEG/PNG image to size prompts")


def object_prompt_text(
    size: tuple[int, int],
    box: dict | None = None,
    point: dict | None = None,
    object_id: int = 0,
) -> str:
    """Build box/point prompt text for one object on frame 0.

    ``box`` is ``{x_min, y_min, x_max, y_max}`` inclusive pixel edges;
    ``point`` is ``{x, y, label}`` with label 1/"positive" (part of the
    object) or 0/"negative" (not part of it).
    """
    if box is None and point is None:
        raise ValueError("a box or point prompt is required")
    prompt_box = None
    if box is not None:
        try:
            coords = {k: int(box[k]) for k in ("x_min", "y_min", "x_max", "y_max")}
        except KeyError as e:
            raise ValueError(f"box prompt needs x_min/y_min/x_max/y_max, missing {e}") from e
        prompt_box = PromptBox(
            left=coords["x_min"],
            top=coords["y_min"],
            right=coords["x_max"] + 1,
            bottom=coords["y_max"] + 1,
        )
    points: list[PromptPoint] = []
    if point is not None:
        try:
            x, y = int(point["x"]), int(point["y"])
        except KeyError as e:
            raise ValueError(f"point prompt needs x/y, missing {e}") from e
        label = point.get("label", 1)
        if label in (1, "positive", "pos", True):
            text_label = "positive"
        elif label in (0, "negative", "neg", False):
            text_label = "negative"
        else:
            raise ValueError(f"point label must be positive/1 or negative/0, got {label!r}")
        points.append(PromptPoint(x=x, y=y, label=text_label))  # type: ignore[arg-type]
    return build_object_prompt(
        size=size,
        objects=[
            PromptObject(id=object_id, prompts=[FramePrompt(frame=0, box=prompt_box, points=points)])
        ],
    )


def _data_url(image_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _user_message(parts: list[dict]) -> list[dict]:
    """Wrap content parts in the single user message SAM 3.1 requires.

    A bare ``[input_text, input_image, ...]`` list is rejected with
    ``400 input[0] did not match any supported type``.
    """
    return [{"role": "user", "content": parts}]


def _image_input(prompt: str, image_bytes: bytes, mime: str) -> list[dict]:
    """Responses ``input`` for one image call: text concept, then the image."""
    return _user_message(
        [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": _data_url(image_bytes, mime)},
        ]
    )


def _video_input(prompt_text: str, video_bytes: bytes, filename: str, container: str) -> list[dict]:
    """Responses ``input`` for one video call: text prompt, then the clip.

    The clip rides inline as an ``input_file`` part (base64 ``file_data``
    + filename) so no separate Files API upload step is needed.
    """
    mime = "video/quicktime" if container == "mov" else "video/mp4"
    return _user_message(
        [
            {"type": "input_text", "text": prompt_text},
            {
                "type": "input_file",
                "file_data": f"data:{mime};base64,{base64.b64encode(video_bytes).decode('ascii')}",
                "filename": filename,
            },
        ]
    )


def segment_image(
    concept: str,
    image_bytes: bytes,
    client: MetaClient,
    box: dict | None = None,
    point: dict | None = None,
    mime: str = "image/jpeg",
) -> tuple[SamResult, dict]:
    """One SAM image call for one concept (+ optional box/point prompt).

    ``input`` is one user message wrapping ``[input_text, input_image]``
    (verified live 2026-10-28 — a bare parts list is rejected with 400).
    A box/point call sends the object-prompt text ALONE as input_text
    (second live correction 2026-10-28: combining it with the phrase is
    rejected with ``400 invalid prompt: object prompts cannot be combined
    with a phrase``); the concept stays in the request record and labels
    the returned SamResult for the overlay. Returns ``(SamResult,
    key-free record)``; an empty result means "not found", not failure.
    """
    concept = _check_concept(concept)
    image_bytes = _check_image_bytes(image_bytes)
    prompt = concept
    if box is not None or point is not None:
        prompt = object_prompt_text(image_size(image_bytes), box, point)
    request = {
        "op": "sam.segment_image.v1",
        "concept": concept,
        "prompt": prompt,
        "image_sha256": __import__("hashlib").sha256(image_bytes).hexdigest(),
        "image_bytes": len(image_bytes),
    }
    events, _text, _usage, record = client.call_stream(
        kind="sam_call",
        model_key="sam",
        request=request,
        estimate_usd=estimate_sam_images(1),
        response_kwargs={
            "input": _image_input(prompt, image_bytes, mime),
            "stream": True,
        },
    )
    seg = parse_image_events(events)
    return result_from_image(concept, seg), record


def segment_with_fallback(
    concept: str,
    fallback: str | None,
    image_bytes: bytes,
    client: MetaClient,
    **kwargs: Any,
) -> tuple[SamResult, list[dict], str]:
    """Segment ``concept``; on an empty ("not found") result retry ONCE with
    ``fallback`` when Spark gave one (a shorter common noun).

    Returns ``(SamResult, records, used_concept)``.
    """
    result, record = segment_image(concept, image_bytes, client, **kwargs)
    if result.found:
        return result, [record], concept
    short = (fallback or "").strip()
    if short and short != concept:
        retry, record2 = segment_image(short, image_bytes, client, **kwargs)
        return retry, [record, record2], short
    return result, [record], concept


def check_video_bytes(data: bytes, filename: str) -> str:
    """Accept MP4/MOV only (H.264/H.265/MPEG-4 per Meta docs; no VP9/WebM).

    Returns the container ("mp4" or "mov"). Transcoding stays on-device.
    """
    name = filename.lower()
    if not (name.endswith(".mp4") or name.endswith(".mov")):
        raise ValueError(f"SAM video must be MP4/MOV H.264, not {filename!r}")
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"qt  "):
            return "mov" if brand == b"qt  " else "mp4"
    # Ftyp box not at offset 4 (e.g. leading moof) — trust the extension;
    # the API rejects non-conforming uploads loudly.
    return "mov" if name.endswith(".mov") else "mp4"


def track_video(
    video_bytes: bytes,
    filename: str,
    client: MetaClient,
    boxes: list[dict] | None = None,
    concepts: list[str] | None = None,
    frames: int = VIDEO_MIN_FRAMES,
    size: tuple[int, int] | None = None,
) -> tuple[SamResult, dict]:
    """SAM video segmentation: concepts + box prompts, at most 3 objects.

    ``input`` is one user message wrapping ``[input_text, input_file]``
    (same wrapper SAM 3.1 requires for images; MP4/MOV H.264 only, clip
    inline as base64 ``file_data``) and box prompts are built with
    :func:`build_object_prompt` on frame 0, so ``size`` (display
    width/height in pixels) is required whenever ``boxes`` are given.
    With ``boxes`` the object-prompt text goes ALONE as input_text
    (phrases cannot be combined with object prompts — same 400 as images);
    concepts stay in the request record and the first labels the result.
    Returns ``(SamResult, key-free record)``.
    """
    container = check_video_bytes(video_bytes, filename)
    boxes = list(boxes or [])
    concepts = [_check_concept(c) for c in (concepts or [])]
    if len(boxes) > MAX_TRACKED_OBJECTS or len(concepts) > MAX_TRACKED_OBJECTS:
        raise ValueError(f"SAM tracks at most {MAX_TRACKED_OBJECTS} objects per request")
    prompt_text = " ".join(concepts) if concepts else "tracked object"
    if boxes:
        if size is None:
            raise ValueError("box prompts need size=(width, height) of the clip")
        objects = []
        for i, b in enumerate(boxes):
            try:
                coords = {k: int(b[k]) for k in ("x_min", "y_min", "x_max", "y_max")}
            except KeyError as e:
                raise ValueError(f"box prompt needs x_min/y_min/x_max/y_max, missing {e}") from e
            objects.append(
                PromptObject(
                    id=i,
                    prompts=[
                        FramePrompt(
                            frame=0,
                            box=PromptBox(
                                left=coords["x_min"],
                                top=coords["y_min"],
                                right=coords["x_max"] + 1,
                                bottom=coords["y_max"] + 1,
                            ),
                        )
                    ],
                )
            )
        prompt_text = build_object_prompt(size=size, objects=objects)
    request = {
        "op": "sam.track_video.v1",
        "container": container,
        "video_bytes": len(video_bytes),
        "concepts": concepts,
        "prompt": prompt_text,
        "frames": max(int(frames), VIDEO_MIN_FRAMES),
    }
    events, _text, _usage, record = client.call_stream(
        kind="sam_call",
        model_key="sam",
        request=request,
        estimate_usd=estimate_sam_video(frames),
        response_kwargs={
            "input": _video_input(prompt_text, video_bytes, filename, container),
            "stream": True,
        },
    )
    seg = parse_video_events(events)
    concept = concepts[0] if concepts else "tracked object"
    return result_from_image(concept, seg), record
