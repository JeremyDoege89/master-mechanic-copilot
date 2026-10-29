#!/usr/bin/env bash
# Import phone .mmrec recordings for judge-demo export (owner-run).
#
# The phone writes its adapter transcript via Recorder.kt to the app cache
# dir (MmcApp.uploadMmrec: cacheDir/session.mmrec, package dev.dreamz.mmc)
# and uploads the same bytes through POST .../blobs. This script pulls every
# .mmrec under that cache dir into a staging dir, verifies each
# (mmrec verify + replay_check.sh), and prints the matching
# scripts/export_session_bundle.py command (session_id comes from the
# recording's meta.json).
#
# Usage:
#   scripts/import_phone_recording.sh [--out data/phone_recordings] [--package dev.dreamz.mmc]
#
# Secret hygiene: no keys are read, printed, or logged.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/data/phone_recordings"
PACKAGE="dev.dreamz.mmc"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --package) PACKAGE="$2"; shift 2 ;;
    -h|--help)
      echo "usage: $(basename "$0") [--out DIR] [--package PKG]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v adb >/dev/null 2>&1 || { echo "import_phone_recording: adb not on PATH" >&2; exit 1; }

mkdir -p "$OUT"
echo "staging dir: $OUT"

# Preferred: run-as keeps the pull working on non-rooted devices; fall back
# to a direct pull of the app cache dir when run-as is unavailable.
REMOTE_CACHE="/data/data/$PACKAGE/cache"
TMP_LIST="$(mktemp)"
trap 'rm -f "$TMP_LIST"' EXIT

if adb shell run-as "$PACKAGE" ls "$REMOTE_CACHE" >/dev/null 2>&1; then
  echo "listing $REMOTE_CACHE via run-as $PACKAGE"
  adb shell run-as "$PACKAGE" ls "$REMOTE_CACHE" | tr -d '\r' >"$TMP_LIST" || true
  pulled=0
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    case "$name" in *.mmrec) ;; *) continue ;; esac
    echo "pulling $name ..."
    # run-as has no pull: stream the bytes out through exec-out.
    if adb exec-out run-as "$PACKAGE" cat "$REMOTE_CACHE/$name" >"$OUT/$name"; then
      pulled=$((pulled + 1))
    else
      echo "WARN: could not pull $name" >&2
      rm -f "$OUT/$name"
    fi
  done <"$TMP_LIST"
  if [ "$pulled" -eq 0 ]; then
    echo "no .mmrec files pulled via run-as (is the recording saved yet? Recorder.kt writes cacheDir/session.mmrec on Upload .mmrec)" >&2
  fi
else
  echo "run-as unavailable; trying direct pull of $REMOTE_CACHE"
  if ! adb pull "$REMOTE_CACHE" "$OUT/cache-pull" >&2; then
    echo "import_phone_recording: cannot read $REMOTE_CACHE (device locked, wrong package $PACKAGE, or no recording yet)" >&2
    exit 1
  fi
  # Flatten any pulled tree into $OUT.
  while IFS= read -r f; do
    cp "$f" "$OUT/$(basename "$f")"
  done < <(find "$OUT/cache-pull" -name '*.mmrec' 2>/dev/null || true)
  rm -rf "$OUT/cache-pull"
fi

shopt -s nullglob
files=("$OUT"/*.mmrec)
if [ "${#files[@]}" -eq 0 ]; then
  echo "no .mmrec recordings in $OUT" >&2
  exit 1
fi

fail=0
for f in "${files[@]}"; do
  base="$(basename "$f")"
  echo "== $base =="
  if ! PYTHONPATH="$ROOT/backend" python3 -m mmc.replay.mmrec verify "$f"; then
    echo "FAIL: mmrec verify $base" >&2
    fail=1
    continue
  fi
  if ! "$ROOT/scripts/replay_check.sh" "$f"; then
    echo "FAIL: replay_check $base" >&2
    fail=1
    continue
  fi
  sid="$(PYTHONPATH="$ROOT/backend" python3 -c 'import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(json.loads(z.read("meta.json").decode()).get("session_id",""))' "$f")"
  if [ -n "$sid" ]; then
    echo "next: scripts/export_session_bundle.py $sid --label \"Ford <model/engine>, <fault>\" --out backend/tests/fixtures/sessions/real-<short>.mmrec"
  else
    echo "next: recording has no session_id in meta.json; export the backend live/upload session id for this drive instead:"
    echo "  scripts/export_session_bundle.py <session_id> --label \"Ford <model/engine>, <fault>\""
  fi
done

exit "$fail"
