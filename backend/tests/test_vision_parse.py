"""M5 vision parsing tests (all offline).

components: schema validation, probability-field rejection, Observation
mapping. instruments: retake rule, confirm gate. keyframes: sharp/change/
tap/torch logic mirroring KeyframeSelectorTest.kt. sam: parser shapes +
video-segmentation response with <=3 objects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mmc.meta import sam as sam_mod
from mmc.meta.sam import (
    MAX_TRACKED_OBJECTS,
    check_video_bytes,
    load_stream_fixture,
    object_prompt_text,
    parse_image_events,
    parse_video_events,
    result_from_image,
)
from mmc.vision import components as comp_mod
from mmc.vision import instruments as instr_mod
from mmc.vision import keyframes as kf


def _components_raw():
    return {
        "components": [
            {"name": "Air Filter Housing", "sam_concept": "air filter housing", "confidence": 0.9},
            {"name": "Battery", "sam_concept": "car battery", "confidence": 0.8},
        ],
        "anomalies": [
            {"type": "corrosion", "region_hint": "battery terminal", "confidence": 0.7}
        ],
    }


# -- components -----------------------------------------------------------


def test_parse_components_accepts_valid():
    assert comp_mod.parse_components(_components_raw())["components"][0]["name"] == "Air Filter Housing"


def test_parse_components_rejects_bad_schema():
    bad = {"components": [{"name": "x"}]}  # missing sam_concept/confidence/anomalies
    with pytest.raises(Exception):
        comp_mod.parse_components(bad)


@pytest.mark.parametrize("key", sorted(comp_mod.BANNED_KEYS))
def test_parse_components_rejects_probability_fields(key):
    raw = _components_raw()
    raw["components"][0][key] = 0.5
    with pytest.raises(ValueError, match="[Pp]robability"):
        comp_mod.parse_components(raw)


def test_parse_components_rejects_nested_probability_fields():
    raw = _components_raw()
    raw["anomalies"][0]["posterior"] = 0.9
    with pytest.raises(ValueError, match="[Pp]robability"):
        comp_mod.parse_components(raw)


def test_to_observations_have_no_probability_keys():
    obs = comp_mod.to_observations(_components_raw(), "ev1")
    assert len(obs) == 3
    blob = str(obs)
    for key in comp_mod.BANNED_KEYS:
        assert f"'{key}'" not in blob
    feats = [o["feature"] for o in obs]
    assert "visual_component:air_filter_housing" in feats
    assert "visual_anomaly:corrosion" in feats
    assert all(o["evidence_ids"] == ["ev1"] for o in obs)


# -- instruments ------------------------------------------------------------


def test_parse_instrument_ok():
    parsed = instr_mod.parse_instrument({"value": 12.42, "units": "V", "confidence": 0.94})
    assert parsed == {"value": 12.42, "units": "V", "confidence": 0.94, "needs_retake": False}


def test_parse_instrument_retake_below_threshold():
    parsed = instr_mod.parse_instrument({"value": 12.4, "units": "V", "confidence": 0.5})
    assert parsed["needs_retake"] is True


def test_parse_instrument_rejects_non_number():
    with pytest.raises(Exception):
        instr_mod.parse_instrument({"value": "twelve", "units": "V", "confidence": 0.9})


def test_confirm_builds_evidence_and_observation():
    reading = instr_mod.parse_instrument({"value": 12.42, "units": "V", "confidence": 0.94})
    evidence, obs = instr_mod.confirm_reading(reading, "blobs/abc", "abc")
    assert evidence["type"] == "measurement" and evidence["blob_ref"] == "blobs/abc"
    assert obs["value"] == 12.42 and obs["evidence_ids"] == [evidence["evidence_id"]]


def test_confirm_refuses_retake_reading():
    reading = instr_mod.parse_instrument({"value": 1.0, "units": "V", "confidence": 0.2})
    with pytest.raises(ValueError, match="retake"):
        instr_mod.confirm_reading(reading, "blobs/abc", "abc")


# -- keyframes --------------------------------------------------------------


def _sharp(w: int = 8, h: int = 8) -> list[int]:
    return [30 if (i + i // w) % 2 == 0 else 220 for i in range(w * h)]


def _flat(v: int = 120, w: int = 8, h: int = 8) -> list[int]:
    return [v] * (w * h)


def test_laplacian_flat_vs_textured():
    assert kf.laplacian_variance(_flat(), 8, 8) < kf.SHARPNESS_THRESHOLD
    assert kf.laplacian_variance(_sharp(), 8, 8) >= kf.SHARPNESS_THRESHOLD


def test_picker_first_sharp_kept_blurry_dropped():
    p = kf.KeyframePicker()
    assert p.offer(_sharp(), 8, 8, 0.0) is True
    assert kf.KeyframePicker().offer(_flat(), 8, 8, 0.0) is False


def test_picker_dedups_unchanged_scene():
    p = kf.KeyframePicker()
    assert p.offer(_sharp(), 8, 8, 0.0) is True
    assert p.offer(_sharp(), 8, 8, 4.0) is False


def test_picker_rate_limits_and_tap_overrides():
    p = kf.KeyframePicker()
    assert p.offer(_sharp(), 8, 8, 0.0) is True
    other = [30 if (i // 8) % 2 == 0 else 220 for i in range(64)]
    assert kf.is_sharp(other, 8, 8)
    assert p.offer(other, 8, 8, 1.0) is False
    assert p.offer(other, 8, 8, 1.5, tapped=True) is True
    assert p.offer(_flat(), 8, 8, 9.0, tapped=True) is False


def test_low_light_gate():
    assert kf.is_low_light(_flat(20)) is True
    assert kf.is_low_light(_flat(120)) is False


# -- sam streams (captured live 2026-10-28, official parser) ------------------

VISION_FIXTURES = Path(__file__).parent / "fixtures" / "vision"


def test_replay_battery_stream_finds_three_objects():
    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    seg = parse_image_events(events)
    assert seg.outcome.status == "completed"
    assert not seg.diagnostics
    res = result_from_image("battery", seg)
    assert res.found
    assert [m.object_id for m in res.masks] == ["0", "1", "2"]
    boxes = {(m.box.x_min, m.box.y_min, m.box.x_max, m.box.y_max) for m in res.masks}
    assert (963, 690, 1131, 831) in boxes  # half-open: x2+1, y2+1
    for m in res.masks:
        assert m.box is not None
        assert m.svg_path.startswith("M")
        assert len(m.rle["size"]) == 2 and all(d > 0 for d in m.rle["size"])
        assert m.rle["counts"]
    overlays = sam_mod.overlays_for_result(res)
    assert len(overlays) == 3 and all(o["box"] is not None for o in overlays)


def test_replay_empty_stream_is_not_found_not_error():
    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_air_filter_housing.jsonl")
    seg = parse_image_events(events)
    assert seg.outcome.status == "completed"
    res = result_from_image("air filter housing", seg)
    assert not res.found and res.masks == []


def test_replay_battery_usage_has_images_processed_not_tokens():
    import json

    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    usage = next(e["response"]["usage"] for e in events if e.get("type") == "response.completed")
    assert usage["images_processed"] == 1
    assert usage["input_tokens"] == 0 and usage["output_tokens"] == 0
    from mmc.meta.budget import actual_cost_from_usage

    assert actual_cost_from_usage("sam-3.1", usage) == pytest.approx(0.0025)


def test_object_prompt_builds_box_grammar():
    text = object_prompt_text(
        (1280, 960), {"x_min": 250, "y_min": 250, "x_max": 750, "y_max": 750}
    )
    assert text == "<0f>0<|box;x1=250;y1=250;x2=750;y2=750;w=1280;h=960|>"


def test_object_prompt_point_labels():
    text = object_prompt_text((1280, 960), point={"x": 10, "y": 20})
    assert "<|point;x=10;y=20;w=1280;h=960|>" in text
    neg = object_prompt_text((1280, 960), point={"x": 10, "y": 20, "label": 0})
    assert neg.startswith("-<|point;") or "-<|point;" in neg
    with pytest.raises(ValueError):
        object_prompt_text((1280, 960))
    with pytest.raises(ValueError):
        object_prompt_text((1280, 960), box={"x_min": 5, "y_min": 5})  # missing keys


def test_concept_gate_rejects_comma_lists_and_blanks():
    with pytest.raises(ValueError):
        sam_mod._check_concept("battery, alternator")
    with pytest.raises(ValueError):
        sam_mod._check_concept("   ")


def _two_frame_video_events() -> list[dict]:
    """Same captured payloads re-emitted under <0f>/<1f> headers."""
    battery = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    done = next(e for e in battery if e.get("type") == "response.output_text.done")
    body = done["text"].split(">", 1)[1]
    text = f"<0f>{body}\n<1f>{body}"
    return [
        {"type": "response.output_text.delta", "delta": text,
         "item_id": "m", "output_index": 0, "content_index": 0},
        {"type": "response.output_text.done", "text": text,
         "item_id": "m", "output_index": 0, "content_index": 0},
        {"type": "response.content_part.done",
         "part": {"type": "output_text", "text": text},
         "item_id": "m", "output_index": 0, "content_index": 0},
        {"type": "response.output_item.done",
         "item": {"id": "m", "type": "message"}, "output_index": 0},
        {"type": "response.completed",
         "response": {"status": "completed", "usage": {"images_processed": 0}}},
    ]


def test_parse_video_stream_frames():
    """Same captured payloads re-emitted under <0f>/<1f> headers parse as
    two video frames (video_segmentation_format)."""
    events = _two_frame_video_events()
    seg = parse_video_events(events)
    assert seg.media == "video"
    frames = sorted(
        {r.frame.frame_index for r in seg.records if getattr(r, "frame", None) is not None}
    )
    assert frames == [0, 1]
    assert len([r for r in seg.records if r.kind == "mask"]) == 6


def test_segment_image_uses_verified_input_shape_and_cost(tmp_path):
    """Fake transport replays the captured battery stream: input must be a
    single user message wrapping [input_text, input_image] with
    stream=True (a bare parts list is rejected live with 400), cost from
    images_processed."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["model"] = model
        seen["kwargs"] = kwargs
        return events

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    res, record = sam_mod.segment_image("battery", b"\xff\xd8fake", client)
    assert res.found and len(res.masks) == 3
    assert seen["kwargs"]["stream"] is True
    _assert_user_message(seen["kwargs"]["input"], "battery")
    assert record["usage"]["images_processed"] == 1
    assert record["cost_usd"] == pytest.approx(0.0025)
    assert client.budget.spend_usd == pytest.approx(0.0025)


def _assert_user_message(inputs: object, concept: str) -> list[dict]:
    """The live-verified SAM shape: ONE user message whose content is
    input_text (the concept) then input_image (data URL)."""
    assert isinstance(inputs, list) and len(inputs) == 1
    message = inputs[0]
    assert message.get("role") == "user"
    parts = message["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_image"]
    assert parts[0]["text"] == concept
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")
    return parts


def test_segment_image_input_is_single_user_message(tmp_path):
    """Regression: SAM 3.1 rejects a bare [input_text, input_image] list
    with ``400 input[0] did not match any supported type``."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return events

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    res, _record = sam_mod.segment_image("battery", b"\xff\xd8fake", client)
    assert res.found
    _assert_user_message(seen["kwargs"]["input"], "battery")


def test_segment_with_fallback_retries_once_on_empty(tmp_path):
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    empty = load_stream_fixture(VISION_FIXTURES / "sam_stream_air_filter_housing.jsonl")
    full = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    calls: list[str] = []

    def transport(model: str, kwargs: dict):
        concept = kwargs["input"][0]["content"][0]["text"]
        calls.append(concept)
        return empty if concept == "air filter housing" else full

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    res, records, used = sam_mod.segment_with_fallback(
        "air filter housing", "battery", b"\xff\xd8fake", client
    )
    assert used == "battery" and res.found
    assert calls == ["air filter housing", "battery"]
    assert len(records) == 2

    # No fallback given: empty result passes through without a second call.
    calls.clear()
    res2, records2, used2 = sam_mod.segment_with_fallback(
        "air filter housing", None, b"\xff\xd8fake", client
    )
    assert used2 == "air filter housing" and not res2.found and len(records2) == 1


def _png_bytes(w: int = 1280, h: int = 960) -> bytes:
    """Minimal PNG header bytes sized w x h (enough for image_size)."""
    import struct

    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", w, h)


@pytest.mark.parametrize(
    "extra",
    [
        {"box": {"x_min": 963, "y_min": 690, "x_max": 1130, "y_max": 830}},
        {"point": {"x": 1000, "y": 750}},
    ],
)
def test_segment_image_object_prompt_sends_no_phrase(tmp_path, extra):
    """Live 2026-10-28 correction: the API rejects
    ``400 invalid prompt: object prompts cannot be combined with a phrase``,
    so a box/point call must send the object-prompt text ALONE as
    input_text. The concept stays in the request record and the SamResult
    as the overlay label."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return events

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    expected = sam_mod.object_prompt_text((1280, 960), **extra)
    res, record = sam_mod.segment_image("battery", _png_bytes(), client, **extra)
    assert res.found and res.concept == "battery"
    parts = seen["kwargs"]["input"][0]["content"]
    assert parts[0]["text"] == expected
    assert "battery" not in parts[0]["text"]
    assert record["request"]["concept"] == "battery"


def test_segment_image_concept_only_sends_just_phrase(tmp_path):
    """Concept-only calls send the bare noun phrase as input_text."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    events = load_stream_fixture(VISION_FIXTURES / "sam_stream_battery.jsonl")
    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return events

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    res, _record = sam_mod.segment_image("battery", b"\xff\xd8fake", client)
    assert res.found
    parts = seen["kwargs"]["input"][0]["content"]
    assert parts[0]["text"] == "battery"


def test_track_video_box_prompts_send_only_object_prompt(tmp_path):
    """Video box prompts follow the same rule: object-prompt text alone as
    input_text (concepts stay in the request record and label the result)."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return _two_frame_video_events()

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    video = b"\x00" * 4 + b"ftyp" + b"isom" + b"\x00" * 64
    box = {"x_min": 963, "y_min": 690, "x_max": 1130, "y_max": 830}
    res, record = sam_mod.track_video(
        video, "clip.mp4", client, boxes=[box], concepts=["battery"], size=(1280, 960)
    )
    assert res.found and res.concept == "battery"
    parts = seen["kwargs"]["input"][0]["content"]
    assert "battery" not in parts[0]["text"]
    assert "<|box;" in parts[0]["text"]
    assert record["request"]["concepts"] == ["battery"]


def test_video_container_gate():
    assert check_video_bytes(b"\x00" * 4 + b"ftyp" + b"isom" + b"\x00" * 64, "clip.mp4") == "mp4"
    with pytest.raises(ValueError, match="MP4/MOV"):
        check_video_bytes(b"RIFF....WEBM", "clip.webm")


def test_track_video_input_is_single_user_message(tmp_path):
    """Video path uses the same user-message wrapper: input_text prompt
    then the clip as an input_file part (not a bare string + sidecar
    upload kwargs)."""
    from mmc.meta.budget import BudgetTracker
    from mmc.meta.client import MetaClient

    seen: dict = {}

    def transport(model: str, kwargs: dict):
        seen["kwargs"] = kwargs
        return _two_frame_video_events()

    client = MetaClient(
        api_key="test-key-not-a-secret",
        models={"sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )
    video = b"\x00" * 4 + b"ftyp" + b"isom" + b"\x00" * 64
    res, _record = sam_mod.track_video(video, "clip.mp4", client, concepts=["battery"])
    assert res.found
    inputs = seen["kwargs"]["input"]
    assert seen["kwargs"]["stream"] is True
    assert "video_filename" not in seen["kwargs"] and "files_purpose" not in seen["kwargs"]
    assert isinstance(inputs, list) and len(inputs) == 1
    message = inputs[0]
    assert message.get("role") == "user"
    parts = message["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_file"]
    assert "battery" in parts[0]["text"]
    assert parts[1]["filename"] == "clip.mp4"
    assert parts[1]["file_data"].startswith("data:video/mp4;base64,")
