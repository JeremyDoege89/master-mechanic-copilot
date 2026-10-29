"""MMC on Modal (M6 §5): replay-only public demo for judging.

Targets Modal CLI 1.6.3. The owner runs (token already in place)::

    modal secret create mmc-secrets MMC_VIN_KEY=...
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


def _resolve_repo_root() -> Path:
    """Repo checkout root.

    On Modal this module runs as ``/root/modal_app.py`` while the tree
    lives at ``/opt/mmc`` (``add_local_dir``), so the file location is
    wrong there: ``MMC_REPO_ROOT`` (``/opt/mmc`` in ``MODAL_ENV``) wins
    whenever it points at a checkout. Locally the file location is the
    fallback. The marker check keeps a stale/bogus env value from
    shadowing a good checkout.
    """
    env = os.environ.get("MMC_REPO_ROOT", "").strip()
    if env and (Path(env) / "packages" / "schemas").is_dir():
        return Path(env)
    return Path(__file__).resolve().parent


REPO_ROOT = _resolve_repo_root()
BACKEND_DIR = REPO_ROOT / "backend"
WEB_DIR = REPO_ROOT / "web"

# Modal function env: the public replay-only defaults.
MODAL_ENV = {
    "MMC_PUBLIC_MODE": "replay",
    "MMC_PUBLIC_LIVE_BUDGET_USD": "0",
    "REPLAY_USE_CACHED_LLM": "true",
    "DATA_DIR": "/data",
    # Tells backend/mmc/_paths.py where the checkout lives on Modal
    # (non-editable installs can't find packages/schemas from __file__).
    # Harmless locally: ignored unless it holds packages/schemas.
    "MMC_REPO_ROOT": "/opt/mmc",
}

BUNDLED_FIXTURES = "tests/fixtures/sessions"

# Modal image upload allowlist (via ignore=): the server needs backend/
# (incl. tests/fixtures/sessions/*.mmrec), packages/, web/ sources,
# config/ and scripts/. Everything else stays local: VCS, secrets, venvs,
# tool caches, built assets, the Android tree, device data dirs and
# research notes. Patterns are Modal add_local_dir ignore globs.
IMAGE_IGNORE = [
    ".git",
    ".env",
    ".env.*",
    "**/.venv",
    "**/node_modules",
    "web/dist",
    "android",
    "android/**",
    "**/__pycache__",
    "**/.pytest_cache",
    "backend/data",
    "data",
    ".agents",
    "docs/research",
    "**/*.apk",
    "**/*.aab",
]


def startup_check(data_dir: str) -> dict:
    """Budget-guard startup check: fail the deploy loudly, never unguarded.

    Both trackers must construct with positive hard stops and the LLM
    cache dir must be writable. Raises on any failure.
    """
    import sys

    # Re-resolve: the env may have been set after this module was imported.
    backend_dir = str(_resolve_repo_root() / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
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
    src = _resolve_repo_root() / "backend" / BUNDLED_FIXTURES
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

    for key, value in MODAL_ENV.items():
        os.environ.setdefault(key, value)
    # Resolve AFTER the env defaults land, so MMC_REPO_ROOT is honoured
    # even when it was set after this module was imported.
    repo_root = _resolve_repo_root()
    backend_dir = str(repo_root / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from mmc import public as public_mod
    from mmc.main import app as backend_app

    public_mod.enforce_public_defaults()
    data_dir = os.environ.get("DATA_DIR", "/data")
    startup_check(data_dir)
    seed_fixtures(data_dir)

    # The built web app ships at <repo>/web/dist (built during image
    # construction); on Modal that is /opt/mmc/web/dist.
    dist = web_dist or (repo_root / "web" / "dist")
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
    .add_local_dir(str(REPO_ROOT), remote_path="/opt/mmc", copy=True, ignore=IMAGE_IGNORE)
    .run_commands(
        # Editable: the container serves the /opt/mmc tree in place, so
        # repo-relative reads (schemas, KP packs, prompts, fixtures)
        # keep working. A non-editable `pip install` copies mmc into
        # site-packages and breaks every one of those lookups.
        "pip install -e /opt/mmc/backend",
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
