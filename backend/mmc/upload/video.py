"""Photo + video upload processing (M5U §4).

Photo: no new path — blobs go through ``POST .../blobs`` then the
existing ``POST .../vision/keyframe`` (Spark components + SAM overlays).

Video: MP4/MOV only (``sam.check_video_bytes``) → ``ffprobe`` duration /
fps / size → Files API upload → ``spark.summarize_video`` (validated
``video_events`` + probability scan) → at most 3 events → ffmpeg sub-clips
→ ``sam.track_video`` per sub-clip. Box hints ride in unverified 0–1000
relative space until the spike confirms Spark's coordinate space; without
a hint, tracking is concept-only. Uploads alone never move posteriors.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from mmc.meta import sam as sam_mod
from mmc.meta.budget import estimate_sam_video, estimate_spark
from mmc.meta.files import upload_file
from mmc.meta.spark import (
    VIDEO_INPUT_TOKENS,
    VIDEO_OUTPUT_TOKENS,
    summarize_video,
)

MAX_VIDEO_EVENTS = 3
EXTRACTOR = "spark:video_events_v1"


def _slug(name: str) -> str:
    slug = "".join(c if (c.isalnum() or c == "_") else "_" for c in name.strip().lower())
    return "_".join(s for s in slug.split("_") if s) or "event"


def pick_events(events: list[dict]) -> list[dict]:
    """Cap the Spark event list at the first ``MAX_VIDEO_EVENTS``."""
    return list(events[:MAX_VIDEO_EVENTS])


def frames_for(duration_s: float, fps: float) -> int:
    """Billed frames for a sub-clip: duration*fps floored at the 50 minimum."""
    return max(sam_mod.VIDEO_MIN_FRAMES, round(duration_s * fps))


def event_box_pixels(box_hint: dict, width: int, height: int) -> dict:
    """Map an unverified 0–1000 relative hint to pixel edges."""
    def scale(value: float, extent: int) -> int:
        return min(extent - 1, max(0, int(float(value) / 1000.0 * extent)))

    x_min = scale(box_hint["x_min"], width)
    y_min = scale(box_hint["y_min"], height)
    x_max = scale(box_hint["x_max"], width)
    y_max = scale(box_hint["y_max"], height)
    return {
        "x_min": min(x_min, x_max),
        "y_min": min(y_min, y_max),
        "x_max": max(x_min, x_max),
        "y_max": max(y_min, y_max),
    }


def probe_clip(data: bytes, filename: str) -> dict:
    """``ffprobe`` duration/fps/size of one MP4/MOV clip."""
    if shutil.which("ffprobe") is None:
        raise ValueError("video probing needs ffprobe (ffmpeg 6.1.1); not installed")
    sam_mod.check_video_bytes(data, filename)
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,duration",
                "-show_entries", "format=duration",
                "-of", "json", tmp.name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise ValueError(f"ffprobe cannot read {filename!r}: {proc.stderr.strip()[:200]}")
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [{}])
    stream = streams[0] if streams else {}
    duration = float(info.get("format", {}).get("duration") or stream.get("duration") or 0.0)
    # NB: the field is r_frame_rate (there is no r_duration_rate); prefer
    # the container-average rate, which exists on every H.264 MP4/MOV.
    rate = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if duration <= 0 or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"ffprobe found no video stream in {filename!r}")
    return {"duration_s": duration, "fps": fps, "width": width, "height": height}


def cut_subclip(data: bytes, filename: str, t_start_s: float, t_end_s: float) -> bytes:
    """Cut ``[t_start_s, t_end_s]`` with ffmpeg (H.264, yuv420p)."""
    if shutil.which("ffmpeg") is None:
        raise ValueError("video cutting needs ffmpeg; not installed")
    sam_mod.check_video_bytes(data, filename)
    if t_end_s <= t_start_s:
        raise ValueError(f"empty sub-clip window [{t_start_s}, {t_end_s}]")
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as src:
        src.write(data)
        src.flush()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as dst:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", str(max(0.0, t_start_s)), "-t", str(t_end_s - max(0.0, t_start_s)),
                    "-i", src.name,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", dst.name,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise ValueError(f"ffmpeg cannot cut {filename!r}: {proc.stderr.strip()[:200]}")
            return Path(dst.name).read_bytes()


def build_evidence(blob_ref: str, content_hash: str, events: list[dict]) -> dict:
    return {
        "evidence_id": uuid.uuid4().hex,
        "type": "video",
        "blob_ref": blob_ref,
        "content_hash": content_hash,
        "events": events,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def to_observations(events: list[dict], evidence_id: str) -> list[dict]:
    ts = datetime.now(timezone.utc).isoformat()
    return [
        {
            "observation_id": uuid.uuid4().hex,
            "feature": f"visual_event:{_slug(str(ev.get('label', '')))}",
            "value": f"{ev.get('t_start_s')}-{ev.get('t_end_s')}s",
            "confidence": 1.0,
            "extractor": EXTRACTOR,
            "evidence_ids": [evidence_id],
            "ts": ts,
        }
        for ev in events
    ]


def _scale_fast_boxes(boxes: list[dict], factor: float) -> list[dict]:
    """Scale fast-scan boxes back to the source-clip coordinates."""
    if factor == 1.0:
        return boxes
    scaled = []
    for entry in boxes:
        box = entry.get("box") or {}
        scaled.append(
            {
                "frame_index": entry.get("frame_index"),
                "object_id": entry.get("object_id"),
                "box": (
                    {
                        "x_min": int(round(box["x_min"] * factor)),
                        "y_min": int(round(box["y_min"] * factor)),
                        "x_max": int(round(box["x_max"] * factor)),
                        "y_max": int(round(box["y_max"] * factor)),
                    }
                    if box
                    else None
                ),
                "score": entry.get("score"),
            }
        )
    return scaled


def merge_frame_results(overlays: list[dict], frame_results: list[dict]) -> list[dict]:
    """Fold worker ``convert_frame_job`` outputs into their overlay entries.

    Each job carries the ``clip_index`` of its overlay; masks land sorted by
    frame. ``found`` flips true when any mask arrives.
    """
    by_clip: dict[int, list[dict]] = {}
    for result in frame_results:
        for mask in result.get("masks", []):
            by_clip.setdefault(int(result.get("clip_index", 0)), []).append(mask)
    for index, overlay in enumerate(overlays):
        masks = sorted(
            by_clip.get(index, []),
            key=lambda m: (m.get("frame_index") or 0, str(m.get("object_id"))),
        )
        if masks:
            overlay["masks"] = masks
            overlay["found"] = True
    return overlays


def analyze_clip(
    data: bytes,
    filename: str,
    client: Any,
    upload: Callable[..., str] | None = None,
    summarize: Callable[..., Any] | None = None,
    track_raw: Callable[..., Any] | None = None,
    probe: Callable[..., dict] | None = None,
    cut: Callable[..., bytes] | None = None,
    defer_masks: bool = False,
) -> dict:
    """Full video pipeline: probe → upload → Spark events → SAM tracking.

    The SAM call returns RAW events; boxes come from a decode-free text
    scan (:func:`sam.extract_boxes_fast`) so the request path never pays
    for mask validation/conversion. With ``defer_masks=False`` (unit
    tests) the capped mask jobs convert inline; with ``True`` (live
    route) the response carries ``masks_pending=True`` plus picklable
    ``_mask_jobs`` for the bounded pool, and overlays fill in when the
    pool finishes. Returns ``{events, overlays, boxes}`` (evidence payload
    without the blob hash — the caller fills ``blob_ref``/``content_hash``).
    The SAM billing floor is enforced in the estimate, and the budget guard
    refuses before ANY call when the clip would breach it. Seams
    (upload/summarize/track_raw/probe/cut) are late-bound so route tests
    can monkeypatch them and run offline on fixtures.
    """
    upload_fn = upload or upload_file
    summarize_fn = summarize or summarize_video
    track_raw_fn = track_raw or sam_mod.track_video_raw
    probe_fn = probe or probe_clip
    cut_fn = cut or cut_subclip
    sam_mod.check_video_bytes(data, filename)
    meta = probe_fn(data, filename)
    duration = float(meta["duration_s"])
    fps = float(meta["fps"])
    width, height = int(meta["width"]), int(meta["height"])
    frames = frames_for(duration, fps)
    # Budget first: one Spark video call + one SAM track per kept event.
    # The exact event count is unknown before Spark answers, so guard the
    # worst case (MAX_VIDEO_EVENTS tracks) before spending anything.
    estimate = estimate_spark(VIDEO_INPUT_TOKENS, VIDEO_OUTPUT_TOKENS) + MAX_VIDEO_EVENTS * estimate_sam_video(
        frames
    )
    client.budget.check(estimate)
    file_id = upload_fn(client, data, filename)
    parsed, _rec = summarize_fn(file_id, client)
    events = pick_events(parsed.get("events", []))
    overlays = []
    pending_jobs: list[dict] = []
    for ev in events:
        t_start = max(0.0, float(ev.get("t_start_s", 0.0)))
        t_end = min(duration, float(ev.get("t_end_s", duration)))
        if t_end <= t_start:
            continue
        subclip = cut_fn(data, filename, t_start, t_end)
        sub_frames = frames_for(t_end - t_start, fps)
        boxes: list[dict] = []
        if isinstance(ev.get("box_hint"), dict):
            try:
                boxes = [event_box_pixels(ev["box_hint"], width, height)]
            except (KeyError, TypeError, ValueError):
                boxes = []
        concept = str(ev.get("sam_concept", "tracked object"))
        raw_events, rec = track_raw_fn(
            subclip, "subclip.mp4", client,
            boxes=boxes or None,
            concepts=[concept],
            frames=sub_frames,
            size=(width, height),
        )
        request = rec.get("request", {}) if isinstance(rec, dict) else {}
        scale = float(request.get("sam_scale", 1.0) or 1.0)
        back = 1.0 / scale if scale != 1.0 else 1.0
        fast_boxes = _scale_fast_boxes(sam_mod.extract_boxes_fast(raw_events), back)
        entry = {
            "label": ev.get("label"),
            "t_start_s": t_start,
            "t_end_s": t_end,
            "concept": ev.get("sam_concept"),
            "found": bool(fast_boxes),
            "boxes": fast_boxes,
            "masks": [],
        }
        overlays.append(entry)
        jobs = sam_mod.build_mask_jobs(raw_events, concept, coord_scale=back)
        for job in jobs:
            job["clip_index"] = len(overlays) - 1
        if defer_masks:
            pending_jobs.extend(jobs)
        else:
            # Finding #12: one bad frame must not fail the whole clip.
            converted = []
            for job in jobs:
                try:
                    converted.append(sam_mod.convert_frame_job(job))
                except Exception as exc:  # noqa: BLE001 - logged, skipped
                    logger.warning(
                        "mask conversion failed for frame %s: %s",
                        job.get("frame_index"),
                        exc,
                    )
            merge_frame_results(overlays, converted)
    out = {
        "events": events,
        "overlays": overlays,
        "boxes": [box for overlay in overlays for box in overlay["boxes"]],
    }
    if defer_masks:
        out["masks_pending"] = True
        out["_mask_jobs"] = pending_jobs
    return out


def submit_mask_jobs(jobs: list[dict], executor: Any = None) -> Any:
    """Hand deferred mask jobs to the bounded pool (one Future, all jobs)."""
    return sam_mod.submit_mask_jobs(jobs, executor)
