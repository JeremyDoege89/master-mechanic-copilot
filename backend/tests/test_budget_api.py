"""M6 §6: GET /budget over the process-wide registries (no keys)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mmc.api import routes as api_routes
from mmc.main import app
from mmc.meta.budget import get_tracker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_MODE", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_LIVE_BUDGET_USD", raising=False)
    api_routes._listeners.clear()
    with TestClient(app) as c:
        yield c
    api_routes._listeners.clear()


def test_budget_reports_both_ledgers_with_remaining(client):
    meta, baseten = get_tracker("meta"), get_tracker("baseten")
    meta.add(4.0)
    baseten.add(2.0)
    try:
        r = client.get("/budget")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["meta"]["spend_usd"] == pytest.approx(meta.spend_usd)
        assert body["meta"]["hard_stop_usd"] == pytest.approx(120.0)
        assert body["meta"]["remaining_usd"] == pytest.approx(120.0 - meta.spend_usd)
        assert body["baseten"]["spend_usd"] == pytest.approx(baseten.spend_usd)
        assert body["baseten"]["hard_stop_usd"] == pytest.approx(80.0)
        assert body["baseten"]["remaining_usd"] == pytest.approx(80.0 - baseten.spend_usd)
        assert body["public_mode"] is False
        assert body["public_live"] is None
        dump = r.text
        assert "MODEL_API_KEY" not in dump and "BASETEN_API_KEY" not in dump
    finally:
        meta.spend_usd -= 4.0
        baseten.spend_usd -= 2.0


def test_budget_shows_public_demo_cap(client, monkeypatch):
    from mmc import public as public_mod

    monkeypatch.setenv("MMC_PUBLIC_MODE", "replay")
    monkeypatch.setenv("MMC_PUBLIC_LIVE_BUDGET_USD", "25")
    demo = public_mod.get_public_tracker()
    demo.add(1.5)
    try:
        body = client.get("/budget").json()
        assert body["public_mode"] is True
        assert body["public_live"]["spend_usd"] == pytest.approx(demo.spend_usd)
        assert body["public_live"]["cap_usd"] == pytest.approx(25.0)
    finally:
        demo.spend_usd -= 1.5
