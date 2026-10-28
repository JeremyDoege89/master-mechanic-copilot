"""M5 server-side keyframe validation/selection (plan step 9).

Mirrors the on-device logic (``KeyframeSelector.kt``) for upload mode:
frames are grayscale pixel rows, decisions are pure functions so both
sides stay identical. No new dependencies — stdlib only.

Rules (SPEC section 7): sharpness via Laplacian variance + motion gate,
at most one keyframe per 3 s, plus on user tap; torch on in low light.
"""

from __future__ import annotations

MIN_INTERVAL_S = 3.0
SHARPNESS_THRESHOLD = 100.0
CHANGE_THRESHOLD = 8.0
LOW_LIGHT_MEAN = 40.0


def luminance_mean(gray: bytes | bytearray | list[int]) -> float:
    """Mean pixel value 0-255; below LOW_LIGHT_MEAN the torch should be on."""
    if not gray:
        raise ValueError("empty frame")
    return sum(gray) / len(gray)


def is_low_light(gray: bytes | bytearray | list[int]) -> bool:
    return luminance_mean(gray) < LOW_LIGHT_MEAN


def laplacian_variance(gray: list[int] | bytes | bytearray, width: int, height: int) -> float:
    """Sharpness: variance of the discrete Laplacian (4-neighbour).

    ``gray`` is row-major, one byte per pixel. Borders are skipped.
    """
    if width < 3 or height < 3:
        raise ValueError("frame too small for Laplacian")
    if len(gray) != width * height:
        raise ValueError(f"expected {width * height} pixels, got {len(gray)}")
    px = list(gray)
    values: list[float] = []
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            i = row + x
            lap = 4 * px[i] - px[i - 1] - px[i + 1] - px[i - width] - px[i + width]
            values.append(float(lap))
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def is_sharp(gray: list[int] | bytes | bytearray, width: int, height: int) -> bool:
    return laplacian_variance(gray, width, height) >= SHARPNESS_THRESHOLD


def mean_abs_diff(a: list[int] | bytes | bytearray, b: list[int] | bytes | bytearray) -> float:
    """Scene-change score between two same-size frames (dedup gate)."""
    if len(a) != len(b) or not a:
        raise ValueError("frames must be non-empty and same size")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def has_changed(prev: list[int] | bytes | bytearray, curr: list[int] | bytes | bytearray) -> bool:
    return mean_abs_diff(prev, curr) >= CHANGE_THRESHOLD


class KeyframePicker:
    """At most one keyframe per MIN_INTERVAL_S, plus on-tap override."""

    def __init__(self) -> None:
        self.last_keyframe_ts: float | None = None
        self.prev_frame: list[int] | None = None

    def offer(
        self,
        gray: list[int] | bytes | bytearray,
        width: int,
        height: int,
        now_s: float,
        tapped: bool = False,
    ) -> bool:
        """True when this frame should be kept (and becomes the reference)."""
        frame = list(gray)
        if tapped and is_sharp(frame, width, height):
            self._accept(frame, now_s)
            return True
        if not is_sharp(frame, width, height):
            return False
        if self.prev_frame is not None and len(self.prev_frame) == len(frame):
            if not has_changed(self.prev_frame, frame):
                return False
        if self.last_keyframe_ts is not None and now_s - self.last_keyframe_ts < MIN_INTERVAL_S:
            return False
        self._accept(frame, now_s)
        return True

    def _accept(self, frame: list[int], now_s: float) -> None:
        self.prev_frame = frame
        self.last_keyframe_ts = now_s
