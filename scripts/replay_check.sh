#!/usr/bin/env bash
# Verify .mmrec bundles (manifest sha256 of every member) via backend/mmc/replay/mmrec.py.
# Usage: scripts/replay_check.sh [bundle ...]
set -euo pipefail
if [ "$#" -eq 0 ]; then
  echo "replay_check: no bundles given, nothing to verify"
  exit 0
fi
# An unexpanded glob arrives literally when no fixtures exist yet.
if [ "$#" -eq 1 ] && [ ! -e "$1" ]; then
  echo "replay_check: no bundles match, nothing to verify"
  exit 0
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$ROOT/backend" exec python3 -m mmc.replay.mmrec verify "$@"
