"""GET /sessions/{id}/blobs/{sha256}: evidence-scoped blob downloads.

A digest streams ONLY when the session's own event log names it in an
``evidence_added`` payload (``blob_ref``/``content_hash``); anything else
is 404 so the public site cannot fetch arbitrary blobs. Covers a live
session (allowed + unreferenced cases) and a replay session restored
from a bundle (photo serves with its content type).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mmc.api import routes as api_routes
from mmc.main import app

FIXTURES = Path(__file__).parent / "fixtures"
IMAGES_DIR = FIXTURES / "images"
SESSIONS_OUT = FIXTURES / "sessions"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_MODE", raising=False)
    api_routes._listeners.clear()
    yield TestClient(app)
    api_routes._listeners.clear()


def _create_session(client: TestClient) -> str:
    r = client.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _upload(client: TestClient, sid: str, data: bytes, filename: str, mime: str) -> dict:
    r = client.post(f"/sessions/{sid}/blobs", files={"file": (filename, data, mime)})
    assert r.status_code == 200, r.text
    return r.json()


def _add_evidence(client: TestClient, sid: str, etype: str, blob_ref: str, sha: str) -> str:
    from mmc import sessions as sess_mod

    eid = uuid.uuid4().hex
    r = client.post(
        f"/sessions/{sid}/events",
        json={
            "type": "evidence_added",
            "payload": {
                "evidence_id": eid,
                "type": etype,
                "blob_ref": blob_ref,
                "content_hash": sha,
                "ts": sess_mod._utc_now(),
            },
        },
    )
    assert r.status_code == 200, r.text
    return eid


def test_referenced_blob_streams_unreferenced_is_404(client):
    sid = _create_session(client)
    photo = (IMAGES_DIR / "engine_bay_02.jpg").read_bytes()
    up = _upload(client, sid, photo, "engine_bay_02.jpg", "image/jpeg")
    _add_evidence(client, sid, "image", up["blob_ref"], up["sha256"])

    good = client.get(f"/sessions/{sid}/blobs/{up['sha256']}")
    assert good.status_code == 200, good.text[:200] if good.status_code != 200 else ""
    assert good.content == photo
    assert good.headers["content-type"].startswith("image/jpeg")

    # Well-formed but never referenced by this session: 404.
    other = hashlib.sha256(b"not-in-this-log").hexdigest()
    assert client.get(f"/sessions/{sid}/blobs/{other}").status_code == 404

    # A real blob that exists in the store but is NOT in this session's log: 404.
    stray = _upload(client, sid, b'{"x": 1}', "stray.json", "application/json")
    assert client.get(f"/sessions/{sid}/blobs/{stray['sha256']}").status_code == 404

    # Malformed digest: 404, not 500.
    assert client.get(f"/sessions/{sid}/blobs/not-a-sha").status_code == 404
    # Unknown session: 404.
    assert client.get(f"/sessions/nope/blobs/{up['sha256']}").status_code == 404


def test_replay_session_serves_bundle_photo(client):
    bundle = SESSIONS_OUT / "sim-vacuum-leak.mmrec"
    assert bundle.exists(), f"{bundle} missing: run scripts/make_demo_bundle.py"
    from mmc.replay import mmrec

    members = mmrec.loads(bundle.read_bytes())
    mmrec.verify_members(members, bundle.name)
    rows = mmrec.event_rows(members)
    images = [
        r["payload"]
        for r in rows
        if r["type"] == "evidence_added" and r["payload"].get("type") == "image"
    ]
    assert images, "demo bundle carries no image evidence"
    sha = str(images[0]["content_hash"])
    assert images[0]["blob_ref"].endswith(sha)

    r = client.post("/replay", json={"fixture": bundle.name})
    # Fixture lookup falls back to the repo copy when DATA_DIR is a fresh tmp dir.
    if r.status_code == 404:
        from mmc.replay import runner

        session = runner.replay_bundle_bytes(bundle.read_bytes(), label=bundle.name)
        sid = session.id
    else:
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]

    got = client.get(f"/sessions/{sid}/blobs/{sha}")
    assert got.status_code == 200, got.text[:200] if got.status_code != 200 else ""
    assert got.headers["content-type"].startswith("image/jpeg")
    assert got.content == (IMAGES_DIR / "engine_bay_02.jpg").read_bytes()

    assert client.get(f"/sessions/{sid}/blobs/{'0' * 64}").status_code == 404
