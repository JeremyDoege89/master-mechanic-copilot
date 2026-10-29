#!/usr/bin/env bash
# M7 §5 acceptance sweep: every AGENTS.md check runnable without keys/car/network.
# Prints `Check | Result | Evidence` (PASS/FAIL/SKIP), exits non-zero on any FAIL.
# Live/owner rows print SKIP with the owner step named, never silently passed.
#
# M7 review #4: every command-based row is decided by the underlying
# command's EXIT CODE via run_row (scripts/sweep_lib.sh) — never by
# grepping output text, so "1 failed, 19 passed" FAILs instead of
# matching a "passed" grep.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0

# shellcheck source=sweep_lib.sh
source "$ROOT/scripts/sweep_lib.sh"

printf '%-42s | %-4s | %s\n' "Check" "Result" "Evidence"
printf -- '--------------------------------------------|------|--------------------\n'

# -- backend suites (offline, .venv interpreter; exit codes decide) --
run_row "schema examples validate" bash -c "cd \"$ROOT/backend\" && .venv/bin/python -m pytest -q tests/test_schemas.py"

run_row "event-chain tamper + threading" bash -c "cd \"$ROOT/backend\" && .venv/bin/python -m pytest -q tests/test_event_store.py tests/test_event_store_threads.py tests/test_chain_properties.py"

run_row "sim scenarios converge + replay" bash -c "cd \"$ROOT/backend\" && .venv/bin/python -m pytest -q tests/test_engine_scenarios.py tests/test_engine_properties.py tests/test_engine_replay.py"

run_row "voice grammar + proxy guards" bash -c "cd \"$ROOT/backend\" && .venv/bin/python -m pytest -q tests/test_voice_commands.py tests/test_voice_proxy.py"

run_row "levels/trace + overlay + bundles" bash -c "cd \"$ROOT/backend\" && .venv/bin/python -m pytest -q tests/test_levels.py tests/test_sam_overlay_display.py tests/test_demo_bundles.py"

# -- full backend suite (must stay under 3 minutes; exit code decides) --
T0=$(date +%s)
FULL_LOG=$(mktemp /tmp/mmc-sweep-full-XXXXXX.log)
if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q >"$FULL_LOG" 2>&1); then
  T1=$(date +%s)
  DT=$((T1 - T0))
  if [ "$DT" -lt 180 ]; then pass "full backend suite <3min" "${DT}s: $(tail -1 "$FULL_LOG" | head -c 160)";
  else fail "full backend suite <3min" "${DT}s >= 180s"; fi
else
  fail "full backend suite <3min" "$(tail -3 "$FULL_LOG" | tr '\n' ' ' | head -c 300)"
fi
rm -f "$FULL_LOG"

# -- bundles / replay (exit codes decide) --
run_row "replay_check bundles verify" "$ROOT/scripts/replay_check.sh" "$ROOT"/backend/tests/fixtures/sessions/*.mmrec

run_row "demo bundles byte-identical" bash -c "cd \"$ROOT\" && backend/.venv/bin/python scripts/make_demo_bundle.py --check"

# -- release dry run (CI-safe, no keystore; exit code decides) --
# M7 review #13 requires the signing passwords even for --check, so the
# sweep exports dummy values (no build runs, values never leave the shell).
if touch /tmp/mmc-sweep-check.jks 2>/dev/null \
  && MMC_KEYSTORE_PATH=/tmp/mmc-sweep-check.jks MMC_KEYSTORE_ALIAS=sweep \
     MMC_KEYSTORE_PASSWORD=sweep-dummy MMC_KEY_PASSWORD=sweep-dummy \
     "$ROOT/scripts/build_release_apk.sh" --check >/dev/null 2>&1; then
  pass "release script --check" "build_release_apk.sh --check"
else fail "release script --check" "build_release_apk.sh --check"; fi
rm -f /tmp/mmc-sweep-check.jks

# -- web (offline; exit code decides; run_row keeps the tails as evidence) --
run_row "web build (tsc strict)" bash -c "cd \"$ROOT/web\" && npm ci --offline && npm run build"

# -- android (offline, seeded gradle home; exit code decides) --
run_row "android unit + assembleDebug" bash -c "cd \"$ROOT/android\" && GRADLE_USER_HOME=/tmp/mmc-gradle-home ./gradlew testDebugUnitTest assembleDebug --offline"

# -- owner / live rows (SKIP, never silently passed) --
skip "real-Ford .mmrec + M4 recording" "owner: on-vehicle checklist docs/SAFETY.md"
skip "M5 photo sets (7/10 overlays)" "owner: 10 saved engine-bay photos"
skip "voice ASR spike + session" "owner outside sandbox: python3 scripts/spike_voice_asr.py (~\$0.08)"
skip "Modal live smoke" "owner: modal deploy + scripts/modal_smoke.sh <url>"
skip "signed-APK phone install" "owner: docs/RELEASE.md build + install from the Release"
skip "judge screenshots" "owner: scripts/screenshot_site.sh <url>, paste shots back"

exit "$FAIL"
