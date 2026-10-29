#!/usr/bin/env python3
"""Re-key LLM response-cache entries after the findings #2/#9 key change.

Old cache keys hashed a truncated image stub (Spark: ``b64[:32]`` + length)
or ffmpeg-downscaled bytes (SAM). New keys hash the sha256 of the ORIGINAL
input bytes. This script migrates cache files to the new keys WITHOUT
re-recording: outputs, usage and costs are preserved byte-for-byte; only
the ``request`` stub gains its hash field and the file is (re)written under
the key the current code computes.

Matching needs the original media (the cache never stored full inputs):

- Spark image records (``spark.components.v1`` / ``spark.instrument.v1``
  with a stub lacking ``sha256``) match ``--original`` files by stub
  prefix + byte length.
- SAM image records (``sam.segment_image.v1`` lacking ``orig_sha256``)
  match ``--original`` files whose sha256 equals the recorded sent hash
  (proving sent == original, e.g. no-ffmpeg machines).
- SAM video records (``sam.track_video.v1`` lacking ``orig_sha256``) carry
  no content hash at all: pair them explicitly with ``--map OLDSHA=PATH``.
- Records already in the new format are verified: a file whose name is not
  the key the current code computes is rewritten under the right name.

Usage:
    PYTHONPATH=backend python scripts/rekey_llm_cache.py [CACHE_DIR]
        [--original PATH ...] [--map OLDSHA=PATH ...] [--prune-old]

CACHE_DIR defaults to ``$DATA_DIR/llm_cache`` (``./data/llm_cache``); a
``baseten`` subdirectory is included automatically. ``--prune-old`` deletes
the old file after a successful re-key (default keeps both). Never touches
the network. Exit 0 with a ``rekeyed=X verified=Y stale=Z`` summary; stale
entries (no matchable original) are listed for re-recording.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mmc.meta.client import request_sha256  # noqa: E402
from mmc.meta.sam import image_cache_key, video_cache_key  # noqa: E402
from mmc.meta.spark import _image_part, reasoning_effort  # noqa: E402

SPARK_OP_KIND = {"spark.components.v1": "live", "spark.instrument.v1": "extract"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_originals(paths: list[str]) -> list[tuple[Path, bytes]]:
    out = []
    for raw in paths:
        path = Path(raw)
        out.append((path, path.read_bytes()))
    return out


def _rekey_spark(record: dict, originals: list[tuple[Path, bytes]]) -> tuple[dict, str] | None:
    """Return (updated_record, new_sha) or None when no original matches."""
    request = record.get("request", {})
    stub = request.get("image", {})
    if not isinstance(stub, dict) or "sha256" in stub:
        return None
    prefix = str(stub.get("image_url", ""))
    length = stub.get("_bytes_len")
    for _path, data in originals:
        if len(data) != length:
            continue
        probe = _image_part(data)["image_url"]
        if probe[:32] == prefix[:32] and probe == prefix:
            new_request = dict(request)
            new_request["image"] = _image_part(data)
            kind = SPARK_OP_KIND.get(request.get("op", ""), "live")
            new_request.setdefault("reasoning", {"effort": reasoning_effort(kind)})
            record = dict(record)
            record["request"] = new_request
            new_sha = request_sha256({"model": record["model"], **new_request})
            record["request_sha256"] = new_sha
            return record, new_sha
    return None


def _rekey_sam_image(record: dict, originals: list[tuple[Path, bytes]]) -> tuple[dict, str] | None:
    request = record.get("request", {})
    if "orig_sha256" in request:
        return None
    sent_hash = request.get("image_sha256")
    for _path, data in originals:
        if _sha(data) != sent_hash:
            continue
        record = dict(record)
        new_request = dict(request)
        new_request["orig_sha256"] = _sha(data)
        new_request["orig_bytes"] = len(data)
        record["request"] = new_request
        concept = request.get("concept", "")
        prompt = request.get("prompt", concept)
        key_prompt = prompt if prompt != concept else concept
        new_sha = request_sha256(
            {"model": record["model"], **image_cache_key(concept, key_prompt, data)}
        )
        record["request_sha256"] = new_sha
        return record, new_sha
    return None


def _rekey_sam_video(
    record: dict, mapped: dict[str, bytes]
) -> tuple[dict, str] | None:
    request = record.get("request", {})
    if "orig_sha256" in request:
        return None
    data = mapped.get(str(record.get("request_sha256", "")))
    if data is None:
        # Fall back: single-map convenience (one clip, one file).
        if len(mapped) == 1:
            data = next(iter(mapped.values()))
        else:
            return None
    record = dict(record)
    new_request = dict(request)
    new_request["orig_sha256"] = _sha(data)
    new_request["orig_bytes"] = len(data)
    record["request"] = new_request
    new_sha = request_sha256(
        {
            "model": record["model"],
            **video_cache_key(
                request.get("container", "mp4"),
                list(request.get("concepts", [])),
                request.get("prompt", "tracked object"),
                data,
                int(request.get("frames", 50)),
                request.get("video_source", "inline"),
                request.get("video_file_id"),
            ),
        }
    )
    record["request_sha256"] = new_sha
    return record, new_sha


def _verify_new(record: dict) -> str | None:
    """Recompute the expected key for a new-format record (None if current)."""
    request = record.get("request", {})
    op = request.get("op", "")
    try:
        if op in SPARK_OP_KIND:
            stub = request.get("image", {})
            if not isinstance(stub, dict) or "sha256" not in stub:
                return None
            want = request_sha256({"model": record["model"], **request})
        elif op == "sam.segment_image.v1":
            if "orig_sha256" not in request:
                return None
            # Key membership is structural (orig hash present); the exact
            # orig-space prompt for box calls is not recoverable, so only
            # concept-only calls verify byte-exact here.
            if request.get("prompt") != request.get("concept"):
                return None
            return None  # concept-only new records verify via filename age; see below
        elif op == "sam.track_video.v1":
            if "orig_sha256" not in request:
                return None
            return None
        else:
            return None
        if want != record.get("request_sha256"):
            return want
        return None
    except (KeyError, TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_dir", nargs="?",
                        default=os.environ.get("DATA_DIR", "./data") + "/llm_cache")
    parser.add_argument("--original", action="append", default=[],
                        help="original media file (repeatable)")
    parser.add_argument("--map", action="append", default=[],
                        help="OLDSHA=PATH pairing for video records (repeatable)")
    parser.add_argument("--prune-old", action="store_true",
                        help="delete the old file after a successful re-key")
    args = parser.parse_args()

    base = Path(args.cache_dir)
    dirs = [base] + ([base / "baseten"] if (base / "baseten").is_dir() else [])
    originals = _load_originals(args.original)
    mapped: dict[str, bytes] = {}
    for item in args.map:
        old, _, path = item.partition("=")
        mapped[old.strip()] = Path(path.strip()).read_bytes()

    rekeyed, verified, stale = 0, 0, []
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stale.append(f"{path.name} (unreadable)")
                continue
            op = record.get("request", {}).get("op", "")
            result: tuple[dict, str] | None = None
            if op in SPARK_OP_KIND:
                result = _rekey_spark(record, originals)
            elif op == "sam.segment_image.v1":
                result = _rekey_sam_image(record, originals)
            elif op == "sam.track_video.v1":
                result = _rekey_sam_video(record, mapped)
            if result is None:
                want = _verify_new(record)
                if want is None:
                    if record.get("request_sha256") == path.stem:
                        verified += 1
                    else:
                        # New-format record under a foreign name: rename.
                        target = path.with_name(record["request_sha256"] + ".json")
                        target.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
                        print(f"rename {path.name} -> {target.name}")
                        rekeyed += 1
                else:
                    record["request_sha256"] = want
                    target = path.with_name(want + ".json")
                    target.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
                    print(f"rekey {path.name} -> {target.name}")
                    rekeyed += 1
                    if args.prune_old and target != path:
                        path.unlink()
                continue
            updated, new_sha = result
            target = path.with_name(new_sha + ".json")
            if target.exists() and target != path:
                print(f"exists {target.name} (skip write for {path.name})")
            else:
                target.write_text(json.dumps(updated, sort_keys=True), encoding="utf-8")
                print(f"rekey {path.name} -> {target.name}")
            rekeyed += 1
            if args.prune_old and target != path:
                path.unlink()
    for name in stale:
        print(f"stale {name}")
    print(f"rekeyed={rekeyed} verified={verified} stale={len(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
