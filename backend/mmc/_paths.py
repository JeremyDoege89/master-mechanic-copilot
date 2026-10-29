"""Repo-root / data-file resolution (Modal-safe).

``pip install /opt/mmc/backend`` installs a non-editable copy into
site-packages, so any ``Path(__file__).parents[N] / "packages" / ...``
lookup breaks there (schemas live outside the ``mmc`` package, tests are
not installed at all). Every repo-relative read must go through this
module instead:

- ``MMC_REPO_ROOT`` env wins when it points at a checkout (Modal sets
  ``MMC_REPO_ROOT=/opt/mmc`` via ``MODAL_ENV``; the container also has
  the full tree at ``/opt/mmc`` from ``add_local_dir``).
- Otherwise walk up from this file to the first ancestor holding
  ``packages/schemas`` (local editable checkout).
- Package-internal data (KP packs, agent prompts, report templates) is
  resolved inside the installed package first so it works with or
  without a checkout present.

Read the env lazily (at call time, not import time) so process env set
between import and first use — e.g. ``modal_app.create_app`` applying
``MODAL_ENV`` defaults before importing ``mmc.main`` — is honoured.
"""

from __future__ import annotations

import os
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_PACKAGE_DIR = _THIS_FILE.parent  # .../mmc  (works installed or editable)


def repo_root() -> Path:
    """Root of the mmc checkout (holds ``packages/``, ``backend/``, ``web/``)."""
    env = os.environ.get("MMC_REPO_ROOT", "").strip()
    if env and (Path(env) / "packages" / "schemas").is_dir():
        return Path(env)
    for parent in [_PACKAGE_DIR, *_PACKAGE_DIR.parents]:
        if (parent / "packages" / "schemas").is_dir():
            return parent
    # Last resort: classic backend/ layout (…/mmc -> …/backend -> repo).
    return _PACKAGE_DIR.parents[1]


def schemas_dir() -> Path:
    """Directory holding the ``packages/schemas/*.json`` sources of truth."""
    return repo_root() / "packages" / "schemas"


def packs_dir() -> Path:
    """Directory holding the bundled KP ``*.yaml`` packs (inside the package)."""
    inside = _PACKAGE_DIR / "kp" / "packs"
    if inside.is_dir():
        return inside
    return repo_root() / "backend" / "mmc" / "kp" / "packs"


def prompts_dir() -> Path:
    """Directory holding the pinned agent prompt ``*.md`` files (in-package)."""
    inside = _PACKAGE_DIR / "agent" / "prompts"
    if inside.is_dir():
        return inside
    return repo_root() / "backend" / "mmc" / "agent" / "prompts"


def repo_fixtures_dir() -> Path:
    """Repo copy of the shipped ``*.mmrec`` judge fixtures (fallback).

    Local dev serves these straight from the checkout; Modal seeds them
    into ``DATA_DIR`` on boot and ``runner.fixture_bytes`` prefers the
    seeded copy, so this is only the offline fallback.
    """
    return repo_root() / "backend" / "tests" / "fixtures" / "sessions"
