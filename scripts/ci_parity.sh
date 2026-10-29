#!/usr/bin/env bash
# CI parity for the backend job (.github/workflows/ci.yml).
#
# Runs the backend suite the way CI does:
#   - no MODEL_API_KEY / BASETEN_API_KEY in the environment (tests that
#     build default clients must set a dummy key via monkeypatch),
#   - Python 3.12 (setup-uv python-version + `uv run --python 3.12`),
#   - ffmpeg installed (apt-get step in CI).
# Then re-runs the `needs_ffmpeg`-marked subset with a PATH that hides
# ffmpeg/ffprobe, proving those paths fail LOUDLY (clear ffmpeg error)
# instead of silently passing media through unscaled.
#
# Secret hygiene: key presence is reported only as set/unset; values are
# never printed, echoed, or logged.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test -n "${MODEL_API_KEY:-}" \
  && echo "MODEL_API_KEY=set (suite runs without it)" \
  || echo "MODEL_API_KEY=unset"
test -n "${BASETEN_API_KEY:-}" \
  && echo "BASETEN_API_KEY=set (suite runs without it)" \
  || echo "BASETEN_API_KEY=unset"

command -v ffmpeg >/dev/null 2>&1 \
  || { echo "ci_parity: ffmpeg not on PATH (CI installs it via apt-get)"; exit 1; }
command -v uv >/dev/null 2>&1 \
  || { echo "ci_parity: uv not on PATH"; exit 1; }

cd "$REPO_ROOT/backend"

echo "== python pin: must resolve 3.12 =="
env -u MODEL_API_KEY -u BASETEN_API_KEY \
  uv run --python 3.12 python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"

echo "== backend suite as CI does (no keys, python 3.12) =="
env -u MODEL_API_KEY -u BASETEN_API_KEY \
  uv run --python 3.12 pytest -q

echo "== no-ffmpeg proof: needs_ffmpeg subset must fail loudly =="
HIDE_ROOT="$(mktemp -d)"
trap 'rm -rf "$HIDE_ROOT"' EXIT
CLEAN_ENTRIES=()
while IFS= read -r dir; do
  [ -n "$dir" ] || continue
  if [ -x "$dir/ffmpeg" ] || [ -x "$dir/ffprobe" ]; then
    farm="$HIDE_ROOT/farm-$(printf '%s' "$dir" | tr -c '[:alnum:]' '_')"
    mkdir -p "$farm"
    for tool in "$dir"/*; do
      base="$(basename "$tool")"
      case "$base" in
        ffmpeg|ffprobe) continue ;;
      esac
      ln -s "$tool" "$farm/$base" 2>/dev/null || true
    done
    CLEAN_ENTRIES+=("$farm")
  else
    CLEAN_ENTRIES+=("$dir")
  fi
done <<< "$(printf '%s' "$PATH" | tr ':' '\n')"
CLEAN_PATH="$(IFS=:; printf '%s' "${CLEAN_ENTRIES[*]}")"
if PATH="$CLEAN_PATH" command -v ffmpeg >/dev/null 2>&1; then
  echo "ci_parity: could not hide ffmpeg from PATH; aborting proof"
  exit 1
fi

set +e
env -u MODEL_API_KEY -u BASETEN_API_KEY PATH="$CLEAN_PATH" \
  uv run --python 3.12 pytest -q -m needs_ffmpeg >"$HIDE_ROOT/noffmpeg.log" 2>&1
subset_status=$?
set -e
if [ "$subset_status" -eq 0 ]; then
  echo "ci_parity: FAIL - needs_ffmpeg subset passed with no ffmpeg (silent pass-through?)"
  exit 1
fi
if ! grep -qi "ffmpeg" "$HIDE_ROOT/noffmpeg.log"; then
  echo "ci_parity: FAIL - no-ffmpeg run failed without mentioning ffmpeg:"
  tail -n 20 "$HIDE_ROOT/noffmpeg.log"
  exit 1
fi
echo "no-ffmpeg proof OK: subset fails loudly with the ffmpeg error message"
echo "== ci_parity green =="
