"""Reader for `.mmrec` recorded-session bundles (SPEC section 6).

A bundle is a zip with exactly these members::

    transcript.jsonl  rows {"t": <ms offset>, "dir": "tx"|"rx", "data": <str>}
    events.jsonl      appended session events (may be empty)
    meta.json         {"format": "mmrec/1", ...} describing the recording
    manifest.json     {"format": "mmrec/1", "files": {name: sha256hex, ...}}

``manifest.json`` lists every other member with the sha256 of its raw bytes.
Written by ``Recorder.kt`` on Android; verified here and by replay_check.sh.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
import zlib
from pathlib import Path

FORMAT = "mmrec/1"
DATA_MEMBERS = ("transcript.jsonl", "events.jsonl", "meta.json")
MANIFEST = "manifest.json"


class MmrecError(ValueError):
    """Raised when a bundle is missing members or fails hash verification."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create(members: dict[str, bytes], meta: dict) -> bytes:
    """Build a bundle zip from member bytes (meta merged with the format tag)."""
    import io

    full_meta = {"format": FORMAT, **meta}
    blobs = {
        "transcript.jsonl": members.get("transcript.jsonl", b""),
        "events.jsonl": members.get("events.jsonl", b""),
        "meta.json": json.dumps(full_meta, sort_keys=True).encode(),
    }
    manifest = {
        "format": FORMAT,
        "files": {name: sha256(blobs[name]) for name in DATA_MEMBERS},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in DATA_MEMBERS:
            zf.writestr(name, blobs[name])
        zf.writestr(MANIFEST, json.dumps(manifest, sort_keys=True).encode())
    return buf.getvalue()


def load(path: str | Path) -> dict[str, bytes]:
    """Return {member_name: raw bytes} for every member of the bundle."""
    with zipfile.ZipFile(path) as zf:
        return {info.filename: zf.read(info.filename) for info in zf.infolist()}


def verify(path: str | Path) -> dict:
    """Verify manifest hashes; return the manifest. Raises MmrecError."""
    try:
        members = load(path)
    except (zipfile.BadZipFile, zlib.error, OSError, KeyError) as e:
        raise MmrecError(f"{path}: unreadable bundle: {e}") from e
    if MANIFEST not in members:
        raise MmrecError(f"{path}: missing {MANIFEST}")
    try:
        manifest = json.loads(members[MANIFEST].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise MmrecError(f"{path}: bad {MANIFEST}: {e}") from e
    if manifest.get("format") != FORMAT:
        raise MmrecError(f"{path}: bad format tag {manifest.get('format')!r}")
    expected = manifest.get("files", {})
    for name in DATA_MEMBERS:
        if name not in members:
            raise MmrecError(f"{path}: missing member {name}")
        if expected.get(name) != sha256(members[name]):
            raise MmrecError(f"{path}: hash mismatch on {name}")
    return manifest


def transcript_rows(path: str | Path) -> list[dict]:
    """Parsed transcript.jsonl rows in file order."""
    members = load(path)
    rows = []
    for line in members["transcript.jsonl"].decode("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("verify", "load"):
        argv = argv[1:]
    if not argv:
        print("mmrec: no bundles given, nothing to verify")
        return 0
    failed = False
    for bundle in argv:
        try:
            manifest = verify(bundle)
            n = len(transcript_rows(bundle))
            print(f"mmrec OK: {bundle} ({n} transcript rows, {len(manifest['files'])} files verified)")
        except MmrecError as e:
            print(f"mmrec FAIL: {e}")
            failed = True
        except FileNotFoundError:
            print(f"mmrec FAIL: not found: {bundle}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
