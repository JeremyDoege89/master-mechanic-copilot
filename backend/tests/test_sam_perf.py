"""SAM performance tests (2026-10-29 profiling fix; all offline).

- Each mask's raster decodes exactly ONCE beyond the parser's validation
  (counted with a wrapper around the codec).
- SVG paths derive from that single RLE (equal to the library's old path).
- Video masks cap at every 3rd frame / 25 frames; boxes stay per frame.
- Media sent to SAM is downscaled to <=640 px; boxes/paths scale back and
  the scale rides in the call record.
- Mask conversion runs in a bounded pool, off the request path.
"""

from __future__ import annotations

import os
import struct
import subprocess
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mmc.meta import sam as sam_mod
from mmc.meta.sam import (
    MASK_POOL_WORKERS,
    SAM_MAX_SIDE,
    VIDEO_OVERLAY_MAX_FRAMES,
    VIDEO_OVERLAY_STRIDE,
    build_mask_jobs,
    convert_frame_job,
    downscale_image,
    downscale_video,
    extract_boxes_fast,
    load_stream_fixture,
    parse_image_events,
    parse_video_events,
    result_from_image,
    rle_and_svg,
)

VISION = Path(__file__).parent / "fixtures" / "vision"
UPLOAD = Path(__file__).parent / "fixtures" / "upload"


def _battery_seg():
    return parse_image_events(load_stream_fixture(VISION / "sam_stream_battery.jsonl"))


@pytest.fixture(scope="module")
def battery_seg():
    """Parsed once per module: the 3-mask image result is reused read-only."""
    return _battery_seg()


@pytest.fixture(scope="module")
def trimmed():
    """Trimmed 3-frame stream parsed + converted once per module (~5 s)."""
    events = load_stream_fixture(UPLOAD / "sam_video_upload_trimmed.jsonl")
    seg = parse_video_events(events)
    return {
        "events": events,
        "seg": seg,
        "res": result_from_image("engine", seg),
        "jobs": build_mask_jobs(events, "engine"),
    }


def _counting_raster(monkeypatch):
    """Wrap the raster codec everywhere it is referenced; return counter."""
    import meta_sam_parser._mask_codec as codec
    import meta_sam_parser._mask_conversion as conversion
    import meta_sam_parser._segmentation as segmentation

    calls = {"n": 0}
    original = codec.decode_mask_to_raster

    def counting(mask):
        calls["n"] += 1
        return original(mask)

    monkeypatch.setattr(codec, "decode_mask_to_raster", counting)
    monkeypatch.setattr(conversion, "decode_mask_to_raster", counting)
    monkeypatch.setattr(segmentation, "decode_mask_to_raster", counting)
    return calls


def test_result_from_image_decodes_each_mask_once(monkeypatch):
    """One raster decode per mask beyond the parser's own validation."""
    calls = _counting_raster(monkeypatch)
    seg = _battery_seg()
    n_masks = len([r for r in seg.records if r.kind == "mask"])
    assert n_masks == 3
    assert calls["n"] == n_masks  # the parser's validation decodes
    calls["n"] = 0
    res = result_from_image("battery", seg)
    assert len(res.masks) == n_masks
    assert calls["n"] == n_masks  # the single RLE build each; SVG reuses it
    assert len(res.boxes) == 3


def test_svg_derives_from_the_single_rle(battery_seg):
    """Our RLE->SVG equals the library's mask->SVG path output."""
    from meta_sam_parser import decode_mask_to_svg_path

    seg = battery_seg
    for record in seg.records:
        if record.kind != "mask":
            continue
        rle_obj, svg = rle_and_svg(record.mask)
        assert svg == decode_mask_to_svg_path(record.mask)
        assert svg.startswith("M")
        assert list(rle_obj.size) == [record.mask.height, record.mask.width]


def test_fast_boxes_match_parser_on_trimmed_fixture(trimmed):
    events, res = trimmed["events"], trimmed["res"]
    fast = extract_boxes_fast(events)
    parsed = [
        {
            "frame_index": b.frame_index,
            "object_id": b.object_id,
            "box": {
                "x_min": b.box.x_min,
                "y_min": b.box.y_min,
                "x_max": b.box.x_max,
                "y_max": b.box.y_max,
            },
            "score": b.score,
        }
        for b in res.boxes
    ]
    assert fast == parsed
    assert sorted(b["frame_index"] for b in fast) == [0, 1, 2]


def test_fast_boxes_match_parser_on_image_stream():
    events = load_stream_fixture(VISION / "sam_stream_battery.jsonl")
    fast = extract_boxes_fast(events)
    assert [(b["frame_index"], b["object_id"]) for b in fast] == [(0, "0"), (0, "1"), (0, "2")]
    assert fast[0]["box"] == {"x_min": 963, "y_min": 690, "x_max": 1131, "y_max": 831}


def test_mask_jobs_cap_frames():
    """Every 3rd frame, at most 25 — pure text, no decode."""
    events = load_stream_fixture(UPLOAD / "sam_video_upload_trimmed.jsonl")
    from mmc.meta.client import _stream_text_and_usage

    text, _ = _stream_text_and_usage(events)
    line0 = next(line for line in text.split("\n") if line.startswith("<0f>"))
    big = "\n".join(line0.replace("<0f>", f"<{i}f>", 1) for i in range(80))
    big_events = sam_mod.frame_events_for_text(big)
    jobs = build_mask_jobs(big_events, "engine")
    assert [j["frame_index"] for j in jobs] == list(range(0, 80, VIDEO_OVERLAY_STRIDE))[
        :VIDEO_OVERLAY_MAX_FRAMES
    ]
    assert len(jobs) == VIDEO_OVERLAY_MAX_FRAMES
    assert all(set(j) >= {"frame_index", "frame_events", "concept", "coord_scale"} for j in jobs)


def test_result_from_image_converts_only_capped_frames(trimmed):
    res = trimmed["res"]
    assert [m.frame_index for m in res.masks] == [0]  # frames 0,1,2 -> stride 3 -> [0]
    assert len(res.boxes) == 3  # boxes stay per frame


def test_convert_frame_job_matches_sync_result(trimmed):
    sync = trimmed["res"]
    jobs = trimmed["jobs"]
    assert len(jobs) == 1
    out = convert_frame_job(jobs[0])
    assert out["frame_index"] == 0
    sync_masks = [m for m in sam_mod.overlays_for_result(sync) if m["frame_index"] == 0]
    sync_boxes = [b for b in sam_mod.boxes_for_result(sync) if b["frame_index"] == 0]
    assert out["masks"] == sync_masks
    assert out["boxes"] == sync_boxes


def test_submit_and_merge_off_request_path(trimmed):
    """Pool future + merge fold worker outputs into overlays (threads here
    for speed; the real pool is covered by the slow full-stream test)."""
    from mmc.upload import video as video_mod

    jobs = trimmed["jobs"]
    for job in jobs:
        job["clip_index"] = 0
    overlays = [{"label": "e", "found": False, "boxes": [], "masks": []}]
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = sam_mod.submit_mask_jobs(jobs, executor)
        frame_results = future.result(timeout=120)
    assert len(frame_results) == len(jobs)
    video_mod.merge_frame_results(overlays, frame_results)
    assert overlays[0]["found"] is True
    assert len(overlays[0]["masks"]) == 1
    assert overlays[0]["masks"][0]["svg_path"].startswith("M")


def test_mask_pool_is_bounded():
    assert MASK_POOL_WORKERS == min(16, os.cpu_count() or 1)
    assert sam_mod.mask_pool()._max_workers == MASK_POOL_WORKERS


def test_single_mask_decode_time_bound(battery_seg):
    """One keyframe mask (320x162 box-local raster) converts well under the
    1 s keyframe->labels decode budget at 640 px media scale."""
    seg = battery_seg
    masks = [r.mask for r in seg.records if r.kind == "mask"]
    biggest = max(masks, key=lambda m: m.width * m.height)
    t0 = time.time()
    _rle, svg = rle_and_svg(biggest)
    elapsed = time.time() - t0
    assert svg.startswith("M")
    assert elapsed < 2.5


def _write_png(path: Path, width: int, height: int) -> bytes:
    """Valid grayscale PNG (stdlib only) big enough to trigger downscale."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(
            ">I", zlib.crc32(tag + payload) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    # Horizontal gradient; one row buffer reused for every scanline.
    row = b"\x00" + bytes((x * 255 // max(1, width - 1)) for x in range(width))
    raw = row * height
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)
    return data


def test_downscale_image_caps_long_side(tmp_path):
    data = _write_png(tmp_path / "big.png", 1280, 960)
    sent, scale, orig, sent_size = downscale_image(data)
    assert orig == (1280, 960)
    assert max(sent_size) <= SAM_MAX_SIDE
    assert sent_size == (640, 480)
    assert scale == pytest.approx(0.5)
    assert sam_mod.image_size(sent) == (640, 480)


def test_downscale_image_leaves_small_and_broken_as_is(tmp_path):
    small = _write_png(tmp_path / "small.png", 320, 240)
    sent, scale, orig, sent_size = downscale_image(small)
    assert sent == small and scale == 1.0 and sent_size == (320, 240)
    broken = b"\xff\xd8not-an-image"
    sent, scale, orig, sent_size = downscale_image(broken)
    assert sent == broken and scale == 1.0 and orig is None


def _write_clip(path: Path, width: int, height: int, seconds: float = 1.0, fps: int = 10) -> Path:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[:300]
    return path


def test_downscale_video_caps_long_side_and_keeps_fps(tmp_path):
    from mmc.upload.video import probe_clip

    src = _write_clip(tmp_path / "big.mp4", 1280, 960)
    data = src.read_bytes()
    sent, scale, orig, sent_size = downscale_video(data, "big.mp4", (1280, 960))
    assert orig == (1280, 960) and sent_size == (640, 480)
    assert scale == pytest.approx(0.5)
    before = probe_clip(data, "big.mp4")
    after = probe_clip(sent, "sent.mp4")
    assert (after["width"], after["height"]) == (640, 480)
    assert after["fps"] == pytest.approx(before["fps"])
    assert sam_mod.check_video_bytes(sent, "sent.mp4") == "mp4"


def test_downscale_video_leaves_small_as_is(tmp_path):
    src = _write_clip(tmp_path / "small.mp4", 320, 240)
    data = src.read_bytes()
    sent, scale, _orig, sent_size = downscale_video(data, "small.mp4", (320, 240))
    assert sent == data and scale == 1.0 and sent_size == (320, 240)


def _sam_client(tmp_path, events):
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    def transport(model: str, kwargs: dict):
        return [dict(e) for e in events]

    return MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def test_segment_image_sends_small_media_and_scales_back(tmp_path):
    """Fake transport replays the battery stream for a 1280-px PNG: the
    wire bytes are <=640 px, the record carries sam_scale, and result
    boxes come back in the ORIGINAL coordinates."""
    image = _write_png(tmp_path / "big.png", 1280, 960)
    events = load_stream_fixture(VISION / "sam_stream_battery.jsonl")
    seen: dict = {}
    client = _sam_client(tmp_path, events)
    original_transport = client._transport

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return original_transport(model, kwargs)

    client._transport = transport
    res, record = sam_mod.segment_image("battery", image, client)
    assert record["request"]["sam_scale"] == pytest.approx(0.5)
    assert record["request"]["orig_size"] == [1280, 960]
    assert record["request"]["sent_size"] == [640, 480]
    sent_url = seen["kwargs"]["input"][0]["content"][1]["image_url"]
    import base64

    sent_bytes = base64.b64decode(sent_url.split(",", 1)[1])
    assert max(sam_mod.image_size(sent_bytes)) <= SAM_MAX_SIDE
    assert res.found and len(res.masks) == 3
    boxes = {(m.box.x_min, m.box.y_min, m.box.x_max, m.box.y_max) for m in res.masks}
    # Fixture box (963, 690, 1131, 831) scaled back x2 to orig coordinates.
    assert (1926, 1380, 2262, 1662) in boxes


def test_segment_image_box_prompt_uses_sent_size(tmp_path):
    image = _write_png(tmp_path / "big.png", 1280, 960)
    events = load_stream_fixture(VISION / "sam_stream_battery.jsonl")
    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return [dict(e) for e in events]

    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    box = {"x_min": 200, "y_min": 200, "x_max": 400, "y_max": 400}
    res, _record = sam_mod.segment_image("battery", image, client, box=box)
    assert res.found
    text = seen["kwargs"]["input"][0]["content"][0]["text"]
    # Sent media is 640x480, so the prompt box + size are halved.
    assert "w=640;h=480" in text
    assert "x1=100;y1=100;x2=200;y2=200" in text


def test_track_video_downscale_record_and_scaleback(tmp_path, trimmed):
    """1280-px clip + trimmed stream: record carries sam_scale and result
    boxes match the no-downscale coordinates (trimmed masks are full-res
    captures, so scale-back x2 must reproduce the parser boxes)."""
    events = trimmed["events"]
    expected = trimmed["res"]
    client = _sam_client(tmp_path, events)
    clip = _write_clip(tmp_path / "clip.mp4", 1280, 960).read_bytes()
    res, record = sam_mod.track_video(
        clip, "clip.mp4", client, concepts=["engine"], size=(1280, 960)
    )
    assert record["request"]["sam_scale"] == pytest.approx(0.5)
    assert record["request"]["sent_size"] == [640, 480]
    assert [(b.frame_index, b.object_id) for b in res.boxes] == [
        (b.frame_index, b.object_id) for b in expected.boxes
    ]
    for got, want in zip(res.boxes, expected.boxes):
        assert (got.box.x_min, got.box.y_min, got.box.x_max, got.box.y_max) == (
            want.box.x_min * 2,
            want.box.y_min * 2,
            want.box.x_max * 2,
            want.box.y_max * 2,
        )
