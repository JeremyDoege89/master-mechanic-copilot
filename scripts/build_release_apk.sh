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
# M7 review #13: both passwords are required even for --check, so a real
# build fails fast here instead of late at Gradle signing.
[ -n "${MMC_KEYSTORE_PATH:-}" ] || fail "MMC_KEYSTORE_PATH is not set (keystore lives outside the repo)"
[ -n "${MMC_KEYSTORE_ALIAS:-}" ] || fail "MMC_KEYSTORE_ALIAS is not set"
[ -n "${MMC_KEYSTORE_PASSWORD:-}" ] || fail "MMC_KEYSTORE_PASSWORD is not set (signing password comes from env, never the repo)"
[ -n "${MMC_KEY_PASSWORD:-}" ] || fail "MMC_KEY_PASSWORD is not set (key password comes from env, never the repo)"
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
# M7 review #13: the diff covers staged AND unstaged changes (diff HEAD),
# and untracked signing files / PEM blocks in new script/gradle files fail
# too — a staged-only or not-yet-staged key must not slip through.
# Only added lines matter (a removed key is gone). The `^+` filter keeps
# deletions and context out of the scan, and the pattern itself is split
# across quotes so this assignment line never matches it either.
key_pat='BEGIN .*PRIVATE '"KEY"
if git -C "$ROOT" diff HEAD -- scripts/build_release_apk.sh android/app/build.gradle \
    | grep '^+' | grep -E "$key_pat" >/dev/null; then
  fail "private-key block in script/gradle diff — refusing"
fi
untracked="$(git -C "$ROOT" status --porcelain | awk '$1 == "??" {print $2}')"
for f in $untracked; do
  case "$f" in
    *.jks|*.keystore|local.properties)
      fail "signing file untracked in worktree ($f) — remove it before building" ;;
  esac
  case "$f" in
    scripts/*|android/*)
      if [ -f "$ROOT/$f" ] && grep -I -E "$key_pat" "$ROOT/$f" >/dev/null 2>&1; then
        fail "private-key block in untracked file $f — refusing"
      fi
      ;;
  esac
done

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
# M7 review #13: apksigner verify is mandatory and fatal. Under
# `set -o pipefail` a failing verify fails the pipeline (nothing masks a
# failure), and a missing apksigner fails the build instead of skipping it.
if command -v apksigner >/dev/null 2>&1; then
  apksigner verify --print-certs "$APK" | grep -Ei 'certificate|SHA-256' | sed 's/[A-Fa-f0-9]\{40,\}/<fingerprint>/g'
else
  fail "apksigner not on PATH — cannot verify the signed APK"
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
