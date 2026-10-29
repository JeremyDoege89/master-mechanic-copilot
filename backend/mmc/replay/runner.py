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
from mmc.store.event_store import (
    GENESIS_PREV_HASH,
    event_hash,
    payload_hash,
)

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


_ENVELOPE_KEYS = frozenset(
    {"seq", "ts", "type", "payload", "payload_sha256", "prev_hash", "hash"}
)


def verify_event_chain(rows: list[dict], label: str = "<memory>") -> None:
    """Verify the hash chain carried inside enveloped bundle rows.

    Finding #6: ``mmrec`` manifest hashes only prove the zip members are
    self-consistent — a hand-crafted bundle can carry a fabricated gated
    ``test_result`` under a fresh valid manifest. When rows carry the
    full event envelope (``payload_sha256``/``prev_hash``/``hash``), the
    inner chain must verify: seq continuity from 1, payload hashes,
    prev-hash linkage from genesis, and recomputed event hashes.
    Legacy slim rows (no envelope fields at all) skip verification.
    Raises ``mmrec.MmrecError`` on any break.
    """
    enveloped = [row for row in rows if _ENVELOPE_KEYS <= set(row)]
    if not enveloped:
        return
    if len(enveloped) != len(rows):
        raise mmrec.MmrecError(f"{label}: mixed enveloped and slim event rows")
    prev = GENESIS_PREV_HASH
    for i, row in enumerate(rows, start=1):
        if row.get("seq") != i:
            raise mmrec.MmrecError(f"{label}: event seq break at row {i}")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise mmrec.MmrecError(f"{label}: event {i} has no payload object")
        if row.get("payload_sha256") != payload_hash(payload):
            raise mmrec.MmrecError(f"{label}: payload hash mismatch on event {i}")
        if row.get("prev_hash") != prev:
            raise mmrec.MmrecError(f"{label}: chain link broken on event {i}")
        if row.get("hash") != event_hash(
            row["seq"], row["ts"], row["type"], row["payload_sha256"], row["prev_hash"]
        ):
            raise mmrec.MmrecError(f"{label}: event hash mismatch on event {i}")
        prev = row["hash"]


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


def restore_reports(session: sess.Session) -> list[str]:
    """Rebuild recorded reports into this session's report dir ($0).

    Bundles carry the ``report_built`` event (with its ``report_id`` and the
    recorded Wonder review) but not the report file it names — so the Report
    tab's one-click ``GET /reports/<id>.json`` 404s after a replay. For
    every recorded ``report_built`` without a stored file, rebuild the
    report deterministically from the replayed state under that SAME id
    (the Wonder review is reused verbatim from the event, $0: no live
    call). Writes files only; the event log is untouched, so replayed
    posteriors stay bit-identical.
    """
    registry = sess.get_registry()
    restored: list[str] = []
    seen: set[str] = set()
    for event in session.store.iter_replay():
        if event.get("type") != "report_built":
            continue
        payload = event.get("payload") or {}
        report_id = payload.get("report_id")
        if not isinstance(report_id, str) or not report_id or report_id in seen:
            continue
        seen.add(report_id)
        if sess.load_report(registry, report_id) is not None:
            continue
        state = sess.compute_state(session)
        report = sess.build_report(session, state)
        report["report_id"] = report_id
        recorded_review = payload.get("wonder_review")
        if isinstance(recorded_review, dict):
            report["wonder_review"] = recorded_review
            report["wonder_unavailable"] = payload.get("wonder_unavailable")
        sess.save_report(registry, report)
        restored.append(report_id)
    return restored


def replay_bundle_bytes(
    data: bytes,
    *,
    user_level: str | None = None,
    tools: list[str] | None = None,
    label: str = "<memory>",
) -> sess.Session:
    """Verify bundle bytes and fold them into a fresh replay session.

    M6B §1.2: judge bundles carry their demo context (``user_level``,
    ``tools``, redacted ``vehicle``) in ``meta.json`` so the replay status
    bar matches the recorded investigation. Explicit arguments override
    the bundle meta; bundles without it (e.g. ``sim-healthy.mmrec``) keep
    the old L1/empty/None defaults.
    """
    import json as _json

    members = mmrec.loads(data)
    mmrec.verify_members(members, label)
    try:
        bundle_meta = _json.loads(members["meta.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError, KeyError):
        bundle_meta = {}
    if user_level is None:
        user_level = str(bundle_meta.get("user_level", "L1"))
    if tools is None:
        tools = list(bundle_meta.get("tools", []))
    vehicle = bundle_meta.get("vehicle")
    if not isinstance(vehicle, dict):
        vehicle = None
    rows = mmrec.event_rows(members)
    verify_event_chain(rows, label)
    registry = sess.get_registry()
    session = registry.create(
        user_level=user_level, tools=list(tools), mode="replay", vehicle=vehicle
    )
    # Bundle-embedded blobs (demo photo bytes, plus every referenced blob
    # for real-vehicle exports) land in the blob store so the
    # evidence-scoped GET can serve them on the replay session.
    # M7 review #8: ownership is recorded for the new session, so the
    # download gate (ownership + own-log reference) passes on replays.
    from mmc.store import blobs as blob_store

    for name, data in mmrec.blob_members(members).items():
        digest = name[len(mmrec.BLOB_PREFIX):]
        if digest != mmrec.sha256(data):
            raise mmrec.MmrecError(f"{label}: blob name/hash mismatch on {name}")
        blob_store.put(data)
        sess.record_session_blob(session.id, digest)
    # Real-vehicle exports carry the cached model responses the session
    # used (llm_cache/... members) so replay and report rebuilds stay $0
    # without network: seed any missing cache files before folding.
    import os as _os
    from pathlib import Path as _Path

    for name, data in mmrec.cache_members(members).items():
        rel = name[len(mmrec.LLM_CACHE_PREFIX):]
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise mmrec.MmrecError(f"{label}: bad cache member {name!r}")
        dest = _Path(_os.environ.get("DATA_DIR", "./data")) / "llm_cache" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)
    fold_rows(session, rows)
    restore_reports(session)
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
    # M7 review #8: the replay inherits the source's blob ownership, so
    # evidence-scoped downloads keep working on the copy.
    try:
        owned = sess._session_blobs_path(source.id).read_text(encoding="utf-8")
        sess._session_blobs_path(session.id).write_text(owned, encoding="utf-8")
    except OSError:
        pass
    fold_rows(session, rows)
    restore_reports(session)
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


def _fixture_search_dirs() -> list:
    """Seeded fixtures first, repo copy as fallback (mirrors fixture_bytes)."""
    from pathlib import Path as _Path

    seeded = sess.data_root() / "fixtures" / "sessions"
    dirs = []
    try:
        if seeded.is_dir():
            dirs.append(seeded)
    except OSError:
        pass
    repo = repo_fixtures_dir()
    if repo not in dirs:
        dirs.append(repo)
    return [d for d in dirs if isinstance(d, _Path)]


def list_fixtures() -> list[dict]:
    """Judge-landing inventory: every bundle, real-vehicle first.

    Merges the seeded ``DATA_DIR/fixtures/sessions`` dir with the repo
    copy (seeded wins on name clash; Modal seeds new/changed bundles on
    boot so real-vehicle drops appear with no code change). Each entry
    carries ``name``, ``origin`` (``"real"`` sorts first), ``label`` and
    ``display_label`` from ``meta.json``. Unreadable bundles are skipped,
    never fatal to the landing.
    """
    import json as _json

    seen: dict[str, dict] = {}
    for directory in _fixture_search_dirs():
        try:
            names = sorted(p.name for p in directory.glob("*.mmrec") if p.is_file())
        except OSError:
            continue
        for name in names:
            if name in seen:
                continue
            meta: dict = {}
            try:
                members = mmrec.load(directory / name)
                meta = _json.loads(members["meta.json"].decode("utf-8"))
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
            origin = mmrec.bundle_origin(meta)
            if origin != "real" and name.startswith("real-"):
                origin = "real"
            seen[name] = {
                "name": name,
                "origin": origin,
                "label": str(meta.get("label", "")),
                "display_label": mmrec.bundle_display_label(meta, name),
                "scenario": str(meta.get("scenario", "")),
            }
    fixtures = sorted(
        seen.values(),
        key=lambda e: (0 if e["origin"] == "real" else 1, e["display_label"].lower(), e["name"]),
    )
    return fixtures
