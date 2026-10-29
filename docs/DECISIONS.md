# Decisions

Newest first. Each entry: date, decision, why. Add one whenever scope, a dependency or an architecture choice changes.

## 2026-10-29 — Real-vehicle sessions become judge demos with one command (no hand editing)

- `scripts/export_session_bundle.py <session_id> [--label ...] [--out NAME.mmrec]` exports a finished live/upload session to a judge `.mmrec`: hash-chained `events.jsonl` (chain verified first), the phone-uploaded adapter transcript (first owned `.mmrec` blob's `transcript.jsonl`, else empty), every `evidence_added`-referenced blob, the Wonder-reviewed report file(s) as `reports/<id>.json`, and every `llm_call`/`sam_call` cached response as `llm_cache/...` (Meta `llm_cache/<sha>.json`, Baseten `llm_cache/baseten/<sha>.json`, Files-API ids for referenced blobs when present). `meta.json` carries `label "real vehicle, real AI outputs"` + `origin real` and the owner `--label` as `display_label`; vehicle is redacted to `vin_last6` only (full VIN never leaves `mmc/vin.py`; events keep their encrypted token so the chain verifies). Refuses, naming the item, on unknown session, non-live/upload mode, missing Wonder-reviewed report, or any missing blob/report/cache. Fixed zip timestamps keep re-exports deterministic.
- `mmc/replay/mmrec.py` gains `REAL_LABEL`/`SIM_LABEL`, `bundle_origin`/`bundle_display_label`, and `cache_members`/`report_members` (bundle extras now `blobs/` + `reports/` + `llm_cache/`); `runner.replay_bundle_bytes` seeds `llm_cache/...` members before folding so real-bundle replay and report rebuilds stay $0, and `runner.list_fixtures()` merges seeded + repo fixtures with real-vehicle first. New `GET /fixtures` serves that order (public-safe: names + labels only); `modal_app.seed_fixtures` already recopies new/changed bundles so drops need no code change.
- Web judge landing (`Replay.tsx`) fetches `/fixtures`, lists every bundle real-first with its label (real `Real vehicle · real AI outputs` vs sim `Simulated · real AI outputs` badges), and the primary **Play the investigation** button plays the first listed bundle — a real one when it exists — with the status chip following the played origin. Fallback to the two hard-coded sim demos when the endpoint is unreachable.
- `scripts/import_phone_recording.sh` adb-pulls the phone cache dir (`run-as dev.dreamz.mmc`, Recorder.kt `cacheDir/session.mmrec` output path) into `data/phone_recordings/`, runs `mmrec verify` + `replay_check.sh` per file, and prints the matching `export_session_bundle.py <session_id>` command (session id from the recording's `meta.json`).
- `docs/JUDGES.md` fixed to the current site (no candidate claimed as the match; photo bytes are bundled and shown with candidate overlays) plus an owner-facing "Turning a real session into a judge demo" section. Regression net: `backend/tests/test_export_session_bundle.py` (round-trip identical state/report, missing-blob refusal, missing-cache refusal, VIN truncation, fixture ordering incl. `/fixtures`).
- No dependencies added. No secrets printed (export/import log ids + hashes only).

## 2026-10-29 — M7 review fixes #1–#14 (voice end-to-end, honest gates, server-side ownership)

- Voice path now works end to end. #1: `AsrProxy.handle_final` awaits an awaitable append, so the route's async append+broadcast actually persists (pinned by `test_handle_final_awaits_async_append`). #11: `run()` races the client receive against pump completion (`FIRST_COMPLETED`), so a normal PUSH_TO_TALK ending no longer hangs with the socket held — while an already-queued stop/PCM frame is still forwarded first (`test_run_returns_after_upstream_finishes_with_client_holding_socket`, `test_proxy_maps_stop_to_endstream`). #3: the proxy measures forwarded PCM bytes itself and ledgers `max(reported, measured)` per turn, so a final without `audioProcessedMs` can never book $0; textless finals (tone `final ""`, empty `speechComplete`) are billed but never appended or forwarded (`test_final_without_reported_duration_bills_forwarded_audio`, `test_empty_final_appends_nothing_and_forwards_nothing`). #12: `check_asr_model_id()` enforces the Meta/contributor guard (`muse-voice-` prefix, contributor refusal) at proxy/factory/config time, matching `MetaClient`.
- Android voice is real. `VoiceTalkSession` streams 24 kHz mic PCM to `/sessions/{id}/asr` (permission only on first tap, start/stop frames, live partial captions, final closes the turn) into `VoiceUiState`; spoken "next test" opens the rank-1 card. Confirm builds the exact payload via the pure `VoiceConfirm` helper (trim needs both staged halves) and POSTs the real test-result route: success appears only on HTTP 200 with the real outcome, errors report the real message, and the card is kept on failure — never a claimed success (`VoiceConfirmTest`, `RealtimeAsrClientTest.captionTextExtractsTranscriptThenText`).
- #5: 2500/rpm tokens are the condition, never the value — trim drops them before extraction (no raw fallback) and voltage prefers the number left of the unit. Both parsers share `backend/tests/fixtures/voice_corpus.json` (15 cases incl. both review utterances), mirrored to Android test resources with a byte-equality backend test and a dependency-free `VoiceCorpusTest` reader; the Kotlin mirror also gained sixty–ninety and negative/and handling.
- Fixtures are live: the owner's real-speech spike turn ("one," → "one, three," → "one, three, five." + final, close 1000) was flipped to `provisional:false`, verified key-free, pinned (`test_asr_fixtures_are_live`) and replayed verbatim through the proxy (`test_proxy_replays_live_spike_fixtures`).
- Reports and blobs. #6: `POST /report` holds `session_lock` across check → Wonder call → append (lock held during the call is pinned by `test_report_build_holds_session_lock_across_wonder_call`; single-loop builds already serialise, the lock documents and enforces the critical section). #7: the recorded-review fallback serves only a still-current review (`find_recorded_wonder_review_if_current`: no diagnostic event past the recording head), else ships null. #8: blob downloads require server-side ownership (`sessions/<id>/blobs.json`, recorded at upload/clear-codes time, restored on replay) AND an own-log evidence reference — forged cross-session evidence gets 404. #9: `SessionRegistry.get` holds a construction lock so concurrent first touches share one `EventStore`. #10: both upload routes stream through `read_upload_capped` (early Content-Length reject + 1 MiB chunks; per-route limit stays patchable).
- Gates that tell the truth. #4: `scripts/acceptance_sweep.sh` decides every row by exit code via `scripts/sweep_lib.sh` (`run_row`), never by grepping for "passed"/"check OK" — proven by `test_acceptance_sweep.py`, which feeds the sweep's own helper a failing `1 failed, 19 passed` stub and asserts FAIL. #13: the release script requires both signing passwords even for `--check`, made `apksigner verify` mandatory and fatal, and scans staged+unstaged added lines plus untracked signing/PEM files (pattern split so the guard never matches itself).
- #14: demo transcripts emit the packed bytes for the real code (`P0303` → `0303`, decodes back through `Parsers.decodeDtcs`), pinned by `test_synth_transcript_dtc_cells_decode_to_snapshot_dtcs`; both bundles regenerated offline at $0 with no demo-cache rekey needed (live outputs untouched) and `--check` green.
- Verification: backend 476 passed + 1 skipped in ~145 s (< 3 min), `ci_parity.sh` green (incl. no-ffmpeg proof), `replay_check` green, Android 117 unit tests green + `assembleDebug`, web build green, acceptance sweep green. No dependencies added. Assumption noted: voice voltage Confirm submits `engine_on: true` (shown on the pending card) since voice logging happens with the engine available; the backend re-validates the payload on the route.

## 2026-10-29 — Voice ASR realtime wired to the documented schema (24 kHz, key-in-handshake, websockets dep)

- The owner's live `scripts/spike_voice_asr.py` run showed the guessed handshake (`type: session_config` + `sample_rate_hz`/`encoding`/`channels`) rejected with `invalid_request_error` (`audioEncoding` required) followed by close 1008. The real protocol (`docs/research/meta-voice/`) is now implemented on both sides: camelCase handshake as the first JSON text frame within 10 s (`authorization.accessToken: "Bearer <key>"` in-frame — the endpoint ignores the HTTP `Authorization` header, so none is sent), `audioEncoding: PCM_24KHZ` (engine-native 16-bit mono 24 kHz), `model`, `mode: PUSH_TO_TALK`, `keywords` vocabulary biasing; raw PCM binary frames paced ~real time (80 ms); `{"type": "endStream"}` half-closes input and the socket is read until close 1000; server events dispatch on `type` (`transcript` + `final`, `audioProgress`, `speech*`, `speaker`, `error`; unknown ignored). `?sessionId=` correlates logs.
- Dependency note (AGENTS.md §8): `websockets>=17` added to `backend/pyproject.toml` — it is the upstream realtime WS *client* (`default_upstream_factory` dials Meta, sends the handshake, validates the sessionId ack). FastAPI/Starlette already serves the client-facing `/sessions/{id}/asr` socket, so nothing else was needed. Owner-verified 17.3 in `backend/.venv`; the factory lazy-imports and raises a naming message when absent, so offline tests (frame-recording fakes) never need it.
- Key hygiene: `session_config()` is key-free (audioEncoding/model/mode/keywords — safe to log and fixture); `build_handshake()` adds `authorization` for the single upstream send; `redacted_handshake()` drops it for the spike fixtures. The key never reaches logs, fixtures, `asr_segment` events, or client frames — pinned by `test_handshake_key_never_leaks` (fake upstream records every frame, sentinel key asserted absent everywhere but the handshake send). Completion in PUSH_TO_TALK is `transcript` with `final: true` (`speechComplete` tolerated); progress/boundary/speaker events stay upstream, partials forward verbatim, finals persist + forward as the existing `{"type": "final"}` client frame. Client `stop` now maps to `endStream`.
- Audio format: `PCM_24KHZ` everywhere — backend `SAMPLE_RATE_HZ` 16000→24000, Android `VoiceAudioSpec` 16→24 kHz (spec comment pins the match with the proxy encoding), spike synth/pacing 80 ms frames. `REPLAY_USE_CACHED_LLM` semantics and the no-fallback voice pin (`test_sam_and_voice_have_no_fallback`) are unchanged.
- Fixtures stay `provisional:true`: the spike now prints every server event type and saves redacted key-free fixtures; the owner reruns it live and a follow-up flips the fixtures to live (same M5U pattern).

## 2026-10-29 — M7 voice, judge fixes, README, release script, sweep (plan `.agents/plans/M7.md` + approved corrections)

- Spike first: `scripts/spike_voice_asr.py` (owner-run outside the sandbox, key from env/`~/.config/meta/model-api.env`, never printed) opens `ASR_REALTIME_URL` with `muse-voice-transcribe-1.0`, streams a synth PCM16 clip, prints the upstream shapes and saves key-free fixtures with `provisional:true`. Until the owner runs it live, `voice.py` builds against `backend/tests/fixtures/asr/` + a fake upstream.
- Upstream WS client: NO new dependency. No `websockets`/`websocket-client` in `.venv` and none added; `default_upstream_factory` raises naming the pending spike choice, tests inject a fake. DECISIONS updated when the spike lands (AGENTS.md §8).
- Voice grammar (`backend/mmc/voice_commands.py`, no LLM): table intents + number words (`twelve point four`, `plus eighteen`), `stage_trim` two-step for `trim_idle_vs_2500`; backend re-parses on confirm (single source of truth, mirrored by `VoiceCommands.kt`). `asr_segment` is audit-only — zero `test_result`/posterior delta until the confirm POST. Proxy (`backend/mmc/meta/voice.py`, `AsrProxy` with injected `upstream_factory`): partials forwarded verbatim (never events), finals append one `asr_segment {text, duration_s, command}` with `estimate_asr` pre-check + actual on the shared Meta tracker, over-budget closes 4402; replay 4409, public 4501, unknown session 4404. `WS /sessions/{id}/asr` beside `/ws`. Android: `voice/RealtimeAsrClient.kt` (URL lifecycle mirrors `openStateSocket`, 16 kHz PCM16 spec) + `VoiceCommands.kt`/`VoiceUiState` (pending-confirm card, hands-free toggle, TTS text selection; on-device TTS, `RECORD_AUDIO` only on first tap, never clear-codes) + minimal `VoicePanel` in `MmcApp.kt`.
- Judge fix 3a (SAM battery): `rank_overlay_masks` (`backend/mmc/report/build.py`) ranks candidates by score — highest is the `match`, rest `other candidate` — surfaced in `sam_overlays` (recorded events untouched), drawn strong vs faint with confidences in `SamOverlay.tsx`, listed with confidences in `report.html.j2` + `Report.tsx`. Judge fix 3b (trace Meaning): `levels.trace_text` L1 now reads direction-then-band (`...makes this more likely; now unlikely (about 2 in 10)`), so web and report agree. The reworded audit drifted the Wonder cache key, so the two `demo_cache/wonder_*.json` records were re-keyed offline (request + `request_sha256` + `audit_sha256` only; recorded outputs untouched) and both `.mmrec` bundles regenerated offline (`--check` green; event diff: only the Wonder `llm_call` request/sha).
- README rewritten per plan (pitch, Mermaid arch, exact model-pin table, Muse Image excluded, local run, Modal URL + `?judge=1`, read-only safety, prior-experience substance, docs index) with `Licence: TBD by owner` and NO `LICENSE` file. `docs/JUDGES.md` frozen (both bundles linked, voice Android-only line, mirrored How-built panel added to the landing hero); `BudgetBadge` shows the `$0 live spend` line in public zero-cap mode.
- Release: `scripts/build_release_apk.sh` (refuses without outside-repo `MMC_KEYSTORE_PATH`/`ALIAS`, `--check` for CI, cert-fingerprint + no-secrets + clean-tree verifies) + `docs/RELEASE.md`; Gradle `signingConfigs.release` reads the same env; version untouched (`0.1.0`); no keystore created. Licence decision stays with the owner.
- `test_fallback.py` updated (not weakened): the `voice.py`-must-not-exist pin predated M7; it now asserts the real contract — `voice.py` exists but wires no `meta.fallback`/`call_text_or_image`/`spark_open`. JVM fix: `VoiceCommands.numberFrom` used the `Double` (`12.0.4`); now `intPart.toInt()`.
- Regression net: `test_voice_commands.py` (9), `test_voice_proxy.py` (7), `test_sam_overlay_display.py` (2), `+1` levels trace test, `VoiceCommandTest` (6) + `RealtimeAsrClientTest` (4), `test_fallback.py +4` lines. Verified: backend 450 passed + 1 skipped in ~145 s (<3 min); Android 108 passed + `assembleDebug`; web `tsc` + vite build; `replay_check.sh` 3/3; `make_demo_bundle.py --check` OK; `acceptance_sweep.sh` all-PASS with owner rows SKIP. Spend $0 (no live calls; owner live checks < $0.50).

## 2026-10-29 — M6 review findings #1–#12 fixed (backend + Android; web/parallel job excluded)

All twelve `.agents/reviews/M6.md` findings fixed in `backend/` + `android/` only (`web/src`, `backend/mmc/report/templates/` untouched — finding #5's web half belongs to the parallel job; the backend pins the upload-mode sequence with `test_f5_public_upload_session_bundle_flow`).

- #1 `POST /events` now allowlists client-postable types (`safety_ack`, `observation_added`, `evidence_added`, `asr_segment`, `test_requested`); engine/privileged types are 403. `scripts/make_demo_bundle.py` appends its `adapter_fingerprinted` via the registry instead (identical bytes), and the `report_built` setup in `test_demo_bundles.py` does the same.
- #2 `post_event`, `post_test_result`, `upload_blob` in `routes.py` 409 on `mode=="replay"`, mirroring the vision/upload guard. One fallout: `test_keyframe_replay_session_is_read_only` now uploads its fixture blob via a writable session (blobs are global).
- #3 `attach_review` maps `ValueError`/`jsonschema.ValidationError` to null + unavailable (never 500s; route guards too); `_scan_banned` lower-cases keys; `wonder_review.json` gains `additionalProperties:false` on review + gap objects.
- #4 `ClearToken` carries the backend token string + session id + expiry; `Allowlist`/`Elm327Driver.query` reject blank/wrong-session/expired tokens; `SessionClient` gains `requestClearCodes`/`confirmClearCodes` (+ pure map builders). Mode 04 stays unreachable without the gated backend flow.
- #6 `runner.verify_event_chain` verifies inner envelope chains on bundle replay (legacy slim rows skip); context restore (`user_level`/tools/vehicle) was already in place and is now pinned by a ≥2-result full-state test.
- #7 report safety notes derive from actual `safety_ack`/`test_performed` log entries (also fixing the `- {"none"}` precedence bug).
- #8 Live screen one-tap ack deleted (points at the test card); offline fallback is an explicit `ackGate` routed by the pure `SafetyGates.ackPath` policy.
- #9 per-session `asyncio.Lock` across check+appends in `post_test_result` and the CSV derived loop (threaded WS-connected test proves one 200 + one 409).
- #10 SRS trigger restricted to `B00xx` + known `B0100` (existing contract); generic `B1234` keeps diagnosing, `safety:srs` flag path unchanged.
- #11 `report_built` carries `log_head_hash`; rebuilds reuse the stored review while no newer diagnostic event exists (`find_current_report_review`), else spend one fresh call.
- #12 `verbal_band` rounds half-up (`floor(p*10+0.5)`, float-epsilon); L1 trace text uses the plain-language `OUTCOME_L1` map with a slug-free fallback.
- Demo bundles regenerated (event diff verified: only the Wonder `llm_call` request/sha from the reworded audit + new `log_head_hash`). The Wonder audit drift required an offline re-key of the two committed `demo_cache/wonder_*.json` request shas (recorded outputs untouched, still schema-valid; scratch script at `/tmp/rekey_wonder_cache.py`).
- Regression net: new `backend/tests/test_m6_review.py` (18 tests), `ClearTokenTest.kt` (5), `+2` ack-path tests, `+2` clear-codes body tests. Backend suite 412 pass in ~2:15; Android `testDebugUnitTest` + `assembleDebug` green offline.

## 2026-10-29 — M6B bundle infrastructure + web pass (bundles owner-built next)

Plan `.agents/plans/M6B.md` implemented in order with the approved
corrections; the two `.mmrec` bundles are NOT committed yet — the owner
runs `scripts/record_demo_cache.py` live (~$0.20 one-time), then
`scripts/make_demo_bundle.py`, then commits. Verified 2026-10-29: backend
386 passed + 8 skipped in 119 s (under the 3-minute cap; 7 new skips are
the cache-gated bundle tests), `scripts/replay_check.sh` OK on
`sim-healthy.mmrec`, `web` `npm ci --offline` + `vite build` green with
`package.json` unchanged. No commit, per instruction.

- Generator (`scripts/make_demo_bundle.py`) drives the real backend
  in-process (`TestClient`, non-public, `REPLAY_USE_CACHED_LLM=true`):
  session (Ford Focus 2012 2.0L I4, `vin_last6` only, L2,
  `[multimeter, fuel pressure gauge]`), genuine fingerprint, DTC/PID
  evidence + sim observations, recorded PDF ranges, rank-1 planner loop
  (keyframe after the 2nd test; DSP synth WAV for `engine_sound_screen`),
  confirmatory battery check with the recorded book spec (manual flip),
  `POST /report` with Wonder from the seeded disk cache ($0, re-validated
  on the way in). Frozen clock + uuid5 ids + fixed zip timestamps make
  two consecutive runs byte-identical (proven in a /tmp dry run with a
  synthetic cache: vacuum 44 events p=0.954, coil 41 events p=0.997,
  replay chain valid, $0 rebuild reusing the embedded review).
- Correction 1 (non-empty overlay): the generator overlays the `battery`
  concept from recorded SAM bytes parsed by the real parser (3 masks) —
  never the live not-found air-filter stream — and fails loudly on an
  empty overlay; `test_demo_bundles.py` asserts ≥1 non-empty mask in both
  `meta.json` and the image evidence payload. Supporting backend change:
  the keyframe route now logs its `overlays` on the image evidence (schema
  tolerates the key, like `spec_ranges`) so replays render recorded
  masks/boxes; photo bytes are not bundled, so the drawer renders shapes
  without the photo (noted in `docs/JUDGES.md`).
- Correction 2 (order): the generator fails fast with `DemoCacheMissing`
  naming `scripts/record_demo_cache.py` (exit 2); bundle tests skip with
  that reason until `demo_cache/{second_opinion,wonder_*}.json` exist.
  The recorder makes only the missing calls (1× Spark Open second
  opinion, 2× Wonder against the same deterministic audits, optional 1×
  SAM concept through the current downscale path), prints per-call cost,
  and hard-refuses above $1.00 cumulative. Provenance: demo files carry
  the original call records (`request_sha256` included); the generator
  seeds the Meta disk cache from them, so a drifted audit misses the
  cache and fails loudly instead of spending.
- Documented deviations from the plan text (letter vs feasibility):
  (a) the planner correctly ranks the battery check too low for
  vacuum/coil to ever be rank 1 inside a convergent run, so it rides as a
  labelled post-loop confirmatory step (still runnable, still through the
  real result route with a real spec and a genuine flip: generic
  overcharging vs book normal at 14.75 V running); vacuum totals 5 tests,
  coil 4; the rank-1 loop itself converges in ≤4 (vacuum
  dtc/listen/trim/hiss, coil dtc/mode06/hiss). (b) The PDF extract runs
  before the loop (the spec must exist before the citing result; evidence
  order never moves posteriors). (c) The outdoors gate is acked once up
  front (safety ack, not a test choice); without it the loop starves on
  ungated tests and stalls. (d) The Spark primary replays the existing
  live fixture (no new call, per the plan's provenance table); only the
  second opinion and Wonder are fresh recordings.
- Backend fixes: `POST /report` reuses the latest `report_built`-embedded
  Wonder review when live Wonder is skipped or fails ($0; otherwise the
  explicit unavailable line stays), and `report_built` now embeds the
  review; `replay_bundle_bytes` honours bundle `meta.json`
  (`user_level`/`tools`/redacted `vehicle`, old bundles fall back to
  L1/empty/None); `mmrec.create` takes an optional zip `date_time`.
- Web (§2, zero new deps): `styles.css` rewritten (~250 lines, CSS
  variables, light + `prefers-color-scheme`/persisted `data-theme`
  toggle, 280px/1fr/340px grid → 1 column <900px, cards, bordered-text
  badges, print rules); `Replay.tsx` judge landing (exact two sentences,
  "Play the investigation" loads `sim-vacuum-leak.mmrec` at cursor 0 and
  autoplays 2× — the old park-at-max is fixed for judge loads only —
  plus coil link, report handoff and the mirrored 5-item checklist),
  status bar (mark, `vin_last6`, sim chip, level switch), icon timeline
  bound to the scrubber, `HypothesisChart` with the ≥0.8 concluded band,
  0.5 line and clickable trace points (expected/measured, LR + source,
  L-level text), next-test cards with EIG/cost, display-only safety
  checkboxes, blocked reasons, still-needed list, and a collapsible
  evidence drawer (AI/DSP/manual/OBD-sim badges + recorded `SamOverlay`
  shapes). `Report.tsx` badges use the same classes;
  `report.html.j2` carries the variables inline with print rules.
  `JUDGE_FIXTURE`, the App judge note and `modal_smoke.sh` now name
  `sim-vacuum-leak.mmrec` (smoke needs the committed bundles).
- `docs/JUDGES.md` is the canonical 60-second script, mirrored on the
  landing. `scripts/screenshot_site.sh` (owner-run, needs `google-chrome`
  off-sandbox) captures the URL-addressable shots; mid-replay, drawer and
  report shots stay manual until pasted back.
- Owner steps to finish M6B: run the recorder live, run the generator,
  commit the two bundles (+ demo cache), re-run `make_demo_bundle.py
  --check` in CI, paste back the screenshots. The Wonder parallel review
  only reports; its fixes land as a separate job (correction 3).

## 2026-10-29 — M6 landed (levels, safety gates, report + Wonder, replay + web, Modal, budget)

Plan `.agents/plans/M6.md` implemented in order, each step with its test
first; full backend suite 378 passed + 1 skipped in ~118 s (under the
3-minute cap); `scripts/replay_check.sh` OK; Android offline 89 unit tests
+ `assembleDebug` green; `web` `npm ci --offline` (local cache) + `vite
build` green; `scripts/modal_smoke.sh` green against a local public-mode
server (health/budget/judge replay/state/report json+html+pdf, $0 spend
delta). No commit, per instruction.

- §1 levels: `mmc/levels.py` is the only renderer (`hypothesis_label`,
  `trace_text`, `instruction`, `summary`, `verbal_band` "Likely (about 6 in
  10)"); both KP packs gained `instructions_by_level` + `label.L2` on every
  test/hypothesis; the loader hard-fails missing L1/L3 text and L1 jargon
  (`levels.JARGON_DENYLIST`: MAF/STFT/DTC/OBD-class acronyms, tool names
  deliberately allowed); `compute_state` serves `instructions_by_level` on
  ranked tests and full metadata (min_level/tools/gate) on blocked tests —
  clients render blocked tests with reasons, never hide them. Prompts pinned
  at `mmc/agent/prompts/` (`level_styles.md` + `system_v1.md` stub; live
  Spark narration stays M7). Tools questionnaire canonical
  (`TOOL_OPTIONS`, 8 tools) at `GET /meta/levels`; Android renders from it
  with baked-in fallback, web has no session-create form so only the L1/L2/L3
  switchers apply there.
- §2 safety as code: `mmc/safety.py` (`GATE_CHECKLISTS` × 6 from SPEC
  §8/SAFETY.md, `stop_triggers_for/from_events`, single-use 5-min hashed
  clear-codes tokens). Gated result route behaviour unchanged (planner block
  → 409 until `safety_ack`); state carries `gate_checklist` per gated test
  and `stop_and_see_pro` (SRS B-codes, HV P0A/P1A codes, ECT ≥ 110 °C,
  `safety:*` user flags — P030n alone does NOT trigger, flashing MIL is a
  reported flag, not an inference). Clear-codes snapshots DTCs/freeze
  frame/Mode 06 as evidence first, needs exact `CLEAR CODES` + engine-off
  attestation; backend never emits Mode 04 (confirm returns the driver's
  one-shot token). Android generalised the sound-only confirm into one
  `SafetyGateCard` (pure `SafetyGates` object + JUnit); web cards show the
  checklist and the pro banner replaces cards on trigger.
- §3 report moved to `mmc/report/` (`build.py`, `html.py`, `pdf.py`,
  `templates/report.html.j2`); `sessions.build_report` delegates.
  Dependency note (AGENTS.md §8): `jinja2>=3.1` declared for HTML
  (autoescape on; sandbox installed it from system packages, Modal/CI
  install from the pin); PDF is stdlib-only (import-graph test). Report
  gains verbatim expected-vs-measured rows (engine-sound rows carry DSP
  numbers with origin `signal_processing`, rendered "Signal processing
  (DSP), not AI"), per-evidence AI-extracted labelling, `fallback_used`,
  second opinions, SAM overlay refs, stop triggers, `vin_last6` (full VIN
  asserted absent), and real `prompts_sha256` over `agent/prompts/*.md`.
  Wonder (`mmc/meta/wonder.py`, `wonder_review.json` → 16 schemas) gets the
  report PLUS the audit bundle (per-hypothesis traces, missing_evidence,
  blocked tests + reasons, second-opinion disagreements, fallback use);
  gaps may cite `test_ref`/`evidence_id`; probability keys refused; one
  `muse-wonder-1.0` call at `EFFORT_WONDER` on the shared Meta tracker,
  stored on the report so rebuilds cost $0; over-budget/API failure ships
  `null` + "AI review unavailable" under the literal "AI review — Muse
  Wonder (does not change the diagnosis)" heading. No Spark Open fallback
  for Wonder. (Two hand-written minified schemas shipped with brace errors;
  fixed and now parse-validated immediately after every schema write.)
- §4 replay: `mmc/replay/runner.py` (`POST /replay`
  bundle_ref|session_id|fixture, fixture seeding for the judge) folds rows
  through the normal append path into read-only `mode=replay` sessions;
  `GET /sessions/{id}/events?after_seq=` + `GET state?upto_seq=` power the
  scrubber. `test_replay_zero_spend` proves $0 on both ledgers with
  posteriors bitwise-equal. Web (`web/src`, no new deps): Replay page (open
  session / .mmrec upload / `?judge=1` one-click sim demo labelled sim,
  scrubber + play at bundle speed, L1/L2/L3 cards, blocked reasons, safety
  checklists, evidence with AI badges, chart, build-report handoff) and
  Report page (level summaries, trace, expected-vs-measured with DSP label,
  AI review, reproducibility, .html/.pdf links); `SamOverlay` renders SVG
  masks/boxes from props; dev proxy added to `vite.config.ts`.
- §5 Modal (CLI 1.6.3 API: `App`/`Image.debian_slim`/`Volume.from_name`/
  `Secret.from_name`/`asgi_app`): root `modal_app.py` serves the API with
  `web/dist` at `/`, volume `mmc-data` at `DATA_DIR`, secret
  `mmc-secrets`, first-boot fixture seeding, and a startup budget-guard
  check that fails loudly. Owner runs `modal secret create` + `modal
  deploy` (sandbox has no network/modal package: verified offline via a
  stub-`modal` import test + the FastAPI wiring + the smoke script against
  a local server).
- §6 budget on the landed `get_tracker(name)` registries (not duplicated):
  `GET /budget` (meta/baseten spend+stop+remaining, `public_mode`,
  `public_live` cap when public; no keys), web `BudgetBadge` (amber ≥80%,
  red at stop, public cap shown), Android read-only Settings line.
- Fix 3 public demo: `MMC_PUBLIC_MODE=replay` default only on Modal (local
  dev unaffected); public allows replay/upload sessions, 501s paid
  vision/PDF/video and skips Wonder; `MMC_PUBLIC_LIVE_BUDGET_USD>0`
  (default 0 = off) enables photo keyframe + instrument only on a separate
  capped tracker (`with_public_budget`, cache-live, no Open second
  opinion), 10 paid calls/hour/IP (429), badge shows the public cap.
  `REPLAY_USE_CACHED_LLM` forced on at startup in public.
- Test hygiene: `test_modal_app` snapshots/restores process env and the
  shared app routes (its `create_app()` otherwise poisoned later tests
  with `MMC_PUBLIC_MODE=replay` — caught by the full suite, not by
  per-file runs); vision `request` params keep a default so direct handler
  calls in old tests still work; `_write_pack` modernised with L2 labels +
  instructions (expectations unchanged).

## 2026-10-29 — M5 review findings 1–15 fixed (blocking 1–4 first)

Each finding got a failing test, then the fix, then green. Backend suite
342 passed + 1 skipped in ~135 s (under the 3-minute cap);
`scripts/replay_check.sh` OK on `sim-healthy.mmrec`; Android
`testDebugUnitTest assembleDebug` green offline (84 unit tests, fresh APK).

- #1/#12 (budget): `budget.get_tracker(name)` is the process-wide registry
  M6's `GET /budget` will read — `"meta"` ($120 `BUDGET_USD_HARD_STOP`)
  and `"baseten"` ($80 `BASETEN_BUDGET_USD_HARD_STOP`), shared by every
  default `MetaClient`/`SparkOpenClient` and every `bind()`. `check`/`add`
  hold a lock; `EventStore.append` (plus reads) holds one lock around the
  seq/head read-modify-write with `check_same_thread=False` (threaded
  appends previously died in sqlite). Tests needing isolation inject an
  explicit `BudgetTracker` (`test_budget_refusal_past_hard_stop` updated).
- #2/#9 (cache keys): `call`/`call_stream` take `cache_key` — the filename
  hashes ORIGINAL input bytes only (`spark._image_part` now carries full
  `sha256`; SAM image/video requests carry `orig_sha256`/`orig_bytes` via
  `image_cache_key`/`video_cache_key`, box prompts re-based to orig-space
  for the key). Sent-bytes hash/scale stay in the request as record
  metadata. Committed `scripts/rekey_llm_cache.py` migrates old entries
  without re-recording (Spark stubs match `--original` by prefix+length,
  SAM by sent==orig sha, video by explicit `--map`); the two live entries
  under `backend/data/llm_cache` re-keyed against `engine_bay_02.jpg`.
- #3: `actual <= 0` with a positive estimate books the estimate and sets
  `cost_estimated: true` on Meta, stream, and Baseten paths.
- #4: `parse_csv` is row-wise — a bad cell drops its row
  (`dropped_rows`), an empty cell null-marks, ISO timestamps never drop
  rows; `derive_tests(parsed, vehicle)` takes `disp_l` from
  `vehicle.engine` (`csv_logs.displacement_l`) and skips MAF with a reason
  when unknown — no 2.0 L assumption. Route passes the session vehicle.
- #5: `analyze_keyframe_full` runs the primary first and starts the second
  opinion only when the primary is Meta-served; a `provider: baseten`
  primary returns `single_source` with one total Baseten call.
- #6/#7/#8: all vision routes (keyframe, instrument, confirm,
  engine-sound) reject replay sessions (409) and map
  `BudgetExceeded`/`NoCachedResponse` to 402; SAM overlay budget refusals
  re-raise (other overlay failures log and continue); the instrument route
  binds the analyzer so Meta `llm_call` records reach the audit log;
  blocking analyzer/DSP/video work runs via `asyncio.to_thread`.
- #10 (Android): `copyYPlane` respects row/pixel strides (pure JDK,
  covered by new `YuvStrideTest`: packed, padded, pixel-stride-2,
  consumed-buffer cases); `toJpegBytes` encodes a real JPEG via
  `Bitmap.compress` (device-only). Sandbox build notes: `/tmp/gh`'s
  Gradle-8.10.2 metadata store lacks camera 1.4.2 descriptors, so
  verification drove the extracted Gradle 8.7 binary directly
  (`GRADLE_USER_HOME=/tmp/gh`, `ANDROID_USER_HOME=/tmp/android-home`,
  `--offline`); `/tmp/gh`'s `metadata-2.10x` root indexes were synced from
  `~/.gradle` (descriptor/file copies alone are invisible without them).
  Committed wrapper/build files unchanged.
- #11: `detect_tone` hunts peaks in every 1 s chunk (refines the winner,
  persistence unchanged); `hiss_metrics` scores the strongest window.
  New non-stationary synths (squeal/hiss starting at 2.5 s) failed before.
- #12 (masks): `submit_mask_jobs` collects per-frame results (failures log
  + yield an `error` entry); the inline `analyze_clip` path skips bad
  frames the same way.
- #13 (SPEC §5 manual > kp > physics): `evaluate` injects a manual
  min/max into the measured payload for range-judged tests
  (`_MANUAL_RANGE_KEYS`: fuel pressure — already `spec_min/max_kpa` — and
  voltage, new `spec_min/max_v`); a manual range now flips outcomes
  (350 kPa normal→low against 380–420; 12.0 V rest low→normal against
  11.5–12.6). Priority is manual > caller-supplied spec > hardcoded.
- #14: `MetaClient` enforces per-key Meta ID prefixes
  (`MODEL_KEY_PREFIXES`: `muse-spark-`/`muse-wonder-`/`sam-`/`muse-voice-`)
  at construction and per call — `MODEL_SPARK=gpt-4o` raises.
- #15: "no evidence before confirm" and "both opinions logged" now assert
  on `events.jsonl` (`evidence_added` absent/`llm_call` × 2).
- Suite speed: `FixtureAnalyzer` parses the battery SAM stream once per
  process (`test_vision_routes.py` 44 s → 4.5 s).

## 2026-10-29 — SAM mask-decode performance fix (decode-once + 640 px + pool)

- Profiling (cProfile + wall probes, live fixture
  `backend/tests/fixtures/upload/sam_video_upload_01.jsonl`, 75 frames at
  1280x960, 7.7 MB): the parser validates every mask at parse time with one
  pure-Python raster decode (~1.4 s each: `parse_video_events` 101.3 s for
  75 masks), and our `sam.result_from_image` then decoded each mask TWICE
  more (`decode_mask_to_rle` + `decode_mask_to_svg_path`, the SVG helper
  re-decoding through the RLE path: 235.0 s, 150 raster decodes for 75
  masks). Total 336 s; `test_live_sam_video_stream_parses` took 355 s and
  the `test_upload_video.py` full-stream tests hung for minutes each.
- Fix 1, decode once (`backend/mmc/meta/sam.py::rle_and_svg`): one
  `decode_mask_to_rle` per mask, SVG from that RLE via the library's own
  RLE->SVG step (`meta_sam_parser._mask_conversion._decode_to_svg_path`,
  takes the RLE object, never touches the raster codec — reachable without
  re-decoding, so no private-codec fork; the installed library is
  untouched). Output is byte-equal to the old two-call path (asserted in
  `test_svg_derives_from_the_single_rle`). `test_result_from_image_decodes_
  each_mask_once` counts raster decodes with a wrapper: 3 (validation) + 3
  (conversion) on the battery fixture, was 3 + 6.
- Fix 2, smaller media: `downscale_image`/`downscale_video` cap the long
  side at `SAM_MAX_SIDE = 640` (ffmpeg; Pillow is not installed, so ffmpeg
  for images too; H.264/yuv420p, fps kept, audio dropped). Boxes/points go
  into the prompt in sent coordinates, result boxes/SVG scale back to
  source coordinates (`coord_scale`, `scale_svg_path`), and the record
  carries `sam_scale` + `orig_size`/`sent_size` + `orig_bytes`. Cost
  unchanged (SAM bills per image/frame). Undecodable/small/tool-less input
  passes through with `sam_scale = 1.0`, never an error. Decode is ~85k
  px/s pure Python (122k-px mask: 1.43 s raster, 1.79 s RLE+SVG), so a
  640-px keyframe mask decodes in ~0.3-0.4 s — back inside the < 3 s
  keyframe->labels budget.
- Fix 3, video masks off the request path: `track_video_raw` returns raw
  events (no parse), `extract_boxes_fast` scans box tokens from the stream
  text with zero mask decodes (proven equal to the official parser's boxes
  on the trimmed fixture AND the multi-object battery image stream — whose
  grammar chains objects comma-separated, `,1<|box...>`, with no repeated
  frame header), and capped mask jobs (`build_mask_jobs`: every 3rd frame,
  at most 25; boxes stay per frame) convert in a bounded
  `ProcessPoolExecutor(min(16, cpu_count))` via the picklable
  `convert_frame_job` (official per-frame parse + single-decode convert in
  the worker). The upload route returns boxes+events immediately with
  `overlays_pending`, stashes finished masks via
  `sessions.note_video_overlays` (new `video_overlays` state key, posteriors
  bit-identical), and pushes the WS state diff — same pattern as the second
  opinion. `SamResult` gains per-frame `boxes` (+ `frame_index` on masks);
  `result_from_image` applies the same caps synchronously.
- Fixtures/tests: `scripts/trim_sam_video_fixture.py` (committed,
  deterministic, `--check` enforced by
  `test_trimmed_fixture_matches_generator_script`) keeps the first 3 frames
  (172 KB) in `sam_video_upload_trimmed.jsonl`; unit tests use it, the full
  75-frame stream runs under a new `slow` marker (pyproject; CI's plain
  `pytest -q` still runs it once). New `backend/tests/test_sam_perf.py`
  (17 tests: single-decode count, caps, fast-vs-parser boxes, downscale +
  scale-back, pool bound, 2.5 s single-mask bound).
- Two latent bugs found by the new tests (both ours, both fixed): (1)
  `upload/video.py::probe_clip` asked ffprobe for `r_duration_rate`, which
  does not exist, so fps always parsed as 0 and EVERY real probe failed —
  now `avg_frame_rate`/`r_frame_rate`; (2)
  `routes_uploads.LiveUploadAnalyzer.bind` rebuilt a default `MetaClient`,
  dropping the analyzer's transport/budget/replay flags (live route could
  never use an injected client) — now propagates like the vision bind.
- Numbers (40-core box, wall): full 75-frame parse+overlays 336 s -> 10.0 s
  (pool, < 25 s target); `test_upload_video` + `test_upload_routes`
  15.7 s; `test_vision_parse` + `test_vision_routes` + fast fixtures
  44.1 s (was 41 + 25 s); `test_sam_perf` 31.5 s; FULL backend suite
  306 passed + 1 skipped in 120 s (before: estimated ~25-30 min from
  the reported per-file costs — 355 s fixture test plus four full-stream
  upload_video tests at ~340 s each — never run end-to-end; target < 3 min).
  replay_check OK (sim-healthy.mmrec). Android
  offline (seeded GRADLE_USER_HOME, Gradle 8.10.2, --offline): 80 unit
  tests, 0 failures; assembleDebug green.

## 2026-10-29 — M5O (Spark Open second opinion + fallback + engine-sound DSP) + phone recorder

- Sibling client, not subclass: `MetaClient` is Responses-shaped while
  Spark Open is Chat Completions, so `backend/mmc/meta/spark_open.py`
  (`SparkOpenClient`) reuses helpers (`canonical_bytes`/`request_sha256`/
  `call_record`/`_redact`), `BudgetTracker`, lazy `openai` import and the
  offline `transport` seam instead of inheriting. Pin is exactly
  `meta-models/muse-spark-open-1026` (contributor/non-Meta IDs refused);
  key `BASETEN_API_KEY`, base `BASETEN_BASE_URL`. Own $80 ledger
  (`BASETEN_BUDGET_USD_HARD_STOP`, Meta's $120 tracker untouched);
  assumed $1.25/M in / $4.25/M out with cached tokens billed at the full
  input rate until Baseten documents otherwise (`budget.estimate_spark_open`
  + `provider="baseten"` branch in `actual_cost_from_usage`, mapping
  `prompt/completion_tokens` + reasoning/cached detail blocks; reasoning
  rides inside completion, never double-billed). Cache under
  `DATA_DIR/llm_cache/baseten`; replay hit $0, miss raises
  `NoCachedResponse`. Records carry `provider:"baseten"`, full
  `reasoning_content`, mapped usage, `cost_usd`, `latency_s`, appended via
  the existing `llm_call` event. Covered by `tests/test_spark_open.py`.
- Second-opinion vision merges on agreement only: `analyze_components_open`
  reuses `COMPONENTS_SYSTEM` + `vision_components` validation +
  `BANNED_KEYS`; `components.merge_opinions` keys on the normalised
  `sam_concept` slug (both→`corroborated` at max confidence, one
  side→`single_source`, same-region conflicting anomaly→`disputed`) with
  `disagreements:[{name, named_by}]`. `observation.json` gains optional
  `agreement`/`sources`; `compute_state` still folds only `test_result`,
  so posteriors are bit-identical (asserted). Latency: Spark 1.3 ships
  inside the 3 s sync budget with `second_opinion_pending:true` and the
  merge lands later via `on_second_opinion` (noted into state as
  `vision_second_opinions`, WS push on the existing socket); 10 s
  per-model timeout, Open timeout→`single_source`, labels never blocked.
  SAM overlays stay on the served primary concepts. Covered by
  `tests/test_second_opinion.py`.
- Resilience fallback (`meta/fallback.py::call_text_or_image`, text+image
  only) fires once on `BudgetExceeded`/`MetaClientError` post-429-retries;
  the record carries `fallback:{from:"meta", reason, detail≤200}`. Wired
  into `spark.analyze_components`/`read_instrument` (optional
  `open_client`; one shared $80 ledger per analyzer for fallback +
  second opinion) — no agent module exists yet, so agent turns wire up
  when it lands. SAM streaming/parser and voice (no `meta/voice.py`, no
  substitute) never fall back (import-graph test). Covered by
  `tests/test_fallback.py`.
- Engine sound is stdlib DSP, never an LLM call (`mmc/vision/
  engine_sound.py`: `wave`/`array`/`math` Goertzel bins, no numpy — no new
  dependency, so no AGENTS.md §8 dep note). WAV 3–10 s (stereo→mono,
  native rate) + live rpm + cylinders (explicit, else parsed from
  `vehicle.engine`; missing→`inconclusive`, never guessed) feeds
  firing-dropout (envelope autocorrelation at `f_fire=(rpm/60)*(cyl/2)`),
  2–6 kHz narrowband squeal (peak/median + 1 s persistence), and 4–8 kHz
  hiss (band ratio + flatness, idle/2500 pair when supplied, else
  idle-only flagged). One KP test `engine_sound_screen`
  (sensory/L1/`engine_running_outdoors`, `dsp.engine_sound`, no cell
  >0.75) in both packs; posteriors move only via the normal test-result
  route (asserted end-to-end: hiss clip → `vacuum_hiss` →
  `vacuum_leak_post_maf` top). `evidence.json` gains type `audio`; new
  endpoint `POST .../vision/engine-sound`. Audio probe confirmed Spark
  Open carries no audio tokens, so `chat_audio()` stays a stub
  (`NotImplementedError` even with `SPARK_OPEN_AUDIO=true` until Baseten
  confirms a shape). Covered by `tests/test_engine_sound.py`.
- Owner runs `scripts/spike_spark_open.py` outside the sandbox (key from
  `~/.config/baseten/api.env`, never printed); fixtures replay at $0
  (`tests/test_m5o_docs.py` skips until they exist). Live cost <$2 of the
  $100 credit; CI $0.
- Phone recorder for `engine_sound_screen`: `audio/EngineSoundRecorder`
  (AudioRecord, fixed 44.1 kHz mono 16-bit — the squeal band reaches
  6 kHz — 5–8 s with auto-stop, pure `WavWriter` for WAV) behind a record
  button on that test's card. Safety checklist
  (`engine_running_outdoors`) first, then the plain mic explanation, then
  runtime `RECORD_AUDIO` only on tap; the clip uploads with the live OBD
  rpm + cylinder count (blank = vehicle on file) and submits through the
  normal result path with its evidence id. Covered by `WavWriterTest`,
  `EngineSoundSpecTest`, `SessionClientSoundTest` (builders return pure
  maps — JVM unit tests cannot touch Android's `org.json` stubs).
- Sandbox note: `~/.gradle` is read-only under the file policy, so
  Android verified with a writable `GRADLE_USER_HOME` at
  `/tmp/mmc-gradle-home` seeded by copying `~/.gradle/caches` (+
  `gradle.properties`), driving the extracted Gradle 8.10.2 binary
  directly with `--offline`.

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
