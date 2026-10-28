# Decisions

Newest first. Each entry: date, decision, why. Add one whenever scope, a dependency or an architecture choice changes.

## 2026-10-28 — M4 gate: sessions API + WS + report, VIN encryption, Android backend URL

- Backend sessions API (`backend/mmc/api/routes.py`, registry in
  `backend/mmc/sessions.py`): `POST /sessions`, `POST .../events`,
  `GET .../state`, `POST .../tests/{ref}/result`, `POST .../blobs`
  (multipart, written through `store/blobs.py` — the route AGENTS.md already
  listed), `POST .../report`, `GET /reports/{id}.json`, `WS .../ws` pushing
  full state after every appended event. Derived state (posteriors, rankings,
  traces) is recomputed from the event log on every read, never stored.
  Safety acks ride the existing append-only events route (`safety_ack`), so no
  extra route was needed; the result route also enforces the gate (409 until
  acked) and refuses already-performed tests.
- VIN: full VIN stored only as `enc:v1:...` (XOR-SHA256 stream + HMAC tag,
  stdlib-only, in `backend/mmc/vin.py`), indexed by sha256 in
  `DATA_DIR/vin_index.json`, last 6 shown/logged. Key from new `MMC_VIN_KEY`
  env (added to `.env.example`, empty); when unset a dev key is generated once
  into `DATA_DIR/vin.key` (0600). The stream cipher is dev-grade — replace
  with AES-GCM (`cryptography`) before production. Schema `vehicle.json`
  `vin_last4` became `vin_last6` per the approved correction.
- New runtime deps: `python-multipart` (blob upload), `jsonschema` +
  `referencing` moved from dev to runtime (payload/report validation);
  `wsproto` added to dev (TestClient websockets). Installed with
  `UV_CACHE_DIR=/tmp/uv-cache uv pip install` because `~/.cache/uv` is
  read-only in this sandbox.
- Android: `SessionClient.kt` (OkHttp — new dep `com.squareup.okhttp3:okhttp:4.12.0`;
  no coroutines, blocking calls on background threads) with HTTP routes, blob
  upload, and a state WebSocket; minimal Compose gate flow
  (Settings → Transport → Live → Next-test → Record → Report). Debug backend
  URL defaults from gradle property `MMC_BACKEND_URL` (Tailscale address of
  the dev host; placeholder `http://100.64.0.1:8000` — the owner sets the real
  IP once via `tailscale ip -4`, or `-PMMC_BACKEND_URL=...`, or on-device in
  Settings, which persists). Sim stays selectable and is the default, so the
  full flow runs with no car. `Recorder` is wrapped at the transport layer so
  the .mmrec transcript is the exact exchange.
- `docs/SAFETY.md` created for the solo owner: Park/Neutral + parking brake,
  outdoors only, phone mounted and logging before any rpm hold, never touch
  the phone while holding rpm, abort list, plus the M2 bundle acceptance and
  the `adb pull` path into `backend/tests/fixtures/sessions/`.

## 2026-10-28 — Android builds run on the build machine, not in GitHub CI

GitHub CI keeps the backend (pytest + replay check) and web build jobs. The Android job is dropped: installing the
SDK on GitHub runners broke, and every Android build and test, and the release APK, runs on dreamz-code anyway.
`cd android && ./gradlew testDebugUnitTest assembleDebug` stays a required local check before each commit.

## 2026-10-28 — M3 reasoning engine decisions

- Engine layout is `backend/mmc/engine/` (`physics`, `bayes`, `evaluator`,
  `planner`, `stoprule`, `trace`) plus `backend/mmc/kp/` (`loader`,
  `packs/ford_gen_v1.yaml`, `packs/generic_gas_v1.yaml`). Only `engine/`
  changes posteriors; the LLM never sets probabilities (AGENTS.md rule 4).
- New runtime dependency `pyyaml` (KP packs are YAML); `hypothesis` was
  already in the dev group and is used for engine property tests.
- Likelihood sanity is a loader hard gate: any cell outside [0.02, 0.95]
  raises, so no single test is a silver bullet; rows must sum to 1 ± 1e-9.
  Hypotheses missing from a test's table get a uniform-over-outcomes row.
- Scenario tests never script test order: each loop step runs the planner's
  top-ranked runnable test with the outcome from `sim_scenarios.py`. Both
  profiles (L1/OBD-only, L3/full tools) converge to the true hypothesis with
  p ≥ 0.8 within ≤ 4 tests on all 7 scenarios — no xfail was needed. Tuning
  went into the pack (stronger "normal" contrasts for healthy vs
  charging/O2/other), never into planner overrides.
- Sim parity: `SimSnapshotTest.kt` writes warm-idle/2500-rpm + ECT-series
  snapshots to `backend/tests/fixtures/sim_snapshots/`; `test_sim_parity.py`
  requires `sim_scenarios.py` to reproduce them within 1e-6.
- Stop rule implements SPEC §5 as coded (P(top) ≥ 0.85 and best EIG <
  0.05 bits); scenario tests assert the looser M3 bar (p ≥ 0.8) per plan.
- Also fixed: `.agents/plans/M3.md` Summary repeated the §6 verification
  commands; §6 is now the single canonical copy.

## 2026-10-28 — M2 implementation decisions

- `Transport.transact` is blocking `(line, timeoutMs) -> String`, not the SPEC's
  `send(line) -> Flow<bytes>`: ELM327 is ASCII line-oriented, and the blocking form
  keeps the driver free of a coroutines dependency and unit-testable on the JVM.
  Timeouts are driver-chosen constants (ATZ 1500 ms / first 0100 2000 ms / steady
  300 ms) passed per call and asserted in `Elm327DriverTest`.
- `minSdk 26 -> 29` per AGENTS.md (the M0 value was a mistake); package is
  `dev.dreamz.mmc` everywhere and AGENTS.md repo-layout paths now say so.
- The never-send rule ("AT TP A0", use "ATSP0") is enforced on the command strings
  the driver actually emits (`Allowlist.NEVER_SEND_COMMANDS` + `Elm327DriverTest`
  scans `commandsSent`), not by grepping source: the literal legitimately appears
  in `Allowlist.kt` as a blocked entry.
- Mode 22 gate is a strict conjunction (ALLOW_MODE22 flag AND profile.mode22 AND
  DID in `config/mode22_allowlist.yaml`); `ClearToken` is a driver-level single-use
  type, backend request/confirm flow deferred to M4/M6.
- `.mmrec` = zip of transcript.jsonl (ms-offset tx/rx rows), events.jsonl, meta.json
  plus manifest.json with sha256 of each member. `Recorder.kt` writes it,
  `backend/mmc/replay/mmrec.py` loads/verifies it (`verify()` wraps even raw
  zip/zlib corruption into `MmrecError`), and `scripts/replay_check.sh` really
  verifies bundles through it. `sim-healthy.mmrec` was generated by a Python
  one-off (`/tmp/gen_mmrec.py`, not committed) with transcript values matching the
  golden fixtures.
- Golden transcripts are canonical in `backend/tests/fixtures/transcripts/` and
  mirrored to `android/app/src/test/resources/transcripts/` for JVM tests.
- Fingerprint matrix (ATPPS / ATCRA 7E8 / echo-tolerance `22F190` + A6 protocol map):
  genuine+obdlink get mode22=true; good_clone keeps multi-PID without mode22;
  limited_clone loses headers and multi-PID. Kotlin `FingerprintTest` writes the
  four emitted profiles to `backend/tests/fixtures/adapter_profiles/` and
  `test_adapter_profiles.py` validates each against `adapter_profile.json`.
- Sim warm-up: first-order ECT to 95 °C (tau 300 s), 70 °C plateau for stuck-open
  thermostat; vacuum-leak trim scales as 18%*(750/rpm)^1.2 (idle +18%, 2500 rpm
  ~+4%); fuel-fuse-removed yields NO DATA (no start) rather than zeros.
- No new dependencies (JUnit 4 + stdlib + `java.util.zip` on Android; stdlib-only
  `mmrec.py`). Real-vehicle recording stays an owner Day 2 task (PLAN.md #7).
- Observed 2026-10-28: `test_chain_properties.py::test_single_byte_mutation_fails_verification`
  (M1, hypothesis-generated input, `--hypothesis-seed=2`) failed deterministically, not a
  flake: a single-byte mutation (e.g. the last digit of a float like 6.3373105101272424e+16)
  can leave the parsed event semantically identical, so recomputed hashes still passed.
  Fixed the same day: `verify_chain()` now also requires each stored line's raw bytes to
  equal the canonical serialization of the parsed event (the exact bytes `append()` writes).
  Regression test `test_float_last_digit_mutation_detected` covers it.

## 2026-10-28 — M0/M1 implementation decisions

- Canonical JSON is `sort_keys, separators=(",", ":"), ensure_ascii=True`, UTF-8 encoded. ASCII-safe
  form stays encodable for every JSON string value (property test found lone surrogates break
  `ensure_ascii=False`) and is byte-identical across implementations. Hash rule otherwise per plan:
  `payload_sha256 = sha256(canonical(payload))`,
  `hash = sha256(canonical({seq, ts, type, payload_sha256, prev_hash}))`, genesis prev 64 zeros.
- SQLite index is non-durable (`journal_mode=MEMORY`, `synchronous=OFF`) with in-memory next-seq/head.
  The JSONL file is the source of truth and the index rebuilds on open. Per-append `COUNT(*)` made
  10k appends quadratic (64.9 s); now < 1 s. `verify_chain()` returns bool, False on any field tamper,
  deleted/reordered/inserted lines, or unreadable/non-UTF-8 bytes.
- Android pins: Gradle 8.10.2, AGP 8.5.2, Kotlin 2.0.21, compileSdk/targetSdk 35,
  buildToolsVersion 35.0.0 (AGP 8.5 defaults to 34.0.0, not installed), minSdk 26, package
  `dev.dreamz.mmc`. Wrapper jar generated from the cached 8.10.2 distribution; properties use the
  standard https URL.
- `session.json` references `vehicle.json` via `$ref`; tests resolve local refs with a
  `referencing` registry. `backend/uv.lock` is gitignored (deps float in CI).

## 2026-10-28 — Full scope restored (supersedes the cut below)

The owner chose to make up the two-day slip with three all-nighters instead of cutting. Every feature in AGENTS.md
ships. The former cuts are now only a fallback order if a day's exit criteria are missed. See `docs/PLAN.md`.

## 2026-10-28 — Cut Mode 22, BLE and Wi-Fi transports up front (superseded)

Setup slipped two days. Classic RFCOMM covers the demo adapters and Fords; the Sim and Replay transports cover the
judges. Muse Wonder review, SAM video tracking and PDF upload are stretch goals. See `docs/PLAN.md`.

## 2026-10-26 — Native Android + web replay viewer + Python backend

Android is the only platform where cheap Bluetooth Classic ELM327 adapters work; judges will have no adapter or
car, so a deployed web Replay & Report viewer is the judge path. iOS waits for a Kotlin Multiplatform core after
the hackathon. Full reasoning: `docs/research/2026-10-26-platform-verdict-and-plan.md` §1.

## 2026-10-26 — The LLM proposes, deterministic code decides

Probabilities change only in `backend/mmc/engine/`. Muse Spark turns messy input into typed Observations and
writes explanations, but it never sets a posterior. This makes every conclusion auditable and replay deterministic.
