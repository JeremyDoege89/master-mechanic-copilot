"""Content-addressed blob store (M1). Blobs live under DATA_DIR/blobs/<sha256>."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def blob_dir() -> Path:
    d = data_dir() / "blobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def put(data: bytes, base: Path | None = None) -> tuple[str, str]:
    """Store bytes; returns (blob_ref, sha256). Idempotent."""
    digest = hashlib.sha256(data).hexdigest()
    dest = (base or blob_dir()) / digest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    return f"blobs/{digest}", digest


def get(blob_ref: str, base: Path | None = None) -> bytes:
    digest = blob_ref.split("/")[-1]
    path = (base or blob_dir()) / digest
    return path.read_bytes()
