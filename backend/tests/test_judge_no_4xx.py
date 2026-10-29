"""Judge run has zero 4xx responses (M6B judge polish).

Drives the exact request sequence the web app performs during a judge
replay (Replay.tsx + Report.tsx + BudgetBadge) with TestClient — no
long-running server — and asserts every response is < 400:

- POST /replay (both judge fixtures), GET events, GET state at every
  scrubber position 0..max (plus the stale-cursor overshoot a session
  switch can send), recorded GET /reports/<id>.{json,html,pdf},
  keyframe blob GETs the evidence drawer <img> tags fetch,
  POST /sessions/<id>/report + the report-page jump GET, GET /budget.

The static layer is covered separately: the deployed site serves
web/dist at / (Modal) and `vite dev` serves web/public locally, so
GET /favicon.ico must resolve — see test_favicon_served_from_static.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from mmc.api import routes as api_routes
from mmc.api.routes_uploads import router as uploads_router
from mmc.api.routes_vision import router as vision_router
from mmc.main import app as api_app

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_PUBLIC = REPO_ROOT / "web" / "public"

JUDGE_FIXTURES = ("sim-vacuum-leak.mmrec", "sim-coil-unplugged.mmrec")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "true")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(api_app) as c:
        yield c
    api_routes._listeners.clear()


def _image_digests(events: list[dict]) -> list[str]:
    digests = []
    for event in events:
        if event.get("type") != "evidence_added":
            continue
        payload = event.get("payload") or {}
        if payload.get("type") != "image":
            continue
        for key in ("content_hash", "blob_ref"):
            raw = payload.get(key) or ""
            digest = str(raw).split("/")[-1].lower()
            if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest):
                digests.append(digest)
                break
    return digests


def test_judge_api_sequence_has_zero_4xx(client):
    """Every request the judge web flow performs answers < 400."""
    seen: list[tuple[str, str, int]] = []

    def check(method: str, url: str, response) -> dict:
        seen.append((method, url, response.status_code))
        assert response.status_code < 400, (
            f"judge run hit {response.status_code} on {method} {url}: "
            f"{response.text[:200]}"
        )
        return response.json() if "application/json" in response.headers.get(
            "content-type", "") else response

    for fixture in JUDGE_FIXTURES:
        replay = check("POST", "/replay",
                       client.post("/replay", json={"fixture": fixture}))
        session_id = replay["session_id"]
        events = check("GET", f"/sessions/{session_id}/events",
                       client.get(f"/sessions/{session_id}/events"))["events"]
        assert events, f"{fixture} replayed zero events"
        max_seq = events[-1]["seq"]
        # Scrubber walk 0..max, plus the stale-cursor overshoot a fixture
        # switch can send (old max against the new, shorter session).
        for upto in list(range(0, max_seq + 1)) + [max_seq + 5]:
            check("GET", f"/sessions/{session_id}/state?upto_seq={upto}",
                  client.get(f"/sessions/{session_id}/state",
                             params={"upto_seq": upto}))
        check("GET", f"/sessions/{session_id}/state",
              client.get(f"/sessions/{session_id}/state"))
        # Report jump from the recorded report_built event.
        built = [e for e in events if e["type"] == "report_built"]
        assert built, f"{fixture} has no recorded report for the Report tab"
        recorded_id = built[-1]["payload"]["report_id"]
        for suffix in (".json", ".html", ".pdf"):
            check("GET", f"/reports/{recorded_id}{suffix}",
                  client.get(f"/reports/{recorded_id}{suffix}"))
        # Evidence-drawer <img> tags: every referenced keyframe photo.
        for digest in _image_digests(events):
            check("GET", f"/sessions/{session_id}/blobs/{digest[:8]}…",
                  client.get(f"/sessions/{session_id}/blobs/{digest}"))
        # Judge "Build report" click on the replay session + report jump.
        report_id = check("POST", f"/sessions/{session_id}/report",
                          client.post(f"/sessions/{session_id}/report"))["report_id"]
        check("GET", f"/reports/{report_id}.json",
              client.get(f"/reports/{report_id}.json"))
    check("GET", "/budget", client.get("/budget"))
    assert not [s for s in seen if s[2] >= 400]
    assert len(seen) > 20


def test_judge_default_bundle_is_a_full_investigation(tmp_path, monkeypatch):
    """The landing auto-plays the first /fixtures entry: it must be a full
    investigation (>= 15 events ending in a report), never a CI stub like
    the 1-event sim-healthy.mmrec that hijacked the landing."""
    from mmc.replay import mmrec, runner

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fixtures = runner.list_fixtures()
    assert fixtures, "judge landing has nothing to play"
    assert all(f["name"] != "sim-healthy.mmrec" for f in fixtures)
    default = fixtures[0]
    rows = mmrec.event_rows(mmrec.loads(runner.fixture_bytes(default["name"])))
    assert len(rows) >= 15, (default["name"], len(rows))
    assert any(r.get("type") == "report_built" for r in rows), default["name"]


def _is_valid_ico(data: bytes) -> bool:
    """ICONDIR with type 1 and at least one directory entry."""
    if len(data) < 6 or data[:4] != b"\x00\x00\x01\x00":
        return False
    count = int.from_bytes(data[4:6], "little")
    return count >= 1 and len(data) >= 6 + 16 * count


def test_favicon_served_from_static():
    """GET /favicon.ico resolves on the static layer (no 404 in judge logs).

    The deployed site mounts web/dist at / and Vite copies web/public
    verbatim to the dist root, so the check-in is web/public/favicon.ico;
    serving it here through StaticFiles mirrors that mount. web/index.html
    must also declare it so browsers use it directly.
    """
    icon = WEB_PUBLIC / "favicon.ico"
    assert icon.is_file(), "web/public/favicon.ico missing (browsers fetch it unprompted)"
    assert _is_valid_ico(icon.read_bytes()), "web/public/favicon.ico is not a valid ICO"
    index = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "favicon.ico" in index, "web/index.html must link /favicon.ico"

    prod = FastAPI()
    prod.include_router(api_routes.router)
    prod.include_router(vision_router)
    prod.include_router(uploads_router)
    prod.mount("/", StaticFiles(directory=str(WEB_PUBLIC), html=True), name="web")
    with TestClient(prod) as c:
        r = c.get("/favicon.ico")
        assert r.status_code == 200, r.text
        assert r.content, "favicon served empty"
