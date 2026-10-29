"""Every .mmrec fixture verifies against its manifest (M2 correction 4)."""

import glob
import io
import json
import zipfile
from pathlib import Path

import pytest

from mmc.replay import mmrec

SESSIONS = Path(__file__).resolve().parent / "fixtures" / "sessions"


def test_all_session_bundles_verify():
    bundles = sorted(glob.glob(str(SESSIONS / "*.mmrec")))
    assert bundles, "expected at least one .mmrec fixture"
    for bundle in bundles:
        manifest = mmrec.verify(bundle)
        assert {"transcript.jsonl", "events.jsonl", "meta.json"} <= set(manifest["files"])
        extras = set(manifest["files"]) - {"transcript.jsonl", "events.jsonl", "meta.json"}
        assert all(
            e.startswith((mmrec.BLOB_PREFIX, mmrec.REPORT_PREFIX, mmrec.LLM_CACHE_PREFIX))
            for e in extras
        ), (bundle, extras)
        for e in extras:
            if e.startswith(mmrec.BLOB_PREFIX):
                assert manifest["files"][e] == e.split("/")[-1], (bundle, e)


def test_bundle_role_defaults_to_ci():
    """Only an explicit role=demo opts a bundle into the judge landing."""
    assert mmrec.bundle_role({"role": "demo"}) == mmrec.ROLE_DEMO
    assert mmrec.bundle_role({}) == mmrec.ROLE_CI
    assert mmrec.bundle_role({"role": "ci"}) == mmrec.ROLE_CI
    assert mmrec.bundle_role({"role": "whatever"}) == mmrec.ROLE_CI


def test_sim_healthy_bundle_shape():
    rows = mmrec.transcript_rows(str(SESSIONS / "sim-healthy.mmrec"))
    assert len(rows) >= 6
    dirs = [r["dir"] for r in rows]
    assert dirs[0] == "tx"
    assert set(dirs) == {"tx", "rx"}
    offsets = [r["t"] for r in rows]
    assert offsets == sorted(offsets)
    txs = [r["data"] for r in rows if r["dir"] == "tx"]
    assert txs[0] == "ATZ"
    assert any(t.startswith("01") for t in txs)


def test_tampered_member_breaks_verify(tmp_path):
    src = SESSIONS / "sim-healthy.mmrec"
    raw = bytearray(src.read_bytes())
    # Flip a byte late in the file (inside a deflated member).
    raw[len(raw) // 2] ^= 0xFF
    bad = tmp_path / "tampered.mmrec"
    bad.write_bytes(bytes(raw))
    with pytest.raises(mmrec.MmrecError):
        mmrec.verify(bad)


def test_swapped_manifest_hash_breaks_verify(tmp_path):
    members = mmrec.load(str(SESSIONS / "sim-healthy.mmrec"))
    manifest = json.loads(members["manifest.json"].decode())
    first = next(iter(manifest["files"]))
    manifest["files"][first] = "0" * 64
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in members.items():
            if name == "manifest.json":
                zf.writestr(name, json.dumps(manifest).encode())
            else:
                zf.writestr(name, blob)
    bad = tmp_path / "rehashed.mmrec"
    bad.write_bytes(buf.getvalue())
    with pytest.raises(mmrec.MmrecError):
        mmrec.verify(bad)
