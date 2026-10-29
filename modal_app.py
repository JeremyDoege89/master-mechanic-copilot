"""MMC on Modal (M6 §5): replay-only public demo for judging.

Targets Modal CLI 1.6.3. The owner runs (token already in place)::

    modal secret create mmc-secrets MODEL_API_KEY=... BASETEN_API_KEY=... MMC_VIN_KEY=...
    modal deploy modal_app.py

then ``scripts/modal_smoke.sh <printed-url>``.

Public safety: ``MMC_PUBLIC_MODE=replay`` is the Modal default (local dev
leaves it unset and is unaffected). Only replay/upload sessions, paid
vision/instrument/PDF/video/Wonder routes stay off unless the optional
judge live demo budget (``MMC_PUBLIC_LIVE_BUDGET_USD``, default 0 = off)
covers photo keyframe/instrument calls — see ``backend/mmc/public.py``.

All serving logic lives in ``create_app()`` so it is testable offline
without the ``modal`` package (tests import this module with a stub
``modal`` and drive the FastAPI wiring directly with TestClient).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = REPO_ROOT / "backend"
WEB_DIR = REPO_ROOT / "web"

# Modal function env: the public replay-only defaults.
MODAL_ENV = {
    "MMC_PUBLIC_MODE": "replay",
    "MMC_PUBLIC_LIVE_BUDGET_USD": "0",
    "REPLAY_USE_CACHED_LLM": "true",
    "DATA_DIR": "/data",
}

BUNDLED_FIXTURES = "tests/fixtures/sessions"


def startup_check(data_dir: str) -> dict:
    """Budget-guard startup check: fail the deploy loudly, never unguarded.

    Both trackers must construct with positive hard stops and the LLM
    cache dir must be writable. Raises on any failure.
    """
    import sys

    sys.path.insert(0, str(BACKEND_DIR))
    from mmc.meta.budget import get_tracker

    meta = get_tracker("meta")
    baseten = get_tracker("baseten")
    if not meta.hard_stop_usd > 0:
        raise RuntimeError(f"meta hard stop misconfigured: {meta.hard_stop_usd!r}")
    if not baseten.hard_stop_usd > 0:
        raise RuntimeError(f"baseten hard stop misconfigured: {baseten.hard_stop_usd!r}")
    if os.environ.get("REPLAY_USE_CACHED_LLM", "").lower() != "true":
        raise RuntimeError("REPLAY_USE_CACHED_LLM must be true in the public deployment")
    cache = Path(data_dir) / "llm_cache"
    cache.mkdir(parents=True, exist_ok=True)
    probe = cache / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return {
        "meta_stop": meta.hard_stop_usd,
        "baseten_stop": baseten.hard_stop_usd,
        "cache_dir": str(cache),
    }


def seed_fixtures(data_dir: str) -> list[str]:
    """Seed bundled .mmrec fixtures into the volume on first boot."""
    src = BACKEND_DIR / BUNDLED_FIXTURES
    dest = Path(data_dir) / "fixtures" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    seeded = []
    for bundle in sorted(src.glob("*.mmrec")):
        target = dest / bundle.name
        if not target.exists():
            shutil.copyfile(bundle, target)
        seeded.append(bundle.name)
    return seeded


def create_app(web_dist: Path | None = None):
    """Build the serving FastAPI app (backend API + web/dist at /)."""
    import sys

    sys.path.insert(0, str(BACKEND_DIR))
    for key, value in MODAL_ENV.items():
        os.environ.setdefault(key, value)

    from mmc import public as public_mod
    from mmc.main import app as backend_app

    public_mod.enforce_public_defaults()
    data_dir = os.environ.get("DATA_DIR", "/data")
    startup_check(data_dir)
    seed_fixtures(data_dir)

    dist = web_dist or (WEB_DIR / "dist")
    if dist.is_dir() and not getattr(backend_app, "_mmc_web_mounted", False):
        from fastapi.staticfiles import StaticFiles

        # Mounted last: API routes keep precedence, the web app serves /.
        backend_app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
        backend_app._mmc_web_mounted = True  # type: ignore[attr-defined]
    return backend_app


# -- Modal objects (1.6.x API; exercised with a stub `modal` offline) ----------

import modal  # noqa: E402  (stubbed in offline tests; real on Modal)

app = modal.App("mmc")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates", "curl", "ffmpeg", "gnupg")
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
        "apt-get install -y nodejs",
    )
    .add_local_dir(str(REPO_ROOT), remote_path="/opt/mmc")
    .run_commands(
        "pip install /opt/mmc/backend",
        "cd /opt/mmc/web && npm ci && npm run build",
    )
)

volume = modal.Volume.from_name("mmc-data", create_if_missing=True)
secrets = [modal.Secret.from_name("mmc-secrets")]


@app.function(
    image=image,
    volumes={"/data": volume},
    secrets=secrets,
    env=MODAL_ENV,
    timeout=600,
    memory=1024,
)
@modal.asgi_app()
def fastapi_app():
    return create_app()
