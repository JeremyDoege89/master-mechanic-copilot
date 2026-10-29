#!/usr/bin/env python3
"""Trim the live 75-frame SAM video stream to its first 3 frames.

Reads ``backend/tests/fixtures/upload/sam_video_upload_01.jsonl`` (live
owner capture, 75 masks), keeps the ``<0f>``–``<2f>`` records verbatim at
the TEXT level (no mask decode — this runs in milliseconds), and writes a
minimal Responses event list with corrected
``usage.video_frames_processed = 3``.

Unit tests use the trimmed copy; only the ``slow``-marked full-stream test
touches the 75-frame original. Deterministic: same input bytes give the
same output bytes.

Usage:
    backend/.venv/bin/python scripts/trim_sam_video_fixture.py [--check]
    --check exits 1 when the checked-in trimmed copy differs (CI freshness).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "tests" / "fixtures" / "upload" / "sam_video_upload_01.jsonl"
DST = ROOT / "backend" / "tests" / "fixtures" / "upload" / "sam_video_upload_trimmed.jsonl"
KEEP_FRAMES = 3

_FRAME_RE = re.compile(r"<(\d+)f>")


def _assemble_text(events: list[dict]) -> str:
    chunks: list[str] = []
    done_text: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "response.output_text.delta" and isinstance(event.get("delta"), str):
            chunks.append(event["delta"])
        elif etype == "response.output_text.done" and isinstance(event.get("text"), str):
            done_text = event["text"]
    return "".join(chunks) if chunks else (done_text or "")


def build_trimmed(events: list[dict], keep: int = KEEP_FRAMES) -> list[dict]:
    text = _assemble_text(events)
    kept: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        match = _FRAME_RE.match(line)
        if match is not None and int(match.group(1)) < keep:
            kept.append(line)
    if len({ _FRAME_RE.match(line).group(1) for line in kept }) != keep:  # type: ignore[union-attr]
        raise ValueError(f"expected frames 0..{keep - 1} in {SRC.name}")
    body = "\n".join(kept)
    completed = next(e for e in events if e.get("type") == "response.completed")
    usage = dict(completed["response"].get("usage") or {})
    usage["video_frames_processed"] = keep
    return [
        {
            "type": "response.output_text.delta",
            "delta": body,
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.output_text.done",
            "text": body,
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.content_part.done",
            "part": {"type": "output_text", "text": body},
            "item_id": "m",
            "output_index": 0,
            "content_index": 0,
        },
        {
            "type": "response.output_item.done",
            "item": {"id": "m", "type": "message"},
            "output_index": 0,
        },
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": usage},
        },
    ]


def main(check: bool = False) -> int:
    events = [json.loads(line) for line in SRC.read_text().splitlines() if line.strip()]
    trimmed = build_trimmed(events)
    rendered = "".join(json.dumps(e) + "\n" for e in trimmed)
    if check:
        if not DST.exists() or DST.read_text() != rendered:
            print(f"{DST.name} is stale: regenerate with scripts/trim_sam_video_fixture.py")
            return 1
        print(f"{DST.name} is fresh ({len(trimmed)} events, {KEEP_FRAMES} frames)")
        return 0
    DST.write_text(rendered)
    print(f"wrote {DST} ({DST.stat().st_size} bytes, {len(trimmed)} events, {KEEP_FRAMES} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv[1:]))
