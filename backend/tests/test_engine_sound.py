"""M5O engine-sound DSP tests (all offline, synthetic stdlib WAVs)."""

from __future__ import annotations

import io
import math
import random
import struct
import wave

import pytest

from mmc.vision import engine_sound as es

SR = 44100
DUR = 5.0
RPM = 750.0
CYL = 6
F_FIRE = es.firing_freq_hz(RPM, CYL)


def _synth(kind: str) -> list[float]:
    n = int(SR * DUR)
    out = [0.0] * n
    rnd = random.Random(7)

    def harm(t: float, amp: float = 1.0) -> float:
        v = 0.0
        for h, a in ((1, 0.5), (2, 0.3), (3, 0.2), (4, 0.12)):
            v += a * math.sin(2 * math.pi * h * F_FIRE * t)
        return v * amp

    for i in range(n):
        t = i / SR
        noise = rnd.random() * 2 - 1
        if kind == "clean":
            out[i] = harm(t) + 0.01 * noise
        elif kind == "misfire":
            idx = int(t / (1.0 / F_FIRE))
            amp = 0.15 if (idx % CYL) == (CYL - 1) else 1.0
            out[i] = harm(t, amp) + 0.01 * noise
        elif kind == "tone":
            out[i] = harm(t) * 0.5 + 0.30 * math.sin(2 * math.pi * 3500 * t) + 0.02 * noise
        elif kind == "hiss":
            out[i] = harm(t) * 0.6 + 0.25 * noise
    if kind == "hiss":
        out = [out[i] - 0.9 * out[i - 1] if i else 0.0 for i in range(n)]
    peak = max(abs(v) for v in out) or 1.0
    return [v / peak * 0.9 for v in out]


def _wav(samples: list[float], channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = []
        for v in samples:
            q = int(max(-1.0, min(1.0, v)) * 32767)
            frames.append(struct.pack("<h", q) * channels)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def test_firing_freq_math():
    assert es.firing_freq_hz(750, 6) == pytest.approx(37.5)
    assert es.firing_freq_hz(2500, 4) == pytest.approx(83.333, abs=0.01)


def test_cylinders_parsed_never_guessed():
    assert es.cylinders_from_vehicle({"engine": "2.0L I4"}) == 4
    assert es.cylinders_from_vehicle({"engine": "3.5L V6"}) == 6
    assert es.cylinders_from_vehicle({"engine": "5.0L V8"}) == 8
    assert es.cylinders_from_vehicle({"engine": "Twin-turbo"}) is None
    assert es.cylinders_from_vehicle({}) is None
    assert es.cylinders_from_vehicle(None) is None


def test_misfire_dropout_fires_with_expected_numbers():
    res = es.analyze(_wav(_synth("misfire")), RPM, CYL)
    assert res["outcome"] == "misfire_dropout"
    assert res["details"]["f_fire_hz"] == pytest.approx(37.5)
    assert res["details"]["dropout_depth"] == pytest.approx(0.84, abs=0.1)
    assert res["details"]["period_s"] == pytest.approx(0.16, abs=0.01)
    assert 0.0 <= res["confidence"] <= 1.0


def test_narrowband_tone_fires_with_expected_numbers():
    res = es.analyze(_wav(_synth("tone")), RPM, CYL)
    assert res["outcome"] == "belt_bearing_tone"
    assert res["details"]["peak_hz"] == pytest.approx(3500, abs=150)
    assert res["details"]["prominence_db"] >= 18.0
    assert res["details"]["duration_s"] >= 1.0


def test_broadband_hiss_fires():
    res = es.analyze(_wav(_synth("hiss")), RPM, CYL)
    assert res["outcome"] == "vacuum_hiss"
    assert res["details"]["band_ratio"] >= 0.45
    assert res["details"]["flatness"] >= 0.55
    assert res["details"]["idle_only"] is True


def test_hiss_idle_cruise_pair_flags_idle_only_false():
    idle = _wav(_synth("hiss"))
    cruise = _wav(_synth("clean"))
    res = es.analyze(idle, RPM, CYL, cruise_wav=cruise, cruise_rpm=2500.0)
    assert res["outcome"] == "vacuum_hiss"
    assert res["details"]["idle_only"] is False
    assert "cruise_band_ratio" in res["details"]


def test_clean_idle_is_normal():
    res = es.analyze(_wav(_synth("clean")), RPM, CYL)
    assert res["outcome"] == "normal"
    assert 0.0 <= res["confidence"] <= 1.0


def test_missing_cylinders_is_inconclusive_never_guessed():
    res = es.analyze(_wav(_synth("misfire")), RPM, None)
    assert res["outcome"] == "inconclusive"
    res2 = es.analyze(_wav(_synth("misfire")), RPM, 99)
    assert res2["outcome"] == "inconclusive"


def test_stereo_mixed_to_mono():
    res = es.analyze(_wav(_synth("clean"), channels=2), RPM, CYL)
    assert res["outcome"] == "normal"


def test_non_wav_raises():
    with pytest.raises(ValueError):
        es.analyze(b"not a wav", RPM, CYL)


def test_observation_shape_and_extractor():
    res = es.analyze(_wav(_synth("tone")), RPM, CYL)
    obs = es.to_observation(res, "ev1")
    assert obs["extractor"] == "dsp:engine_sound_v1"
    assert obs["feature"] == "acoustic:narrowband_tone"
    assert obs["value"]["peak_hz"] == pytest.approx(3500, abs=150)
    assert obs["evidence_ids"] == ["ev1"]


def test_both_packs_load_with_sound_test():
    from mmc.kp.loader import PACKS_DIR, load_pack

    for name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / name)
        test = pack.tests["engine_sound_screen"]
        assert test.kind == "sensory"
        assert test.min_level == "L1"
        assert test.safety_gate == "engine_running_outdoors"
        assert test.evaluator == "dsp.engine_sound"
        assert test.outcomes == [
            "misfire_dropout", "belt_bearing_tone", "vacuum_hiss", "normal", "inconclusive",
        ]
        for hyp, row in test.likelihoods.items():
            assert abs(sum(row[o] for o in test.outcomes) - 1.0) <= 1e-9, (name, hyp)
            for outcome, p in row.items():
                assert 0.02 <= p <= 0.95, (name, hyp, outcome)
                assert p <= 0.75, (name, hyp, outcome)


def test_evaluator_passthrough_and_posterior_route():
    from mmc.engine import evaluator as evaluator_mod
    from mmc.kp.loader import PACKS_DIR, load_pack

    pack = load_pack(PACKS_DIR / "ford_gen_v1.yaml")
    test = pack.tests["engine_sound_screen"]
    outcome, expected = evaluator_mod.evaluate(test, {"outcome": "vacuum_hiss"})
    assert outcome == "vacuum_hiss"
    assert expected["source"]["kind"] == "kp"
    with pytest.raises(ValueError):
        evaluator_mod.evaluate(test, {"outcome": "not_an_outcome"})


def test_audio_stub_still_off_even_when_flagged(monkeypatch):
    from mmc.meta.spark_open import SparkOpenClient

    client = SparkOpenClient(
        api_key="k", cache_dir="/tmp/mmc-nope", replay_cached=False,
        transport=lambda m, kw: None, sleep=lambda s: None,
    )
    with pytest.raises(NotImplementedError):
        client.chat_audio()
    monkeypatch.setenv("SPARK_OPEN_AUDIO", "true")
    with pytest.raises(NotImplementedError):
        client.chat_audio()


def test_engine_sound_end_to_end_moves_posterior_only_via_test_result(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from mmc.api import routes_vision as vision_routes
    from mmc.main import app
    from mmc import sessions as sess_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    client = TestClient(app)
    sid = client.post(
        "/sessions",
        json={"user_level": "L1", "tools": [], "mode": "live",
              "vehicle": {"make": "Ford", "model": "F-150", "year": 2015,
                          "engine": "3.5L V6"}},
    ).json()["session_id"]
    before = [h["current"] for h in client.get(f"/sessions/{sid}/state").json()["hypotheses"]]
    blob = client.post(
        f"/sessions/{sid}/blobs", files={"file": ("idle.wav", _wav(_synth("hiss")), "audio/wav")}
    ).json()["blob_ref"]
    ack = client.post(f"/sessions/{sid}/events",
                      json={"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}})
    assert ack.status_code == 200, ack.text
    r = client.post(f"/sessions/{sid}/vision/engine-sound",
                    json={"blob_ref": blob, "rpm": 750.0, "cylinders": 6})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "vacuum_hiss"
    mid = [h["current"] for h in client.get(f"/sessions/{sid}/state").json()["hypotheses"]]
    assert mid == before  # DSP observations never move posteriors by themselves
    t = client.post(f"/sessions/{sid}/tests/engine_sound_screen/result",
                    json={"measured": {"outcome": body["outcome"]},
                          "evidence_ids": [body["evidence_id"]]})
    assert t.status_code == 200, t.text
    after = [h["current"] for h in client.get(f"/sessions/{sid}/state").json()["hypotheses"]]
    assert after != before  # only the test_result route moves posteriors
    top = client.get(f"/sessions/{sid}/state").json()["hypotheses"][0]
    assert top["hypothesis_id"] == "vacuum_leak_post_maf"
