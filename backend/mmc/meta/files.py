"""Upload-mode Files API helper (M5U plan section 2, item 6).

PDF/video bytes ride to the Meta Model API through the OpenAI SDK Files API
on the same ``base_url``/key as every other call. The returned ``file_id``
is cached key-free under ``DATA_DIR/llm_cache/files/<sha256>.json`` so
replay/offline runs reuse the id with no network; in replay mode a cache
miss raises ``NoCachedResponse`` (never spends), mirroring ``MetaClient``.

Contributor-ID refusal is inherited: ``upload_file`` never touches model IDs,
and every downstream call goes through ``MetaClient`` guards.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable


def _cache_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")) / "llm_cache" / "files"


def _cache_path(sha: str) -> Path:
    return _cache_dir() / f"{sha}.json"


def upload_file(
    client: Any,
    data: bytes,
    filename: str,
    purpose: str = "user_data",
    transport: Callable[..., Any] | None = None,
) -> str:
    """Upload ``data`` and return its ``file_id`` (cached by sha256).

    ``client`` is a :class:`MetaClient` (only its ``_api_key``/``base_url``
    are used). ``transport`` is an offline seam
    ``(api_key, base_url, data, filename, purpose) -> file_id``; the real
    path lazily imports ``openai`` so offline imports never need the SDK.
    """
    if not data:
        raise ValueError("upload_file needs non-empty bytes")
    sha = hashlib.sha256(data).hexdigest()
    path = _cache_path(sha)
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("file_id") and cached.get("sha256") == sha:
                return str(cached["file_id"])
        except (OSError, ValueError):
            pass
    replay_cached = bool(getattr(client, "replay_cached", False))
    if replay_cached:
        from .client import NoCachedResponse

        raise NoCachedResponse(f"REPLAY_USE_CACHED_LLM=true and no cached file upload for {sha}")
    if transport is not None:
        file_id = transport(client._api_key, client.base_url, data, filename, purpose)
    else:  # pragma: no cover - needs network + key; the owner spike covers it
        try:
            import openai  # lazy: offline imports must not require the SDK
        except ImportError as e:
            raise RuntimeError(
                "the openai package is not installed; pass a transport for offline use"
            ) from e
        sdk = openai.OpenAI(api_key=client._api_key, base_url=client.base_url)
        resp = sdk.files.create(file=(filename, data), purpose=purpose)
        file_id = getattr(resp, "id", None) or str(resp)
    record = {"file_id": str(file_id), "sha256": sha, "filename": filename, "purpose": purpose}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return str(file_id)
