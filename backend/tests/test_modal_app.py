"""M6 §5: modal_app imports with a stub `modal` and serves offline.

The sandbox has no network and no `modal` package: the real
`modal deploy`/`modal secret create` run owner-side (Modal CLI 1.6.3).
Here we prove the module targets that API (App/Image/Volume/Secret/
asgi_app call shapes) and that the FastAPI wiring serves directly.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_modal_app():
    stub = MagicMock(name="modal")
    sys.modules["modal"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "modal_app_under_test", REPO_ROOT / "modal_app.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("modal", None)
    return module, stub


def test_modal_objects_target_cli_1_6_api():
    module, stub = _load_modal_app()
    stub.App.assert_called_with("mmc")
    stub.Image.debian_slim.assert_called_with(python_version="3.12")
    stub.Volume.from_name.assert_called_with("mmc-data", create_if_missing=True)
    stub.Secret.from_name.assert_called_with("mmc-secrets")
    assert stub.asgi_app.called, "expected an asgi_app web endpoint"
    # Public replay-only defaults travel with the deployment.
    assert module.MODAL_ENV["MMC_PUBLIC_MODE"] == "replay"
    assert module.MODAL_ENV["MMC_PUBLIC_LIVE_BUDGET_USD"] == "0"
    assert module.MODAL_ENV["REPLAY_USE_CACHED_LLM"] == "true"
    assert module.MODAL_ENV["DATA_DIR"] == "/data"


def test_create_app_serves_api_and_web_offline(tmp_path, monkeypatch):
    import os

    module, _stub = _load_modal_app()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    for key in ("MMC_PUBLIC_MODE", "MMC_PUBLIC_LIVE_BUDGET_USD"):
        monkeypatch.delenv(key, raising=False)

    # create_app() writes the Modal defaults into the process env and
    # mounts / on the shared app: snapshot both and restore afterwards so
    # no later test observes the public deployment.
    from mmc.main import app as shared_app

    saved_env = {key: os.environ.get(key) for key in module.MODAL_ENV}
    saved_routes = len(shared_app.routes)
    saved_mounted = getattr(shared_app, "_mmc_web_mounted", False)
    try:
        app = module.create_app()
        assert module.MODAL_ENV["MMC_PUBLIC_MODE"] == "replay"
        assert os.environ["MMC_PUBLIC_MODE"] == "replay"
        # Seeded judge fixture lands in the data dir on boot.
        assert (tmp_path / "fixtures" / "sessions" / "sim-healthy.mmrec").exists()

        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            budget = c.get("/budget")
            assert budget.status_code == 200, budget.text
            assert budget.json()["public_mode"] is True
            replay = c.post("/replay", json={"fixture": "sim-healthy.mmrec"})
            assert replay.status_code == 200, replay.text
            web = c.get("/?judge=1")
            assert web.status_code == 200, web.text
            assert '<div id="root">' in web.text
            assert "/assets/" in web.text
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        del shared_app.routes[saved_routes:]
        if not saved_mounted and hasattr(shared_app, "_mmc_web_mounted"):
            delattr(shared_app, "_mmc_web_mounted")
