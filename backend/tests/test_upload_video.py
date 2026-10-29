"""M5U video tests (§4): container gate, caps, floors, offline pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mmc.meta import sam as sam_mod
from mmc.meta.budget import BudgetTracker, estimate_sam_video
from mmc.upload import video as video_mod

FIXTURES = Path(__file__).parent / "fixtures" / "upload"


TRIMMED = "sam_video_upload_trimmed.jsonl"  # first 3 frames of the live 75-frame capture


def _stream_client(tmp_path, captured: dict, name: str = TRIMMED):
    from mmc.meta.client import MetaClient
    from mmc.meta.sam import load_stream_fixture

    # Trimmed live capture (3 tracked frames, found=True). The full 75-frame
    # stream runs only under the slow marker (test_upload_fixtures.py).
    events = load_stream_fixture(FIXTURES / name)

    def transport(model: str, kwargs: dict):
        captured["kwargs"] = kwargs
        captured["model"] = model
        return [dict(e) for e in events]

    return MetaClient(
        api_key="test-key-not-a-secret",
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
    )


def test_non_h264_containers_rejected():
    with pytest.raises(ValueError, match="MP4/MOV"):
        sam_mod.check_video_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64, "clip.webm")
    with pytest.raises(ValueError, match="MP4/MOV"):
        sam_mod.check_video_bytes(b"not a video", "clip.avi")
    assert sam_mod.check_video_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64, "clip.mp4") == "mp4"


def test_more_than_three_events_capped():
    events = [{"t_start_s": float(i), "t_end_s": float(i + 1), "label": f"e{i}", "sam_concept": "engine"} for i in range(6)]
    assert len(video_mod.pick_events(events)) == 3


def test_subclip_frames_floored_at_50_in_estimate():
    assert video_mod.frames_for(0.5, 10.0) == 50
    assert video_mod.frames_for(10.0, 30.0) == 300
    assert estimate_sam_video(video_mod.frames_for(0.5, 10.0)) == pytest.approx(
        estimate_sam_video(50)
    )


def test_box_hint_maps_0_1000_space_to_pixels():
    box = video_mod.event_box_pixels(
        {"x_min": 0, "y_min": 0, "x_max": 1000, "y_max": 1000}, 1280, 960
    )
    assert box == {"x_min": 0, "y_min": 0, "x_max": 1279, "y_max": 959}
    box = video_mod.event_box_pixels(
        {"x_min": 250, "y_min": 250, "x_max": 750, "y_max": 750}, 1000, 1000
    )
    assert box == {"x_min": 250, "y_min": 250, "x_max": 750, "y_max": 750}


def test_video_source_switch_validation():
    with pytest.raises(ValueError, match="video_source"):
        sam_mod.track_video(b"\x00" * 16, "c.mp4", client=None, video_source="dvd")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="video_file_id"):
        sam_mod.track_video(b"\x00" * 16, "c.mp4", client=None, video_source="file_id")  # type: ignore[arg-type]


def test_track_video_defaults_to_inline(tmp_path):
    import inspect

    assert inspect.signature(sam_mod.track_video).parameters["video_source"].default == "inline"


def test_track_video_sends_file_id_part(tmp_path):
    captured: dict = {}
    client = _stream_client(tmp_path, captured)
    result, _record = sam_mod.track_video(
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64,
        "clip.mp4",
        client,
        concepts=["engine"],
        video_source="file_id",
        video_file_id="file-abc",
    )
    assert result.found is True  # trimmed stream tracks on all 3 frames
    assert len(result.boxes) == 3
    assert len(result.masks) == 1  # frames 0,1,2 capped to every 3rd frame
    assert result.masks[0].frame_index == 0
    parts = captured["kwargs"]["input"][0]["content"]
    file_part = next(p for p in parts if p["type"] == "input_file")
    assert file_part == {"type": "input_file", "file_id": "file-abc"}


def test_track_video_inline_sends_file_data(tmp_path):
    captured: dict = {}
    client = _stream_client(tmp_path, captured)
    result, _record = sam_mod.track_video(
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64, "clip.mp4", client, concepts=["engine"]
    )
    assert result.found is True
    assert len(result.boxes) == 3
    assert len(result.masks) == 1
    parts = captured["kwargs"]["input"][0]["content"]
    file_part = next(p for p in parts if p["type"] == "input_file")
    assert "file_data" in file_part and file_part["filename"] == "clip.mp4"


def _offline_clip(**kw):
    """analyze_clip with fully fake seams (no ffmpeg, no network)."""
    calls: dict = {}

    class FakeBudget:
        def check(self, estimate: float) -> None:
            calls["estimate"] = estimate

    class FakeClient:
        budget = FakeBudget()

    def fake_upload(client, data, filename):
        calls["uploaded"] = filename
        return "file-1"

    def fake_summarize(file_id, client):
        assert file_id == "file-1"
        return (
            {
                "events": [
                    {"t_start_s": 0.0, "t_end_s": 1.0, "label": "Prime", "sam_concept": "pump"},
                    {"t_start_s": 1.0, "t_end_s": 2.0, "label": "Idle", "sam_concept": "engine"},
                ]
            },
            {},
        )

    def fake_track_raw(subclip, filename, client, **kw):
        calls.setdefault("tracks", []).append(kw)
        # One box-only frame per subclip: no mask decode anywhere here.
        text = "<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=1280;h=960|>"
        return (
            [
                {"type": "response.output_text.delta", "delta": text},
                {"type": "response.output_text.done", "text": text},
            ],
            {"request": {"sam_scale": 1.0}},
        )

    out = video_mod.analyze_clip(
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64,
        "clip.mp4",
        FakeClient(),
        upload=fake_upload,
        summarize=fake_summarize,
        track_raw=fake_track_raw,
        probe=lambda data, filename: {"duration_s": 3.0, "fps": 30.0, "width": 1280, "height": 960},
        cut=lambda data, filename, a, b: b"subclip",
        **kw,
    )
    return out, calls


def test_analyze_clip_offline_happy_path():
    out, calls = _offline_clip()
    assert len(out["events"]) == 2
    assert len(out["overlays"]) == 2
    assert calls["uploaded"] == "clip.mp4"
    assert calls["estimate"] > 0
    assert all(kw["frames"] >= 50 for kw in calls["tracks"])
    # Boxes return on the request path; the box-only frame has no mask.
    assert len(out["boxes"]) == 2
    assert all(o["boxes"] and not o["masks"] for o in out["overlays"])
    assert all(o["found"] for o in out["overlays"])
    assert out["overlays"][0]["boxes"][0]["box"] == {
        "x_min": 10, "y_min": 20, "x_max": 31, "y_max": 41,
    }


def test_analyze_clip_defer_returns_boxes_with_pending_masks():
    out, _calls = _offline_clip(defer_masks=True)
    assert out["masks_pending"] is True
    assert len(out["boxes"]) == 2  # boxes right away, no decode
    assert all(o["masks"] == [] for o in out["overlays"])
    assert len(out["_mask_jobs"]) == 2  # one overlay frame per subclip
    assert all(j["clip_index"] in (0, 1) for j in out["_mask_jobs"])


def test_merge_frame_results_folds_masks_into_overlays():
    overlays = [
        {"label": "a", "found": False, "boxes": [], "masks": []},
        {"label": "b", "found": False, "boxes": [], "masks": []},
    ]
    frame_results = [
        {"clip_index": 1, "frame_index": 3,
         "masks": [{"object_id": "0", "frame_index": 3, "svg_path": "M1"}]},
        {"clip_index": 0, "frame_index": 0,
         "masks": [{"object_id": "0", "frame_index": 0, "svg_path": "M0"}]},
    ]
    video_mod.merge_frame_results(overlays, frame_results)
    assert overlays[0]["found"] is True
    assert [m["frame_index"] for m in overlays[0]["masks"]] == [0]
    assert [m["frame_index"] for m in overlays[1]["masks"]] == [3]


def test_analyze_clip_budget_refusal_spends_nothing():
    from mmc.meta.budget import BudgetExceeded

    calls = {"uploaded": False}

    class BrokeClient:
        budget = BudgetTracker(hard_stop_usd=0.0001)

    def fake_upload(client, data, filename):
        calls["uploaded"] = True
        return "file-1"

    with pytest.raises(BudgetExceeded):
        video_mod.analyze_clip(
            b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64,
            "clip.mp4",
            BrokeClient(),
            upload=fake_upload,
            probe=lambda data, filename: {"duration_s": 3.0, "fps": 30.0, "width": 640, "height": 480},
        )
    assert calls["uploaded"] is False


def test_live_video_event_list_validates():
    from mmc.meta.spark import VALIDATORS

    record = json.loads((FIXTURES / "spark_video_events_01.json").read_text())
    data = json.loads(record["output_text"])
    VALIDATORS["video_events"].validate(data)
    assert len(data["events"]) == 1
    assert data["events"][0]["sam_concept"] == "hood"
    assert video_mod.pick_events(data["events"] * 4) == (data["events"] * 4)[:3]
