"""M6 §5 + fix 3: public replay-only mode with an optional paid photo demo.

Default (MMC_PUBLIC_LIVE_BUDGET_USD=0): only replay/upload sessions;
live vision/instrument, PDF/video uploads and Wonder stay off. With a
budget > 0, photo keyframe + instrument run against a separate capped
tracker with a per-IP rate limit; Wonder/SAM video/video uploads stay off.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mmc import sessions as sess
from mmc.api import routes as api_routes
from mmc.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MMC_PUBLIC_MODE", "replay")
    monkeypatch.delenv("MMC_PUBLIC_LIVE_BUDGET_USD", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def _create(client: TestClient, mode: str):
    return client.post(
        "/sessions", json={"user_level": "L1", "tools": [], "mode": mode}
    )


def test_public_allows_only_replay_and_upload_sessions(client):
    assert _create(client, "replay").status_code == 200
    assert _create(client, "upload").status_code == 200
    live = _create(client, "live")
    assert live.status_code == 400, live.text
    sim = _create(client, "sim")
    assert sim.status_code == 400, sim.text


def test_public_blocks_paid_vision_without_demo_budget(client):
    from mmc.api import routes_vision
    from mmc.meta.client import MetaClient

    calls = {"n": 0}

    def boom(model, kwargs):
        calls["n"] += 1
        raise AssertionError("no live calls in public mode")

    analyzer = routes_vision.LiveVisionAnalyzer(
        client=MetaClient(
            api_key="test-key",
            cache_dir="/tmp/mmc-public-test-cache",
            replay_cached=False,
            transport=boom,
            sleep=lambda s: None,
        ),
        open_client=None,
    )
    prev = routes_vision.get_analyzer()
    routes_vision.set_analyzer(analyzer)
    try:
        session_id = _create(client, "upload").json()["session_id"]
        up = client.post(
            f"/sessions/{session_id}/blobs", files={"file": ("x.jpg", b"fake-jpeg")}
        )
        assert up.status_code == 200, up.text
        blob_ref = up.json()["blob_ref"]

        key = client.post(
            f"/sessions/{session_id}/vision/keyframe", json={"blob_ref": blob_ref}
        )
        assert key.status_code == 501, key.text
        inst = client.post(
            f"/sessions/{session_id}/vision/instrument", json={"blob_ref": blob_ref}
        )
        assert inst.status_code == 501, inst.text
        assert calls["n"] == 0
    finally:
        routes_vision.set_analyzer(prev)


def test_public_blocks_video_and_pdf_uploads(client):
    session_id = _create(client, "upload").json()["session_id"]
    pdf = client.post(
        f"/sessions/{session_id}/blobs", files={"file": ("m.pdf", b"%PDF-1.4 fake")}
    )
    blob_ref = pdf.json()["blob_ref"]
    r = client.post(
        f"/sessions/{session_id}/uploads/pdf/extract", json={"blob_ref": blob_ref}
    )
    assert r.status_code == 501, r.text
    vid = client.post(
        f"/sessions/{session_id}/blobs", files={"file": ("c.mp4", b"fake-mp4")}
    )
    r2 = client.post(
        f"/sessions/{session_id}/uploads/video/analyze",
        json={"blob_ref": vid.json()["blob_ref"]},
    )
    assert r2.status_code == 501, r2.text


def test_public_demo_budget_serves_instrument_on_separate_tracker(
    client, tmp_path, monkeypatch
):
    from mmc import public as public_mod
    from mmc.api import routes_vision
    from mmc.meta.budget import get_tracker
    from mmc.meta.client import MetaClient

    monkeypatch.setenv("MMC_PUBLIC_LIVE_BUDGET_USD", "50")

    reading = {"value": 12.1, "units": "V", "confidence": 0.9, "display_text": "12.1"}
    calls = {"n": 0}

    def transport(model, kwargs):
        calls["n"] += 1
        return SimpleNamespace(
            output_text=json.dumps(reading), usage={"input_tokens": 100, "output_tokens": 10}
        )

    meta_before = get_tracker("meta").spend_usd
    analyzer = routes_vision.LiveVisionAnalyzer(
        client=MetaClient(
            api_key="test-key",
            cache_dir=tmp_path / "pub-cache",
            replay_cached=False,
            transport=transport,
            sleep=lambda s: None,
        ),
        open_client=None,
    )
    prev = routes_vision.get_analyzer()
    routes_vision.set_analyzer(analyzer)
    try:
        session_id = _create(client, "upload").json()["session_id"]
        up = client.post(
            f"/sessions/{session_id}/blobs", files={"file": ("m.jpg", b"fake-jpeg")}
        )
        body = {"blob_ref": up.json()["blob_ref"]}
        r = client.post(f"/sessions/{session_id}/vision/instrument", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["value"] == 12.1
        assert calls["n"] == 1
        assert get_tracker("meta").spend_usd == meta_before
        assert public_mod.get_public_tracker().spend_usd > 0
    finally:
        routes_vision.set_analyzer(prev)


def test_public_rate_limit_is_per_ip():
    from fastapi import HTTPException

    from mmc import public as public_mod

    ip = "10.9.9.9-test"
    base = 1_700_000_000.0
    for i in range(10):
        public_mod.check_public_rate(ip, now=base + i)
    with pytest.raises(HTTPException) as exc:
        public_mod.check_public_rate(ip, now=base + 10)
    assert exc.value.status_code == 429
    public_mod.check_public_rate("10.9.9.10-test", now=base + 10)


def test_public_report_ships_without_wonder(client):
    session_id = _create(client, "replay").json()["session_id"]
    r = client.post(f"/sessions/{session_id}/report")
    assert r.status_code == 200, r.text
    report = client.get(f"/reports/{r.json()['report_id']}.json").json()
    assert report["wonder_review"] is None
    assert "public" in (report["wonder_unavailable"] or "").lower()
