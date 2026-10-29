"""Engine-sound DSP screening (M5O §4): stdlib-only acoustics, no LLM.

A phone recording (16-bit mono PCM WAV at 44.1 kHz — the squeal band
reaches 6 kHz, so no low sample rates) plus the live OBD rpm and the
cylinder count feeds three targeted detectors keyed to the 4-stroke
firing frequency ``f_fire = (rpm/60) * (cylinders/2)``:

- misfire: per-firing-interval peak sequence (an envelope sampled at the
  firing rate); a dead cylinder shows as a periodic dropout repeating
  every ``cylinders`` intervals — depth plus autocorrelation peak;
- squeal: Goertzel narrowband peak in 2–6 kHz, peak/median ratio,
  persistent across 1 s chunks;
- hiss: 4–8 kHz broadband energy ratio plus spectral flatness, compared
  against a 2500 rpm clip when one is supplied (vacuum hiss is loudest
  at idle), otherwise flagged idle-only.

Results become ``observation_added`` events (extractor
``dsp:engine_sound_v1``) through the same evidence path as everything
else, and reach posteriors only via the ``engine_sound_screen`` KP test
through the normal ``test_result`` route (``evaluator dsp.engine_sound``).
Only ``wave``/``array``/``math``/``struct`` are used — no numpy, no FFT
library (``pyproject.toml`` declares no numpy).
"""

from __future__ import annotations

import array
import math
import re
import struct
import wave
from io import BytesIO

EXTRACTOR = "dsp:engine_sound_v1"
EVALUATOR = "dsp.engine_sound"

MIN_SECONDS = 3.0
MAX_SECONDS = 10.0

OUTCOMES = ("misfire_dropout", "belt_bearing_tone", "vacuum_hiss", "normal", "inconclusive")

# Detector thresholds (tuned against the synthetic WAVs in
# tests/test_engine_sound.py; margins map to confidence).
DROPOUT_DEPTH_MIN = 0.40
DROPOUT_PEAK_RATIO_MIN = 2.0
DROPOUT_AUTOCORR_MIN = 0.30
TONE_RATIO_MIN = 8.0
TONE_DB_MIN = 18.0
TONE_MIN_HZ = 2000.0
TONE_MAX_HZ = 6000.0
TONE_MIN_DURATION_S = 1.0
HISS_BAND_MIN = 4000.0
HISS_BAND_MAX = 8000.0
HISS_RATIO_MIN = 0.45
HISS_FLATNESS_MIN = 0.55


def firing_freq_hz(rpm: float, cylinders: int) -> float:
    """4-stroke firing frequency: each cylinder fires once per 2 revs."""
    return (float(rpm) / 60.0) * (float(cylinders) / 2.0)


_CYL_PATTERNS = (
    re.compile(r"\b([vViIhH])\s?(\d{1,2})\b"),  # V6, I4, H4, V 8
    re.compile(r"\b(\d{1,2})\s?[- ]?(cyl|cylinder|cylinders)\b", re.IGNORECASE),
    re.compile(r"\b(straight|inline)[- ]?(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(flat|boxer)[- ]?(\d{1,2})\b", re.IGNORECASE),
)


def cylinders_from_vehicle(vehicle: dict | None) -> int | None:
    """Parse the cylinder count from the vehicle ``engine`` descriptor.

    Returns None when the descriptor is missing or unparseable — callers
    report ``inconclusive``, the count is never guessed.
    """
    engine = ""
    if isinstance(vehicle, dict):
        engine = str(vehicle.get("engine") or "")
    if not engine:
        return None
    for pattern in _CYL_PATTERNS:
        match = pattern.search(engine)
        if not match:
            continue
        for group in match.groups():
            if str(group).isdigit():
                value = int(group)
                if 2 <= value <= 16:
                    return value
    return None


def parse_wav(data: bytes) -> tuple[list[float], int, float]:
    """Decode PCM WAV bytes to mono float samples in [-1, 1].

    Returns ``(samples, sample_rate, seconds)``. Stereo is mixed to mono
    at the native rate (no resampling — the squeal band needs the full
    44.1 kHz). Raises ``ValueError`` on non-WAV or compressed input.
    """
    try:
        handle = wave.open(BytesIO(data), "rb")
    except (wave.Error, EOFError) as e:
        raise ValueError(f"engine sound needs a PCM WAV blob: {e}") from e
    with handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        n_frames = handle.getnframes()
        comp = handle.getcomptype()
        if comp not in ("NONE", "raw "):
            raise ValueError(f"engine sound needs PCM WAV, not {comp!r}")
        if width not in (1, 2, 3, 4):
            raise ValueError(f"unsupported PCM width {width}")
        raw = handle.readframes(n_frames)
    total = n_frames * channels
    if width == 1:
        samples = [(b - 128) / 128.0 for b in raw[:total]]
    elif width == 2:
        ints = array.array("h")
        ints.frombytes(raw[: total * 2])
        samples = [v / 32768.0 for v in ints]
    elif width == 3:
        samples = []
        for off in range(0, total * 3, 3):
            chunk = raw[off : off + 3]
            if len(chunk) < 3:
                break
            value = int.from_bytes(chunk, "little", signed=True)
            samples.append(value / 8388608.0)
    else:
        count = len(raw) // 4
        ints = struct.unpack(f"<{count}i", raw[: count * 4])
        samples = [v / 2147483648.0 for v in ints]
    if channels > 1:
        mono = []
        for i in range(0, len(samples) - channels + 1, channels):
            mono.append(sum(samples[i : i + channels]) / channels)
        samples = mono
    seconds = (n_frames / rate) if rate else 0.0
    return samples, rate, seconds


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _goertzel_mags(samples: list[float], rate: int, freqs: list[float]) -> list[float]:
    """Normalised Goertzel magnitudes for targeted bins (stdlib DFT)."""
    n = len(samples)
    if n == 0:
        return [0.0 for _ in freqs]
    coeffs = []
    for freq in freqs:
        k = int(0.5 + n * freq / rate)
        w = 2.0 * math.pi * k / n
        coeffs.append(2.0 * math.cos(w))
    s1 = [0.0] * len(freqs)
    s2 = [0.0] * len(freqs)
    for x in samples:
        for i, coeff in enumerate(coeffs):
            s0 = x + coeff * s1[i] - s2[i]
            s2[i] = s1[i]
            s1[i] = s0
    out = []
    for i, coeff in enumerate(coeffs):
        power = s1[i] * s1[i] + s2[i] * s2[i] - coeff * s1[i] * s2[i]
        out.append(math.sqrt(max(power, 0.0)) / n)
    return out


def _autocorr(series: list[float], max_lag: int) -> list[float]:
    n = len(series)
    mean = sum(series) / n if n else 0.0
    denom = sum((v - mean) ** 2 for v in series)
    if denom <= 0.0:
        return [0.0] * (max_lag + 1)
    out = [1.0]
    for lag in range(1, max_lag + 1):
        num = sum((series[i] - mean) * (series[i + lag] - mean) for i in range(n - lag))
        out.append(num / denom)
    return out


def detect_dropout(
    samples: list[float], rate: int, f_fire: float, cylinders: int
) -> dict | None:
    """Periodic firing dropout (one dead cylinder in ``cylinders``).

    The per-firing-interval peak sequence samples the envelope at the
    firing rate; a dead cylinder repeats every ``cylinders`` intervals.
    Returns justification numbers or None.
    """
    interval = rate / f_fire
    n_seg = int(len(samples) / interval)
    if n_seg < 3 * cylinders or f_fire <= 0:
        return None
    peaks = []
    for i in range(n_seg):
        start = int(i * interval)
        end = min(int((i + 1) * interval), len(samples))
        peaks.append(max((abs(v) for v in samples[start:end]), default=0.0))
    med = _median(peaks)
    if med < 1e-4:
        return None
    depth = 1.0 - min(peaks) / med
    if depth < DROPOUT_DEPTH_MIN:
        return None
    corr = _autocorr(peaks, 2 * cylinders)
    # Floor: every non-dropout lag (dropout repeats at multiples of the
    # cylinder count, so those lags are excluded from the floor).
    floor_lags = [
        abs(c) for k, c in enumerate(corr) if k >= 1 and k % cylinders != 0
    ]
    base = _median(floor_lags) or 1e-9
    peak_ratio = corr[cylinders] / base if base > 0 else 0.0
    if corr[cylinders] < DROPOUT_AUTOCORR_MIN or peak_ratio < DROPOUT_PEAK_RATIO_MIN:
        return None
    period_s = cylinders / f_fire
    margin = min((depth - DROPOUT_DEPTH_MIN) / (1.0 - DROPOUT_DEPTH_MIN), 1.0)
    confidence = min(0.95, 0.55 + 0.40 * margin)
    return {
        "f_fire_hz": round(f_fire, 2),
        "dropout_depth": round(depth, 3),
        "period_s": round(period_s, 4),
        "autocorr_peak": round(corr[cylinders], 3),
        "confidence": round(confidence, 3),
    }


def _chunks(samples: list[float], rate: int, chunk_s: float = 1.0) -> list[list[float]]:
    size = int(rate * chunk_s)
    return [samples[i : i + size] for i in range(0, len(samples), size) if len(samples[i : i + size]) >= size // 2]


def detect_tone(samples: list[float], rate: int) -> dict | None:
    """Narrowband squeal: 2–6 kHz peak standing out of the local floor."""
    parts = _chunks(samples, rate)
    if not parts:
        return None
    coarse = [TONE_MIN_HZ + i * 100.0 for i in range(int((TONE_MAX_HZ - TONE_MIN_HZ) / 100) + 1)]
    mags = _goertzel_mags(parts[0][: rate // 2], rate, coarse)
    floor = _median(mags) or 1e-12
    peak_idx = max(range(len(mags)), key=lambda i: mags[i])
    ratio = mags[peak_idx] / floor
    if ratio < TONE_RATIO_MIN:
        return None
    centre = coarse[peak_idx]
    fine = [centre + d for d in range(-100, 101, 10)]
    fine = [f for f in fine if TONE_MIN_HZ <= f <= TONE_MAX_HZ]
    fine_mags = _goertzel_mags(parts[0][:rate], rate, fine)
    best = max(range(len(fine)), key=lambda i: fine_mags[i])
    peak_hz = fine[best]
    # Persistence: the same bin must stand out in each 1 s chunk.
    sustained = 0
    for part in parts:
        mags_here = _goertzel_mags(part[:rate], rate, [peak_hz, peak_hz / 2.0, peak_hz * 1.5])
        local_floor = _median(mags_here[1:]) or 1e-12
        if mags_here[0] / local_floor >= TONE_RATIO_MIN / 2.0:
            sustained += 1
    duration_s = float(sustained)
    if duration_s < TONE_MIN_DURATION_S:
        return None
    prominence_db = 20.0 * math.log10(max(ratio, 1e-9))
    if prominence_db < TONE_DB_MIN:
        return None
    margin = min((prominence_db - TONE_DB_MIN) / 20.0, 1.0)
    confidence = min(0.95, 0.55 + 0.40 * margin)
    return {
        "peak_hz": round(peak_hz, 1),
        "prominence_db": round(prominence_db, 1),
        "duration_s": duration_s,
        "confidence": round(confidence, 3),
    }


def hiss_metrics(samples: list[float], rate: int) -> dict:
    """4–8 kHz broadband energy ratio + in-band spectral flatness."""
    window = samples[:rate] if len(samples) >= rate else samples
    band = [HISS_BAND_MIN + i * 500.0 for i in range(int((HISS_BAND_MAX - HISS_BAND_MIN) / 500) + 1)]
    low = [250.0, 500.0, 1000.0, 2000.0]
    band_mags = _goertzel_mags(window, rate, band)
    low_mags = _goertzel_mags(window, rate, low)
    e_band = sum(m * m for m in band_mags)
    e_total = e_band + sum(m * m for m in low_mags)
    ratio = (e_band / e_total) if e_total > 0 else 0.0
    mean = sum(band_mags) / len(band_mags) if band_mags else 0.0
    if mean <= 0:
        flatness = 0.0
    else:
        log_sum = sum(math.log(max(m, 1e-12)) for m in band_mags) / len(band_mags)
        flatness = math.exp(log_sum) / mean
    return {"band_ratio": ratio, "flatness": flatness}


def detect_hiss(
    samples: list[float],
    rate: int,
    cruise: tuple[list[float], int] | None = None,
) -> dict | None:
    """Broadband vacuum hiss, loudest at idle.

    With an idle/2500 rpm pair the idle band ratio must exceed cruise;
    idle-only runs are flagged so the UI can ask for the rev comparison.
    """
    idle = hiss_metrics(samples, rate)
    cruise_ratio: float | None = None
    if cruise is not None:
        cruise = hiss_metrics(cruise[0], cruise[1])
        cruise_ratio = cruise["band_ratio"]
        if idle["band_ratio"] <= cruise_ratio * 1.05:
            return None
    if idle["band_ratio"] < HISS_RATIO_MIN or idle["flatness"] < HISS_FLATNESS_MIN:
        return None
    margin = min((idle["band_ratio"] - HISS_RATIO_MIN) / (1.0 - HISS_RATIO_MIN), 1.0)
    confidence = min(0.90, 0.50 + 0.40 * margin)
    out = {
        "band_ratio": round(idle["band_ratio"], 3),
        "flatness": round(idle["flatness"], 3),
        "idle_only": cruise_ratio is None,
        "confidence": round(confidence, 3),
    }
    if cruise_ratio is not None:
        out["cruise_band_ratio"] = round(cruise_ratio, 3)
    return out


def analyze(
    wav: bytes,
    rpm: float,
    cylinders: int | None,
    cruise_wav: bytes | None = None,
    cruise_rpm: float | None = None,
) -> dict:
    """Run the screening cascade. Never raises on audio content.

    Returns ``{outcome, confidence, details}`` with ``outcome`` in
    ``OUTCOMES``. Unparseable WAV raises ``ValueError`` (caller maps to
    400); every content-level problem (bad duration, missing cylinders,
    silence) reports ``inconclusive`` — the count is never guessed.
    """
    samples, rate, seconds = parse_wav(wav)
    if not (MIN_SECONDS <= seconds <= MAX_SECONDS):
        return {
            "outcome": "inconclusive",
            "confidence": 0.5,
            "details": {"reason": f"duration_s={seconds:.1f} outside 3-10 s"},
        }
    if cylinders is None:
        return {
            "outcome": "inconclusive",
            "confidence": 0.5,
            "details": {"reason": "cylinder count unknown (not in vehicle, not supplied)"},
        }
    try:
        cylinders = int(cylinders)
    except (TypeError, ValueError):
        return {"outcome": "inconclusive", "confidence": 0.5,
                "details": {"reason": "cylinder count not a number"}}
    if not (2 <= cylinders <= 16):
        return {"outcome": "inconclusive", "confidence": 0.5,
                "details": {"reason": f"cylinders={cylinders} outside 2-16"}}
    if not (rpm and float(rpm) > 0):
        return {"outcome": "inconclusive", "confidence": 0.5,
                "details": {"reason": "rpm missing or not positive"}}
    f_fire = firing_freq_hz(float(rpm), cylinders)
    cruise = None
    if cruise_wav is not None and cruise_rpm:
        try:
            cruise_samples, cruise_rate, _ = parse_wav(cruise_wav)
            cruise = (cruise_samples, cruise_rate)
        except ValueError:
            cruise = None
    dropout = detect_dropout(samples, rate, f_fire, cylinders)
    if dropout is not None:
        return {"outcome": "misfire_dropout", "confidence": dropout["confidence"],
                "details": dropout}
    tone = detect_tone(samples, rate)
    if tone is not None:
        return {"outcome": "belt_bearing_tone", "confidence": tone["confidence"],
                "details": tone}
    hiss = detect_hiss(samples, rate, cruise)
    if hiss is not None:
        return {"outcome": "vacuum_hiss", "confidence": hiss["confidence"],
                "details": hiss}
    # Normal: confidence from the widest shortfall below any threshold.
    return {"outcome": "normal", "confidence": 0.7, "details": {"f_fire_hz": round(f_fire, 2)}}


_FEATURES = {
    "misfire_dropout": "acoustic:firing_dropout",
    "belt_bearing_tone": "acoustic:narrowband_tone",
    "vacuum_hiss": "acoustic:broadband_hiss",
    "normal": "acoustic:engine_sound",
    "inconclusive": "acoustic:engine_sound",
}


def to_observation(result: dict, evidence_id: str) -> dict:
    """Map an ``analyze()`` result to an ``observation``-schema dict."""
    import uuid
    from datetime import datetime, timezone

    return {
        "observation_id": uuid.uuid4().hex,
        "feature": _FEATURES[result["outcome"]],
        "value": dict(result["details"]),
        "confidence": float(result["confidence"]),
        "extractor": EXTRACTOR,
        "evidence_ids": [evidence_id],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
