"""Findings #2 + #9: response-cache keys must be the sha256 of the ORIGINAL
input bytes everywhere (Spark image, SAM image, SAM video, Spark Open),
with the sent-bytes hash and scale kept as record metadata.

#2 probe: two different 1024-byte JPEGs sharing a header produce identical
request SHAs, so replay of a second photo silently returns the first one's
answer at $0.
#9 probe: the SAM image key hashes ffmpeg-downscaled bytes, so a cache
recorded with ffmpeg misses without it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from mmc.meta.budget import BudgetTracker
from mmc.meta.client import MetaClient, NoCachedResponse

VISION = Path(__file__).parent / "fixtures" / "vision"

# Same first 24 bytes (one shared JPEG SOI/APP header) + different tails, so
# the old stub (b64[:32] + length) collides while the full sha256 does not.
_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\x00\x00\x00\x00"
assert len(_HEADER) == 24
IMG_A = _HEADER + b"A" * 1000
IMG_B = _HEADER + b"B" * 1000
EMPTY_STREAM = VISION / "sam_stream_air_filter_housing.jsonl"


def _meta_client(tmp_path, transport):
    return MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3", "sam": "sam-3.1"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "cache",
        replay_cached=False,
        transport=transport,
        sleep=lambda s: None,
    )


def _replay_client(tmp_path, transport):
    client = _meta_client(tmp_path, transport)
    client.replay_cached = True
    return client


def _spark_transport(payload: dict):
    def transport(model, kwargs):
        return SimpleNamespace(
            output_text=json.dumps(payload),
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    return transport


def test_spark_image_stub_carries_full_original_sha():
    from mmc.meta.spark import _image_part

    stub = _image_part(IMG_A)
    assert stub["sha256"] == hashlib.sha256(IMG_A).hexdigest()
    assert _image_part(IMG_B)["sha256"] != stub["sha256"]


def test_spark_replay_second_photo_misses_first_cache(tmp_path):
    """#2 scenario: recording photo A then replaying photo B must not
    return A's components at $0."""
    from mmc.meta import spark as spark_mod

    comp_a = {"components": [{"name": "A", "sam_concept": "a", "confidence": 0.9}], "anomalies": []}
    live = _meta_client(tmp_path, _spark_transport(comp_a))
    parsed_a, _ = spark_mod.analyze_components(IMG_A, live)

    def boom(model, kwargs):
        raise AssertionError("replay must not touch the network on a hit")

    replay = _replay_client(tmp_path, boom)
    _, record_a = spark_mod.analyze_components(IMG_A, replay)
    assert record_a["cached"] is True
    assert parsed_a["components"][0]["name"] == "A"
    with __import__("pytest").raises(NoCachedResponse):
        spark_mod.analyze_components(IMG_B, replay)


def test_sam_image_key_uses_original_bytes(tmp_path):
    """#9 scenario: same original, different sent bytes (ffmpeg vs not)
    must share one cache key; the sent hash rides as record metadata."""
    from mmc.meta import sam as sam_mod

    events = sam_mod.load_stream_fixture(EMPTY_STREAM)

    def transport(model, kwargs):
        return [dict(e) for e in events]

    sent_variants = iter(
        [
            (b"SENT-A" * 100, 0.5, (100, 100), (50, 50)),
            (b"SENT-B" * 100, 0.5, (100, 100), (50, 50)),
        ]
    )
    real_downscale = sam_mod.downscale_image
    shas = []
    try:
        sam_mod.downscale_image = lambda b: next(sent_variants)  # noqa: E731
        client = _meta_client(tmp_path, transport)
        _, record_a = sam_mod.segment_image("battery", IMG_A, client)
        _, record_b = sam_mod.segment_image("battery", IMG_A, client)
        shas = [record_a["request_sha256"], record_b["request_sha256"]]
    finally:
        sam_mod.downscale_image = real_downscale
    assert shas[0] == shas[1]
    for record in (record_a, record_b):
        assert record["request"]["orig_sha256"] == hashlib.sha256(IMG_A).hexdigest()
        assert record["request"]["orig_bytes"] == len(IMG_A)


def test_sam_image_distinct_originals_get_distinct_keys(tmp_path):
    from mmc.meta import sam as sam_mod

    events = sam_mod.load_stream_fixture(EMPTY_STREAM)

    def transport(model, kwargs):
        return [dict(e) for e in events]

    client = _meta_client(tmp_path, transport)
    _, record_a = sam_mod.segment_image("battery", IMG_A, client)
    _, record_b = sam_mod.segment_image("battery", IMG_B, client)
    assert record_a["request_sha256"] != record_b["request_sha256"]
    assert record_a["request"]["orig_sha256"] != record_b["request"]["orig_sha256"]


def test_sam_video_request_carries_original_sha(tmp_path):
    from mmc.meta import sam as sam_mod

    clip = b"\x00\x00\x00\x18ftypmp42" + b"V" * 4096
    sent_variants = iter(
        [
            (b"SENT-A" * 500, 0.5, (1280, 960), (640, 480)),
            (b"SENT-B" * 500, 0.5, (1280, 960), (640, 480)),
        ]
    )
    real_downscale = sam_mod.downscale_video
    try:
        sam_mod.downscale_video = lambda *a, **k: next(sent_variants)  # noqa: E731
        live_input, _prompt, _sent, _container, request, _scale, _key = (
            sam_mod._prepare_video_call(clip, "clip.mp4", None, ["engine"], 50, (1280, 960), "inline", None)
        )
    finally:
        sam_mod.downscale_video = real_downscale
    assert request["orig_sha256"] == hashlib.sha256(clip).hexdigest()
    assert request["orig_bytes"] == len(clip)


def test_sam_video_key_stable_across_sent_variants(tmp_path):
    from mmc.meta import sam as sam_mod

    clip = b"\x00\x00\x00\x18ftypmp42" + b"V" * 4096
    calls = {"n": 0}

    def transport(model, kwargs):
        calls["n"] += 1
        return sam_mod.load_stream_fixture(
            Path(__file__).parent / "fixtures" / "upload" / "sam_video_upload_trimmed.jsonl"
        )

    sent_variants = iter(
        [
            (b"SENT-A" * 500, 0.5, (1280, 960), (640, 480)),
            (b"SENT-B" * 500, 0.5, (1280, 960), (640, 480)),
        ]
    )
    real_downscale = sam_mod.downscale_video
    try:
        sam_mod.downscale_video = lambda *a, **k: next(sent_variants)  # noqa: E731
        client = _meta_client(tmp_path, transport)
        _, record_a = sam_mod.track_video_raw(
            clip, "clip.mp4", client, concepts=["engine"], size=(1280, 960)
        )
        _, record_b = sam_mod.track_video_raw(
            clip, "clip.mp4", client, concepts=["engine"], size=(1280, 960)
        )
    finally:
        sam_mod.downscale_video = real_downscale
    assert record_a["request_sha256"] == record_b["request_sha256"]


def test_spark_open_stub_carries_full_original_sha():
    from mmc.meta.spark_open import _image_stub

    stub = _image_stub(IMG_A)
    assert stub["_sha256"] == hashlib.sha256(IMG_A).hexdigest()
    assert _image_stub(IMG_B)["_sha256"] != stub["_sha256"]
