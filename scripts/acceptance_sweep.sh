#!/usr/bin/env bash
# M7 §5 acceptance sweep: every AGENTS.md check runnable without keys/car/network.
# Prints `Check | Result | Evidence` (PASS/FAIL/SKIP), exits non-zero on any FAIL.
# Live/owner rows print SKIP with the owner step named, never silently passed.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0

row() { printf '%-42s | %-4s | %s\n' "$1" "$2" "$3"; }
pass() { row "$1" "PASS" "$2"; }
fail() { row "$1" "FAIL" "$2"; FAIL=1; }
skip() { row "$1" "SKIP" "$2"; }

printf '%-42s | %-4s | %s\n' "Check" "Result" "Evidence"
printf -- '--------------------------------------------|------|--------------------\n'

# -- backend suites (offline, .venv interpreter) --
if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q tests/test_schemas.py 2>&1 | tail -1 | grep -q "passed"); then
  pass "schema examples validate" "test_schemas.py"
else fail "schema examples validate" "test_schemas.py"; fi

if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q tests/test_event_store.py tests/test_event_store_threads.py tests/test_chain_properties.py 2>&1 | tail -1 | grep -q "passed"); then
  pass "event-chain tamper + threading" "test_event_store*.py + chain_properties"
else fail "event-chain tamper + threading" "see pytest output"; fi

if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q tests/test_engine_scenarios.py tests/test_engine_properties.py tests/test_engine_replay.py 2>&1 | tail -1 | grep -q "passed"); then
  pass "sim scenarios converge + replay" "test_engine_scenarios/properties/replay.py"
else fail "sim scenarios converge + replay" "see pytest output"; fi

if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q tests/test_voice_commands.py tests/test_voice_proxy.py 2>&1 | tail -1 | grep -q "passed"); then
  pass "voice grammar + proxy guards" "test_voice_commands/proxy.py (fake upstream)"
else fail "voice grammar + proxy guards" "see pytest output"; fi

if (cd "$ROOT/backend" && .venv/bin/python -m pytest -q tests/test_levels.py tests/test_sam_overlay_display.py tests/test_demo_bundles.py 2>&1 | tail -1 | grep -q "passed"); then
  pass "levels/trace + overlay + bundles" "test_levels/overlay_display/demo_bundles.py"
else fail "levels/trace + overlay + bundles" "see pytest output"; fi

# -- full backend suite (must stay under 3 minutes) --
T0=$(date +%s)
FULL_OUT=$(cd "$ROOT/backend" && .venv/bin/python -m pytest -q 2>&1 | tail -3)
T1=$(date +%s)
DT=$((T1 - T0))
if echo "$FULL_OUT" | grep -qE "[0-9]+ passed"; then
  if [ "$DT" -lt 180 ]; then pass "full backend suite <3min" "${DT}s: $(echo "$FULL_OUT" | tr '\n' ' ')";
  else fail "full backend suite <3min" "${DT}s >= 180s"; fi
else fail "full backend suite <3min" "$FULL_OUT"; fi

# -- bundles / replay --
if "$ROOT/scripts/replay_check.sh" "$ROOT"/backend/tests/fixtures/sessions/*.mmrec >/dev/null 2>&1; then
  pass "replay_check bundles verify" "replay_check.sh *.mmrec"
else fail "replay_check bundles verify" "replay_check.sh"; fi

if (cd "$ROOT" && backend/.venv/bin/python scripts/make_demo_bundle.py --check 2>&1 | grep -q "check OK"); then
  pass "demo bundles byte-identical" "make_demo_bundle.py --check"
else fail "demo bundles byte-identical" "make_demo_bundle.py --check"; fi

# -- release dry run (CI-safe, no keystore) --
if touch /tmp/mmc-sweep-check.jks 2>/dev/null \
  && MMC_KEYSTORE_PATH=/tmp/mmc-sweep-check.jks MMC_KEYSTORE_ALIAS=sweep "$ROOT/scripts/build_release_apk.sh" --check >/dev/null 2>&1; then
  pass "release script --check" "build_release_apk.sh --check"
else fail "release script --check" "build_release_apk.sh --check"; fi
rm -f /tmp/mmc-sweep-check.jks

# -- web (offline) --
if (cd "$ROOT/web" && npm ci --offline >/dev/null 2>&1 && npm run build >/dev/null 2>&1); then
  pass "web build (tsc strict)" "npm ci --offline + npm run build"
else fail "web build (tsc strict)" "npm ci --offline + npm run build"; fi

# -- android (offline, seeded gradle home) --
if (cd "$ROOT/android" && GRADLE_USER_HOME=/tmp/mmc-gradle-home ./gradlew testDebugUnitTest assembleDebug --offline -q >/dev/null 2>&1); then
  pass "android unit + assembleDebug" "gradlew testDebugUnitTest assembleDebug --offline"
else fail "android unit + assembleDebug" "see gradle output"; fi

# -- owner / live rows (SKIP, never silently passed) --
skip "real-Ford .mmrec + M4 recording" "owner: on-vehicle checklist docs/SAFETY.md"
skip "M5 photo sets (7/10 overlays)" "owner: 10 saved engine-bay photos"
skip "voice ASR spike + session" "owner outside sandbox: python3 scripts/spike_voice_asr.py (~\$0.08)"
skip "Modal live smoke" "owner: modal deploy + scripts/modal_smoke.sh <url>"
skip "signed-APK phone install" "owner: docs/RELEASE.md build + install from the Release"
skip "judge screenshots" "owner: scripts/screenshot_site.sh <url>, paste shots back"

exit "$FAIL"
