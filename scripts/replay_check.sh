#!/usr/bin/env bash
# M0 stub: replay determinism check lands with M3 fixtures.
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
echo "replay_check: M0 stub (no fixtures yet)"
exit 0
