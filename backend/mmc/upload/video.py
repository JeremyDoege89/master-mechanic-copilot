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
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
                "-show_entries", "stream=width,height,r_duration_rate,duration",
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
    rate = str(stream.get("r_duration_rate") or stream.get("avg_frame_rate") or "0/1")
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


def analyze_clip(
    data: bytes,
    filename: str,
    client: Any,
    upload: Callable[..., str] = upload_file,
    summarize: Callable[..., Any] = summarize_video,
    track: Callable[..., Any] = sam_mod.track_video,
    probe: Callable[..., dict] = probe_clip,
    cut: Callable[..., bytes] = cut_subclip,
) -> dict:
    """Full video pipeline: probe → upload → Spark events → SAM tracking.

    Returns ``{events, overlays, evidence}`` (evidence payload without the
    blob hash — the caller fills ``blob_ref``/``content_hash``). The SAM
    billing floor is enforced in the estimate, and the budget guard refuses
    before ANY call when the clip would breach it. Seams (upload/summarize/
    track/probe/cut) are injectable so routes tests run offline on fixtures.
    """
    sam_mod.check_video_bytes(data, filename)
    meta = probe(data, filename)
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
    file_id = upload(client, data, filename)
    parsed, _rec = summarize(file_id, client)
    events = pick_events(parsed.get("events", []))
    overlays = []
    for ev in events:
        t_start = max(0.0, float(ev.get("t_start_s", 0.0)))
        t_end = min(duration, float(ev.get("t_end_s", duration)))
        if t_end <= t_start:
            continue
        subclip = cut(data, filename, t_start, t_end)
        sub_frames = frames_for(t_end - t_start, fps)
        boxes: list[dict] = []
        if isinstance(ev.get("box_hint"), dict):
            try:
                boxes = [event_box_pixels(ev["box_hint"], width, height)]
            except (KeyError, TypeError, ValueError):
                boxes = []
        result, _rec2 = track(
            subclip, "subclip.mp4", client,
            boxes=boxes or None,
            concepts=[str(ev.get("sam_concept", "tracked object"))],
            frames=sub_frames,
            size=(width, height) if boxes else None,
        )
        overlays.append(
            {
                "label": ev.get("label"),
                "t_start_s": t_start,
                "t_end_s": t_end,
                "concept": ev.get("sam_concept"),
                "found": result.found,
                "masks": sam_mod.overlays_for_result(result),
            }
        )
    return {"events": events, "overlays": overlays}
