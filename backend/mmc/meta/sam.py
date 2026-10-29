"""M5 SAM 3.1 segmentation calls (reconciled with the live API 2026-10-28).

Verified request shape (live run 2026-10-28: concept "battery" on
``engine_bay_02.jpg``; captured streams in
``backend/tests/fixtures/vision/sam_stream_*.jsonl``): Responses API with
``stream=True`` and ``input`` as ONE user message wrapping the parts —
``[{"role": "user", "content": [{"type": "input_text", "text": "<concept>"},
{"type": "input_image", "image_url": "data:<mime>;base64,..."}]}]``. A bare
``[input_text, input_image]`` list is REJECTED with
``400 input[0] did not match any supported type``. Video wraps the same way
with an ``input_file`` part for the clip — verified live 2026-10-29
(``scripts/spike_meta_upload.py`` on the Files-API MP4): BOTH shapes work,
inline ``file_data`` + filename and Files API ``file_id``, 75 masks each.
The default stays ``"inline"`` (no extra upload round-trip); ``"file_id"``
stays as a one-keyword option. SAM does NOT return JSON: it
streams ``output_text``
in its own grammar, one line per frame, e.g.
``<0f>0<|box;x1=963;y1=690;x2=1130;y2=830;w=1280;h=960|><|mask;x=0;y=0;data=141,168,...|>``.
Boxes are inclusive pixel edges in the source-media space (``w``/``h`` of
the token); the official parser reports them half-open
(``right = x2 + 1``).

All parsing goes through the official ``meta-sam-parser`` package:
:func:`parse_responses_stream` with :func:`image_segmentation_format` /
:func:`video_segmentation_format` over the raw event stream,
:func:`decode_mask_to_rle` for overlays plus the library's RLE->SVG step
(:func:`_decode_to_svg_path` takes the RLE object, so it never touches the
raster codec again), and :func:`build_object_prompt` for box/point prompts.

Performance (profiled 2026-10-29): the parser validates every mask at parse
time with one pure-Python raster decode per mask (~1.4 s each on the live
75-frame clip), so :func:`result_from_image` must add exactly ONE more
raster decode per mask — the RLE build — and derive the SVG path from that
RLE. Media sent to SAM is first downscaled to at most 640 px on the long
side (:data:`SAM_MAX_SIDE`); boxes are scaled back to the source
coordinates for overlays and the scale is recorded in the call record.
Mask rasters are box-crop-relative, so mask SVG paths are scaled back AND
shifted by the scaled box origin (RLE sizes are scaled; their origin stays
the box origin) — without the shift every mask draws in the top-left
corner.

An empty result (``status: completed`` with empty text, as in
``sam_stream_air_filter_housing.jsonl``) is a NORMAL "not found" outcome,
not an error — longer phrases miss while short common nouns hit
("battery" found 3 objects on the same photo). Callers should use
:func:`segment_with_fallback` to retry once with a shorter concept.

Cost: SAM usage carries no tokens, only ``images_processed`` (1 per image
call, $0.0025) or ``video_frames_processed`` (75 on the live M5U clip,
$0.015 with the 50-frame floor); the ledger entry comes from those via
``budget.actual_cost_from_usage``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading

logger = logging.getLogger(__name__)
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from meta_sam_parser import (
    RLEObject,
    SegmentationMask,
    build_object_prompt,
    decode_mask_to_rle,
    image_segmentation_format,
    parse_responses_stream,
    video_segmentation_format,
)
from meta_sam_parser._mask_conversion import (
    _decode_to_svg_path as _rle_objects_to_svg_paths,
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

# Long-side cap (px) for media sent to SAM. SAM bills per image/frame, not
# per pixel, so this cuts pure-Python decode time ~4x at zero cost change.
SAM_MAX_SIDE = 640
# Overlay caps per clip: masks only for what the UI shows (every 3rd frame,
# at most 25 frames); boxes stay per frame (they need no mask decode).
VIDEO_OVERLAY_STRIDE = 3
VIDEO_OVERLAY_MAX_FRAMES = 25
# Bounded worker pool for mask conversion (parse validation + RLE + SVG).
MASK_POOL_WORKERS = min(16, os.cpu_count() or 1)


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
    frame_index: int | None = None


@dataclass
class SamFrameBox:
    """One tracked box: cheap (no mask decode), so kept for every frame."""

    object_id: str
    frame_index: int | None
    box: SamBox | None = None
    score: float | None = None


@dataclass
class SamResult:
    concept: str
    masks: list[SamMask] = field(default_factory=list)
    boxes: list[SamFrameBox] = field(default_factory=list)
    raw_output: str = ""
    outcome: str = "completed"

    @property
    def found(self) -> bool:
        """An empty result is a normal "not found", not an error."""
        return len(self.masks) > 0 or len(self.boxes) > 0


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


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def image_cache_key(
    concept: str,
    prompt: str,
    image_bytes: bytes,
    mime: str = "image/jpeg",
) -> dict:
    """Stable cache-key fields for one image call (findings #2/#9).

    Keyed on the ORIGINAL input bytes only: the downscaled bytes SAM
    actually receives vary by machine (ffmpeg present or not), so the
    sent-bytes hash and scale stay in the request record as metadata and
    out of the key. ``prompt`` must be the orig-space prompt (for
    box/point calls, built from the unscaled box).
    """
    return {
        "op": "sam.segment_image.v1",
        "concept": concept,
        "prompt": prompt,
        "mime": mime,
        "orig_sha256": _sha256_hex(image_bytes),
        "orig_bytes": len(image_bytes),
    }


def video_cache_key(
    container: str,
    concepts: list[str],
    prompt: str,
    video_bytes: bytes,
    frames: int,
    video_source: str,
    video_file_id: str | None = None,
) -> dict:
    """Stable cache-key fields for one video call (findings #2/#9).

    Same contract as :func:`image_cache_key`: ORIGINAL clip bytes only;
    the sent-bytes hash, sent size and scale ride in the request record.
    """
    key: dict[str, object] = {
        "op": "sam.track_video.v1",
        "container": container,
        "concepts": list(concepts),
        "prompt": prompt,
        "orig_sha256": _sha256_hex(video_bytes),
        "orig_bytes": len(video_bytes),
        "frames": max(int(frames), VIDEO_MIN_FRAMES),
        "video_source": video_source,
    }
    if video_source == "file_id" and video_file_id:
        key["video_file_id"] = video_file_id
    return key


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


def rle_and_svg(mask: SegmentationMask) -> tuple[RLEObject, str]:
    """Decode one mask's raster exactly ONCE and derive both artifacts.

    :func:`decode_mask_to_rle` performs the single raster decode; the SVG
    path comes from the library's own RLE->SVG step, which takes the RLE
    object and never touches the raster codec again. (Calling the public
    ``decode_mask_to_svg_path`` here as well would decode the same raster a
    second time — that was the 3-decodes-per-mask bug.)
    """
    rle_obj = decode_mask_to_rle(mask)
    paths = _rle_objects_to_svg_paths(rle_obj)
    if len(paths) != 1:
        raise RuntimeError("SVG conversion returned multiple paths for one mask.")
    return rle_obj, paths[0]


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: M/L coordinate pairs as emitted by the RLE->SVG step (``Mx yLx y...Z``).
_PAIR_RE = re.compile(r"([ML])(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)")


def scale_svg_path(path: str, factor: float) -> str:
    """Scale every coordinate in an SVG path string by ``factor``."""
    if factor == 1.0 or not path:
        return path

    def _scaled(match: re.Match[str]) -> str:
        value = float(match.group(0)) * factor
        return str(int(value)) if value.is_integer() else repr(value)

    return _NUMBER_RE.sub(_scaled, path)


def offset_svg_path(path: str, dx: float, dy: float) -> str:
    """Shift an SVG path by ``(dx, dy)`` (x-coords by ``dx``, y by ``dy``).

    SAM emits mask rasters in box-crop space, so the RLE->SVG path is
    crop-relative too: after :func:`scale_svg_path` it must be shifted by
    the (scaled) box origin to land in image coordinates. Only ``M``/``L``
    pairs (the whole grammar the converter emits) are shifted; anything
    else passes through untouched.
    """
    if not path or (dx == 0 and dy == 0):
        return path

    def _shifted(match: re.Match[str]) -> str:
        x = float(match.group(2)) + dx
        y = float(match.group(3)) + dy
        fmt = lambda v: str(int(v)) if v.is_integer() else repr(v)
        return f"{match.group(1)}{fmt(x)} {fmt(y)}"

    return _PAIR_RE.sub(_shifted, path)


def scale_rle(rle_obj: Any, factor: float) -> Any:
    """Nearest-neighbour scale of a crop-space RLE mask by ``factor``.

    RLE-level only (bitmap decode/re-encode, no raster-codec call), so the
    single-decode budget in :func:`result_from_image` is unaffected. The
    RLE stays box-crop-relative — its origin is the mask's box origin —
    but its ``size`` comes back in original-coordinate pixels, matching
    the scaled SVG path. ``factor == 1.0`` returns the object unchanged.
    """
    if factor == 1.0:
        return rle_obj
    from meta_sam_parser._mask_conversion import (
        _DataArray,
        _decode as _rle_to_bitmap,
        _encode as _bitmap_to_rle,
    )

    height, width = int(rle_obj.size[0]), int(rle_obj.size[1])
    new_h = max(1, int(round(height * factor)))
    new_w = max(1, int(round(width * factor)))
    if (new_h, new_w) == (height, width):
        return rle_obj
    src = _rle_to_bitmap(rle_obj).data  # column-major bytes, crop space
    dst = bytearray(new_h * new_w)
    for col in range(new_w):
        src_col = min(width - 1, int(col / factor))
        base_src = src_col * height
        base_dst = col * new_h
        for row in range(new_h):
            dst[base_dst + row] = src[base_src + min(height - 1, int(row / factor))]
    encoded = _bitmap_to_rle(_DataArray(bytes(dst), (new_h, new_w)))
    if isinstance(encoded, list):
        raise RuntimeError("RLE scaling returned multiple masks for one mask.")
    return encoded


def _scaled_box(box: SamBox | None, factor: float) -> SamBox | None:
    if box is None or factor == 1.0:
        return box
    return SamBox(
        x_min=int(round(box.x_min * factor)),
        y_min=int(round(box.y_min * factor)),
        x_max=int(round(box.x_max * factor)),
        y_max=int(round(box.y_max * factor)),
        score=box.score,
    )


def _record_frame(record: Any) -> int | None:
    frame = getattr(record, "frame", None)
    index = getattr(frame, "frame_index", None)
    return int(index) if index is not None else None


def _record_box(record: Any) -> SamBox:
    return SamBox(
        x_min=int(record.left),
        y_min=int(record.top),
        x_max=int(record.right),
        y_max=int(record.bottom),
        score=record.confidence,
    )


def result_from_image(
    concept: str,
    seg: Any,
    *,
    mask_stride: int = VIDEO_OVERLAY_STRIDE,
    max_mask_frames: int = VIDEO_OVERLAY_MAX_FRAMES,
    coord_scale: float = 1.0,
) -> SamResult:
    """Convert a segmentation result into overlay-ready masks and boxes.

    Boxes are cheap (parsed records, no decode) and kept for every frame.
    Masks cost one raster decode each, so video masks are capped to every
    ``mask_stride``-th frame and at most ``max_mask_frames`` frames — what
    the UI shows. Image results (no frame index) convert every mask.
    ``coord_scale`` maps result coordinates back (e.g. 1/sam_scale after a
    pre-SAM downscale). Boxes scale; mask SVG paths scale AND shift by the
    scaled box origin, because SAM mask rasters are box-crop-relative while
    boxes are image-relative — without the shift every mask draws in the
    top-left corner. The stored RLE is scaled the same way (its origin
    stays the box origin); only the overlay SVG path carries full image
    coordinates.
    """
    result = SamResult(
        concept=concept,
        raw_output=seg.raw_output,
        outcome=getattr(seg.outcome, "status", "completed"),
    )
    boxes: dict[tuple[int | None, str], Any] = {}
    for record in seg.records:
        if record.kind == "box":
            boxes.setdefault((_record_frame(record), record.object_id), record)
    mask_frames = sorted(
        {_record_frame(r) for r in seg.records if r.kind == "mask" and _record_frame(r) is not None}
    )
    convert_frames: set[int | None] | None = None
    if mask_frames:
        convert_frames = set(mask_frames[::mask_stride][:max_mask_frames])
    for record in seg.records:
        if record.kind == "box":
            result.boxes.append(
                SamFrameBox(
                    object_id=record.object_id,
                    frame_index=_record_frame(record),
                    box=_scaled_box(_record_box(record), coord_scale),
                    score=record.confidence,
                )
            )
            continue
        if record.kind != "mask":
            continue
        frame_index = _record_frame(record)
        if convert_frames is not None and frame_index not in convert_frames:
            continue
        box_rec = boxes.get((frame_index, record.object_id))
        box = _record_box(box_rec) if box_rec is not None else None
        rle_obj, svg_path = rle_and_svg(record.mask)
        # Mask raster is box-crop-relative: its image-space origin is the
        # box origin (the parser's per-mask bounds; the looked-up box as
        # fallback), scaled back exactly like the box itself.
        bounds = getattr(record, "bounds", None)
        if bounds is not None:
            origin = (int(bounds.left), int(bounds.top))
        elif box is not None:
            origin = (box.x_min, box.y_min)
        else:
            origin = (0, 0)
        scaled_origin = (
            int(round(origin[0] * coord_scale)),
            int(round(origin[1] * coord_scale)),
        )
        rle_obj = scale_rle(rle_obj, coord_scale)
        svg_path = offset_svg_path(
            scale_svg_path(svg_path, coord_scale),
            scaled_origin[0],
            scaled_origin[1],
        )
        result.masks.append(
            SamMask(
                object_id=record.object_id,
                box=_scaled_box(box, coord_scale),
                svg_path=svg_path,
                rle={"size": list(rle_obj.size), "counts": rle_obj.counts},
                score=record.confidence,
                frame_index=frame_index,
            )
        )
    result.masks.sort(key=lambda m: (m.frame_index or 0, m.object_id))
    result.boxes.sort(key=lambda b: (b.frame_index or 0, b.object_id))
    return result


def _box_json(box: SamBox | None) -> dict | None:
    if box is None:
        return None
    return {"x_min": box.x_min, "y_min": box.y_min, "x_max": box.x_max, "y_max": box.y_max}


def boxes_for_result(result: SamResult) -> list[dict]:
    """JSON-ready per-frame boxes (every frame; no mask decode needed)."""
    return [
        {
            "object_id": b.object_id,
            "frame_index": b.frame_index,
            "box": _box_json(b.box),
            "score": b.score,
        }
        for b in result.boxes
    ]


def overlays_for_result(result: SamResult) -> list[dict]:
    """JSON-ready overlay masks (boxes + SVG paths) for the keyframe route."""
    return [
        {
            "object_id": m.object_id,
            "frame_index": m.frame_index,
            "box": _box_json(m.box),
            "svg_path": m.svg_path,
            "score": m.score,
        }
        for m in result.masks
    ]


_BOX_TOKEN_RE = re.compile(r"(?:<(\d+)f>)?,?(\d+)<\|box;([^|]*)\|>")
_FRAME_LINE_RE = re.compile(r"<(\d+)f>")


def extract_boxes_fast(events: list[dict]) -> list[dict]:
    """Per-frame boxes straight from the stream text — no mask decode.

    The request path uses this to return boxes right away; the expensive
    mask validation/conversion happens later in pool workers. Box edges are
    inclusive in the token and reported half-open (``x2+1``/``y2+1``), exactly
    like the official parser. Grammar note (captured streams): the first
    record opens with ``<frame>object<|box;...|>`` and further objects on
    the same frame follow comma-separated (``,1<|box;...|>``) with no
    repeated frame header — the mask payload alphabet excludes ``,``,
    ``<`` and ``|``, so the token regex cannot misfire inside mask data.
    """
    from .client import _stream_text_and_usage

    text, _usage = _stream_text_and_usage(events)
    boxes: list[dict] = []
    frame_index = 0
    for match in _BOX_TOKEN_RE.finditer(text):
        if match.group(1) is not None:
            frame_index = int(match.group(1))
        params: dict[str, str] = {}
        for part in match.group(3).split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip()] = value.strip()
        try:
            x1, y1, x2, y2 = (int(params[k]) for k in ("x1", "y1", "x2", "y2"))
        except (KeyError, ValueError):
            continue
        score: float | None = None
        if params.get("c") not in (None, ""):
            try:
                score = float(params["c"])
            except ValueError:
                score = None
        boxes.append(
            {
                "frame_index": frame_index,
                "object_id": str(match.group(2)),
                "box": {"x_min": x1, "y_min": y1, "x_max": x2 + 1, "y_max": y2 + 1},
                "score": score,
            }
        )
    return boxes


def split_video_frames(events: list[dict]) -> dict[int, str]:
    """Group the assembled stream text by source-video frame index."""
    from .client import _stream_text_and_usage

    text, _usage = _stream_text_and_usage(events)
    frames: dict[int, list[str]] = {}
    for line in text.split("\n"):
        if not line.strip():
            continue
        match = _FRAME_LINE_RE.match(line)
        if match is None:
            continue
        frames.setdefault(int(match.group(1)), []).append(line)
    return {index: "\n".join(lines) for index, lines in frames.items()}


def frame_events_for_text(frame_text: str) -> list[dict]:
    """Minimal Responses event list carrying one frame's records.

    Lets a pool worker officially parse (and validate) a single frame
    without touching the other frames' masks.
    """
    return [
        {
            "type": "response.output_text.delta",
            "delta": frame_text,
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.output_text.done",
            "text": frame_text,
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.content_part.done",
            "part": {"type": "output_text", "text": frame_text},
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.output_item.done",
            "item": {"id": "m", "type": "message"},
            "output_index": 0,
        },
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": {"video_frames_processed": 1}},
        },
    ]


def build_mask_jobs(
    events: list[dict],
    concept: str,
    *,
    coord_scale: float = 1.0,
    stride: int = VIDEO_OVERLAY_STRIDE,
    max_frames: int = VIDEO_OVERLAY_MAX_FRAMES,
) -> list[dict]:
    """One picklable mask-conversion job per overlay frame.

    Only every ``stride``-th frame, at most ``max_frames`` — what the UI
    shows. Boxes for the other frames come from :func:`extract_boxes_fast`
    and need no job.
    """
    by_frame = split_video_frames(events)
    picked = sorted(by_frame)[::stride][:max_frames]
    return [
        {
            "frame_index": index,
            "frame_events": frame_events_for_text(by_frame[index]),
            "concept": concept,
            "coord_scale": coord_scale,
        }
        for index in picked
    ]


def convert_frame_job(job: dict) -> dict:
    """ProcessPool entry: officially parse ONE frame and convert its masks.

    Plain data in and out (picklable). The parser's validation decode and
    the single conversion decode both happen here, inside the worker —
    never on the request path.
    """
    seg = parse_video_events(job["frame_events"])
    result = result_from_image(
        job.get("concept", "tracked object"),
        seg,
        coord_scale=float(job.get("coord_scale", 1.0)),
    )
    return {
        "frame_index": job["frame_index"],
        "clip_index": job.get("clip_index", 0),
        "boxes": boxes_for_result(result),
        "masks": overlays_for_result(result),
    }


_MASK_POOL: ProcessPoolExecutor | None = None


def mask_pool() -> ProcessPoolExecutor:
    """Shared bounded pool for mask conversion (lazy: never at import)."""
    global _MASK_POOL
    if _MASK_POOL is None:
        _MASK_POOL = ProcessPoolExecutor(max_workers=MASK_POOL_WORKERS)
    return _MASK_POOL


def submit_mask_jobs(jobs: list[dict], executor: Any = None) -> Future:
    """Convert mask jobs in parallel; one Future for all overlay results.

    Finding #12: per-frame results are collected — one failing frame is
    logged and yields an ``error`` entry instead of discarding the whole
    batch with no log.
    """
    pool = executor or mask_pool()
    futures = [pool.submit(convert_frame_job, job) for job in jobs]
    done: Future = Future()

    def _watch() -> None:
        results: list[dict] = []
        for job, future in zip(jobs, futures):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - collected per frame
                logger.warning(
                    "mask conversion failed for frame %s: %s",
                    job.get("frame_index"),
                    exc,
                )
                results.append(
                    {
                        "frame_index": job.get("frame_index"),
                        "clip_index": job.get("clip_index", 0),
                        "boxes": [],
                        "masks": [],
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    }
                )
        done.set_result(results)

    threading.Thread(target=_watch, daemon=True).start()
    return done


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


def _ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def _even(value: int) -> int:
    return max(2, int(value) // 2 * 2)


def downscale_image(
    image_bytes: bytes, max_side: int = SAM_MAX_SIDE
) -> tuple[bytes, float, tuple[int, int] | None, tuple[int, int] | None]:
    """Shrink a JPEG/PNG so its long side is at most ``max_side`` (ffmpeg).

    Returns ``(sent_bytes, scale, orig_size, sent_size)`` with
    ``scale = sent/orig <= 1``. Undecodable, already-small, or
    ffmpeg-less input is sent as-is with ``scale == 1.0`` — never an error.
    SAM bills per image, not per pixel, so cost does not change.
    """
    try:
        width, height = image_size(image_bytes)
    except ValueError:
        return image_bytes, 1.0, None, None
    if max(width, height) <= max_side or _ffmpeg_bin() is None:
        return image_bytes, 1.0, (width, height), (width, height)
    factor = max_side / max(width, height)
    sent_w, sent_h = max(2, int(width * factor)), max(2, int(height * factor))
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as src:
            src.write(image_bytes)
            src.flush()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as dst:
                proc = subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error", "-i", src.name,
                        "-vf", f"scale={sent_w}:{sent_h}", "-q:v", "3", dst.name,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    return image_bytes, 1.0, (width, height), (width, height)
                sent = Path(dst.name).read_bytes()
        actual_w, actual_h = image_size(sent)
    except (OSError, ValueError):
        return image_bytes, 1.0, (width, height), (width, height)
    return sent, actual_w / width, (width, height), (actual_w, actual_h)


def downscale_video(
    video_bytes: bytes,
    filename: str,
    size: tuple[int, int] | None,
    max_side: int = SAM_MAX_SIDE,
) -> tuple[bytes, float, tuple[int, int] | None, tuple[int, int] | None]:
    """Shrink an MP4/MOV clip so its long side is at most ``max_side``.

    H.264/yuv420p, frame rate untouched, audio dropped (SAM ignores it).
    Same ``(sent_bytes, scale, orig_size, sent_size)`` contract as
    :func:`downscale_image`; anything undecodable/small/tool-less goes
    through unchanged with ``scale == 1.0``.
    """
    if size is None:
        return video_bytes, 1.0, None, None
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0 or max(width, height) <= max_side:
        return video_bytes, 1.0, (width, height), (width, height)
    if _ffmpeg_bin() is None:
        return video_bytes, 1.0, (width, height), (width, height)
    try:
        check_video_bytes(video_bytes, filename)
    except ValueError:
        return video_bytes, 1.0, (width, height), (width, height)
    factor = max_side / max(width, height)
    sent_w, sent_h = _even(width * factor), _even(height * factor)
    suffix = Path(filename).suffix or ".mp4"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as src:
            src.write(video_bytes)
            src.flush()
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as dst:
                proc = subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error", "-i", src.name,
                        "-vf", f"scale={sent_w}:{sent_h}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-preset", "veryfast", "-crf", "23", "-an", dst.name,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    return video_bytes, 1.0, (width, height), (width, height)
                sent = Path(dst.name).read_bytes()
    except OSError:
        return video_bytes, 1.0, (width, height), (width, height)
    return sent, sent_w / width, (width, height), (sent_w, sent_h)


def _scale_prompt_box(box: dict | None, scale: float) -> dict | None:
    if box is None or scale == 1.0:
        return box
    return {k: int(round(int(box[k]) * scale)) for k in ("x_min", "y_min", "x_max", "y_max")}


def _scale_prompt_point(point: dict | None, scale: float) -> dict | None:
    if point is None or scale == 1.0:
        return point
    scaled = dict(point)
    scaled["x"] = int(round(int(point["x"]) * scale))
    scaled["y"] = int(round(int(point["y"]) * scale))
    return scaled


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


def _video_file_id_input(prompt_text: str, video_file_id: str) -> list[dict]:
    """Responses ``input`` for one video call referencing a Files API upload.

    Same single-user-message wrapper, but the clip rides as an ``input_file``
    part carrying only ``file_id`` (no inline bytes). Which of the two the
    API accepts is decided by the M5U owner spike (``scripts/spike_meta_upload.py``);
    see ``track_video``'s ``video_source`` switch.
    """
    return _user_message(
        [
            {"type": "input_text", "text": prompt_text},
            {"type": "input_file", "file_id": video_file_id},
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
    key_mime = mime  # the caller's mime: stable across machines (the wire
    # mime flips to JPEG only when the local ffmpeg transcodes)
    sent, scale, orig_size, sent_size = downscale_image(image_bytes)
    if sent is not image_bytes:
        mime = "image/jpeg"  # ffmpeg transcodes to JPEG
    prompt = concept
    if box is not None or point is not None:
        prompt = object_prompt_text(
            sent_size or image_size(sent),
            _scale_prompt_box(box, scale),
            _scale_prompt_point(point, scale),
        )
    request = {
        "op": "sam.segment_image.v1",
        "concept": concept,
        "prompt": prompt,
        "image_sha256": _sha256_hex(sent),
        "image_bytes": len(sent),
        "orig_sha256": _sha256_hex(image_bytes),
        "orig_bytes": len(image_bytes),
        "sam_scale": scale,
        "orig_size": list(orig_size) if orig_size else None,
        "sent_size": list(sent_size) if sent_size else None,
    }
    # Finding #9: the cache key uses the ORIGINAL bytes (plus the
    # orig-space prompt for box/point calls, which the API receives
    # scaled into sent space). The sent-bytes hash/scale stay in the
    # request as record metadata only.
    if (box is not None or point is not None) and orig_size:
        try:
            key_prompt = object_prompt_text(tuple(orig_size), box, point)
        except ValueError:
            key_prompt = prompt
    else:
        key_prompt = concept if box is None and point is None else prompt
    events, _text, _usage, record = client.call_stream(
        kind="sam_call",
        model_key="sam",
        request=request,
        estimate_usd=estimate_sam_images(1),
        response_kwargs={
            "input": _image_input(prompt, sent, mime),
            "stream": True,
        },
        cache_key=image_cache_key(concept, key_prompt, image_bytes, key_mime),
    )
    seg = parse_image_events(events)
    back = 1.0 / scale if scale != 1.0 else 1.0
    return result_from_image(concept, seg, coord_scale=back), record


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


def _prepare_video_call(
    video_bytes: bytes,
    filename: str,
    boxes: list[dict] | None,
    concepts: list[str] | None,
    frames: int,
    size: tuple[int, int] | None,
    video_source: str,
    video_file_id: str | None,
) -> tuple[list[dict], str, bytes, str, dict, float]:
    """Shared downscale + prompt + request + input for both video callers.

    Returns ``(live_input, prompt_text, sent, container, request, scale,
    cache_key)``. Box prompts are scaled into the SENT (possibly
    downscaled) media space; results scale back by ``1/scale``.
    ``cache_key`` is the stable original-bytes key (findings #2/#9).
    """
    if video_source not in ("inline", "file_id"):
        raise ValueError(f"video_source must be 'inline' or 'file_id', not {video_source!r}")
    if video_source == "file_id" and not video_file_id:
        raise ValueError("video_source='file_id' needs video_file_id from the Files API")
    container = check_video_bytes(video_bytes, filename)
    boxes = list(boxes or [])
    concepts = [_check_concept(c) for c in (concepts or [])]
    if len(boxes) > MAX_TRACKED_OBJECTS or len(concepts) > MAX_TRACKED_OBJECTS:
        raise ValueError(f"SAM tracks at most {MAX_TRACKED_OBJECTS} objects per request")
    sent, scale, orig_size, sent_size = downscale_video(video_bytes, filename, size)
    prompt_text = " ".join(concepts) if concepts else "tracked object"
    if boxes:
        if sent_size is None:
            raise ValueError("box prompts need size=(width, height) of the clip")
        objects = []
        for i, b in enumerate(boxes):
            try:
                coords = {
                    k: int(round(int(b[k]) * scale))
                    for k in ("x_min", "y_min", "x_max", "y_max")
                }
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
        prompt_text = build_object_prompt(size=sent_size, objects=objects)
    request = {
        "op": "sam.track_video.v1",
        "container": container,
        "video_bytes": len(sent),
        "sent_sha256": _sha256_hex(sent),
        "orig_sha256": _sha256_hex(video_bytes),
        "orig_bytes": len(video_bytes),
        "video_source": video_source,
        "concepts": concepts,
        "prompt": prompt_text,
        "frames": max(int(frames), VIDEO_MIN_FRAMES),
        "sam_scale": scale,
        "orig_size": list(orig_size) if orig_size else None,
        "sent_size": list(sent_size) if sent_size else None,
    }
    if video_source == "file_id":
        assert video_file_id is not None
        request["video_file_id"] = video_file_id
        live_input = _video_file_id_input(prompt_text, video_file_id)
    else:
        live_input = _video_input(prompt_text, sent, filename, container)
    # Finding #9: key on the ORIGINAL clip bytes. Box prompts ride
    # scaled into sent space on the wire; the key uses the orig-space
    # prompt so ffmpeg/no-ffmpeg machines share one cache entry.
    if boxes and size is not None:
        try:
            key_objects = []
            for i, b in enumerate(boxes):
                coords = {k: int(b[k]) for k in ("x_min", "y_min", "x_max", "y_max")}
                key_objects.append(
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
            key_prompt = build_object_prompt(size=tuple(size), objects=key_objects)
        except (KeyError, TypeError, ValueError):
            key_prompt = prompt_text
    else:
        key_prompt = prompt_text
    cache_key = video_cache_key(
        container, concepts, key_prompt, video_bytes, frames, video_source, video_file_id
    )
    return live_input, prompt_text, sent, container, request, scale, cache_key


def track_video(
    video_bytes: bytes,
    filename: str,
    client: MetaClient,
    boxes: list[dict] | None = None,
    concepts: list[str] | None = None,
    frames: int = VIDEO_MIN_FRAMES,
    size: tuple[int, int] | None = None,
    video_source: str = "inline",
    video_file_id: str | None = None,
) -> tuple[SamResult, dict]:
    """SAM video segmentation: concepts + box prompts, at most 3 objects.

    ``input`` is one user message wrapping ``[input_text, input_file]``
    (same wrapper SAM 3.1 requires for images; MP4/MOV H.264 only) and box
    prompts are built with :func:`build_object_prompt` on frame 0, so
    ``size`` (display width/height in pixels) is required whenever ``boxes``
    are given. With ``boxes`` the object-prompt text goes ALONE as input_text
    (phrases cannot be combined with object prompts — same 400 as images);
    concepts stay in the request record and the first labels the result.
    Returns ``(SamResult, key-free record)``.

    ``video_source`` picks how the clip rides in the ``input_file`` part:
    ``"inline"`` (default, no extra upload round-trip) sends base64
    ``file_data`` + filename; ``"file_id"`` sends only
    ``{"file_id": video_file_id}`` for a clip already uploaded via the
    Files API. Verified live 2026-10-29 (``scripts/spike_meta_upload.py``):
    BOTH shapes work (75 masks each on the spike clip), so the default
    stays ``"inline"`` and ``"file_id"`` stays as a one-keyword option.

    The clip is downscaled to :data:`SAM_MAX_SIDE` before the call and
    result coordinates are scaled back; the scale rides in the record.
    """
    live_input, _prompt, _sent, _container, request, scale, cache_key = _prepare_video_call(
        video_bytes, filename, boxes, concepts, frames, size, video_source, video_file_id
    )
    concepts = request["concepts"]
    events, _text, _usage, record = client.call_stream(
        kind="sam_call",
        model_key="sam",
        request=request,
        estimate_usd=estimate_sam_video(frames),
        response_kwargs={"input": live_input, "stream": True},
        cache_key=cache_key,
    )
    concept = concepts[0] if concepts else "tracked object"
    back = 1.0 / scale if scale != 1.0 else 1.0
    seg = parse_video_events(events)
    return result_from_image(concept, seg, coord_scale=back), record


def track_video_raw(
    video_bytes: bytes,
    filename: str,
    client: MetaClient,
    boxes: list[dict] | None = None,
    concepts: list[str] | None = None,
    frames: int = VIDEO_MIN_FRAMES,
    size: tuple[int, int] | None = None,
    video_source: str = "inline",
    video_file_id: str | None = None,
) -> tuple[list[dict], dict]:
    """SAM video call WITHOUT parsing: downscale, send, return raw events.

    The request path uses this plus :func:`extract_boxes_fast` to return
    boxes right away; mask validation/conversion moves to pool workers via
    :func:`build_mask_jobs`. Same prompts, sizes, and call record as
    :func:`track_video` — only the parse/convert step is deferred.
    """
    live_input, _prompt, _sent, _container, request, _scale, cache_key = _prepare_video_call(
        video_bytes, filename, boxes, concepts, frames, size, video_source, video_file_id
    )
    events, _text, _usage, record = client.call_stream(
        kind="sam_call",
        model_key="sam",
        request=request,
        estimate_usd=estimate_sam_video(frames),
        response_kwargs={"input": live_input, "stream": True},
        cache_key=cache_key,
    )
    return events, record
