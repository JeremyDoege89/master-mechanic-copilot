# Release: signed simulator APK (owner-run)

The release APK runs the full gate flow on the simulator transport (the
default), so it needs no car. Version stays whatever
`android/app/build.gradle` declares (`versionName`, `versionCode`).

## 1. Create the keystore (once, outside the repo)

```sh
keytool -genkeypair -v -keystore ~/.config/mmc/release.jks \
  -alias mmc-release -keyalg RSA -keysize 2048 -validity 10000
```

The keystore, its path and its passwords are never committed
(`*.jks`/`*.keystore`/`local.properties` are gitignored; the build
refuses when `MMC_KEYSTORE_PATH` points inside the repo).

## 2. Build

```sh
export MMC_KEYSTORE_PATH=~/.config/mmc/release.jks
export MMC_KEYSTORE_ALIAS=mmc-release
export MMC_KEYSTORE_PASSWORD='…'
export MMC_KEY_PASSWORD='…'
scripts/build_release_apk.sh
```

The script runs `testDebugUnitTest` then `assembleRelease`, prints the
signing-cert fingerprint (never the key), checks the APK contains no
`.env`/keystore entry, and prints the APK sha256. Dry-run for CI:

```sh
scripts/build_release_apk.sh --check
```

## 3. Publish (only when asked)

```sh
gh release create v0.1.0 \
  android/app/build/outputs/apk/release/app-release.apk \
  --title v0.1.0 --notes-file <notes>
```

Upload the APK plus its sha256. Publishing the GitHub Release itself is
owner-run, only when asked (AGENTS.md: don't push unless asked).

## 4. Phone install (owner manual check)

Install the APK from the Release on a phone, point it at the backend in
Settings, and run the simulator flow end to end (connect → live →
next test → report).
