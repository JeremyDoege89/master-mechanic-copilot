#!/usr/bin/env bash
# M0: boot backend + web for local development.
# Usage: scripts/dev.sh  (Ctrl-C stops both)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd "$ROOT/backend" && uv run uvicorn mmc.main:app --port 8000) &
(cd "$ROOT/web" && npm run dev -- --port 5173) &
wait
