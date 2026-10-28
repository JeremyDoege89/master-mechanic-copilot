"""Append-only, hash-chained event store (M1).

Chain rule (per approved correction):
  payload_sha256 = sha256(canonical_json(payload))
  hash = sha256(canonical_json({seq, ts, type, payload_sha256, prev_hash}))
where canonical_json = JSON with sorted keys, no whitespace, UTF-8 encoded.
The first event's prev_hash is 64 zeros.

The JSONL file is the source of truth; the SQLite index next to it is a
rebuildable cache. verify_chain() returns False on ANY tampering: changed
seq/ts/type/payload/prev_hash/hash, deleted lines, reordered lines, or
inserted lines.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

GENESIS_PREV_HASH = "0" * 64

EVENT_TYPES = frozenset(
    {
        "session_started",
        "adapter_fingerprinted",
        "evidence_added",
        "observation_added",
        "test_requested",
        "safety_ack",
        "test_performed",
        "test_result",
        "hypothesis_proposed",
        "posterior_updated",
        "llm_call",
        "sam_call",
        "asr_segment",
        "codes_clear_requested",
        "codes_cleared",
        "report_built",
    }
)


def canonical_bytes(obj: object) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, ASCII-safe, UTF-8 encoded.

    ensure_ascii=True keeps the form encodable for every JSON string value
    (including lone surrogates) and byte-identical across implementations.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(payload: dict) -> str:
    return sha256_hex(canonical_bytes(payload))


def event_hash(seq: int, ts: str, type: str, payload_sha256: str, prev_hash: str) -> str:
    return sha256_hex(
        canonical_bytes(
            {
                "seq": seq,
                "ts": ts,
                "type": type,
                "payload_sha256": payload_sha256,
                "prev_hash": prev_hash,
            }
        )
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventStore:
    """JSONL event log with a hash chain and a SQLite index."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self.index_path = self.path.with_suffix(self.path.suffix + ".index.sqlite")
        self._db = sqlite3.connect(self.index_path)
        # The index is a rebuildable cache (JSONL is the source of truth),
        # so it takes no durability cost: no fsync, no journal file.
        self._db.execute("PRAGMA journal_mode=MEMORY")
        self._db.execute("PRAGMA synchronous=OFF")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS events"
            " (seq INTEGER PRIMARY KEY, type TEXT NOT NULL, ts TEXT NOT NULL, hash TEXT NOT NULL)"
        )
        self._reindex()
        self._f = self.path.open("a", encoding="utf-8")
        row = self._db.execute("SELECT MAX(seq) FROM events").fetchone()
        self._next_seq = (row[0] or 0) + 1
        head = self._db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        self._head = head[0] if head else GENESIS_PREV_HASH

    def _reindex(self) -> None:
        self._db.execute("DELETE FROM events")
        for event in self._read_all():
            self._db.execute(
                "INSERT INTO events (seq, type, ts, hash) VALUES (?, ?, ?, ?)",
                (event["seq"], event["type"], event["ts"], event["hash"]),
            )
        self._db.commit()

    def _read_all(self) -> list[dict]:
        if getattr(self, "_f", None) is not None:
            self._f.flush()
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return [json.loads(line) for line in text.splitlines()]

    def __len__(self) -> int:
        return self._next_seq - 1

    def head_hash(self) -> str:
        return self._head

    def append(self, type: str, payload: dict, ts: str | None = None) -> dict:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type!r}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        seq = self._next_seq
        ts = ts or utc_now_iso()
        ph = payload_hash(payload)
        prev = self._head
        event = {
            "seq": seq,
            "ts": ts,
            "type": type,
            "payload": payload,
            "payload_sha256": ph,
            "prev_hash": prev,
            "hash": event_hash(seq, ts, type, ph, prev),
        }
        self._f.write(canonical_bytes(event).decode("ascii") + "\n")
        self._f.flush()
        self._db.execute(
            "INSERT INTO events (seq, type, ts, hash) VALUES (?, ?, ?, ?)",
            (seq, type, ts, event["hash"]),
        )
        self._db.commit()
        self._next_seq = seq + 1
        self._head = event["hash"]
        return event

    def verify_chain(self) -> bool:
        """True iff every line parses and the whole chain is intact.

        Each stored line's raw bytes must equal the canonical serialization
        of the parsed event (the exact bytes append() writes). This catches
        single-byte mutations that leave the parsed value unchanged (e.g. a
        float's last digit changed so it parses to the same double, or
        escape/encoding variants), where recomputed hashes alone still pass.
        """
        try:
            raw_bytes = self.path.read_bytes()
        except OSError:
            return False
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Bytes that are no longer valid UTF-8 (e.g. a mutated multibyte
            # character): the chain cannot verify.
            return False
        if not raw.strip():
            return True
        if not raw.endswith("\n"):
            return False
        lines = raw.split("\n")[:-1]
        prev = GENESIS_PREV_HASH
        for i, line in enumerate(lines):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return False
            if line.encode("utf-8") != canonical_bytes(event):
                return False
            if not isinstance(event, dict):
                return False
            for key in ("seq", "ts", "type", "payload", "payload_sha256", "prev_hash", "hash"):
                if key not in event:
                    return False
            if (
                event["seq"] != i + 1
                or not isinstance(event["ts"], str)
                or event["type"] not in EVENT_TYPES
                or not isinstance(event["payload"], dict)
                or event["prev_hash"] != prev
            ):
                return False
            if event["payload_sha256"] != payload_hash(event["payload"]):
                return False
            if (
                event["hash"]
                != event_hash(
                    event["seq"],
                    event["ts"],
                    event["type"],
                    event["payload_sha256"],
                    event["prev_hash"],
                )
            ):
                return False
            prev = event["hash"]
        return True

    def iter_replay(self):
        """Yield events in seq order (payloads in full event envelopes)."""
        for event in self._read_all():
            yield event

    def get(self, seq: int) -> dict | None:
        rows = self._db.execute("SELECT seq FROM events WHERE seq = ?", (seq,)).fetchone()
        if rows is None:
            return None
        return self._read_all()[seq - 1]

    def close(self) -> None:
        try:
            self._f.flush()
            self._f.close()
        finally:
            self._db.commit()
            self._db.close()

    def __del__(self) -> None:  # best-effort cleanup for short-lived test handles
        try:
            self.close()
        except Exception:
            pass
