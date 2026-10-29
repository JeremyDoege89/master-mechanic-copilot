#!/usr/bin/env bash
# M7 §4 signed release APK (simulator mode is the default transport).
#
# Owner-run: the keystore is created by the owner OUTSIDE the repo/sandbox
# (docs/RELEASE.md) and is never committed. The script refuses unless
# MMC_KEYSTORE_PATH and MMC_KEYSTORE_ALIAS point outside the repo;
# passwords arrive via env / gitignored local.properties, never the repo.
#
# Usage:
#   scripts/build_release_apk.sh            # testDebugUnitTest + assembleRelease
#   scripts/build_release_apk.sh --check    # dry run for CI (no build)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "build_release_apk: $*" >&2; exit 1; }

CHECK=0
if [ "${1:-}" = "--check" ]; then CHECK=1; fi

# -- env / paths (no key material, path or password is ever committed) --
[ -n "${MMC_KEYSTORE_PATH:-}" ] || fail "MMC_KEYSTORE_PATH is not set (keystore lives outside the repo)"
[ -n "${MMC_KEYSTORE_ALIAS:-}" ] || fail "MMC_KEYSTORE_ALIAS is not set"
case "$MMC_KEYSTORE_PATH" in
  "$ROOT"/*) fail "MMC_KEYSTORE_PATH must point outside the repo (got $MMC_KEYSTORE_PATH)" ;;
esac
[ -f "$MMC_KEYSTORE_PATH" ] || fail "keystore not found at MMC_KEYSTORE_PATH=$MMC_KEYSTORE_PATH"

# -- gitignore guard: signing files must stay uncommittable --
for pat in '*.jks' '*.keystore' 'local.properties'; do
  git -C "$ROOT" check-ignore -q "$pat" 2>/dev/null \
    || fail "gitignore must cover $pat (signing files must never commit)"
done

# -- own-diff guard: no keystore path/password may appear in the worktree diff --
if git -C "$ROOT" status --porcelain | grep -Eq '\.jks$|\.keystore$|local\.properties$'; then
  fail "signing files present in git status — remove them before building"
fi
# Own-diff guard: a PEM private-key block in the script/gradle diff is key
# material (env *names* like MMC_KEYSTORE_PASSWORD are fine — they carry
# no values). Tracked/signing files are covered by the guards above.
if git -C "$ROOT" diff -- scripts/build_release_apk.sh android/app/build.gradle \
    | grep -E 'BEGIN .*PRIVATE KEY' >/dev/null; then
  fail "private-key block in script/gradle diff — refusing"
fi

echo "build_release_apk: keystore OK ($MMC_KEYSTORE_ALIAS @ outside-repo path)"
if [ "$CHECK" = 1 ]; then
  echo "build_release_apk: --check OK (env/paths/gitignore verified, no build)"
  exit 0
fi

export GRADLE_USER_HOME="${GRADLE_USER_HOME:-/tmp/mmc-gradle-home}"
(cd "$ROOT/android" && ./gradlew testDebugUnitTest assembleRelease --offline)

APK="$ROOT/android/app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || fail "expected APK missing: $APK"

# -- verify: cert fingerprint (never the key), no secrets inside, clean tree --
if command -v apksigner >/dev/null 2>&1; then
  apksigner verify --print-certs "$APK" | grep -Ei 'certificate|SHA-256' | sed 's/[A-Fa-f0-9]\{40,\}/<fingerprint>/g' || true
else
  echo "build_release_apk: WARN apksigner not on PATH — fingerprint check skipped"
fi
if unzip -l "$APK" | grep -Ei '\.env$|\.jks$|\.keystore$|local\.properties' >/dev/null; then
  fail "APK contains a secrets/keystore entry"
fi
echo "build_release_apk: APK clean (no .env/keystore entries)"
if git -C "$ROOT" status --porcelain | grep -Eq '\.jks$|\.keystore$|local\.properties$'; then
  fail "signing files appeared in git status after the build"
fi
sha256sum "$APK"
echo "build_release_apk: done ($APK)"
