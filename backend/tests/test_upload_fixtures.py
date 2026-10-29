"""M5U upload fixture tests (§1): live spike outputs stay honest.

All three fixtures are key-free, schema-parse, and carry provisional:false +
captured:"spike_meta_upload.py owner run" since the owner run of
scripts/spike_meta_upload.py on 2026-10-29. The live flag must match the
capture note in docs/DECISIONS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UPLOAD = Path(__file__).parent / "fixtures" / "upload"

BANNED = ("Authorization", "api_key", "Bearer ", "MODEL_API_KEY")


def _load(name: str) -> dict:
    return json.loads((UPLOAD / name).read_text(encoding="utf-8"))


def test_upload_fixtures_carry_no_credentials():
    for name in ("spark_pdf_spec_01.json", "spark_video_events_01.json"):
        blob = (UPLOAD / name).read_text(encoding="utf-8")
        for banned in BANNED:
            assert banned not in blob, f"{name} contains {banned!r}"
    for name in ("sam_video_upload_01.jsonl", "sam_video_upload_trimmed.jsonl"):
        blob = (UPLOAD / name).read_text(encoding="utf-8")
        for banned in BANNED:
            assert banned not in blob, f"{name} contains {banned!r}"


def test_trimmed_fixture_matches_generator_script():
    """The 3-frame unit-test copy is byte-identical to what the committed
    trim script generates from the live 75-frame capture."""
    import subprocess
    import sys

    script = ROOT / "scripts" / "trim_sam_video_fixture.py"
    assert script.exists()
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_trimmed_fixture_holds_first_three_frames():
    from mmc.meta.client import _stream_text_and_usage
    from mmc.meta.sam import load_stream_fixture

    events = load_stream_fixture(UPLOAD / "sam_video_upload_trimmed.jsonl")
    text, usage = _stream_text_and_usage(events)
    assert usage.get("video_frames_processed") == 3
    frames = sorted(
        {int(line[1:].split("f>", 1)[0]) for line in text.split("\n") if line.strip()}
    )
    assert frames == [0, 1, 2]


def test_live_flags_match_decisions_capture_note():
    decisions = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    for name in ("spark_pdf_spec_01", "spark_video_events_01", "sam_video_upload_01"):
        assert name in decisions, f"DECISIONS.md never mentions {name}"
        record_name = f"{name}.json" if not name.startswith("sam_") else f"{name}.jsonl"
        if record_name.endswith(".json"):
            record = _load(record_name)
            assert record["provisional"] is False, f"{record_name} must be live"
            assert record["captured"] == "spike_meta_upload.py owner run", (
                f"{record_name} must carry the live capture note"
            )
    assert "LIVE" in decisions


def test_live_spark_pdf_spec_parses():
    from mmc.meta.spark import _validate_spec_ranges

    record = _load("spark_pdf_spec_01.json")
    ranges = _validate_spec_ranges(record["output_text"])
    assert len(ranges) == 3
    by_id = {r["spec_id"]: r for r in ranges}
    assert by_id["fuel_pressure_kpa"]["min"] == 280
    assert by_id["fuel_pressure_kpa"]["max"] == 420
    assert by_id["fuel_pressure_kpa"]["units"] == "kPa"
    assert by_id["charging_voltage_v"]["min"] == 13.5
    assert by_id["charging_voltage_v"]["max"] == 14.8
    assert by_id["charging_voltage_v"]["units"] == "V"
    assert by_id["ect_warm_plateau_degc"]["min"] == 90
    assert by_id["ect_warm_plateau_degc"]["max"] == 98
    assert by_id["ect_warm_plateau_degc"]["units"] == "degC"


def test_live_spark_video_events_parse():
    import jsonschema

    from mmc.meta.spark import VALIDATORS

    record = _load("spark_video_events_01.json")
    data = json.loads(record["output_text"])
    VALIDATORS["video_events"].validate(data)
    assert len(data["events"]) == 1
    event = data["events"][0]
    assert event["label"] == "hood open showing engine bay"
    assert event["sam_concept"] == "hood"


@pytest.mark.slow
def test_live_sam_video_stream_parses():
    """Full 75-frame live stream: boxes for every frame, masks capped to
    every 3rd frame (25), converted in the bounded pool.

    Wall target < 25 s on a many-core box (serial was 336 s: 101 s parse +
    235 s convert); the bound relaxes on small machines where the pool has
    few workers, but must stay far below the old serial cost.
    """
    import os
    import time

    from mmc.meta.client import _stream_text_and_usage
    from mmc.meta.sam import (
        build_mask_jobs,
        convert_frame_job,
        extract_boxes_fast,
        load_stream_fixture,
        submit_mask_jobs,
    )

    events = load_stream_fixture(UPLOAD / "sam_video_upload_01.jsonl")
    assert len(events) >= 1
    _text, usage = _stream_text_and_usage(events)
    assert usage.get("video_frames_processed") == 75
    t0 = time.time()
    boxes = extract_boxes_fast(events)  # request path: no mask decode
    t_boxes = time.time() - t0
    assert len(boxes) == 75  # boxes stay per frame
    assert sorted(b["frame_index"] for b in boxes) == list(range(75))
    jobs = build_mask_jobs(events, "engine")
    assert [j["frame_index"] for j in jobs] == list(range(0, 75, 3))  # 25 overlay frames
    future = submit_mask_jobs(jobs)  # bounded pool: min(16, cpu_count)
    frame_results = future.result(timeout=600)
    wall = time.time() - t0
    assert len(frame_results) == 25
    masks = [m for r in frame_results for m in r["masks"]]
    assert len(masks) == 25
    assert all(m["svg_path"].startswith("M") for m in masks)
    assert all(m["box"] is not None for m in masks)
    # Worker boxes for converted frames match the fast scan.
    fast_by_frame = {b["frame_index"]: b for b in boxes}
    for result in frame_results:
        for box in result["boxes"]:
            assert box == fast_by_frame[box["frame_index"]]
    # One spot frame converts identically through the sync path.
    sync_one = convert_frame_job(jobs[0])
    assert frame_results[0] == sync_one
    cpus = os.cpu_count() or 1
    bound = 25.0 if cpus >= 16 else 240.0
    assert wall < bound, f"75-frame parse+overlays took {wall:.1f}s (bound {bound}s)"
    assert t_boxes < 5.0
