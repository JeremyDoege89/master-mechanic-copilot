"""M5U upload fixture tests (§1): live spike outputs stay honest.

All three fixtures are key-free, schema-parse, and carry provisional:false +
captured:"spike_meta_upload.py owner run" since the owner run of
scripts/spike_meta_upload.py on 2026-10-29. The live flag must match the
capture note in docs/DECISIONS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    blob = (UPLOAD / "sam_video_upload_01.jsonl").read_text(encoding="utf-8")
    for banned in BANNED:
        assert banned not in blob


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


def test_live_sam_video_stream_parses():
    from mmc.meta.client import _stream_text_and_usage
    from mmc.meta.sam import load_stream_fixture, parse_video_events, result_from_image

    events = load_stream_fixture(UPLOAD / "sam_video_upload_01.jsonl")
    assert len(events) >= 1
    _text, usage = _stream_text_and_usage(events)
    assert usage.get("video_frames_processed") == 75
    seg = parse_video_events(events)
    result = result_from_image("engine", seg)
    assert result.found is True  # live stream tracks the box on all 75 frames
    assert len(result.masks) == 75
