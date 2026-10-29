# Master Mechanic Copilot — product and technical spec

Source: docs/research/2026-10-26-platform-verdict-and-plan.md, sections 3–8. AGENTS.md holds the build rules, schemas, API contracts and milestones; this file holds the product and engineering detail behind them. If the two disagree, AGENTS.md wins.

## 3. Product spec

### Positioning

**"A master mechanic in your pocket that shows its work."** Competitors explain codes or chat. This product runs a real diagnostic investigation:
- it correlates live data, the camera and your observations;
- it tells you the single most valuable next test for your skill and tools;
- it shows why each suspect rose or fell;
- it hands you a report that a shop (or a judge) can replay.

### Competitive landscape

| Product | What it does well | Gap this product owns |
|---|---|---|
| **FIXD** | Simple consumer UX. Premium ($99.99 billed annually per FIXD's support page; $12.99/month on the iOS App Store) adds an "AI Mechanic" chat, repair-cost estimates and Emissions Precheck. | Chat isn't an auditable state; no guided tests; no vision |
| **BlueDriver** | Repair Reports built on "a database of millions of actual mechanic repairs" / confirmed fixes; enhanced codes; no subscription [obdadvisor](https://obdadvisor.com/best-obd2-app/) | Statistical "most common fix", not this-car evidence; no next-test reasoning |
| **Snap-on Intelligent Diagnostics / SureTrack** | "Smart Data" shows PIDs relevant to the code, highlights "vehicle data that is out of expected range", plus SureTrack real-world fixes and TSBs [autosuccessonline](https://www.autosuccessonline.com/snap-on-offers-family-of-intelligent-diagnostics-products/) | Pro-only, expensive, no consumer mode, no camera fusion |
| **Autel MaxiSys Ultra S2** | Topology 3.0, oscilloscope, AI inspection for dents and scratches, Motor TruSpeed [auteldealer](https://www.auteldealer.com/products/autel-maxisys-ultra-s2) | AI is cosmetic or inspection-focused rather than causal reasoning; tablet lock-in |
| **Launch X431 / Topdon** | Bidirectional and coding depth | Same as Autel |
| **Car Scanner / Torque / OBDLink app / Innova RS2** | Live data, dashboards, logging | Data without reasoning |
| **LLM "AI mechanic" apps (MechanicAI, AI Mechanic, MECH AI, MechanicGPT)** | MechanicAI: "Type 'my car shudders at idle' and AI examines all your data to rank the most likely causes" [google](https://play.google.com/store/apps/details?id=org.olkdrsi.ai.mechanicai&hl=en) | Free-form LLM output: no explicit probabilities, no test log, no expected-vs-measured, no reproducibility |

**Takeaway:** "AI + OBD2" is already crowded, with several Play Store apps launched in 2026. Don't pitch "AI reads your codes." Pitch **"evidence-driven diagnosis with an audit trail"**, the Snap-on Intelligent Diagnostics idea made multimodal, explainable and adaptive for consumers.

### Personas and experience levels

Onboarding asks one question, then a tools checklist:

| Level | Label shown | Vocabulary | Tests offered | Data shown |
|---|---|---|---|---|
| L1 | "I drive it — never opened the hood" | Plain words and analogies ("the engine is getting too much air") | Look/listen/smell checks, guided photos, "is the light flashing?" Hard **Stop & see a pro** points. | 3 key gauges, traffic-light status |
| L2 | "I do my own oil changes / basic repairs" | Light jargon, defined inline | Visual inspection, connector checks, battery voltage with a multimeter, smoke-free vacuum checks (listen, propane is excluded), swap tests | Trims, MAF, ECT graphs |
| L3 | "Professional technician" | Full jargon, terse | Mode 06, spec values, pinouts from uploaded manuals, voltage-drop tests, smoke test, fuel pressure | Raw PIDs, freeze frame, Mode 06 table, transcript |

**Tools-on-hand checklist:** none / multimeter / test light / jack + stands / vacuum gauge / smoke machine / fuel pressure gauge / scope / enhanced scan tool. The planner never recommends a test that needs a tool you don't have. It may suggest "a $20 multimeter would let you check X."

**Explaining uncertainty to L1 users:** use natural frequencies and verbal bands together ("Likely (about 6 in 10)"). Always show "what would change my mind." Never show a single "answer" until the leading hypothesis is above 0.8 *and* the top discriminating test has been run.

### Core flows

1. **Onboarding:** experience level → tools → privacy choice (local-only VIN/location vs cloud) → safety acknowledgement.
2. **Connect:** pair an ELM327/OBDLink (Classic or BLE) → adapter fingerprint (genuine/clone and capability matrix) → protocol detect → VIN (Mode 09) → vPIC decode → recalls.
3. **Intake:** symptoms by voice or text ("rough idle when cold, CEL on") → the app reads DTCs (03/07/0A), freeze frame (02), readiness (01 PID 01) and Mode 06.
4. **Live session (main screen):** split view. Camera with SAM overlays on top; live PID strip and hypothesis bars below. The "Next best test" card has instructions sized to your level and an *expected result* panel.
5. **Evidence capture:** tap to snap or record a 5–10 s clip; voice notes; manual measurement entry ("battery 12.1 V"); vision reads multimeter photos.
6. **Update:** each test result produces an expected-vs-measured card → likelihood ratio → posterior update, with an explanation line.
7. **Conclude:** stop rule (P(top) ≥ 0.85, or no test left with positive net value) → recommendation with repair options and a pro referral if needed → **Report**.
8. **Upload mode (no car):** upload a scan-tool CSV/PDF, photos, a video and notes → the same engine runs → the next-test list becomes a to-do list for later.

### Key screens

1. Onboarding
2. Adapter/vehicle
3. **Live Investigation**: camera, PID strip, hypothesis bars, next-test card
4. Test runner (step-by-step + capture)
5. Evidence timeline
6. Hypothesis detail (probability history chart + explanation trace)
7. Report (PDF/web)
8. Upload
9. Settings/safety

---

## 4. System architecture and tech stack

```
[Android app (Kotlin/Compose)]
  ├─ AdapterLayer: ClassicRfcommTransport | BleTransport | WifiTcpTransport | SimTransport
  ├─ Elm327Driver (init, fingerprint, request queue, parsers)  ← read-only allowlist
  ├─ CameraX (preview, ImageAnalysis keyframes, 5–10 s clip recorder, torch)
  ├─ Mic → WebSocket → Muse Voice Transcribe (realtime)
  └─ Session client (event append, WebSocket for state)
          │ HTTPS/WSS
[Orchestrator backend (Python 3.12, FastAPI)]
  ├─ EventStore (append-only JSONL + SQLite index, SHA-256 hash chain)
  ├─ ReasoningEngine (deterministic Bayesian core + VOI planner)
  ├─ KnowledgePack (fault models, tests, spec ranges; YAML, versioned)
  ├─ LLM Agent (Muse Spark 1.3, tool calling + structured output)
  ├─ Vision service (Muse Spark image/video understanding + SAM 3.1)
  ├─ Integrations: NHTSA vPIC + recalls/complaints, search grounding
  ├─ Budget guard, model-version pinning, prompt store
  └─ ReportGenerator (JSON → HTML/PDF, replay bundle .zip)
[Web app (Vite + React + TS)]
  ├─ Replay mode (plays recorded bundles through the backend)
  └─ Report viewer (+ @meta-sam/react overlays)
```

**Hosting:**
- Run the backend on a small cloud VM or container for the judging period so judges get a reliable URL.
- Your DL380p (no GPU) is fine for development and as a CPU-only fallback, but don't make judges depend on a home connection.
- Muse Glimmer's 30B open weights could run quantized on CPU with 256 GB RAM via llama.cpp, but slowly. Treat that as a post-hackathon offline mode experiment, not the demo path. **Unverified performance.**

### Meta model usage map

| Model | Job in the product | Why this model |
|---|---|---|
| **Muse Spark 1.3** (`muse-spark-1.3`, Standard mode) | Orchestrator agent: interprets symptoms, maps evidence to structured observations, picks from planner-ranked tests, writes level-adapted explanations, reads images (gauges, multimeters, scan-tool screens, manual pages) via image understanding, summarizes uploaded videos, extracts spec tables from PDFs | 1,048,576-token context; tool calling; structured output; image/video/PDF input; $1.25 in / $4.25 out per 1M tokens [meta](https://dev.meta.ai/docs/models) [meta](https://dev.meta.ai/docs/quickstart) [meta](https://dev.meta.ai/docs/overview) [meta](https://dev.meta.ai/docs/pricing-rate-limits) |
| **SAM 3.1** (`sam-3.1`) | Under-hood component segmentation and video tracking for overlays ("MAF sensor", "coolant reservoir", "battery terminal"), leak-region masks, belt masks | Boxes and pixel masks, identity across frames; $2.50 per 1,000 images, $0.20 per 1,000 video frames [meta](https://dev.meta.ai/models/sam-3-1) [meta](https://dev.meta.ai/docs/pricing-rate-limits) |
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | Hands-free mode for techs: realtime dictation of observations and commands ("next test", "log 12.4 volts"), with keyword biasing for part names | Realtime WebSocket `wss://api.meta.ai/v1/asr/realtime`; $0.18/hour; 25 evaluated languages; **no TTS** (use Android TextToSpeech for spoken replies) [meta](https://dev.meta.ai/docs/overview) |
| **Muse Wonder** (`muse-wonder-1.0`) | Optional "senior review" pass at report time: audits the investigation log for reasoning gaps and writes the technician narrative. Also used through Muse Code during development. | Strongest model; $4 in / $20 out, so use it once per report, not live [meta](https://dev.meta.ai/docs/overview) [meta](https://dev.meta.ai/docs/pricing-rate-limits) |
| **Search grounding** (`web_search` tool on Spark) | TSB/known-issue lookups with citations, shown as low-weight "context" evidence | $2.50 per 1,000 queries [meta](https://dev.meta.ai/docs/pricing-rate-limits) |
| **Muse Code** | Builds the whole repo from the spec | AGENTS.md conventions, `/plan` skill, parallel subagents [meta](https://dev.meta.ai/docs/muse-code/configuration) [meta](https://dev.meta.ai/docs/cookbook/bundled-skills) |
| Muse Image | Leave it out of diagnosis (generated images could be mistaken for evidence). It could make onboarding illustrations at most. | Avoid looking like a gimmick |
| **Muse Spark Open** (`meta-models/muse-spark-open-1026` via Baseten) | Second-opinion vision (corroborates Spark 1.3 keyframes on agreement only, never probabilities) + fallback narration for text/image when the Meta API is over budget or 5xx; audio unavailable (Oct 29 probe: audio inputs carry no tokens) | Open weights over Chat Completions (`https://inference.baseten.co/v1`); ledger assumes $1.25 in / $4.25 out per 1M tokens with its own $80 hard stop; text ~3.3 s, image ~6.5 s live, so Spark 1.3 answers first and Open corroborates async |
| Muse Glimmer | Post-hackathon on-device/offline explanations (use Muse Spark Open above for second opinions today) | Apache 2.0 open weights (Glimmer) [meta](https://dev.meta.ai/docs/overview) |

**Muse Spark 1.3 audio caveat:** Meta's docs say audio understanding in Muse Spark 1.3 is "not fully supported." [meta](https://dev.meta.ai/docs/video-understanding) Route speech through Voice Transcribe, and send video to Spark for its visual content only.

---

## 5. Diagnostic reasoning engine

### Design principle: the LLM proposes, the math decides

Free-form chat is unauditable. Split responsibilities:

1. **Knowledge Pack (deterministic, versioned YAML).**
   - Fault hypotheses (e.g., `vacuum_leak_post_maf`, `maf_contaminated`, `maf_circuit_open`, `iat_circuit_open`, `fuel_delivery_low`, `thermostat_stuck_open`, `ignition_misfire_cyl_n`, `charging_system_low`, `o2_sensor_slow`, `exhaust_leak_pre_o2`), each with a prior per vehicle class.
   - Tests, each with cost (minutes, $), risk level, required tools, minimum skill and a safety gate.
   - **Likelihood tables:** P(outcome | hypothesis) for each test's discrete outcomes (e.g., trims at idle ≥ +10% → "lean_idle").
2. **Bayesian core (code).** A multi-hypothesis model with a "none/other" leak hypothesis. Evidence updates the posterior via likelihood ratios. The MVP uses a naive-Bayes approximation with explicit conditional-dependence groups: correlated evidence such as STFT and LTFT is merged into one feature, so it isn't double-counted. Upgrade to a proper Bayesian network (pgmpy) after the hackathon.
3. **Muse Spark agent (tool-calling).** It turns messy input (voice, photos, uploaded PDFs) into **typed Observations**. It may propose *new* hypotheses or likelihood entries, but they enter as `proposed` with a capped prior and are labeled "AI-proposed" in the trace. It narrates. It never edits probabilities directly.
4. **Planner (code).** It ranks candidate tests by expected information gain (entropy reduction of the hypothesis distribution) divided by a cost function, then filters by tools, skill and safety.

This mirrors the decision-theoretic troubleshooting literature:
- Heckerman, Breese and Rommelse's "Decision-theoretic troubleshooting" (Communications of the ACM 38(3), 1995) and the related "Troubleshooting under Uncertainty" work use a Bayesian-network representation with a one-step value-of-information heuristic, demonstrated on an automobile starting network.
- Linköping's automotive framework splits a **planner** that computes recommendations from a **diagnoser** that computes "fault probabilities and test outcome probabilities given current diagnostic information." It uses dynamic Bayesian networks because repairs change the system state. [tandfonline](https://www.tandfonline.com/doi/full/10.1080/08839514.2016.1156955)
- Patent literature describes choosing the test that minimizes "a combined measure of the test cost… and the value provided by the test… estimated using an information entropy metric." [uspto](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8463641)

### Next-best-test scoring

```
score(t) = EIG(t) / (w_time*minutes + w_money*$ + w_risk*risk_level + w_skill*max(0, skill_req - user_skill)*∞_gate)
EIG(t) = H(P) - Σ_o P(o) * H(P | o)
Hard filters: required tools ⊆ user tools; safety_gate satisfied; not already performed with same conditions.
Stop rule: max P ≥ 0.85 AND best remaining EIG < 0.05 bits, OR no positive-score test → conclude / refer.
```

**Missing-evidence detection.** For the top-3 hypotheses, list evidence types whose outcome would most change the ranking but have no Observation yet (e.g., "no fuel-trim reading at 2,500 rpm", "no visual of intake boot"). This is just EIG restricted to zero-cost data already obtainable (PIDs, a photo) and is shown as "I still need…".

### Expected vs measured: physics and spec checks

Each TestResult stores `expected` (a range plus its source) and `measured` (value, units, conditions). An evaluator maps the comparison to a discrete outcome. Source priority:
1. a spec from a user-uploaded manual page (cited page hash);
2. vehicle-specific knowledge pack data;
3. the physics/rule-of-thumb model below.

**These rule-of-thumb ranges are engineering heuristics. Label them "generic" in the UI and override them with manual specs whenever available.**

- **MAF plausibility:** expected g/s ≈ (RPM/120) × displacement_L × VE × ρ_air × (MAP/BARO), with ρ_air ≈ 1.18 g/L at 25 °C and VE ≈ 0.75–0.9 when warm. A 5.0 L V8 at 650 rpm, MAP 30/100 kPa, works out to about 4–5 g/s. Measured below 70% of expected while trims are positive suggests MAF under-reporting or contamination.
- **Fuel trims:** total trim (STFT+LTFT) within ±10% is normal. More than +10% at idle that normalises at 2,500 rpm points to an unmetered air leak, because a fixed leak is a shrinking fraction of total airflow as RPM rises. Lean at all loads points to fuel delivery or MAF.
- **O2 (narrowband, upstream):** a warm sensor at 2,500 rpm should cross 0.45 V repeatedly, swinging about 0.1–0.9 V. A lazy or stuck sensor fails this.
- **ECT warm-up:** should reach a thermostat-regulated plateau (typically ~88–105 °C) within roughly 10–15 min of idle or drive. A plateau well below that suggests the thermostat is stuck open (cross-check P0128).
- **Charging:** about 12.4–12.7 V at rest (engine off, settled); about 13.5–14.7 V running (Mode 01 PID 0x42 control-module voltage, or a multimeter).
- **Mode 06 misfire monitors:** per-cylinder misfire counts (on CAN, the J1979 misfire MIDs) give an expected-vs-measured "0 vs N" per cylinder. Mode 06 also gives manufacturer test limits (min/max) for catalyst, O2 and EVAP monitors, which are ideal expected-vs-measured data straight from the ECU. Support varies by vehicle; Fords generally populate it (assumption, verify on your vehicles).

### Explanation trace (why a hypothesis moved)

Every posterior update writes a trace entry like this:

```json
{"seq":42,"hypothesis":"vacuum_leak_post_maf","prior":0.31,"posterior":0.58,
 "evidence_id":"ev_0017","test_id":"trim_idle_vs_2500",
 "expected":"lean_idle_normal_cruise if leak","measured":"STFT+LTFT idle +18.7%, 2500rpm +4.1%",
 "likelihood_ratio":3.2,"lr_source":"kp:ford-gen-v1#trim_rpm_split",
 "text_L1":"The engine is getting extra air at idle that disappears at higher speed — classic sign of an air leak.",
 "text_L3":"Idle total trim +18.7% normalising to +4.1% @2500: unmetered air; LR 3.2."}
```

### Schemas

The canonical JSON Schemas (draft 2020-12) are in the build spec (§11, `packages/schemas/`). Summary:

| Schema | Key fields |
|---|---|
| **Vehicle** | `vehicle_id`, `vin` (optional, hashable), `vpic` (decoded subset), `year/make/model/engine`, `displacement_l`, `fuel`, `is_hybrid_or_ev`, `adapter_profile` |
| **Session** | `session_id`, `vehicle_id`, `user_level`, `tools[]`, `mode` (live/upload/replay), `model_pins{}`, `kp_version`, `engine_version`, `started_at`, `event_log_head_hash` |
| **Evidence** | `evidence_id`, `type` (obd_pid/dtc/freeze_frame/mode06/image/video/audio_transcript/manual_page/measurement/user_report), `source`, `captured_at`, `content_hash`, `blob_ref`, `derived_from[]` |
| **Observation** | `observation_id`, `evidence_ids[]`, `feature` (from the KP vocabulary), `value`, `units`, `conditions{rpm,ect,load}`, `confidence`, `extractor` (`rule`/`muse-spark-1.3@…`/`human`) |
| **TestPerformed** | `test_id`, `kp_test_ref`, `requested_at`, `performed_by`, `conditions`, `safety_ack`, `status` |
| **TestResult** | `result_id`, `test_id`, `expected{range/outcome, source}`, `measured{value, units}`, `outcome`, `evaluator`, `evidence_ids[]` |
| **Hypothesis** | `hypothesis_id`, `label`, `origin` (kp/ai_proposed), `prior`, `current`, `history[]` (seq, p, cause), `trace[]` |
| **NextTestRecommendation** | `rank`, `kp_test_ref`, `eig_bits`, `cost{minutes, usd, risk}`, `score`, `why`, `expected_outcomes[]{outcome, p, effect}`, `instructions_by_level`, `blocked_reasons[]` |
| **Report** | `report_id`, `session_id`, `summary_by_level`, `conclusion`, `hypotheses_final[]`, `tests[]`, `evidence_index[]`, `safety_notes[]`, `reproducibility{model_pins, kp_version, engine_version, prompts_hash, log_head_hash}` |

### Prompt and tool-calling design

The **system prompt** (stored, versioned, hashed) fixes:
- the role: "diagnostic investigator, not a guesser";
- the output contract: only call tools or return schema-valid JSON;
- the level-specific style guide;
- the safety policy;
- the rule "never state a probability that was not returned by `get_state`."

**Tools exposed to Muse Spark:**

| Tool | Purpose |
|---|---|
| `get_state()` | Hypotheses, posteriors, missing evidence, test log |
| `add_observation(obs)` | Schema-validated; the engine maps it to a feature |
| `propose_hypothesis(h)` | Capped prior; flagged as AI-proposed |
| `rank_next_tests(k)` | Planner output |
| `explain_update(seq, level)` | Level-adapted wording for a trace entry |
| `request_capture(kind, target)` | Asks the app for a photo, clip or PID burst |
| `segment(concept, media_id)` | SAM 3.1 wrapper |
| `read_instrument(image_id)` | Spark vision → `{value, units, confidence}` |
| `lookup_vehicle(vin)` | vPIC + recalls |
| `search_known_issues(query)` | Grounding; low-weight context only |
| `read_manual_page(file_id, question)` | Spec extraction with the page cited |

**Determinism:**
- Pin model IDs (`muse-spark-1.3`, not aliases).
- Store every request and response verbatim, hashed.
- Set low reasoning effort for extraction and higher for narration.
- **Unverified:** whether Model API honours `temperature`/`seed`. Reproducibility therefore comes from **replaying stored tool outputs**, not re-sampling. The Bayesian core is fully deterministic given the same Observations.

### Reproducibility (event sourcing)

- Append-only `events.jsonl`. Each event is `{seq, ts, type, payload, payload_sha256, prev_hash, hash}`.
- Blobs (frames, clips, PDFs) are content-addressed by SHA-256.
- **Replay** re-runs the engine from events, with LLM tool outputs taken from the log, and must reproduce identical posteriors. That is a CI test.
- The report embeds the head hash, so any tampering is detectable.

---

## 6. OBD / ELM327 integration spec

### Transport

| Transport | How |
|---|---|
| **Classic** | RFCOMM via SPP UUID `00001101-0000-1000-8000-00805F9B34FB` |
| **BLE** | Discover the vendor UART-like service (common FFE0/FFE1 or FFF0/FFF1; store per-adapter profiles) |
| **Wi-Fi** | TCP to `192.168.0.10:35000` |
| **Sim** | In-process simulator |

All transports implement `send(line) → Flow<bytes>`, with `>` prompt framing.

### Init sequence (with clone tolerance)

```
ATZ            (reset; wait up to 1.5 s; read banner)
ATE0           echo off
ATL0           linefeeds off
ATS0           spaces off      (tolerate "?": parser handles spaces)
ATH1           headers on      (needed to separate ECUs 7E8/7E9)
ATAT1          adaptive timing (tolerate "?")
ATST 32        timeout ~200 ms (tolerate "?")
ATSP0          auto protocol   (NOT "ATTP A0": known to lock some clones)
0100           triggers search; tolerate "SEARCHING..." then data
ATDPN          record protocol number
ATI / AT@1 / STI (OBDLink) / ATPPS   fingerprint
ATRV           adapter-measured battery voltage
```

**Clone fingerprinting:**
- FORScan's forum documents "v2.1" clones that answer `?` to commands like `ATPPS` and `ATR1`, and that only read the first 2 bytes of a command string. [forscan](https://forscan.org/forum/viewtopic.php?t=1575) [github](https://github.com/TheTom/elm327_obd_for_mac/blob/main/ELM327_TECHNICAL_REFERENCE.md)
- One ELMduino user found `AT TP A0` "locks up my ELM327 and it becomes completely unresponsive." [github](https://github.com/PowerBroker2/ELMduino/issues/69)
- The app therefore probes a capability matrix (`ATPPS`, `ATCRA 7E8`, `ATS0`, `ATAT1`, `ATSH 7E0`, and a 3-byte request such as `22F190` echo-tolerance) and stores `adapter_profile`:
  - **genuine/OBDLink:** full features, Mode 22 allowed;
  - **good clone:** Mode 01–0A, headers;
  - **limited clone:** Mode 01/03 only, no multi-PID, single-ECU.

### Response handling

| Response | Action |
|---|---|
| `SEARCHING...` | Strip; extend timeout once |
| `NO DATA` | PID unsupported now; mark it, don't retry in a hot loop |
| `CAN ERROR` / `BUS INIT: ...ERROR` | Ignition off or wrong protocol: prompt "Turn key to ON", then re-run `ATSP0` |
| `BUFFER FULL` | Reduce request rate; disable multi-PID |
| `STOPPED` | A new command interrupted a response; serialize requests strictly (single-flight queue) |
| `?` | Unsupported AT command; record it in the profile |
| `UNABLE TO CONNECT` | Show the connection checklist |

Multi-frame ISO-TP responses (e.g., VIN) are parsed from `0:`, `1:` line prefixes (headers on) or reassembled.

### Supported services (MVP, read-only)

| Service | Use |
|---|---|
| 01 | Supported-PID bitmaps (00/20/40/60/80/A0/C0), readiness (01), live PIDs |
| 02 | Freeze frame for the DTC that set it |
| 03 / 07 / 0A | Stored / pending / permanent DTCs |
| **04** | **Clear DTCs: gated** (see §8) |
| 05 | Non-CAN O2 tests (rare on CAN vehicles; CAN uses 06) |
| 06 | On-board monitor test results with min/max limits: **core expected-vs-measured source** |
| 09 | VIN (02), CALID (04), CVN (06) |
| 22 (Ford, genuine/OBDLink only) | **Read-only** DIDs via `ATSH 7E0` (PCM). Allowlist only; post-MVP for most features. |

### Priority PID list

| Group | PIDs |
|---|---|
| Engine state | 0x04 load, 0x05 ECT, 0x0C RPM, 0x0D VSS, 0x0F IAT, 0x11 TPS |
| Fuel | 0x06/0x07 STFT/LTFT B1, 0x08/0x09 B2, 0x03 fuel status |
| Air | 0x10 MAF, 0x0B MAP, 0x33 BARO |
| Ignition | 0x0E timing advance |
| O2 | 0x14–0x1B narrowband; 0x24–0x2B / 0x34–0x3B wideband λ |
| Electrical | 0x42 control module voltage |
| Other | 0x46 ambient, 0x1F run time, 0x21 distance with MIL, 0x30/0x31 warm-ups/distance since clear (inspection-readiness context) |

### Polling

On CAN, request up to 6 PIDs in one Mode 01 message (e.g., `010C0D0511060710`) when the adapter profile allows it. Typical ELM327-over-Bluetooth throughput is on the order of 10–20 single-PID responses per second for good adapters, and lower for clones. *Estimate, not measured: benchmark on your adapters on Day 2.* Default to a fast group (RPM, load, trims, MAF, O2) at ~5 Hz and a slow group (ECT, IAT, voltage, BARO) at 0.5 Hz.

### Safety allowlist (enforced in the driver, not the UI)

**Allowed request first-bytes:** `01 02 03 05 06 07 09 0A`, and `22` (only if `adapter_profile.mode22 && did ∈ allowlist`). `04` is allowed only with a one-shot signed token from the clear-codes flow.

**Blocked always:**
- `10` (except default session)
- `11` (ECU reset)
- `14` (UDS clear)
- `27` (security access)
- `28` (comm control)
- `2E`/`2F` (write/IO control)
- `31` (routines)
- `34`–`37` (download/upload)
- `3D`
- `85`
- raw `ATMA` monitor-all in consumer mode

**Legal note:** reading standardized OBD data is broadly permitted. Even read-only enhanced access is best limited to your own vehicles in the MVP. Bidirectional controls and writes are out of scope.

### Simulator and recorded sessions

- `Elm327Sim` is a state machine with a virtual vehicle model (RPM, ECT warm-up curve, trims responding to a "leak" parameter).
- It supports scenario injection (`maf_unplugged`, `iat_unplugged`, `vacuum_leak`, `fuel_fuse_removed`, `coil_unplugged_cyl3`) and three personalities (genuine / good clone / bad clone), including `?` replies, `SEARCHING...`, `BUFFER FULL` and 2-byte truncation.
- A **Recorded session** is a `.mmrec` zip containing the raw adapter transcript with timestamps, camera keyframes and clips, transcripts, `events.jsonl` and `manifest.json` with hashes. `ReplayTransport` feeds the transcript back at original timing, so the backend can't tell replay from live.

---

## 7. Vision and upload pipeline

**Division of labour (per Meta's docs):** SAM 3.1 "locates the objects you name; it doesn't answer questions or reason about a scene." Muse Spark handles scene understanding, then "pass the concept to SAM 3.1 for the segmentation work." [meta](https://dev.meta.ai/docs/sam/segmenting) [meta](https://dev.meta.ai/docs/sam/segmenting)

### Live mode

1. **On-device keyframe selection.** CameraX ImageAnalysis at 15 fps. Compute sharpness (Laplacian variance) and motion. Pick a keyframe when the frame is sharp and the scene has changed beyond a threshold, at most one every 3 s, plus on user tap. Turn the torch on automatically in low light.
2. **"What am I looking at?"** Send the keyframe (downscaled to about 1024 px long side) to Muse Spark with the vehicle context. It returns structured JSON: `{components:[{name, sam_concept, confidence}], anomalies:[{type: leak|corrosion|frayed_wire|belt_glazing|cracked_hose, region_hint, confidence}]}`.
3. **Labeling.** For the 1–3 components relevant to the current next test, call SAM 3.1 with a short noun phrase per request. SAM has **one concept per request**, and comma lists "return zero matches." [meta](https://dev.meta.ai/docs/sam/segmenting) Use phrases like "air filter housing", "battery terminal", "coolant reservoir".
   - Generic SAM vocabulary won't know "MAF sensor" reliably. Have Spark give a box or point for it, then use SAM's **box/point prompts** to get a clean mask.
4. **Tracking for overlays.** Record a 5–10 s clip and send it to SAM video segmentation with box prompts. Return masks per frame, rendered as overlay playback. Live overlays between keyframes use simple on-device box tracking (optical flow) so the UI feels real-time.
5. **Instrument reading.** Multimeter, gauge or another scan tool's screen → Spark → `{value, units, confidence, needs_retake}`. Ask for a retake if confidence is below 0.8. Always show the value to the user for confirmation before it becomes Evidence.

**SAM constraints and the "16 objects" figure:**
- Video input must be MP4/MOV with H.264/H.265/MPEG-4. VP9/WebM aren't accepted. Clips can be up to 15,000 frames. [meta](https://dev.meta.ai/docs/sam/segmenting)
- Each video request is billed for at least 50 frames. [meta](https://dev.meta.ai/docs/pricing-rate-limits)
- The earlier claim of "up to 16 tracked objects per frame" was **not re-verified** in Meta's docs during this research. Design for at most 3 concurrent tracked objects anyway.

### Cost per 20-minute live session

| Item | Assumption | Cost |
|---|---|---|
| Spark keyframe analysis | ~120 keyframes × ~2.5k input + 400 output tokens (image token cost unverified) | ≈ $0.38 + $0.20 |
| Spark orchestration turns | ~60 turns × 15k cached context + 800 out | ≈ $0.15–0.40 with caching |
| SAM image prompts | 60 × $0.0025 | $0.15 |
| SAM video clips | 6 clips × 300 frames × $0.0002 | $0.36 |
| Voice | 20 min × $0.18/h | $0.06 |
| Wonder report review | 60k in / 4k out | ≈ $0.32 |
| **Total** | | **≈ $1.50–2.00 per session** (estimate) |

**Interpretation:** the $150 credit covers roughly 75–100 full sessions, which is enough for development plus judging if replay mode serves **cached** model outputs. **Make replay mode use cached outputs by default.** It also makes judging deterministic and free.

### Latency targets

These are targets, not measured values:
- keyframe → labels: under 3 s;
- tap → next-test update: under 2 s (the engine is local and instant; only narration waits on the LLM);
- voice command: under 1 s partials.

**Realtime API status:**
- Meta documents a realtime **speech-to-text** WebSocket, but Voice Transcribe "does not synthesize speech or provide a speech-to-speech conversation API." [meta](https://dev.meta.ai/docs/overview)
- No realtime video streaming endpoint was found. "Live" therefore means keyframes plus short clips, which is also cheaper and more auditable.

### Upload mode

- Photos, then the same Spark path.
- Videos go through the Files API (`purpose: "user_data"`), then Spark video understanding gives a timestamped event list, and the relevant sub-clips go to SAM.
- Test-result PDFs and scan-tool exports, plus owner/service manual pages, go to Spark as PDFs. It extracts spec tables into `SpecRange` objects with page citations and content hashes. This is the legal replacement for scraping ALLDATA.
- CSV logs (Torque/Car Scanner/FORScan exports) are parsed deterministically and mapped to PIDs.

---

## 8. Knowledge sources, licensing, safety and privacy

### Data you can legally use

- **NHTSA vPIC:** free, no key. Use `DecodeVinValues/{vin}?format=json` and batch `DecodeVINValuesBatch` (max 50 VINs). vPIC is populated from manufacturers' 565 submittals. [dot](https://vpic.nhtsa.dot.gov/api/)
- **NHTSA recalls and complaints APIs:** public, no key. [github](https://github.com/the-ai-entrepreneur-ai-hub/nhtsa-recall-monitor-docs)
  - Recalls are matched by **make, model and year, not by VIN range**, so present them as "may apply — confirm with dealer." [apify](https://apify.com/insight.solutions/product-recalls-api)
  - Complaints are a valuable symptom prior ("other owners of this model report…"). Label it anecdotal.
- **TSBs/manufacturer communications:** NHTSA hosts manufacturer communications. Surface links via search grounding rather than bulk-scraping. *Availability and format not verified in this research.*
- **Generic DTC definitions:** SAE J2012 is a copyrighted standard. Write your own short descriptions for generic P0xxx codes, or rely on widely published public descriptions, rather than copying the SAE text verbatim. *Licensing nuance unverified; treat it as a legal review item.*
- **EPA fueleconomy.gov:** engine, displacement and drivetrain context.

### Do not scrape

ALLDATA, Mitchell 1, Identifix and Ford's subscription service information (Motorcraft Service) are licensed products. That is why the product's spec layer comes from **user-uploaded** manuals and pages, kept private to the user's account.

### Safety guardrails (hard-coded, not prompt-only)

**Hazard gates before any physical test.** The UI shows a checklist the user must confirm, and L1 users get stricter variants:

| Hazard | Gate |
|---|---|
| Hot engine/coolant | Never open a hot radiator or reservoir cap. Cooling system tests require engine off and cool for L1/L2. |
| Fans and belts | Electric fans can start with the key off. Keep hands, hair and clothing clear; remove loose items. |
| Running engine | Outdoors or with exhaust extraction only. Parking brake on, in Park or Neutral, wheels chocked. |
| Fuel | No fuel-line disconnection for L1/L2. No propane/carb-cleaner leak tests (fire risk); recommend a smoke test by a pro instead. |
| Ignition | No spark-gap tests on coil-on-plug systems for L1/L2. Warn about high voltage. |
| SRS/airbags | Never probe yellow connectors or SRS components. Show "see a pro." |
| Hybrid/EV | If vPIC or the VIN says hybrid/EV: orange cables are off-limits, and no under-hood tests beyond visual for anyone below L3 with HV training. |
| Lifting | No working under a vehicle supported only by a jack. Stands on solid ground are required. L1 gets no under-vehicle tests. |
| Driving tests | Never ask the driver to look at the phone while driving. Logging-only, with a passenger operating the app. |

**Stop-and-see-a-pro triggers:**
- flashing MIL (active misfire that can damage the catalyst);
- overheating;
- oil pressure warning;
- brake or steering symptoms;
- fuel smell;
- smoke;
- any SRS or HV fault.

**Clear codes (Mode 04):**
- Behind a two-step modal: it explains that clearing erases freeze-frame data and **resets readiness monitors**, so an emissions inspection can fail until the drive cycles complete.
- The app snapshots DTCs, freeze frame and Mode 06 into the log first.
- It requires engine off, key on, and typed confirmation.
- It is never offered by the AI as a "fix."

**ECU writes:** none, enforced by the driver allowlist and covered by a unit test.

**Disclaimers:**
- "Guidance, not a guarantee; you are responsible for working safely; consult a qualified technician."
- Probabilities are labeled as estimates.
- The report says which evidence was AI-extracted.

### Privacy

- The VIN is personal data in many jurisdictions. Store it encrypted, show it truncated, and include it in reports only by user choice.
- No location collection in the MVP.
- Use Standard mode (`muse-spark-1.3`, never `-contributor`) for all user data.
- Delete-session support, plus a local-only mode post-hackathon.

### App store considerations

- **Google Play:** Bluetooth permissions (`BLUETOOTH_CONNECT`/`BLUETOOTH_SCAN` with `neverForLocation`), a camera rationale, and a data-safety form.
- **Apple:** an MFi program for Classic adapters (or partner with OBDLink/vLinker, whose MX+/FS/MS are already MFi-certified), [obdautodoctor](https://www.obdautodoctor.com/obd-adapters/) plus `NSBluetoothAlwaysUsageDescription` and `NSLocalNetworkUsageDescription` for Wi-Fi adapters.
- Both stores scrutinize health/safety claims, so avoid "guaranteed diagnosis."
