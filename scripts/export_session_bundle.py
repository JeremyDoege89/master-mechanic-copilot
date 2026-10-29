"""Export a finished backend session to a judge .mmrec bundle (owner-run).

Usage:
    scripts/export_session_bundle.py <session_id> [--label "Ford F-150 2.7L, MAF unplugged"] [--out NAME.mmrec] [--data-dir DIR]

Reads the live/upload session under DATA_DIR/sessions/<session_id>/ and
writes a judge bundle with:

- the hash-chained events.jsonl (byte-identical, chain verified first),
- the adapter transcript the phone uploaded (first owned .mmrec blob's
  transcript.jsonl, else empty),
- every blob referenced by evidence_added (photos, clips, audio, PDFs),
- the report file(s) with their Wonder review,
- the cached model responses the session used (llm_cache/... members),
  so replay is $0 and byte-identical.

VIN: the full VIN never leaves mmc/vin.py. The exported meta.json
carries vin_last6 only (never vin/vin_encrypted); events keep their
encrypted token so the hash chain verifies.

Refuses (exit 1, naming the item) when the session is unknown, not
live/upload, has no Wonder-reviewed report, or any referenced blob,
report file, or cached response is missing. Never prints secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures" / "sessions"

sys.path.insert(0, str(BACKEND_DIR))

BUNDLE_DATE_TIME = (2026, 10, 29, 0, 0, 0)


def _resolve_data_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("DATA_DIR", "").strip()
    if env:
        return Path(env)
    candidate = BACKEND_DIR / "data"
    if candidate.is_dir():
        return candidate
    return Path("./data")


def _fail(message: str) -> int:
    print(f"export_session_bundle: {message}", file=sys.stderr)
    return 1


def _is_baseten_record(payload: dict) -> bool:
    if str(payload.get("provider", "")).lower() == "baseten":
        return True
    model = str(payload.get("model", ""))
    return model.startswith("meta-models/")


def _cache_rel_for(payload: dict, sha: str) -> str:
    if _is_baseten_record(payload):
        return f"baseten/{sha}.json"
    return f"{sha}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a finished session to a judge .mmrec.")
    parser.add_argument("session_id", help="backend session id (live or upload mode)")
    parser.add_argument("--label", default="", help='judge display label, e.g. "Ford F-150 2.7L, MAF unplugged"')
    parser.add_argument("--out", default="", help="output .mmrec path (default: fixtures/real-<short>.mmrec)")
    parser.add_argument("--data-dir", default="", help="DATA_DIR override (default: $DATA_DIR or backend/data)")
    args = parser.parse_args(argv)

    session_id = str(args.session_id).strip()
    if not session_id or "/" in session_id or session_id.startswith("."):
        return _fail(f"bad session_id {args.session_id!r}")

    data_root = _resolve_data_dir(args.data_dir or None)
    os.environ["DATA_DIR"] = str(data_root)

    from mmc import sessions as sess_mod
    from mmc.replay import mmrec

    registry = sess_mod.get_registry()
    session = registry.get(session_id)
    if session is None:
        return _fail(f"unknown session {session_id!r} under {data_root}")

    mode = str(session.meta.get("mode", ""))
    if mode not in ("live", "upload"):
        return _fail(f"session {session_id[:8]}… mode is {mode!r}: export needs live or upload")

    events_path = data_root / "sessions" / session_id / "events.jsonl"
    if not events_path.is_file():
        return _fail(f"missing events log for session {session_id[:8]}…: {events_path}")
    try:
        events_raw = events_path.read_bytes()
    except OSError as e:
        return _fail(f"cannot read events log for session {session_id[:8]}…: {e}")
    if not session.store.verify_chain():
        return _fail(f"hash chain broken for session {session_id[:8]}…: refusing to export")

    rows: list[dict] = []
    for line in events_raw.decode("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))

    # -- referenced blobs -------------------------------------------------
    # evidence_added payloads name blobs via blob_ref and/or content_hash.
    blob_refs: dict[str, str] = {}  # digest -> evidence_id
    for row in rows:
        if row.get("type") != "evidence_added":
            continue
        payload = row.get("payload") or {}
        eid = str(payload.get("evidence_id", ""))
        for key in ("blob_ref", "content_hash"):
            raw = payload.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            digest = raw.split("/")[-1].lower()
            if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                blob_refs.setdefault(digest, eid)

    blob_bytes: dict[str, bytes] = {}
    for digest, eid in sorted(blob_refs.items()):
        path = data_root / "blobs" / digest
        if not path.is_file():
            return _fail(f"missing blob {digest} (evidence {eid[:8]}…) for session {session_id[:8]}…")
        try:
            blob_bytes[digest] = path.read_bytes()
        except OSError as e:
            return _fail(f"cannot read blob {digest} (evidence {eid[:8]}…): {e}")
        if hashlib.sha256(blob_bytes[digest]).hexdigest() != digest:
            return _fail(f"blob hash mismatch on {digest} (evidence {eid[:8]}…)")

    # -- cached model responses -------------------------------------------
    # Every llm_call/sam_call payload carries request_sha256; the cache
    # file must exist or a clean replay/report rebuild would spend.
    cache_needed: dict[str, dict] = {}  # rel -> payload hint
    for row in rows:
        if row.get("type") not in ("llm_call", "sam_call"):
            continue
        payload = row.get("payload") or {}
        sha = str(payload.get("request_sha256", ""))
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
            return _fail(f"event seq {row.get('seq')}: bad request_sha256 {sha!r}")
        rel = _cache_rel_for(payload, sha.lower())
        cache_needed.setdefault(rel, payload)

    cache_bytes: dict[str, bytes] = {}
    for rel in sorted(cache_needed):
        path = data_root / "llm_cache" / rel
        if not path.is_file():
            hint = cache_needed[rel]
            model = str(hint.get("model", "?"))
            return _fail(f"missing cached response llm_cache/{rel} (model {model}) for session {session_id[:8]}…")
        try:
            cache_bytes[rel] = path.read_bytes()
        except OSError as e:
            return _fail(f"cannot read cached response llm_cache/{rel}: {e}")
        try:
            json.loads(cache_bytes[rel].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return _fail(f"cached response llm_cache/{rel} is not JSON: {e}")

    # Files-API id cache for referenced blobs (PDF/video uploads): include
    # when present so replay never re-uploads; absent is fine (not every
    # blob rides the Files API).
    for digest in sorted(blob_refs):
        fpath = data_root / "llm_cache" / "files" / f"{digest}.json"
        if fpath.is_file():
            try:
                cache_bytes[f"files/{digest}.json"] = fpath.read_bytes()
            except OSError:
                pass

    # -- reports with Wonder review ---------------------------------------
    built = [r for r in rows if r.get("type") == "report_built"]
    if not built:
        return _fail(f"session {session_id[:8]}… has no report_built event: build the report first")
    reviewed = [r for r in built if isinstance((r.get("payload") or {}).get("wonder_review"), dict)]
    if not reviewed:
        return _fail(f"session {session_id[:8]}… has no Wonder-reviewed report: rebuild with Wonder first")
    report_ids: list[str] = []
    for row in built:
        rid = (row.get("payload") or {}).get("report_id")
        if isinstance(rid, str) and rid and rid not in report_ids:
            report_ids.append(rid)
    report_bytes: dict[str, bytes] = {}
    for rid in report_ids:
        rpath = data_root / "reports" / f"{rid}.json"
        if not rpath.is_file():
            return _fail(f"missing report file reports/{rid}.json for session {session_id[:8]}…")
        try:
            raw = rpath.read_bytes()
            json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as e:
            return _fail(f"cannot read report file reports/{rid}.json: {e}")
        report_bytes[rid] = raw

    # -- adapter transcript the phone uploaded ------------------------------
    # The phone uploads its local .mmrec (Recorder.kt, cacheDir/session.mmrec)
    # through POST .../blobs; ownership is recorded server-side. The first
    # owned blob that parses as an .mmrec with transcript.jsonl wins.
    transcript_raw = b""
    owned_path = data_root / "sessions" / session_id / "blobs.json"
    owned: list[str] = []
    if owned_path.is_file():
        try:
            owned = [str(x) for x in json.loads(owned_path.read_text(encoding="utf-8"))]
        except ValueError:
            owned = []
    for digest in owned:
        bpath = data_root / "blobs" / digest.lower()
        if not bpath.is_file():
            continue
        try:
            data = bpath.read_bytes()
            members = mmrec.loads(data)
            t = members.get("transcript.jsonl")
        except Exception:
            continue
        if t is not None:
            transcript_raw = t
            break

    # -- redacted vehicle (truncated VIN only) ------------------------------
    vehicle = session.meta.get("vehicle") or {}
    redacted: dict = {}
    if isinstance(vehicle, dict):
        for key in ("make", "model", "year", "engine", "vin_last6"):
            if vehicle.get(key) is not None:
                redacted[key] = vehicle[key]
    # Never carry the plaintext or encrypted VIN in the judge meta: the
    # full value never leaves mmc/vin.py; the landing shows vin_last6.
    assert "vin" not in redacted or redacted.get("vin") == redacted.get("vin_last6")
    redacted.pop("vin", None)
    redacted.pop("vin_encrypted", None)

    display_label = str(args.label).strip() or f"Real vehicle session {session_id[:8]}"
    meta = {
        "role": "demo",
        "origin": "real",
        "label": mmrec.REAL_LABEL,
        "display_label": display_label,
        "title": display_label,
        "scenario": "real-vehicle",
        "session_id": session_id,
        "exported_ts": datetime.now(timezone.utc).isoformat(),
        "user_level": session.meta.get("user_level", "L1"),
        "tools": list(session.meta.get("tools", [])),
        "vehicle": redacted,
        "engine_version": session.meta.get("engine_version"),
        "kp_version": session.meta.get("kp_version"),
        "model_pins": session.meta.get("model_pins"),
        "reports": list(report_ids),
        "event_count": len(rows),
        "blob_count": len(blob_bytes),
        "cached_response_count": len(cache_bytes),
    }

    members: dict[str, bytes] = {
        "transcript.jsonl": transcript_raw,
        "events.jsonl": events_raw,
    }
    for digest, data in blob_bytes.items():
        members[f"blobs/{digest}"] = data
    for rid, data in report_bytes.items():
        members[f"reports/{rid}.json"] = data
    for rel, data in cache_bytes.items():
        members[f"llm_cache/{rel}"] = data

    try:
        bundle = mmrec.create(members, meta, date_time=BUNDLE_DATE_TIME)
        mmrec.verify_members(mmrec.loads(bundle), "export")
    except mmrec.MmrecError as e:
        return _fail(f"built bundle failed verification: {e}")

    # Safety: the full VIN must not appear anywhere in the bundle outside
    # vin.py. The plaintext was never on disk; assert the bundle carries
    # no vin/vin_encrypted keys in meta (events keep their encrypted
    # token so the chain verifies, never plaintext).
    bundle_meta = json.loads(mmrec.loads(bundle)["meta.json"].decode("utf-8"))
    veh = bundle_meta.get("vehicle") or {}
    if "vin_encrypted" in veh or ("vin" in veh and veh.get("vin") != veh.get("vin_last6")):
        return _fail("refusing: exported meta would carry VIN material beyond vin_last6")

    out = Path(args.out) if args.out else FIXTURES_DIR / f"real-{session_id[:8]}.mmrec"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bundle)
    sha = hashlib.sha256(bundle).hexdigest()
    print(f"exported {session_id[:8]}… -> {out} ({len(bundle)} bytes, sha256 {sha})")
    print(f"label: {display_label}")
    print(f"events={len(rows)} blobs={len(blob_bytes)} cached={len(cache_bytes)} reports={len(report_ids)}")
    print(f"replay: scripts/replay_check.sh {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
