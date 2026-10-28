"""VIN confidentiality (M4).

The full VIN is stored encrypted at rest and indexed by sha256; only the
last 6 characters are ever displayed or logged. The key comes from the
``MMC_VIN_KEY`` env var (64 hex chars = 32 bytes). When unset, a random dev
key is generated once into ``DATA_DIR/vin.key`` (mode 0600).

Construction (dev-grade, stdlib-only): XOR with a SHA-256 counter keystream
plus an HMAC-SHA-256 tag. This keeps M4 free of new crypto dependencies;
replace with AES-GCM (``cryptography``) before any production use.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

KEY_ENV = "MMC_VIN_KEY"
KEY_FILE = "vin.key"
PREFIX = "enc:v1"


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data"))


def get_key() -> bytes:
    """Return the 32-byte VIN key, generating a dev key on first use."""
    raw = os.environ.get(KEY_ENV, "").strip()
    if raw:
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            raise ValueError(f"{KEY_ENV} must be 64 hex characters (32 bytes)")
        if len(key) != 32:
            raise ValueError(f"{KEY_ENV} must be 64 hex characters (32 bytes)")
        return key
    key_path = data_dir() / KEY_FILE
    if key_path.exists():
        return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
    key = secrets.token_bytes(32)
    data_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key.hex())
    return key


def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:n]


def encrypt_vin(vin: str, key: bytes | None = None) -> str:
    """Encrypt a full VIN; returns an opaque ``enc:v1:...`` token."""
    key = key if key is not None else get_key()
    data = vin.encode("utf-8")
    nonce = secrets.token_bytes(12)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(key, nonce, len(data))))
    tag = hmac.new(key, b"mmc-vin" + nonce + ct, hashlib.sha256).hexdigest()
    return f"{PREFIX}:{nonce.hex()}.{ct.hex()}.{tag}"


def decrypt_vin(token: str, key: bytes | None = None) -> str:
    """Decrypt an ``enc:v1:...`` token; raises ValueError on bad tag/format."""
    key = key if key is not None else get_key()
    try:
        assert token.startswith(f"{PREFIX}:")
        nonce_hex, ct_hex, tag = token[len(PREFIX) + 1 :].split(".")
        nonce = bytes.fromhex(nonce_hex)
        ct = bytes.fromhex(ct_hex)
    except (ValueError, AssertionError) as e:
        raise ValueError("malformed VIN token") from e
    expect = hmac.new(key, b"mmc-vin" + nonce + ct, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("VIN token authentication failed")
    pt = bytes(a ^ b for a, b in zip(ct, _keystream(key, nonce, len(ct))))
    return pt.decode("utf-8")


def sha256_vin(vin: str) -> str:
    """Index key for a VIN: sha256 of the plaintext (never the plaintext)."""
    return hashlib.sha256(vin.encode("utf-8")).hexdigest()


def last6(vin: str) -> str:
    """The only VIN substring that may be displayed or logged."""
    return vin[-6:]
