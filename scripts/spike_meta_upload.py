#!/usr/bin/env python3
"""M5U upload spike (plan .agents/plans/M5U.md section 1).

Live-calls the Meta Model API through backend/mmc/meta/ and saves the raw
KEY-FREE responses as fixtures under backend/tests/fixtures/upload/.

Usage (from repo root, OUTSIDE the sandbox — it needs network + a key):
    backend/.venv/bin/python scripts/spike_meta_upload.py [--pdf PATH] [--clip PATH]

Key: MODEL_API_KEY env, or ~/.config/meta/model-api.env. The key is never
printed and never stored: fixtures hold only call_record() output (no
headers, no key, file bytes replaced by length stubs).

Calls, in order (each prints its own outcome; a failed call never stops the
later ones, but the exit is non-zero unless every call succeeds):
  1. Files API upload of a self-made one-page spec PDF (purpose="user_data")
     -> muse-spark-1.3 structured spec extraction.
  2. Files API upload of a short H.264 MP4 (purpose="user_data")
     -> Spark video understanding (timestamped event list).
  3. SAM video segmentation with one box prompt on that MP4 — tries the
     INLINE input_file part FIRST, then a Files API file_id part, and
     records which one the API accepts (plus the exact error for the
     other). NEITHER video shape was verified live before this spike
     (only the image path was); flip sam.track_video's ``video_source``
     default to whatever wins here.

Test inputs are self-made only (no service-manual pages):
  (a) upload_spec_test.pdf, written by the stdlib minimal-PDF writer below
      (deterministic bytes; committed with SOURCE.txt = "self-generated").
  (b) short H.264 MP4 made with:
      ffmpeg -loop 1 -i backend/tests/fixtures/images/engine_bay_02.jpg \\
        -t 3 -c:v libx264 -pix_fmt yuv420p /tmp/mmc-spike-clip.mp4
      (shape-checking only; the clip itself is gitignored if > 1 MB, the
      recipe is committed in SOURCE.txt).

Cost is logged from the usage numbers the API returns, not from estimates.
Exits non-zero without writing fixtures when the API is unreachable or the
key is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mmc.meta.budget import actual_cost_from_usage  # noqa: E402
from mmc.meta.client import MetaClient  # noqa: E402
from mmc.meta.files import upload_file  # noqa: E402
from mmc.meta.sam import check_video_bytes, track_video  # noqa: E402
from mmc.meta.spark import extract_spec_ranges, summarize_video  # noqa: E402

FIXTURE_DIR = ROOT / "backend" / "tests" / "fixtures" / "upload"
DEFAULT_IMAGE = ROOT / "backend" / "tests" / "fixtures" / "images" / "engine_bay_02.jpg"

# Self-generated spec table: the spike PDF and the provisional fixture agree
# on exactly these rows.
SPEC_ROWS = [
    ("Fuel pressure", "280", "420", "kPa"),
    ("Charging voltage (running)", "13.5", "14.8", "V"),
    ("ECT warm plateau", "90", "98", "degC"),
]


def write_spec_test_pdf(path: Path) -> bytes:
    """Write the self-made one-page spec PDF (stdlib only, deterministic bytes).

    Fixed header/objects, no timestamps: the same call always yields the
    same bytes, so the committed copy is reproducible from this function.
    """
    lines = ["SPECIFICATIONS - SELF-GENERATED TEST PAGE", ""]
    for name, lo, hi, units in SPEC_ROWS:
        lines.append(f"{name}: {lo} - {hi} {units}")

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 12 Tf 72 720 Td 14 TL\n"
    for i, line in enumerate(lines):
        content += f"({esc(line)}) Tj\n"
        if i < len(lines) - 1:
            content += "T*\n"
    content += "ET"
    content_bytes = content.encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content_bytes) + content_bytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_at)
    )
    path.write_bytes(bytes(out))
    return bytes(out)


def load_key() -> str:
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config" / "meta" / "model-api.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("MODEL_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("spike: no key — set MODEL_API_KEY or ~/.config/meta/model-api.env")


def save_record(name: str, record: dict) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record["provisional"] = False
    record["captured"] = "spike_meta_upload.py owner run"
    text = json.dumps(record, indent=2, sort_keys=True)
    for banned in ("Authorization", "api_key", "Bearer "):
        if banned in text:
            raise SystemExit(f"spike: refusing to write fixture containing {banned!r}")
    path = FIXTURE_DIR / name
    path.write_text(text + "\n", encoding="utf-8")
    return path


def save_stream(name: str, events: list[dict]) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / name
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    return path


def make_clip(image: Path, dest: Path) -> bool:
    """Build the 3 s H.264 test clip from the CC0 engine-bay photo."""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loop", "1", "-i", str(image),
                "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"spike: WARN could not build MP4 clip ({e}); video calls will be skipped")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="M5U live upload spike")
    ap.add_argument("--pdf", default="")
    ap.add_argument("--clip", default="")
    args = ap.parse_args()

    key = load_key()

    # Scratch DATA_DIR: proves the llm_call/sam_call event path without
    # touching real session data.
    tmp = Path(tempfile.mkdtemp(prefix="mmc-spike-upload-"))
    os.environ["DATA_DIR"] = str(tmp)
    import mmc.sessions as sess_mod

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    logged: list[str] = []

    def on_call(record: dict) -> None:
        event = registry.append(session, record["kind"], record)
        logged.append(f"{record['kind']} seq={event['seq']} sha={event['hash'][:12]}")

    client = MetaClient(api_key=key, replay_cached=False, on_call=on_call)
    print(f"spike: models spark={client.models.get('spark')} sam={client.models.get('sam')}")

    ok: dict[str, bool] = {}
    records: list[dict] = []

    # 1. Self-made PDF -> Files API -> Spark spec extraction.
    pdf_path = Path(args.pdf) if args.pdf else (FIXTURE_DIR / "upload_spec_test.pdf")
    if not pdf_path.exists():
        write_spec_test_pdf(pdf_path)
        print(f"spike: wrote self-generated PDF {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()
    print(f"spike: PDF {pdf_path} ({len(pdf_bytes)} bytes)")
    try:
        pdf_file_id = upload_file(client, pdf_bytes, "upload_spec_test.pdf")
        print(f"spike: [1a] Files upload PDF -> OK file_id={pdf_file_id}")
        ranges, rec1 = extract_spec_ranges(pdf_file_id, client, page_hint="Page 1.")
        p1 = save_record("spark_pdf_spec_01.json", rec1)
        records.append(rec1)
        print(f"spike: [1b] Spark spec extract -> OK {p1.name}: {len(ranges)} ranges")
        for r in ranges:
            print(f"spike:       {r.get('spec_id')} {r.get('min')}-{r.get('max')} {r.get('units')}")
        ok["pdf_extract"] = True
    except Exception as e:  # noqa: BLE001 - spike keeps going after a failed call
        print(f"spike: [1] PDF extract -> FAILED: {type(e).__name__}: {e}")
        ok["pdf_extract"] = False

    # 2. H.264 MP4 -> Files API -> Spark video understanding.
    clip_path = Path(args.clip) if args.clip else Path(tmp) / "spike_clip.mp4"
    clip_bytes = b""
    if not args.clip or not clip_path.exists():
        if make_clip(DEFAULT_IMAGE, clip_path):
            clip_bytes = clip_path.read_bytes()
    else:
        clip_bytes = clip_path.read_bytes()
    if clip_bytes:
        print(f"spike: clip {clip_path} ({len(clip_bytes)} bytes)")
        try:
            container = check_video_bytes(clip_bytes, "spike_clip.mp4")
            print(f"spike: [2a] check_video_bytes -> OK container={container}")
            clip_file_id = upload_file(client, clip_bytes, "spike_clip.mp4")
            print(f"spike: [2b] Files upload MP4 -> OK file_id={clip_file_id}")
            events_out, rec2 = summarize_video(clip_file_id, client)
            p2 = save_record("spark_video_events_01.json", rec2)
            records.append(rec2)
            print(f"spike: [2c] Spark video events -> OK {p2.name}: "
                  f"{len(events_out.get('events', []))} events")
            for ev in events_out.get("events", []):
                print(f"spike:       [{ev.get('t_start_s')}-{ev.get('t_end_s')}s] "
                      f"{ev.get('label')} (sam_concept={ev.get('sam_concept')!r})")
            ok["video_events"] = True
        except Exception as e:  # noqa: BLE001 - spike keeps going after a failed call
            print(f"spike: [2] video understanding -> FAILED: {type(e).__name__}: {e}")
            ok["video_events"] = False
    else:
        print("spike: [2] video understanding -> SKIPPED (no clip)")
        ok["video_events"] = False

    # 3. SAM video: inline input_file FIRST, then Files API file_id.
    # Neither shape was verified live before this spike — record which the
    # API accepts and the exact error for the other.
    if clip_bytes:
        box = {"x_min": 100, "y_min": 100, "x_max": 400, "y_max": 400}
        try:
            result_inline, rec_inline = track_video(
                clip_bytes, "spike_clip.mp4", client,
                boxes=[box], concepts=["engine"],
                frames=50, size=(1280, 960),
                video_source="inline",
            )
            records.append(rec_inline)
            print(f"spike: [3a] SAM video inline input_file -> OK "
                  f"{len(result_inline.masks)} masks found={result_inline.found}")
            ok["sam_inline"] = True
        except Exception as e:  # noqa: BLE001 - spike keeps going after a failed call
            print(f"spike: [3a] SAM video inline input_file -> FAILED: "
                  f"{type(e).__name__}: {e}")
            ok["sam_inline"] = False
        try:
            clip_file_id = upload_file(client, clip_bytes, "spike_clip.mp4")
            result_fid, rec_fid = track_video(
                clip_bytes, "spike_clip.mp4", client,
                boxes=[box], concepts=["engine"],
                frames=50, size=(1280, 960),
                video_source="file_id", video_file_id=clip_file_id,
            )
            records.append(rec_fid)
            print(f"spike: [3b] SAM video file_id part -> OK "
                  f"{len(result_fid.masks)} masks found={result_fid.found}")
            ok["sam_file_id"] = True
        except Exception as e:  # noqa: BLE001 - spike keeps going after a failed call
            print(f"spike: [3b] SAM video file_id part -> FAILED: "
                  f"{type(e).__name__}: {e}")
            ok["sam_file_id"] = False
        # Save whichever SAM stream ran (inline preferred); raw events only.
        saved = False
        for rec in records:
            if rec.get("kind") == "sam_call" and rec.get("events"):
                save_stream("sam_video_upload_01.jsonl", rec["events"])
                print("spike: [3c] saved sam_video_upload_01.jsonl "
                      f"({len(rec['events'])} raw stream events)")
                saved = True
                break
        if not saved:
            print("spike: [3c] no SAM stream to save (both video shapes failed)")
            ok["sam_inline"] = ok.get("sam_inline", False)
    else:
        print("spike: [3] SAM video -> SKIPPED (no clip)")
        ok["sam_inline"] = False
        ok["sam_file_id"] = False

    # Spend from returned usage, per call.
    total = 0.0
    for rec in records:
        usage = rec.get("usage", {})
        cost = actual_cost_from_usage(rec.get("model", ""), usage) or rec.get("cost_usd", 0.0)
        total += cost
        print(f"spike: {rec['kind']} model={rec.get('model')} usage={usage} cost=${cost:.6f}")
    print(f"spike: total spend from API usage: ${total:.6f} (ledger: ${client.budget.spend_usd:.6f})")
    print("spike: events: " + "; ".join(logged))
    print(f"spike: scratch session {session.id} under {tmp} (not committed)")

    verdict = {k: ("OK" if v else ("FAIL" if v is False else "SKIP")) for k, v in ok.items()}
    print(f"spike: outcome {verdict}")
    print("spike: SAM video verdict: "
          f"inline={'OK' if ok.get('sam_inline') else 'FAIL'}, "
          f"file_id={'OK' if ok.get('sam_file_id') else 'FAIL'} "
          "-> set sam.track_video video_source default to the winner")
    if not all(ok.values()):
        print("spike: FAILED calls above need corrections before fixtures flip to captured")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
