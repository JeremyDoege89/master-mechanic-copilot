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


def _add_local_dir_kwargs(stub):
    for c in stub.mock_calls:
        name = c[0] if len(c) > 0 else ""
        if "add_local_dir" in str(name):
            kwargs = c[2] if len(c) > 2 else {}
            return c[1], kwargs
    raise AssertionError("add_local_dir was not called on the Modal image")


def test_modal_objects_target_cli_1_6_api():
    module, stub = _load_modal_app()
    stub.App.assert_called_with("mmc")
    stub.Image.debian_slim.assert_called_with(python_version="3.12")
    stub.Volume.from_name.assert_called_with("mmc-data", create_if_missing=True)
    stub.Secret.from_name.assert_called_with("mmc-secrets")
    assert stub.asgi_app.called, "expected an asgi_app web endpoint"
    # add_local_dir is followed by run_commands, so Modal requires copy=True.
    _args, kwargs = _add_local_dir_kwargs(stub)
    assert kwargs.get("copy") is True, "add_local_dir must pass copy=True"
    ignore = kwargs.get("ignore") or getattr(module, "IMAGE_IGNORE", [])
    joined = "\n".join(str(p) for p in ignore)
    for token in (".env", ".venv", "node_modules", "android"):
        assert any(token in str(p) for p in ignore), f"ignore list must cover {token}: {joined}"
    # Public replay-only defaults travel with the deployment.
    assert module.MODAL_ENV["MMC_PUBLIC_MODE"] == "replay"
    assert module.MODAL_ENV["MMC_PUBLIC_LIVE_BUDGET_USD"] == "0"
    assert module.MODAL_ENV["REPLAY_USE_CACHED_LLM"] == "true"
    assert module.MODAL_ENV["DATA_DIR"] == "/data"
    # Live bug §1: one container serves everything (sessions live in one
    # container's memory/volume view), concurrent inputs keep polling fast,
    # one container stays warm through judging.
    fn_kwargs: dict = {}
    for c in stub.mock_calls:
        name = c[0] if len(c) > 0 else ""
        if str(name).endswith("function"):
            kw = c[2] if len(c) > 2 else {}
            if "volumes" in kw or "image" in kw:
                fn_kwargs = dict(kw)
    assert fn_kwargs, "expected an @app.function serving definition"
    assert fn_kwargs.get("max_containers") == 1, fn_kwargs
    assert fn_kwargs.get("min_containers") == 1, fn_kwargs
    assert fn_kwargs.get("scaledown_window"), f"scaledown_window keeps one warm: {fn_kwargs}"
    conc_kwargs: dict | None = None
    for c in stub.mock_calls:
        name = c[0] if len(c) > 0 else ""
        if "concurrent" in str(name):
            kw = dict(c[2]) if len(c) > 2 else {}
            # The stub also records the decorator application
            # (concurrent(...)(fn), args-only): want the settings call.
            if "max_inputs" in kw:
                conc_kwargs = kw
                break
    assert conc_kwargs is not None, "expected @modal.concurrent for concurrent inputs"
    assert conc_kwargs.get("max_inputs"), f"concurrent must set max_inputs: {conc_kwargs}"


def test_create_app_serves_api_and_web_offline(tmp_path, monkeypatch):
    import os

    module, _stub = _load_modal_app()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    # Public replay mode needs no paid-model keys: mmc-secrets holds only
    # MMC_VIN_KEY at first deploy, so both must be absent here.
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    for key in ("MMC_PUBLIC_MODE", "MMC_PUBLIC_LIVE_BUDGET_USD"):
        monkeypatch.delenv(key, raising=False)

    # create_app() writes the Modal defaults into the process env and
    # mounts / on the shared app: snapshot both and restore afterwards so
    # no later test observes the public deployment.
    from mmc.main import app as shared_app

    # The backend job has no built web/dist: stage a tiny temporary one
    # (index + one asset) so this test never needs the real web build.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script src="/assets/app.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("// test stub", encoding="utf-8")

    saved_env = {key: os.environ.get(key) for key in module.MODAL_ENV}
    saved_routes = len(shared_app.routes)
    saved_mounted = getattr(shared_app, "_mmc_web_mounted", False)
    try:
        app = module.create_app(web_dist=dist)
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
        from mmc import sessions as sess_mod

        sess_mod.register_store_refresh(None)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        del shared_app.routes[saved_routes:]
        if not saved_mounted and hasattr(shared_app, "_mmc_web_mounted"):
            delattr(shared_app, "_mmc_web_mounted")


def test_public_replay_startup_needs_no_api_keys(tmp_path, monkeypatch):
    """Import + startup_check in public replay mode need no paid-model keys."""
    import os

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert "MODEL_API_KEY" not in os.environ
    assert "BASETEN_API_KEY" not in os.environ
    module, _stub = _load_modal_app()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "true")
    module.startup_check(str(tmp_path))


def _load_modal_app_from(path: Path, name: str):
    """Import modal_app.py from an arbitrary location with a stub `modal`."""
    stub = MagicMock(name="modal")
    sys.modules["modal"] = stub
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("modal", None)
    return module, stub


def test_copied_modal_app_resolves_repo_from_env(tmp_path, monkeypatch):
    """Modal runs modal_app.py as /root/modal_app.py, not from the repo.

    Copies modal_app.py to an unrelated directory (so the file location
    points nowhere useful), sets MMC_REPO_ROOT at the real checkout, and
    drives create_app() + the judge path — /health, /replay of the judge
    fixture, report .json/.html/.pdf — through TestClient. A repo-root
    derived from the file location fails here (fixtures/web dist miss);
    schema reads via MMC_REPO_ROOT must also hold for the non-editable
    layout, where site-packages has no packages/schemas sibling.
    """
    import os
    import shutil

    fake_root = tmp_path / "fake_root"
    fake_root.mkdir()
    copied = fake_root / "modal_app.py"
    shutil.copyfile(REPO_ROOT / "modal_app.py", copied)
    assert copied.read_text() == (REPO_ROOT / "modal_app.py").read_text()

    data_dir = tmp_path / "data"
    monkeypatch.setenv("MMC_REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    for key in ("MMC_PUBLIC_MODE", "MMC_PUBLIC_LIVE_BUDGET_USD"):
        monkeypatch.delenv(key, raising=False)

    module, _stub = _load_modal_app_from(copied, "modal_app_copy_under_test")
    # The copy's own location must NOT be mistaken for the checkout.
    assert module._resolve_repo_root() == REPO_ROOT
    assert module.BACKEND_DIR == REPO_ROOT / "backend"
    assert module.MODAL_ENV["MMC_REPO_ROOT"] == "/opt/mmc"

    from mmc.main import app as shared_app

    saved_env = {key: os.environ.get(key) for key in module.MODAL_ENV}
    saved_routes = len(shared_app.routes)
    saved_mounted = getattr(shared_app, "_mmc_web_mounted", False)
    try:
        app = module.create_app()
        assert os.environ["MMC_REPO_ROOT"] == str(REPO_ROOT)
        assert (data_dir / "fixtures" / "sessions" / "sim-healthy.mmrec").exists()

        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            replay = c.post("/replay", json={"fixture": "sim-healthy.mmrec"})
            assert replay.status_code == 200, replay.text
            session_id = replay.json()["session_id"]
            built = c.post(f"/sessions/{session_id}/report")
            assert built.status_code == 200, built.text
            report_id = built.json()["report_id"]
            as_json = c.get(f"/reports/{report_id}.json")
            assert as_json.status_code == 200, as_json.text
            assert as_json.json()["session_id"] == session_id
            as_html = c.get(f"/reports/{report_id}.html")
            assert as_html.status_code == 200, as_html.text
            assert "<html" in as_html.text.lower()
            as_pdf = c.get(f"/reports/{report_id}.pdf")
            assert as_pdf.status_code == 200, as_pdf.text[:200]
            assert as_pdf.content.startswith(b"%PDF")
    finally:
        from mmc import sessions as sess_mod

        sess_mod.register_store_refresh(None)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        del shared_app.routes[saved_routes:]
        if not saved_mounted and hasattr(shared_app, "_mmc_web_mounted"):
            delattr(shared_app, "_mmc_web_mounted")


def test_site_packages_copy_reads_schemas_from_repo_root_env(tmp_path):
    """Non-editable installs have no packages/schemas next to mmc/.

    Copies the installed ``mmc`` package to an isolated dir (no
    ``packages/`` sibling, like site-packages), imports it there with
    MMC_REPO_ROOT pointing at the real checkout, and exercises every
    repo-relative read. Under the old ``parents[N] / "packages"`` lookups
    this fails with FileNotFoundError — the Modal crash.
    """
    import os
    import shutil
    import subprocess

    isolated = tmp_path / "sitepkgs"
    shutil.copytree(REPO_ROOT / "backend" / "mmc", isolated / "mmc")
    assert not (isolated / "packages").exists()

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "sys.path.insert(0, os.environ['ISOLATED'])\n"
        "import mmc\n"
        "assert mmc.__file__.startswith(os.environ['ISOLATED']), mmc.__file__\n"
        "from mmc._paths import schemas_dir\n"
        "assert (schemas_dir() / 'vision_components.json').is_file()\n"
        "from mmc.meta import spark as spark_mod\n"
        "spark_mod._validators_cached()['vision_components'].validate(\n"
        "    json.loads((schemas_dir() / 'examples' /\n"
        "        'vision_components.example.json').read_text()))\n"
        "from mmc.meta import wonder as wonder_mod\n"
        "wonder_mod._validator()\n"
        "from mmc.vision import components as comp_mod, instruments as inst_mod\n"
        "comp_mod._validator()\n"
        "inst_mod._validator()\n"
        "from mmc.upload import specs as specs_mod\n"
        "specs_mod._validator()\n"
        "from mmc import sessions as sess_mod\n"
        "sess_mod._validators()\n"
        "from mmc.report import build as build_mod\n"
        "build_mod._validate_report\n"
        "build_mod.prompts_sha256()\n"
        "from mmc.kp.loader import load_pack\n"
        "from mmc._paths import packs_dir\n"
        "load_pack(packs_dir() / 'ford_gen_v1.yaml')\n"
        "print('schemas-ok')\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ISOLATED": str(isolated),
        "MMC_REPO_ROOT": str(REPO_ROOT),
        "DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(isolated),
    }
    proc = subprocess.run(
        [sys.executable, str(probe)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "schemas-ok" in proc.stdout, proc.stderr[-3000:]


def test_seed_fixtures_replaces_stale_bundle(tmp_path):
    """Live bug §3: a volume copy whose sha256 differs gets recopied."""
    import hashlib

    module, _stub = _load_modal_app()
    src = (
        module._resolve_repo_root() / "backend" / module.BUNDLED_FIXTURES / "sim-healthy.mmrec"
    )
    assert src.is_file(), f"fixture missing: {src}"
    dest = tmp_path / "fixtures" / "sessions"
    dest.mkdir(parents=True)
    stale = dest / "sim-healthy.mmrec"
    stale.write_bytes(b"stale-bundle-bytes")
    module.seed_fixtures(str(tmp_path))
    assert stale.read_bytes() == src.read_bytes()
    assert hashlib.sha256(stale.read_bytes()).hexdigest() == hashlib.sha256(
        src.read_bytes()
    ).hexdigest()
    # Identical bytes are left alone (no rewrite churn on every boot).
    mtime = stale.stat().st_mtime_ns
    module.seed_fixtures(str(tmp_path))
    assert stale.stat().st_mtime_ns == mtime


def test_create_app_registers_volume_reload_hook(tmp_path, monkeypatch):
    """Live bug §1: create_app wires volume.reload() behind lookup misses."""
    import os

    module, _stub = _load_modal_app()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    for key in ("MMC_PUBLIC_MODE", "MMC_PUBLIC_LIVE_BUDGET_USD"):
        monkeypatch.delenv(key, raising=False)

    from mmc import sessions as sess_mod
    from mmc.main import app as shared_app

    saved_env = {key: os.environ.get(key) for key in module.MODAL_ENV}
    saved_routes = len(shared_app.routes)
    saved_mounted = getattr(shared_app, "_mmc_web_mounted", False)
    sess_mod.register_store_refresh(None)
    try:
        module.create_app()
        assert sess_mod._STORE_REFRESH_HOOK is not None
        # The stub `modal` volume's reload() is a no-op: must never raise.
        sess_mod.refresh_store_from_volume()
    finally:
        sess_mod.register_store_refresh(None)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        del shared_app.routes[saved_routes:]
        if not saved_mounted and hasattr(shared_app, "_mmc_web_mounted"):
            delattr(shared_app, "_mmc_web_mounted")


def test_session_lookup_miss_reloads_volume_before_404(tmp_path, monkeypatch):
    """Live bug §1: a disk miss reloads the volume view once before giving up."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from mmc import sessions as sess_mod

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="sim", vehicle=None)
    sid = session.id
    # Simulate a stale volume view: the session dir is hidden from disk
    # and from the in-memory cache until the "reload" restores it.
    registry._sessions.pop(sid, None)
    src = tmp_path / "sessions" / sid
    hidden = tmp_path / f".hidden-{sid}"
    src.rename(hidden)
    calls: list[str] = []

    def fake_reload() -> None:
        calls.append("reload")
        hidden.rename(src)

    sess_mod.register_store_refresh(fake_reload)
    try:
        assert registry.get(sid) is not None
        assert calls == ["reload"]
        # A genuinely unknown id still misses (after exactly one reload).
        assert registry.get("does-not-exist") is None
        assert calls == ["reload", "reload"]
    finally:
        sess_mod.register_store_refresh(None)


def test_report_lookup_miss_reloads_volume_before_404(tmp_path, monkeypatch):
    """Live bug §1: same reload-before-404 for the report file lookup."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from mmc import sessions as sess_mod

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="sim", vehicle=None)
    state = sess_mod.compute_state(session)
    report = sess_mod.build_report(session, state)
    sess_mod.save_report(registry, report)
    rid = report["report_id"]
    path = tmp_path / "reports" / f"{rid}.json"
    assert path.is_file()
    hidden = tmp_path / f".hidden-{rid}.json"
    path.rename(hidden)
    calls: list[str] = []

    def fake_reload() -> None:
        calls.append("reload")
        if hidden.exists():
            hidden.rename(path)

    sess_mod.register_store_refresh(fake_reload)
    try:
        loaded = sess_mod.load_report(registry, rid)
        assert loaded is not None and loaded["report_id"] == rid
        assert calls == ["reload"]
        assert sess_mod.load_report(registry, "does-not-exist") is None
    finally:
        sess_mod.register_store_refresh(None)
