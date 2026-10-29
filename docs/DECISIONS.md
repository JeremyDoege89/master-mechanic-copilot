# Decisions

Newest first. Each entry: date, decision, why. Add one whenever scope, a dependency or an architecture choice changes.

## 2026-10-29 — SAM video cost fix + per-call reasoning effort

- Cost bug: SAM video usage reports `video_frames_processed: 75` (no
  tokens). The ledger logged $0.010000 (the 50-frame pre-call estimate)
  instead of $0.015 (75 frames at $0.20/1k). Fixed
  `budget.actual_cost_from_usage` to bill `video_frames_processed` via
  `estimate_sam_video` — the 50-frame minimum is `max(50, frames)`, a
  floor, never a cap. Covered by
  `test_sam_video_actual_cost_uses_frames_with_minimum` (75 -> $0.015,
  3 -> $0.01 floor).
- Model quality (credits plentiful): the Responses API accepts
  `reasoning={"effort": ...}` (`low|medium|high|xhigh`) on
  `muse-spark-1.3` and `muse-wonder-1.0`; no call set it until now.
  Per-call-kind effort from env (in `.env.example`):
  `EFFORT_SPARK_LIVE=medium` (live keyframe "what am I looking at",
  keyframe->labels target < 3 s), `EFFORT_SPARK_EXTRACT=high` (PDF spec
  tables, video events, instrument reading), `EFFORT_SPARK_AGENT=high`
  (orchestration / explanations), `EFFORT_WONDER=xhigh` (report review,
  one call per report). `spark.reasoning_effort(kind)` reads env with
  those defaults (unknown values fall back); every Spark call puts the
  effort both in the cached/logged `request` record (`reasoning.effort`,
  so the response-cache hash stays deterministic per effort) and in the
  live `reasoning` kwarg. Covered by `backend/tests/test_meta_effort.py`
  (defaults, override, request/cache determinism).

## 2026-10-29 — M5U reconciled with the live run (LIVE, not provisional)

- Owner run `scripts/spike_meta_upload.py` 2026-10-29 (outside the sandbox,
  key from `~/.config/meta/model-api.env`): every call succeeded. Files API
  upload (`purpose="user_data"`, OpenAI SDK Files API on the same
  `base_url`/key, id cached under `DATA_DIR/llm_cache/files/<sha256>.json`)
  -> Spark spec extraction: 3 ranges (`fuel_pressure_kpa` 280-420 kPa,
  `charging_voltage_v` 13.5-14.8 V, `ect_warm_plateau_degc` 90-98 degC).
  Spark video events on the Files-API MP4: 1 event (`[0.0-2.36s]` "hood open
  showing engine bay", `sam_concept` "hood"). SAM video worked with BOTH the
  inline `input_file` part (`file_data: data:video/mp4;base64,...` +
  `filename`) and the Files API `file_id` part (`{"file_id": ...}`),
  75 masks each on the spike clip.
- Verified request shapes (all one user message wrapping the parts):
  Spark PDF/video `{"role": "user", "content": [{"type": "input_text", ...},
  {"type": "input_file", "file_id": ...}]}`; SAM video `input_text` (object
  prompt alone when boxes are given) + `input_file` either inline
  `file_data` or `file_id`. SAM video usage carries no tokens, only
  `video_frames_processed: 75` (cost $0.015 at $0.20/1k frames with the
  50-frame floor — see the cost fix entry above); Spark usage carries
  `input/output_tokens` plus `reasoning_tokens` (billed as output, already
  in the ledger math).
- `sam.track_video` defaults to `video_source="inline"` (no extra upload
  round-trip); `"file_id"` stays as a one-keyword option for clips already
  uploaded via the Files API. `upload/video.py analyze_clip` uses the
  default (inline sub-clips), so no call-site change was needed.
- Fixtures flipped to LIVE (not provisional):
  `backend/tests/fixtures/upload/spark_pdf_spec_01.json` /
  `spark_video_events_01.json` (`call_record` dicts, `"provisional": false`
  + `"captured": "spike_meta_upload.py owner run"`) and
  `sam_video_upload_01.jsonl` (live 83-event stream, 75 tracked masks).
  Tests replay them (`test_upload_fixtures.py` asserts the 3 ranges / 1
  event / 75 masks + `video_frames_processed == 75`; `test_upload_video.py`
  replays the live stream for both `video_source` shapes and asserts the
  `inline` default). `SOURCE.txt` provenance updated.

## 2026-10-28 — M5U upload mode implemented (spike pending)

- Upload mode per `.agents/plans/M5U.md` §§1–5: new schemas
  `spec_range.json` + `video_events.json` (15 schemas total);
  `meta/files.py` Files API upload (sha256 local cache, replay-miss raises);
  `meta/spark.py` `extract_spec_ranges` / `summarize_video` (prompt-shaped
  JSON + schema validation + probability scan; the LLM never supplies
  `extractor`/`source_blob_sha256` — `upload/specs.py` attaches them);
  `upload/{specs,csv_logs,video}.py`; `api/routes_uploads.py` (blobs first,
  processing second; fixture-analyzer seam; replay sessions read-only for
  processing routes); session `mode="upload"`; `evaluator` applies manual
  `min/max/units` onto `expected`; result route takes optional `spec_id`
  (unknown → 400, nothing appended); Android `uploadFile`/`extractPdf`/
  `parseCsv`/`analyzeVideo` + Upload screen via system picker (no new
  permissions, no manifest changes). No new dependencies: stdlib PDF
  writer + CSV, system ffmpeg/ffprobe only.
- SAM video was NOT verified live (only the image path was):
  `sam.track_video` keeps a one-keyword `video_source` switch
  (`"inline"` default sends base64 `file_data`; `"file_id"` sends a Files
  API id). `scripts/spike_meta_upload.py` tries inline first, then
  file_id, prints each call's outcome (keeps going after failures), and
  records which the API accepts plus the exact error — flip the default
  to the winner after the owner run.
- CSV moves posteriors only via derived `test_result`s through the normal
  fold (already-performed / not-runnable → reported skips); PDF/video
  uploads append evidence + observations only. Golden CSVs are hand-written
  from the M3 sim numbers (vacuum leak → `lean_idle_normal_cruise`).
- Verified 2026-10-28: backend 226 passed + 1 skipped (pre-existing skip);
  38 upload tests; Android 70 unit tests incl. new `SessionClientTest`;
  `replay_check.sh` OK. Owner spike (`scripts/spike_meta_upload.py`,
  outside sandbox) still pending — provisional fixtures stay until it flips
  `provisional`/`captured`.

## 2026-10-28 — M5U upload fixtures PROVISIONAL (spike pending)

- `backend/tests/fixtures/upload/spark_pdf_spec_01.json`,
  `spark_video_events_01.json` (`call_record` dicts) and
  `sam_video_upload_01.jsonl` (raw stream events) are PROVISIONAL:
  `"provisional": true` + `"captured": null` until the owner runs
  `scripts/spike_meta_upload.py` outside the sandbox, which replaces them
  with live outputs and flips both. Code and tests pass on the
  provisional shape and are re-checked after capture.
- SAM video was NOT verified live (only the image path was): the spike
  tries the inline `input_file` part first, then a Files API `file_id`
  part, records which the API accepts (and the exact error for the
  other), and `sam.track_video` keeps a one-keyword `video_source`
  switch (`"inline"` default) so the winner is one keyword flip.

## 2026-10-28 — M5 reconciled with the live SAM API + Android builds offline

1. Real SAM 3.1 shape (two captured streams in
   `backend/tests/fixtures/vision/sam_stream_*.jsonl`, real CC0 photo
   `backend/tests/fixtures/images/engine_bay_02.jpg`, corrected by a live
   run 2026-10-28 — concept "battery" on `engine_bay_02.jpg`): Responses
   API with `stream=True` and `input` as ONE user message wrapping the
   parts, `[{"role": "user", "content": [{"type": "input_text",
   "text": "<concept>"}, {"type": "input_image",
   "image_url": "data:<mime>;base64,..."}]}]`. A bare
   `[input_text, input_image]` list is REJECTED with
   `400 input[0] did not match any supported type` (the earlier note
   claiming no wrapper was wrong). Video wraps the same way with an
   `input_file` part for the clip. SAM does NOT return JSON — it
   streams `output_text` grammar `<0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;x=0;y=0;data=<w>,<h>,<payload>|>`
   (inclusive pixel box edges; one line per frame: `<Nf>`). Usage carries
   no tokens, only `images_processed=1` per image call ($0.0025 at
   $2.50/1k); `budget.actual_cost_from_usage` now reads that key for SAM
   models and the client falls back to the pre-call estimate only when the
   stream carries no counts.
2. `backend/mmc/meta/sam.py` rewritten around the official
   `meta-sam-parser 0.1.0` (new runtime dep, already in pyproject):
   `parse_responses_stream(...)` with `image_segmentation_format()` /
   `video_segmentation_format()` over the raw event list (driven via
   `asyncio` from sync code), `decode_mask_to_rle` /
   `decode_mask_to_svg_path` for overlay masks, `build_object_prompt` for
   box/point prompts (pixel-space, inclusive edges in, half-open out).
   New `MetaClient.call_stream` accumulates text (deltas, else done-text)
   + terminal usage, caches the full event list for $0 replays, and keeps
   the 429/budget/key-free-record semantics of `call`. The provisional
   `sam_mask_01.json` fixture is deleted; tests replay the captured
   streams. Hand-rolled `parse_sam_response`/`normalize_box`/`SamParseError`
   are gone.
3. Empty completed stream is a NORMAL "not found", never an error
   (`SamResult.found == False`). Live evidence: "battery" found 3 objects
   while "air filter housing" found nothing on the same photo. Spark's
   prompt now demands a 1-2-word common `sam_concept` ("battery",
   "air filter", "radiator cap") plus an optional even-shorter
   `sam_fallback` (new optional schema field, old fixtures still valid);
   `segment_with_fallback` retries ONCE with the fallback, and the
   keyframe route uses it (overlay records now carry `used_concept` +
   `found` + SVG-path masks). `instrument_01.json` stays PROVISIONAL until
   owner meter photos land.
4. `engine_bay_02.jpg` is now the default in the spike
   (`scripts/spike_meta_vision.py`: Spark -> concept+fallback SAM ->
   box-prompt SAM from the first found box, records saved per attempt),
   the live test (`test_vision_live.py`: direct "battery" assert plus the
   Spark-concept fallback path) and eval (`eval_m5.py --offline` replays
   both captured streams, $0). The synthetic `engine_bay_01.jpg` stays
   only where a synthetic image is what a test needs.
5. Android: CameraX stays pinned to 1.4.2 (1.5+ needs AGP 8.9 +
   compileSdk 36). Builds run offline with `GRADLE_USER_HOME=/tmp/gradle-home`
   (seeded copy of `~/.gradle`: wrapper + 8.10.2 + modules-2 +
   transforms-4 + build-cache; `~/.gradle` itself is not writable in the
   sandbox). Two compile fixes: `Gray` is now `internal` (it was
   `private` while `internal downsampleGray` returned it) and
   `extension.lower()` -> `extension.lowercase()` (removed in Kotlin 2.0).
   `./gradlew testDebugUnitTest assembleDebug --offline`: BUILD SUCCESSFUL,
   68 unit tests, 0 failures, fresh `app-debug.apk`.
6. Second live correction the same day: concept-only calls work, but a
   box/point call sending `"<concept> <object-prompt>"` fails with
   `400 invalid prompt: object prompts cannot be combined with a phrase`.
   Verified live: the object-prompt text ALONE as input_text works (e.g.
   `<0f>0<|box;x1=963;y1=690;x2=1130;y2=830;w=1280;h=960|>` returns a box
   + mask). `segment_image` and `track_video` now send only the
   object-prompt text when a box/point prompt is given; the concept(s)
   stay in the request record and label the `SamResult` for the overlay.
   Covered by `test_segment_image_object_prompt_sends_no_phrase`
   (box + point), `test_segment_image_concept_only_sends_just_phrase`
   and `test_track_video_box_prompts_send_only_object_prompt`.

## 2026-10-28 — M5 Vision (spike-first; live spike NOT run — provisional fixtures)

1. Live spike (`scripts/spike_meta_vision.py`: Spark scene JSON + SAM concept
   mask + SAM box-prompt mask through `backend/mmc/meta/`, key-free records
   to `backend/tests/fixtures/vision/`, spend from returned `usage`) ran
   PARTIALLY from the sandbox on a borrowed SDK: one Spark call on the
   synthetic engine-bay photo succeeded 2026-10-28 ~19:32 UTC and its record
   is now `spark_components_01.json` (LIVE, not provisional). Verified Spark
   shapes: `response.output_text` carries the JSON directly; `usage` carries
   `input_tokens_details.cached_tokens` (0) and
   `output_tokens_details.reasoning_tokens` (1428 of 1499 output tokens —
   billed as output, already covered by the ledger math); the model returned
   2 components (air filter housing 0.95, coolant reservoir 0.93), no
   anomalies, passing the schema + no-probability gates unchanged. Spend for
   that call: $0.00779825 from returned usage. The follow-up SAM call hung
   >30 min on a socket blackhole, so `sam_mask_01.json` and
   `instrument_01.json` stay hand-shaped PROVISIONAL; owner re-runs the spike
   outside the sandbox for those and reconciles `mmc/meta/sam.py` (box/point
   grammar, masks/boxes/detections shapes) then. Lesson applied the same day:
   `MetaClient` takes `timeout_s` (default 120 s) into the OpenAI client
   instead of SDK-default timeouts; unit-tested with a stubbed SDK module.
2. New dependency `openai>=1.0` (pyproject): required by AGENTS.md "Use the
   OpenAI Python SDK". The SDK is imported lazily in `meta/client.py`, so the
   backend and all tests stay importable/runnable offline; only a real
   network call needs it. No other new dependencies (vision math + keyframes
   are stdlib-only; Android CameraX below).
3. Structured output = prompt-constrained JSON + server-side JSON Schema
   validation (`vision_components` / `vision_instrument`, new in
   `packages/schemas/` + examples), not Responses `text.format`, so the code
   does not depend on unverified SDK-version behaviour; validation happens
   before use per AGENTS.md hard constraint 4.
4. `MetaClient` (one Responses shell): env-pinned Standard IDs defaulting to
   muse-spark-1.3 / sam-3.1 / muse-wonder-1.0 / muse-voice-transcribe-1.0,
   contributor IDs refused; 429 retry (exp backoff+jitter, Retry-After);
   budget pre-check + ledger from returned `usage` (SAM per-image/frame falls
   back to the estimate since its usage carries no tokens); disk cache by
   sha256(canonical request), REPLAY_USE_CACHED_LLM=true replays for $0 and
   raises on a miss. `call_record()` strips headers/keys — unit-tested, and
   the spike refuses to write fixtures containing credential markers.
5. SAM grammar assumptions to verify in the spike: one concept per request
   (comma lists rejected client-side), box/point prompts in a 0-1000 space,
   `parse_sam_response` accepts masks/boxes/detections shapes and raises
   `SamParseError` naming seen keys otherwise; video via Files API
   `purpose="user_data"`, MP4/MOV only (extension + ftyp brand check,
   transcode stays on-device), max 3 tracked objects.
6. Vision routes: keyframe appends evidence_added + observation_added (engine
   posteriors provably untouched — compute_state folds only test_result);
   `components.py` rejects any probability-named key anywhere in LLM output;
   instrument value becomes Evidence ONLY via the confirm route (retake when
   confidence < 0.8, confirm refuses retake readings).
7. Android: CameraX 1.6.2 (core/camera2/lifecycle/video/view — present in the
   local artifact cache) for CameraScreen (15 fps ImageAnalysis,
   Laplacian-variance + motion gate, <=1 keyframe/3 s + tap, auto torch,
   ~1024 px upload) and ClipRecorder (5-10 s MP4 H.264 for SAM); pure
   `KeyframeSelector` mirrors `vision/keyframes.py` exactly and is unit-tested
   on both sides. Sandbox could NOT run gradle (no network; camera-video
   transitive binaries absent machine-wide): KeyframeSelector.kt +
   KeyframeSelectorTest.kt were compiled with the cached Kotlin 2.0.21
   compiler and all 10 tests pass; full `./gradlew testDebugUnitTest
   assembleDebug` must run with network (owner). CameraScreen/ClipRecorder
   compile-review only.
8. `scripts/eval_m5.py`: live mode needs owner photos + labels (INCOMPLETE
   until they land); `--offline` scores recorded fixtures through the real
   parse gates ($0). `backend/tests/fixtures/m5_eval/` is gitignored except
   its README.

## 2026-10-28 — M4 phone-test fixes (protocol framing, auto-measure, evidence gate, scenario picker, session restore)

1. Protocol label (`Parsers.cleanAtResponse`, used by `Fingerprint` for ATDPN
   and ATI): the raw `A6\r\r>` reply kept its CR framing and `>` prompt, so
   the codebook lookup missed and the raw string reached the UI label and
   would have reached stored profiles. Stripping lives in the
   parser/fingerprint layer, not the UI. The checked-in
   `adapter_profiles/*.json` fixtures were already clean (emitted from
   unframed replies; the emitting test rewrote byte-identical files), so no
   regeneration was needed. Golden transcript
   `atdpn-framed.txt` + `ParsersTest.atdpnFramingStripsCrAndPrompt` and
   `FingerprintTest.framedAtdpnAndBannerDoNotLeakRawFraming` cover it.
2. Test-run flow: OBD-kind tests (`obd_read`/`obd_calc`/`obd_procedure`,
   from the new `kind` field on state `next_tests` entries, with a hardcoded
   fallback set) are measured live over the adapter — DTC/MAF+IAT/trims/ECT
   samples/Mode 06 (new `Elm327Driver.queryMode06` with ISO-TP reassembly,
   still allowlist-gated)/O2 samples — and submitted directly; the trim
   split test captures idle, prompts "Hold 2,500 rpm now" with a 5 s
   countdown, then captures cruise. Manual kinds (visual/sensory/measurement)
   get an outcome radio form built from the test's `expected_outcomes`. Raw
   JSON left the normal flow and sits behind a "Debug JSON" toggle.
3. Evidence gate: `build_report` checks the stop rule first; before it fires
   the conclusion reads "Not enough evidence yet — … Current suspects: …"
   at all three summary levels instead of "Most likely: …". The Live/Test/
   Report screens mirror it client-side (`hasConcluded`, same 0.85/0.05
   thresholds) and label the ranked list as current suspects. Covered by
   `backend/tests/test_report_evidence.py`.
4. Transport screen has a simulator scenario picker (all 7 `Scenario`
   values, default HEALTHY) wired into the `Elm327Sim` the Sim transport
   connects with, so each planted fault is demoable on-device.
5. Session restore: the controller lives in `MmcViewModel`; `onPause`
   persists a Context-free `MmcSessionSnapshot` into the `SavedStateHandle`
   and a fresh process reapplies it and calls `resumeSession` (GET state +
   reopen WS, rebuild the Sim driver) instead of creating a new session.
   Restore logic is unit-tested (`MmcViewModelTest`, no Context needed).
   No new Gradle dependencies: `ViewModel`/`SavedStateHandle`/`by viewModels()`
   resolve from the existing transitive graph. Sandbox note: `~/.gradle` is
   read-only here, so verification used `GRADLE_USER_HOME=/tmp/gh` (seeded
   copy of the dist + caches) with `--offline`; the committed `build.gradle`
   is unchanged.

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
