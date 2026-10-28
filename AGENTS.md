# AGENTS.md — Master Mechanic Copilot ("MMC")

> Dates: the "(D1)", "(D2)"… labels on the milestones below are from the original schedule. The current
> schedule, scope cuts and owner tasks are in docs/PLAN.md; follow its dates. Product detail is in docs/SPEC.md.

## Mission
Build a multimodal vehicle-diagnosis copilot: Android app (live OBD2 via ELM327 + camera + voice), Python orchestrator backend, and React web Replay/Report viewer. The core is an auditable reasoning engine: explicit hypothesis probabilities, a logged test record, missing-evidence detection, next-best-test ranking, expected-vs-measured comparison, explanation traces, and reproducible event-sourced reports.

## Hard constraints (never violate)
1. READ-ONLY vehicle access. The ELM driver must reject any request whose first byte is not in ALLOW = {01,02,03,05,06,07,09,0A}, plus 22 only when adapter_profile.mode22==true AND DID in config/mode22_allowlist.yaml. Mode 04 only with a single-use ClearToken issued by the clear-codes flow. Blocked always: 10,11,14,27,28,2E,2F,31,34,35,36,37,3D,85. Unit test required.
2. Never send "AT TP A0"; use "ATSP0".
3. All AI calls use Meta Model API, base URL https://api.meta.ai/v1, Standard-mode model IDs only (never "*-contributor"). Pin exact model IDs from env.
4. The LLM never sets probabilities. Only engine/ code changes posteriors. LLM outputs must pass JSON Schema validation before use.
5. Every state change is an appended event (hash-chained). No in-place mutation of session history.
6. Safety gates are code, not prompt text. A test with safety_gate != none cannot be shown as runnable until the user confirms the gate checklist.
7. No secrets in the repo. .env is gitignored. No scraping of ALLDATA/Mitchell/Identifix/Motorcraft Service.
8. Don't commit or push unless asked. Don't add dependencies that aren't listed below without noting why in docs/DECISIONS.md.

## Repo layout
```
mmc/
  AGENTS.md
  docs/ SPEC.md RULES.md DECISIONS.md JUDGES.md SAFETY.md
  packages/schemas/            # JSON Schema 2020-12, source of truth
    vehicle.json session.json evidence.json observation.json
    test_performed.json test_result.json hypothesis.json
    next_test.json report.json event.json adapter_profile.json
  backend/                     # Python 3.12, FastAPI, uv
    pyproject.toml
    mmc/
      api/ routes_sessions.py routes_events.py routes_replay.py routes_reports.py ws.py
      store/ event_store.py blobs.py           # JSONL + SQLite index, sha256 chain
      engine/ bayes.py planner.py evaluator.py physics.py trace.py stoprule.py
      kp/ loader.py  packs/ford_gen_v1.yaml generic_gas_v1.yaml
      agent/ orchestrator.py tools.py prompts/ system_v1.md level_styles.md
      meta/ client.py spark.py sam.py voice.py wonder.py budget.py
      vision/ keyframes.py instruments.py components.py
      integrations/ vpic.py nhtsa.py
      report/ build.py templates/report.html.j2
      replay/ bundle.py runner.py
    tests/ (pytest)  fixtures/ sessions/*.mmrec  transcripts/*.txt
  android/                     # Kotlin, Compose, CameraX, minSdk 29
    app/src/main/java/com/mmc/
      adapter/ Transport.kt ClassicRfcommTransport.kt BleTransport.kt WifiTcpTransport.kt
               SimTransport.kt ReplayTransport.kt
      elm/ Elm327Driver.kt Allowlist.kt Fingerprint.kt Parsers.kt Pids.kt Elm327Sim.kt
      camera/ CameraScreen.kt KeyframeSelector.kt ClipRecorder.kt
      voice/ RealtimeAsrClient.kt
      session/ SessionClient.kt Recorder.kt
      ui/ onboarding/ live/ testrunner/ timeline/ hypothesis/ report/ settings/
    app/src/test/ (JUnit)  ElmAllowlistTest.kt Elm327SimTest.kt ParsersTest.kt
  web/                         # Vite + React + TS
    src/ pages/Replay.tsx pages/Report.tsx components/HypothesisChart.tsx SamOverlay.tsx
  scripts/ dev.sh replay_check.sh
  .env.example
```

## Environment variables (.env.example)
```
MODEL_API_KEY=            # Meta Model API key
META_BASE_URL=https://api.meta.ai/v1
MODEL_SPARK=muse-spark-1.3
MODEL_WONDER=muse-wonder-1.0
MODEL_SAM=sam-3.1
MODEL_ASR=muse-voice-transcribe-1.0
ASR_REALTIME_URL=wss://api.meta.ai/v1/asr/realtime
BUDGET_USD_HARD_STOP=120   # backend refuses new paid calls past this (spend alerts do not stop billing)
REPLAY_USE_CACHED_LLM=true
DATA_DIR=./data
ALLOW_MODE22=false
```

## Schemas (write these exact files; abbreviated here, expand with descriptions)
```json
// hypothesis.json
{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"hypothesis","type":"object",
 "required":["hypothesis_id","label","origin","prior","current","history","trace"],
 "properties":{
  "hypothesis_id":{"type":"string","pattern":"^[a-z0-9_]+$"},
  "label":{"type":"object","properties":{"L1":{"type":"string"},"L2":{"type":"string"},"L3":{"type":"string"}}},
  "origin":{"enum":["kp","ai_proposed"]},
  "prior":{"type":"number","minimum":0,"maximum":1},
  "current":{"type":"number","minimum":0,"maximum":1},
  "history":{"type":"array","items":{"type":"object","required":["seq","p","cause_event"],
     "properties":{"seq":{"type":"integer"},"p":{"type":"number"},"cause_event":{"type":"string"}}}},
  "trace":{"type":"array","items":{"type":"object",
     "required":["seq","evidence_id","prior","posterior","likelihood_ratio","lr_source"],
     "properties":{"seq":{"type":"integer"},"evidence_id":{"type":"string"},"test_id":{"type":["string","null"]},
      "expected":{"type":"string"},"measured":{"type":"string"},
      "prior":{"type":"number"},"posterior":{"type":"number"},
      "likelihood_ratio":{"type":"number","exclusiveMinimum":0},"lr_source":{"type":"string"},
      "text_L1":{"type":"string"},"text_L3":{"type":"string"}}}}}}
// test_result.json
{"$id":"test_result","type":"object","required":["result_id","test_id","expected","measured","outcome","evaluator"],
 "properties":{"result_id":{"type":"string"},"test_id":{"type":"string"},
  "expected":{"type":"object","required":["source"],"properties":{
     "min":{"type":"number"},"max":{"type":"number"},"outcome":{"type":"string"},"units":{"type":"string"},
     "source":{"type":"object","required":["kind"],"properties":{"kind":{"enum":["manual_page","kp","physics","mode06_limit"]},
       "ref":{"type":"string"},"content_hash":{"type":"string"}}}}},
  "measured":{"type":"object","required":["value"],"properties":{"value":{},"units":{"type":"string"},
     "conditions":{"type":"object","properties":{"rpm":{"type":"number"},"ect_c":{"type":"number"},"load_pct":{"type":"number"}}}}},
  "outcome":{"type":"string"},"evaluator":{"type":"string"},
  "evidence_ids":{"type":"array","items":{"type":"string"}}}}
// next_test.json
{"$id":"next_test","type":"object","required":["rank","kp_test_ref","eig_bits","cost","score","why","expected_outcomes"],
 "properties":{"rank":{"type":"integer"},"kp_test_ref":{"type":"string"},"eig_bits":{"type":"number"},
  "cost":{"type":"object","properties":{"minutes":{"type":"number"},"usd":{"type":"number"},"risk":{"enum":[0,1,2,3]}}},
  "score":{"type":"number"},"why":{"type":"string"},
  "expected_outcomes":{"type":"array","items":{"type":"object","properties":{"outcome":{"type":"string"},"p":{"type":"number"},
     "effect":{"type":"object","additionalProperties":{"type":"number"}}}}},
  "required_tools":{"type":"array","items":{"type":"string"}},"min_level":{"enum":["L1","L2","L3"]},
  "safety_gate":{"type":"string"},"instructions_by_level":{"type":"object"},
  "blocked_reasons":{"type":"array","items":{"type":"string"}}}}
// event.json
{"$id":"event","type":"object","required":["seq","ts","type","payload","payload_sha256","prev_hash","hash"],
 "properties":{"seq":{"type":"integer"},"ts":{"type":"string","format":"date-time"},
  "type":{"enum":["session_started","adapter_fingerprinted","evidence_added","observation_added",
     "test_requested","safety_ack","test_performed","test_result","hypothesis_proposed","posterior_updated",
     "llm_call","sam_call","asr_segment","codes_clear_requested","codes_cleared","report_built"]},
  "payload":{"type":"object"},"payload_sha256":{"type":"string"},"prev_hash":{"type":"string"},"hash":{"type":"string"}}}
```
Also write: vehicle.json, session.json (incl. model_pins, kp_version, engine_version, user_level, tools[], mode),
evidence.json (type enum: obd_pid,dtc,freeze_frame,mode06,image,video,audio_transcript,manual_page,measurement,user_report,csv_log; content_hash; blob_ref),
observation.json (feature from KP vocabulary, value, units, conditions, confidence, extractor),
test_performed.json, report.json (summary_by_level, conclusion, hypotheses_final, tests, evidence_index, safety_notes,
reproducibility{model_pins,kp_version,engine_version,prompts_sha256,log_head_hash}), adapter_profile.json
(kind genuine|obdlink|good_clone|limited_clone, supports{multi_pid,headers,mode22,atpps,atcra}, protocol, banner).

## Knowledge pack format (backend/mmc/kp/packs/*.yaml)
```yaml
version: ford-gen-v1
hypotheses:
  vacuum_leak_post_maf: {prior: 0.15, label: {L1: "Air leak after the airflow sensor", L3: "Unmetered air leak"}}
  maf_contaminated: {prior: 0.12, ...}
  # maf_circuit_open, iat_circuit_open, ect_circuit, fuel_delivery_low, thermostat_stuck_open,
  # ignition_misfire_cyl (param n), charging_low, o2_slow_b1s1, other (leak node, prior 0.1)
tests:
  trim_idle_vs_2500:
    kind: obd_procedure
    min_level: L1
    tools: []
    minutes: 2
    risk: 0
    safety_gate: engine_running_outdoors
    outcomes: [lean_idle_normal_cruise, lean_both, normal, rich]
    evaluator: physics.trim_rpm_split
    likelihoods:   # P(outcome | hypothesis)
      vacuum_leak_post_maf: {lean_idle_normal_cruise: 0.75, lean_both: 0.15, normal: 0.08, rich: 0.02}
      maf_contaminated:     {lean_idle_normal_cruise: 0.15, lean_both: 0.65, normal: 0.15, rich: 0.05}
      fuel_delivery_low:    {lean_idle_normal_cruise: 0.10, lean_both: 0.70, normal: 0.15, rich: 0.05}
      other:                {lean_idle_normal_cruise: 0.20, lean_both: 0.20, normal: 0.50, rich: 0.10}
  visual_intake_boot: {kind: visual, min_level: L1, tools: [], minutes: 3, risk: 0, safety_gate: engine_off, ...}
  maf_pid_plausibility: {kind: obd_calc, evaluator: physics.maf_expected, ...}
  mode06_misfire_counts: {kind: obd_read, min_level: L2, ...}
  battery_voltage_rest_running: {kind: measurement, tools: [multimeter], ...}
  listen_fuel_pump_prime: {kind: sensory, min_level: L1, ...}
  ect_warmup_curve: {kind: obd_procedure, minutes: 15, ...}
dependency_groups: [[stft_b1, ltft_b1]]   # merged into one feature to avoid double counting
```
Hypotheses not mentioned in a test's likelihoods get a default uniform-over-outcomes likelihood.

## API contracts (backend)
- POST /sessions {vehicle?, user_level, tools[], mode} → {session_id}
- POST /sessions/{id}/events {type, payload} → {seq, hash}   (append-only)
- POST /sessions/{id}/blobs (multipart) → {blob_ref, sha256}
- GET  /sessions/{id}/state → {hypotheses[], next_tests[], missing_evidence[], tests_log[]}
- POST /sessions/{id}/tests/{test_ref}/result {measured, conditions, evidence_ids} → {test_result, updates[]}
- POST /sessions/{id}/vision/keyframe {blob_ref} → {components[], anomalies[], overlays[]}
- POST /sessions/{id}/vision/instrument {blob_ref} → {value, units, confidence, needs_retake}
- POST /sessions/{id}/clear-codes/request → {token, consequences_text}; POST .../confirm {token, typed_confirmation}
- POST /sessions/{id}/report → {report_id, url}; GET /reports/{id}(.json|.html)
- POST /replay {bundle_ref, speed} → {session_id}  (uses cached LLM outputs when REPLAY_USE_CACHED_LLM)
- WS   /sessions/{id}/ws → server pushes state diffs
- Voice: Android streams PCM to backend WS /sessions/{id}/asr, backend proxies to ASR_REALTIME_URL (keeps key server-side)

## Meta client rules (backend/mmc/meta)
- Use the OpenAI Python SDK with base_url=META_BASE_URL and api_key=MODEL_API_KEY (Responses API).
- spark.py: structured output with our JSON Schemas; tool calling for agent; log every request/response as llm_call event with sha256.
- sam.py: Responses API on model sam-3.1, stream=true; ONE noun-phrase concept per request; support box/point prompt grammar; parse with meta_sam_parser (Python). Video via Files API upload; MP4/MOV H.264 only; transcode on device if needed.
- voice.py: realtime WS proxy; keyword biasing list from KP component names.
- wonder.py: report review only (one call per report).
- budget.py: estimate cost per call from documented prices (Spark $1.25/M in, $0.15/M cached, $4.25/M out; Wonder $4/$20; SAM $2.50/1k images, $0.20/1k frames min 50 frames/request; ASR $0.18/h; search $2.50/1k) and hard-stop at BUDGET_USD_HARD_STOP.
- Retry 429 with exponential backoff + jitter, honour Retry-After.

## ELM327 driver spec (android/elm)
Init: ATZ, ATE0, ATL0, ATS0, ATH1, ATAT1, ATST32, ATSP0, 0100, ATDPN, ATI, AT@1, STI, ATPPS, ATRV. Tolerate "?" on optional commands; record capability.
Single-flight request queue; prompt '>' framing; timeouts 1.5 s for ATZ, 2 s first 0100, 300 ms steady-state.
Handle SEARCHING..., NO DATA, CAN ERROR, BUS INIT ERROR, BUFFER FULL, STOPPED, UNABLE TO CONNECT, '?' as typed results.
Parse multi-ECU (headers 7E8/7E9), ISO-TP multi-frame ("0:","1:"), supported-PID bitmaps, DTC decoding (P/C/B/U), Mode 06 (MID/TID/value/min/max with unit/scaling table).
Multi-PID (up to 6) only if profile.multi_pid. Fast group ~5 Hz, slow group 0.5 Hz.

## Simulator spec (Elm327Sim.kt + backend/tests/fixtures)
- Personalities: GENUINE, GOOD_CLONE, BAD_CLONE (answers '?' to ATPPS/ATR1/ATCRA, truncates commands >2 bytes, no multi-PID).
- Vehicle model: rpm, load, ect warm-up (first-order to 95 °C, tau 300 s; stuck-open thermostat plateau 70 °C), maf from physics.maf_expected, trims from injected leak/fuel/maf scalars, o2 switching.
- Scenarios: healthy, maf_unplugged (MAF PID 0, P0102), iat_unplugged (IAT −40, P0113), vacuum_leak (idle trims +18%, 2500 rpm +4%), fuel_fuse_removed (no start), coil_unplugged(n) (Mode 06 misfire counts on n, P030n), thermostat_stuck_open (P0128).

## Milestones and acceptance criteria
M0 Scaffold (D1): repo tree, schemas, .env.example, CI running pytest + gradle test + web build.
  ✓ `scripts/dev.sh` boots backend + web; schema files validate their own examples.
M1 Event store (D2): append, hash chain, blobs, SQLite index, replay iterator.
  ✓ Tampering with any event breaks verify_chain(); 10k events append < 2 s.
M2 ELM driver + simulator + Classic transport (D2): allowlist enforced; fingerprinting.
  ✓ ElmAllowlistTest: every blocked service throws; Mode 04 without token throws.
  ✓ Elm327SimTest: each personality completes init; BAD_CLONE degrades to limited profile without crash.
  ✓ On a real vehicle: VIN, DTCs, 10 PIDs logged to a .mmrec bundle.
M3 Reasoning engine (D3): bayes.py, planner.py (EIG/cost, filters, stop rule), evaluator.py, physics.py, trace.py.
  ✓ For each sim scenario fixture, the true hypothesis is ranked #1 with p ≥ 0.8 within ≤ 4 recommended tests.
  ✓ Planner never returns a test whose tools ⊄ user tools or min_level > user level.
  ✓ Replay of a fixture reproduces identical posteriors (bitwise float equality after rounding to 1e-9).
M4 GATE end-to-end (D4): Android live → backend → state over WS → next-test card → JSON report.
  ✓ Manual check on the user's Ford; screen recording saved.
M5 Vision (D5): keyframe selector, Spark component/anomaly JSON, SAM masks, instrument reading with confirm step.
  ✓ On 10 saved engine-bay photos, ≥ 7 have at least one correct labelled overlay; multimeter reading within ±1 digit on 8/10.
M6 UX levels + report + web replay (D7): L1/L2/L3 text styles, tools questionnaire, safety gates, report HTML/PDF, web Replay page.
  ✓ Same session renders different instructions for L1 vs L3; gated tests show checklist first.
  ✓ Public URL replays a bundled real session with no API spend when REPLAY_USE_CACHED_LLM=true.
M7 Voice + Wonder review + judge mode (D8): ASR proxy, "log …" commands, report review, docs/JUDGES.md.
  ✓ Voice command "next test" advances; report contains Wonder review section labelled as AI review.

## Test plan
- Unit: parsers (golden transcripts in backend/tests/fixtures/transcripts), allowlist, bayes update math, EIG, physics functions, schema validation.
- Property tests: posteriors always sum to 1 ± 1e-9; no negative probabilities.
- Integration: simulator scenarios end-to-end through backend; replay determinism.
- Recorded-session fixtures: at least 4 real .mmrec bundles from the user's Fords (healthy, MAF unplugged, vacuum leak, coil unplugged) — run in CI via ReplayTransport.
- Manual: on-vehicle checklist in docs/SAFETY.md.

## Verification commands
```
cd backend && uv run pytest -q
cd android && ./gradlew testDebugUnitTest assembleDebug
cd web && npm ci && npm run build
scripts/replay_check.sh backend/tests/fixtures/sessions/*.mmrec
```

## Do NOT
- Do not call any non-Meta AI model or API.
- Do not use contributor-mode model IDs.
- Do not let the LLM output probabilities that bypass engine/.
- Do not implement any ECU write, routine, security access, or bidirectional control.
- Do not offer clearing codes as a fix; Mode 04 only via the gated flow that explains readiness-monitor reset.
- Do not generate images as evidence (no Muse Image in the diagnostic path).
- Do not import code from the user's pre-hackathon projects; implement fresh.
- Do not log full VINs in plaintext logs; store encrypted, display truncated.

## Workflow for the agent
1. Start with /plan for each milestone; save plans to .agents/plans/; wait for approval.
2. Implement milestone; run verification commands; update docs/DECISIONS.md.
3. Use parallel subagents only for independent packages (android/ vs backend/ vs web/).
