"""Replay runner (M6 §4): bundles and sessions fold through the normal path.

``POST /replay`` verifies an ``.mmrec`` bundle (or copies a session's event
log), creates a ``mode=replay`` session, and folds every row through the
same append path live traffic uses — so replayed posteriors are
bit-identical and replay sessions stay read-only (no spend, no log
mutation: vision/upload processing routes reject them).
"""

from __future__ import annotations

from typing import Any

from mmc import sessions as sess
from mmc._paths import repo_fixtures_dir
from mmc.replay import mmrec

# Shipped fixtures (judge demo + CI). Modal seeds these under DATA_DIR (§5);
# the repo copy (via MMC_REPO_ROOT) is the fallback for local dev.
# Resolved lazily: tests/ is not installed, so a file-relative lookup
# breaks in non-editable installs.

_RESERVED_ROW_KEYS = frozenset(
    {"seq", "ts", "type", "payload", "payload_sha256", "prev_hash", "hash"}
)


def _row_payload(row: dict) -> dict:
    """Payload for one bundle row (tolerates legacy slim rows without one)."""
    payload = row.get("payload")
    if isinstance(payload, dict):
        return dict(payload)
    return {k: v for k, v in row.items() if k not in _RESERVED_ROW_KEYS}


def fold_rows(session: sess.Session, rows: list[dict]) -> int:
    """Append bundle/session rows via the registry (validates like live).

    ``session_started`` rows are skipped: creating the replay session
    already logged one. Returns the number of folded events.
    """
    registry = sess.get_registry()
    folded = 0
    for row in rows:
        type = str(row.get("type", ""))
        if not type or type == "session_started":
            continue
        registry.append(session, type, _row_payload(row))
        folded += 1
    return folded


def replay_bundle_bytes(
    data: bytes,
    *,
    user_level: str = "L1",
    tools: list[str] | None = None,
    label: str = "<memory>",
) -> sess.Session:
    """Verify bundle bytes and fold them into a fresh replay session."""
    members = mmrec.loads(data)
    mmrec.verify_members(members, label)
    registry = sess.get_registry()
    session = registry.create(
        user_level=user_level, tools=list(tools or []), mode="replay", vehicle=None
    )
    fold_rows(session, mmrec.event_rows(members))
    return session


def replay_session(source_id: str) -> sess.Session:
    """Copy an existing session's log into a fresh replay session."""
    registry = sess.get_registry()
    source = registry.get(source_id)
    if source is None:
        raise KeyError(f"unknown session {source_id!r}")
    # Reuse the already-redacted vehicle dict (vin_encrypted/vin_last6 pass
    # through as opaque fields; the plaintext VIN never existed server-side).
    redacted = dict(source.meta.get("vehicle") or {})
    session = registry.create(
        user_level=source.meta.get("user_level", "L1"),
        tools=list(source.meta.get("tools", [])),
        mode="replay",
        vehicle=redacted or None,
    )
    rows = [
        {"type": e["type"], "payload": e["payload"]} for e in source.store.iter_replay()
    ]
    fold_rows(session, rows)
    return session


def fixture_bytes(name: str) -> bytes:
    """Read a seeded fixture bundle (repo copy; Modal seeds DATA_DIR too)."""
    if "/" in name or name.startswith("."):
        raise ValueError(f"bad fixture name {name!r}")
    seeded = sess.data_root() / "fixtures" / "sessions" / name
    if seeded.exists():
        return seeded.read_bytes()
    path = repo_fixtures_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"unknown fixture {name!r}")
    return path.read_bytes()
