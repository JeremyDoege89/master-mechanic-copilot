# Master Mechanic Copilot: platform verdict, hackathon plan, and a Muse Code build spec

**Build a native Android app (Kotlin) for live use, plus a web replay and report viewer that judges can open without an adapter or a car. Add iOS after the hackathon by sharing the core code through Kotlin Multiplatform, and keep the web app as the viewer and the shop dashboard.** Android is where cheap Bluetooth Classic ELM327 adapters work and where your existing skills are. The web viewer solves the problem that judges will have neither hardware nor a vehicle. iOS can't use the most common cheap adapters at all, so it belongs in the long-term product rather than the 9-day build.

## TL;DR

- **Platform:** for the hackathon, build native Android (Kotlin + Compose + CameraX + Bluetooth Classic/BLE) plus a deployed web "Replay & Report" viewer that plays back real recorded sessions. For the product, ship Android first, then iOS through a Kotlin Multiplatform shared core (BLE, Wi-Fi and MFi adapters only), and use the web app as the shop/customer portal. iOS apps can't open Bluetooth Classic SPP links to third-party adapters, so most cheap ELM327 clones never work on an iPhone.
- **What wins:** the moat is an auditable reasoning engine rather than another "AI explains your code" chat. Hypotheses carry explicit probabilities. Every update is computed by deterministic code from logged evidence. The next test is ranked by expected information gain per unit of cost, risk and skill. Each session produces a hash-chained, replayable report. Muse Spark 1.3 orchestrates and reads images, SAM 3.1 labels and tracks components, and Muse Voice Transcribe runs hands-free mode. That gives a clear, multi-model Meta-tooling story.
- **Rules risk:** the Official Rules text was still not directly readable. Participant summaries of the official participant guide and Discord say only work done during the event counts, the demo must show only that work, the repo must be public, and a three-minute demo video is required. Do not paste in your existing ECU or manual-extractor code; rebuild it with Muse Code and disclose that prior knowledge. Confirm the details in Discord on Day 1.

---

## 1. Platform verdict

### Verdict

| Phase | Build | Why |
|---|---|---|
| **Hackathon (Oct 26 – Nov 4)** | **Native Android app** (Kotlin, Jetpack Compose, CameraX, Classic RFCOMM + BLE) + **web Replay & Report viewer** (React, deployed) + Python orchestrator backend | Classic SPP adapters work only on Android. You already ship Android apps and speak CAN/UDS. Judges get a URL that replays a real Ford session with no hardware. |
| **Product (2027+)** | Android → **iOS via Kotlin Multiplatform** (shared reasoning client, ELM protocol, schemas) → web portal for shops and customer reports | US consumers lean iOS, so iOS is needed for consumer reach. Shops need a browser dashboard and shareable reports. |
| **Not recommended now** | Web-only PWA as the live client; Flutter or React Native rewrites | Web Serial over Bluetooth now exists in Android Chrome, but camera, torch, background behaviour and adapter quirks are far easier to handle natively. A framework switch burns days. |

### Why iOS can't be the hackathon client

- iOS gives third-party apps no access to Bluetooth Classic SPP. They can use BLE adapters, Wi-Fi adapters, or MFi-certified Classic adapters. OBD Auto Doctor states: "Classic Bluetooth dongles do not work with OBD Auto Doctor on iPhone or iPad — except for the OBDLink MX+, vLinker FS, and vLinker MS, which are Made for iPhone certified." [obdautodoctor](https://www.obdautodoctor.com/obd-adapters/)
- Generic BLE adapters also need per-model support. They use different GATT services, so "a generic BLE adapter will not work… automatically" (OBD Auto Doctor). [obdautodoctor](https://www.obdautodoctor.com/obd-adapters/)
- Wi-Fi adapters work on iOS but run their own access point, often at the default `192.168.0.10:35000`. iOS tends to "abandon a network with no internet" (Brimly Auto), so the phone can drop back to cellular. The adapter's AP also has no internet, which hurts a cloud-AI app unless cellular data stays up. On Android, the OpenStreetMap wiki notes "you will not have internet access" over a Wi-Fi dongle unless you configure a static IP. [brimlyauto](https://brimlyauto.com/blog/how-to-use-obd2-scanner-with-iphone/) [apple](https://apps.apple.com/us/app/-/id6759842542) [openstreetmap](https://wiki.openstreetmap.org/wiki/OpenStreetView/OBD2)
- Your demo vehicles are Fords, and your existing tooling is Android-first.

### Why web isn't the live client (but is essential)

- **Web Bluetooth** is BLE-only and Chromium-only; Safari/iOS don't support it.
- **Web Serial over Bluetooth RFCOMM** shipped on desktop Chrome 117 for paired Bluetooth Classic devices (Chrome for Developers blog). It reached Android in Chrome 138, which went stable on June 24, 2025. The Chrome 138 release notes say "Chrome on Android now supports Web Serial API over Bluetooth RFCOMM." One caveat applies: "connected/disconnected events and SerialPort.connected property can't be fully supported on Android over Bluetooth yet." A secondary source gives a Chrome 148 date, but that most likely refers to a separate feature: wired serial on Android, which Chromium said would be "available in 2026Q2 on a limited set of devices." Treat Android Web Serial as real but young.
- **Android WebView** reportedly doesn't support Web Serial, so a hybrid app wrapper can't use it. [github](https://github.com/Temel00/emelbros-app/issues/219)
- WebKit's position on Web Serial is negative, so iOS browsers won't get it. [google](https://groups.google.com/a/chromium.org/g/blink-dev/c/BqUGCcurReE)
- A PWA is therefore technically possible on Android Chrome and desktop Chrome. It's a nice "no-install" stretch goal, but not the primary build.
- What web is essential for: the judge path. A public **Replay mode** streams a recorded real session (raw ELM transcript, frames, voice) through the same backend and reasoning engine. A **Report viewer** renders the reproducible investigation report.

### Cross-platform options compared

| Option | Classic SPP | BLE | Camera/torch | Fit for you in 9 days |
|---|---|---|---|---|
| Native Kotlin (Android) | Full (`BluetoothSocket` RFCOMM) | Full | CameraX: preview, ImageAnalysis frames, video, torch | **Best** |
| Kotlin Multiplatform + Compose MP | Android-only (expect/actual) | Kable (Android/iOS) | Platform-specific | Best long-term path. Compose MP for iOS has been stable since 1.8.0 (May 6, 2025). JetBrains reports "over 96% of teams using Compose Multiplatform on iOS report no major performance concerns." Kable gives shared BLE. |
| Flutter | Android-only plugins | flutter_blue_plus | Good | New language and toolchain; no Classic advantage on iOS |
| React Native/Expo | react-native-bluetooth-classic (Android) | react-native-ble-plx | Good | Same Classic limit; JS bridge for high-rate frames |
| Native Swift (iOS) | MFi only (External Accessory) | Core Bluetooth | AVFoundation, excellent | Post-hackathon |

**Assumption:** the framework capability claims for Flutter and React Native come from general knowledge of those plugin ecosystems; they weren't re-verified in this research.

### Market context

- **US vs global share:** StatCounter (Jan–Sep 2026, via Mobiloud) puts iOS at 59.8% of US mobile web traffic, while Android holds 68.5% worldwide. [mobiloud](https://www.mobiloud.com/blog/android-vs-ios-market-share/) Counterpoint puts iOS at 51% of US smartphone *sales* in Q2 2026. [gsmchina](https://gsmchina.com/smartphone-os-market-share-statistics-17206/) A US consumer product therefore needs iOS eventually; a global or technician product is Android-heavy.
- **Pro technicians:** they mostly live on Android-based scan tablets (Autel MaxiSys, Launch X431, Topdon) and Snap-on platforms, plus Windows laptops for OEM software. *This is general industry knowledge, not verified in this research cycle.* An Android tablet build is a natural second target.
- **Distribution:** Torque Pro is Android-only and depends on Classic. FIXD and BlueDriver bundle their own adapters so they control iOS compatibility. BlueDriver Pro sells for $99.95 on BlueDriver's US store (marked down from $119.95), with "Zero Subscription Fees or Add-ons".

---

## 2. Hackathon rules and constraints

**Status (as of Oct 26, ~1 PM CT):**
- Both official event pages still say "Official Rules will be published before the Hackathon begins on October 26, 2026." [meta](https://dev.meta.ai/events/global-hackathon) [cerebralvalley](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
- The participant guide (`/details`) and waiver pages exist, but neither I nor the subagent could open them.
- Everything below marked **secondhand** comes from participants who read the guide or Discord, or from a blog that quoted the pre-event waiver. Verify it in Discord `#rules` and `#faqs` today.

| Topic | Best available info | Source quality | Impact on build |
|---|---|---|---|
| **Pre-existing code** | "Only work done during the event counts; the demo must show only that work." Discord `#rules`: "anything built from 26 Oct to 4 Nov counts… or the project is disqualified." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (participant README, read Oct 26) | **Do not import your existing ECU/UDS or manual-extractor code.** Re-implement with Muse Code during the event and say in the README that you have prior domain experience. Open-source libraries are reportedly fine if you follow their licences. |
| **Non-Meta models** | "Must use Meta models via Meta Model API or the open-weight models." Nothing found explicitly bans other models alongside them. [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Make every AI call a Meta model. Use no third-party LLMs, so there is nothing to dispute. |
| **Team size** | Indie: "solo or in teams of up to four people" [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Secondhand (pre-event blog) | Solo is fine |
| **Demo video** | "Three-minute demo video required." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Script to 2:45 |
| **Submission fields** | "public repo, a working demo video link and every team member added" [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (Discord) | Public GitHub repo from Day 1, with secrets excluded |
| **Judges running it** | Not stated. The official page says Indie is "Judged on creativity, technical ambition, and whether it works." [meta](https://dev.meta.ai/events/global-hackathon) | Primary (event page) | Provide the web Replay URL + APK + simulator so "whether it works" can be checked without hardware |
| **Judging weights** | **Conflict:** the pre-event blog reading of the waiver gives Impact 30 / Meta tooling 25 / Demo 25 / Technical depth 20. A participant's Oct 26 reading of the guide says "equal weight, 0 to 10" across the same four criteria. [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) [github](https://github.com/sholaola23/meta-hackathon-2026) | Both secondhand | The four criteria are the same either way. Optimise for all four, and weight the demo and the Meta-tooling story heavily. |
| **Anti-projects** | "AI Job Application Screener" and "Simple Image Analyzers" [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Don't let the camera feature look like a "simple image analyzer". Always tie vision to the diagnostic state. |
| **Prizes** | $1M pool across two tracks (primary). "Anticipated" Indie prizes of $150k/$100k/$75k/$50k/$25k, and bonus prizes up to $50k such as a community-vote award [meta](https://dev.meta.ai/events/global-hackathon) [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Primary for the pool; the rest secondhand | No automotive or hardware track found |
| **IP** | Entrant keeps ownership; organizers get a "perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence" [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Secondhand (pre-event waiver) | Keep proprietary future assets (verified-fix DB, adapter firmware tricks) out of the submission |
| **Credits/billing** | $150 Meta credits; the claim form "wasn't out yet on 26 Oct". A spend-limit notification "does **not** stop billing." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (Discord) | Add a client-side budget guard to the backend |
| **Data/training** | Contributor mode trains Meta models on your content. **Muse Code subscriptions default to Contributor mode.** [meta](https://dev.meta.ai/docs/muse-code/subscriptions) [meta](https://dev.meta.ai/docs/pricing-rate-limits) | Primary (Meta docs) | Use `muse-spark-1.3` (Standard) in the app. Switch Muse Code's Contributor mode off if you don't want your repo used for training. |

**Kickoff schedule (secondhand):**
- Agents 101/102 workshops on Oct 27–28 at 12 PM ET [github](https://github.com/sholaola23/meta-hackathon-2026)
- Fireworks session on Oct 30 [github](https://github.com/sholaola23/meta-hackathon-2026)
- Discord office hours on weekdays, 10–11 AM ET [github](https://github.com/sholaola23/meta-hackathon-2026)

---

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
| Muse Glimmer / Spark Open | Post-hackathon on-device/offline explanations | Apache 2.0 open weights (Glimmer) [meta](https://dev.meta.ai/docs/overview) |

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

---

## 9. Hackathon execution

The deadline is **Wed Nov 4, 9:00 PM ET (8:00 PM CT)**. [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) Plan to submit by **3 PM CT Nov 4**.

| Day | Date | Goals | Exit criteria |
|---|---|---|---|
| D1 | Mon Oct 26 (PM) | Confirm rules in Discord (pre-existing code, video, weights). Create a public repo. `muse init`, then paste the build spec into AGENTS.md + `docs/SPEC.md`. Turn Muse Code Contributor mode off. API key in `.env`. Spike: one Spark image call + one SAM call on an engine-bay photo. | Both API calls work; repo public; rules notes in `docs/RULES.md` |
| D2 | Tue Oct 27 | Milestones M1–M2: schemas, event store, ELM driver + simulator + Classic transport. **Record a baseline session on Ford #1** (healthy, warm idle, 2,500 rpm hold). Benchmark PID rate. | Phone reads VIN, DTCs and live PIDs from the Ford; simulator tests pass |
| D3 | Wed Oct 28 | M3: reasoning engine + knowledge pack (6 fault families), planner, explanation trace, replay determinism test | Unit tests: planted-fault fixtures converge to the correct top hypothesis |
| **D4 GATE** | **Thu Oct 29** | **Ugly end-to-end:** live Ford → backend → hypotheses update → next-test card on the phone → JSON report. | **If not working by 9 PM, cut** vision tracking, voice and Mode 22, and focus on OBD + photo + engine. |
| D5 | Fri Oct 30 | M4: camera keyframes → Spark labels → SAM masks; instrument reading; observation capture | Overlay on a real engine bay; multimeter photo becomes Evidence |
| D6 | Sat Oct 31 | **Planted-fault recording day** (daylight, driveway, cool engine): 3–4 scenarios × 2 vehicles. Expected-vs-measured cards; Mode 06. | `.mmrec` bundles saved; each converges correctly |
| D7 | Sun Nov 1 | M5: experience-adaptive UX (L1/L3), report generator, web Replay + Report viewer deployed | Public URL replays a real session end-to-end |
| D8 | Mon Nov 2 | M6: voice hands-free, Wonder report review, safety gates, judge mode, budget guard, polish. Shoot demo footage. | All acceptance tests green; footage captured |
| D9 | Tue Nov 3 | Edit video, README, architecture diagram, submission text. **Feature freeze 6 PM.** | Draft submission saved |
| D10 | Wed Nov 4 | Buffer, bug fixes only, final smoke test of the public URL + APK, submit by 3 PM CT | Submitted |

**Cut list, in order:**
1. Mode 22 enhanced Ford DIDs
2. BLE and Wi-Fi transports (keep Classic)
3. Wonder review pass
4. Voice
5. SAM video tracking (keep single-image SAM)
6. Upload of PDFs

**Never cut:** the simulator, replay, the event log/report, the explanation trace or the safety gates.

### Planted-fault demo scenarios (on your Fords)

| Scenario | How | Expected signature | Safety / reversibility |
|---|---|---|---|
| **MAF unplugged** | Engine off, unplug MAF connector, start, idle | P0102 (may need some run time); MAF PID ≈ 0 or default; the PCM falls back to a speed-density estimate | **Safe, fully reversible.** Plug back in engine-off; clear the code via the gated flow (shows the feature). Idle only, no driving. |
| **IAT unplugged** | Engine off, unplug IAT (sometimes integrated in the MAF; check your engine) | IAT PID reads ≈ −40 °C; P0113 | Safe, reversible. Good "expected vs measured": ambient vs −40. |
| **Unmetered air leak** | Engine off, cool; loosen the intake boot clamp *after* the MAF, or pull a small PCV/vacuum hose (**not** the brake booster line) | Idle STFT+LTFT climbs above +10–15%, normalises at 2,500 rpm; P0171/P0174 need more run time | Safe at idle. Don't drive. Keep debris out of the intake. Re-secure the clamp. |
| **Fuel pump fuse/relay removed** | Engine off, remove the fuel pump fuse per the owner's manual | Crank-no-start; no trims; the next test becomes "listen for pump prime at key-on" | Safe if cranking is limited to a few seconds. Reversible. Avoid on vehicles with inertia-switch quirks. |
| **Coil connector unplugged (one cylinder)** | Engine off, unplug one COP connector, start | Mode 06 misfire counts on that cylinder; P030X; rough idle | **Caution:** unburnt fuel can overheat the catalyst. Limit to 60 s idle, no load, then restore. Keep hands clear of the running engine. |
| **ECT unplugged** | Engine off | ECT reads −40 °C or high default; fans may run full | Safe, short duration; restore before driving. |
| **Avoid** | Disconnecting the battery while running; SRS connectors; fuel lines; anything while driving | n/a | Unsafe or damaging |

**Assumption:** the exact codes and PCM fallback behaviour vary by Ford engine and year. Record what *your* vehicles do on D6; that recording becomes the ground truth.

### Demo video script (2:45)

**0:00–0:15 Hook.** Real driveway, Ford, CEL on. Voice-over: "Every app can read a code. A master mechanic tells you what to *test* next — and why. That's what we built with Meta's models."

**0:15–0:35 Onboarding.** Pick "I've never opened the hood" and tools "none". Pair the ELM327. VIN decoded; one recall flagged "may apply".

**0:35–1:20 Live investigation.**
- The phone reads P0171 plus freeze frame. Hypothesis bars: vacuum leak 34%, MAF dirty 22%, fuel delivery 18%…
- The next-test card says "Hold 2,500 rpm for 10 s", with the expected result shown.
- Trims drop from +19% to +4%. The bars move, and a trace line appears in plain English.

**1:20–1:50 Camera.**
- Point at the engine bay: SAM outlines the "air intake duct" and "air filter housing".
- Next test: "Look at the rubber boot between filter and throttle." The camera clip shows the loose clamp, and Spark flags "gap at clamp".
- Vacuum leak 87%. Stop rule hit.

**1:50–2:10 Switch to Pro mode, same session.** Raw PIDs, Mode 06 table, expected-vs-measured cards, a hands-free voice note: "log clamp re-secured, trims plus two."

**2:10–2:30 Report.** Open the report URL: timeline, probability history chart, evidence hashes, "Replay this investigation" button. Show it replaying in the browser with no hardware.

**2:30–2:45 Meta stack slide + close.** "Muse Spark reasons and sees, SAM 3.1 finds parts, Voice Transcribe keeps hands free, Muse Code built it. Every conclusion is auditable."

### Submission checklist

- [ ] Public GitHub repo; `.env` excluded; README covering what it is, the architecture, the Meta model map, how to run the simulator and replay, a safety statement, and a statement of prior experience / what was built during the event
- [ ] Demo video ≤ 3:00, public link
- [ ] Deployed web Replay & Report URL, plus 2–3 bundled real sessions
- [ ] Signed release APK attached to a GitHub Release, with a simulator mode that works without an adapter
- [ ] `docs/JUDGES.md`: a 60-second path (open URL → pick "P0171 Ford F-150" → play)
- [ ] Every team member added (solo); track: Indie; theme: "Build for Your World" (and "Push the Frontier" for the physical-system angle)
- [ ] Cost guard verified; API keys rotated after recording

---

## 10. Post-hackathon roadmap

| Quarter | Product | Notes |
|---|---|---|
| Q1 2027 | Android public beta; adapter compatibility list; real Mode 22 Ford read-only pack; offline report export | Recommend OBDLink MX+ (Classic + MFi iOS), vLinker FS/MS, and Veepeak BLE+ for iOS (marketed as a genuine ELM327 v2.2, BLE) [obdadvisor](https://obdadvisor.com/elm327/) |
| Q2 2027 | **iOS** via KMP shared core (protocol, engine client, schemas) + SwiftUI or Compose MP UI; BLE/Wi-Fi/MFi only | Kable for BLE; handle local-network permission for Wi-Fi |
| Q2–Q3 2027 | **Shop tier:** multi-vehicle, customer-facing report links, technician hands-free, integrations (shop management systems via CSV/API; specific vendor APIs to be evaluated) | Web portal |
| Q3 2027+ | **Verified-fix knowledge base:** after each session, ask "what fixed it?", then a confirmed-outcome dataset that recalibrates likelihoods (BlueDriver's moat, but tied to evidence patterns rather than codes alone) | Requires consent and anonymization |
| Later | Glimmer on-device mode; scope/meter BLE integrations; oil/fluid image analysis | |

**Pricing:**
- FIXD Premium is $99.99 billed annually (FIXD support page) and bundles the AI Mechanic. BlueDriver sells hardware with no subscription. OBDLink and Carly pricing **weren't verified in this research.**
- Suggested model: free tier (codes, 1 investigation/month); Pro consumer about $6–9/mo; Technician about $25–40/mo per seat, with unlimited sessions and customer reports.
- Unit economics: at ≈ $1.50–2.00 model cost per full session (estimate), consumer tiers need session caps or cached and cheaper paths (Contributor mode is **not** appropriate for customer data).

**Risks:**
- liability for consumer repair advice;
- clone-adapter support burden;
- incumbents (FIXD, BlueDriver, Autel) adding similar LLM features;
- Meta API pricing or model changes;
- the organizers' perpetual licence to the hackathon submission (keep future proprietary data out of it);
- possible future access restrictions on OEM data and gateways (e.g., secure gateway modules on newer vehicles).

---

## 11. Muse Code build spec (copy everything in this block into `AGENTS.md` + `docs/SPEC.md`)

````markdown
# AGENTS.md — Master Mechanic Copilot ("MMC")

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
````

---

## Caveats

- **Hackathon rules are unverified.** No primary Official Rules text was readable. The judging weights conflict between sources (30/25/25/20 vs equal), and the pre-existing-code policy comes from a participant's summary of the participant guide and Discord. Confirm all of this in Discord before relying on it.
- **Meta API details:**
  - **Verified from Meta's docs:** prices, rate limits, model IDs, SAM's one-concept-per-request rule and video format limits, Voice Transcribe's realtime endpoint and lack of TTS, Muse Code's AGENTS.md/`/plan` behaviour and Contributor-mode default.
  - **Not verified:** the "16 tracked objects" SAM limit, image token costs, and temperature/seed support.
- **Web Serial on Android:** Bluetooth RFCOMM support shipped in Chrome 138 (stable June 24, 2025). The Chrome 148 date in one secondary source most likely refers to wired serial on Android, which Chromium said would be available "on a limited set of devices." Android WebView is reportedly unsupported.
- **Engineering estimates:** cost-per-session, PID throughput and latency targets are estimates. So are the diagnostic rules of thumb (trim thresholds, MAF formula, charging voltages), which are generic engineering heuristics; override them with manual specs.
- **Planted faults:** the exact codes and timing will vary by Ford engine and year. The coil-unplug scenario carries real catalyst-heat risk if prolonged.
- **Competitor pricing:** the competitor list and FIXD/BlueDriver pricing come from review sites and app listings. Pricing for OBDLink, Carly and the pro tools wasn't verified.