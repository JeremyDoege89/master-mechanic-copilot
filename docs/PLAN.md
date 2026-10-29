# Master Mechanic Copilot — hackathon plan

**Event:** Meta AI Developer Hackathon, Indie track (solo). **Deadline:** Wed Nov 4, 9:00 PM ET (8:00 PM CT).
**Our submit target:** Wed Nov 4, 3:00 PM CT. **Re-baselined:** Wed Oct 28 (7 working days left, including today).

What we're building, why, and how are in `AGENTS.md` (build rules, schemas, API contracts, milestones) and
`docs/SPEC.md` (product and engineering detail). The original research, including the first 10-day schedule, is
in `docs/research/`. This file is the schedule and the checklist; update it as things land.

## Pitch (one line)

A master mechanic in your pocket that shows its work: live OBD + camera + voice feed a deterministic Bayesian
engine that ranks the next best test and produces a hash-chained, replayable report. Muse Spark reasons and sees,
SAM 3.1 finds parts, Voice Transcribe keeps hands free, and Muse Code built it.

## Where we are (Oct 29 early AM)

| Item | State |
|---|---|
| Toolchain on dreamz-code (`setup-dreamz-code.sh`) | Done Oct 27: Muse Code, uv/Python 3.12, Node 24, JDK 21, Android SDK + CLI, ffmpeg, gh |
| GitHub CLI login | Done (`JeremyDoege89`) |
| Meta Model API key (`meta-set-key`, `meta-smoke`) | Done; `meta-smoke` passed Oct 28 |
| Muse Code settings (`~/.config/muse/settings.json`) | Done Oct 28: Contributor mode off; Claude Code personal rules not imported, so Muse follows `AGENTS.md` only |
| Muse Code sign-in + trusting `~/mmc` | Done; headless `muse exec` works (driven via `.agents/muse-run.sh`) |
| Rules confirmed in Discord | Done by owner Oct 28 |
| Repo | Public on GitHub (`JeremyDoege89/master-mechanic-copilot`) |
| `.env` | Created from `.env.example` without the key (the key comes from `~/.config/meta/model-api.env` via your shell) |
| Phone (moto g, adb over Tailscale) | Connected Oct 28 |
| Code | Written by Muse Code, driven headlessly (`.agents/muse-run.sh`; plans in `.agents/plans/`). **Done (Oct 29 midday): M0-M3, M4 built (Ford check pending), M5 vision, upload mode, Spark Open, M6, M6B, M7 (voice proxy + grammar, judge polish, README, release script, acceptance sweep).** Live voice transcription verified with real speech. Judge site live on Modal. GitHub CI green again (was red 05:18-11:18 UTC: runner lacked ffmpeg/keys; `scripts/ci_parity.sh` now mirrors CI). **Running: fixes for the 14 M7 review findings** (voice persistence, Android voice wiring, sweep false-PASS). Waiting on owner: Ford baseline/M4 check, M5 photos, licence + keystore + build-story decisions. |
| Model routing | Checked Oct 28 against `GET /v1/models`. **Product:** Spark 1.3 for reasoning, vision, instrument reading and PDF/video extraction; SAM 3.1 for masks and tracking; Voice Transcribe for hands-free; Wonder once per report for review; Muse Image kept out of diagnosis. **Muse Code:** `*-plan` and `*-review` jobs run on `muse-wonder-1.0`, builds and fixes on `muse-spark-1.3` (high), set in `.agents/muse-run.sh`. Standard IDs only, never `-contributor`. |
| CI | GitHub runs backend tests + replay check + web build; Android builds and tests run on dreamz-code (see DECISIONS) |

We are two days behind the research schedule: its D1 (setup) and D2 (event store + ELM driver) haven't happened.
We're making that up with three all-nighters, not with cuts. **Full scope stays in.**

## Scope

Everything in `AGENTS.md` and `docs/SPEC.md` ships: all four transports (Classic, BLE, Wi-Fi, Sim + Replay), Mode 22
read-only Ford DIDs, camera + SAM single-image *and* video tracking, instrument reading, PDF manual and scan-tool
upload, voice hands-free, Muse Wonder report review, L1–L3 UX, web Replay + Report, judge mode.

- **Mode 22 needs a genuine ELM327 or OBDLink adapter.** If the adapter on hand is a clone, Mode 22 is built and
  tested against the simulator's GENUINE personality, and the demo shows it there.
- **Fallback order, only if a day's exit criteria are missed** (not planned cuts): Mode 22 → Wi-Fi → BLE → Wonder →
  voice → SAM video → PDF upload. Never cut: simulator, replay, event log + hash chain, report, explanation trace,
  safety gates, allowlist.

- **Sponsor credits (Oct 28): Modal and Baseten.** Modal hosts the backend + web Replay/Report for judging (compute only).
  Baseten gives $100 and up to 2M tokens/min on **Muse Spark Open** (Meta's open-weight model) for this hackathon:
  OpenAI-compatible Chat Completions at `https://inference.baseten.co/v1`, model exactly
  `meta-models/muse-spark-open-1026` (the key works with nothing else), always reasons, takes images and audio.
  Uses, all text/observations only (never probabilities): (1) **engine-sound evidence**: a short audio clip becomes
  typed observations (misfire tick, belt squeal, vacuum hiss, knock), something Spark 1.3's audio support doesn't cover;
  (2) fallback narration when the Meta budget guard trips or the API is down; (3) the "open weights" part of the
  Meta-tooling story. Stretch after M7, spike earlier because it's cheap.

## Sleep and safety rules for the push

- **All-nighters: Wed Oct 28, Thu Oct 29, Mon Nov 2.** Sleep: Fri, Sat, Sun and Tue nights.
- **No planted faults or hands near a running engine on a day after an all-nighter.** Thursday's baseline recording
  is read-only (plug in, key on, idle outdoors) and is fine. Planted faults are Saturday, after Friday's sleep.
- **Demo footage is Monday daytime, after Sunday's sleep.** Submission is Wednesday, after Tuesday's sleep.
- **Before each sleep night, queue a well-specified milestone for Muse** (plan already approved) so it keeps working.

## Schedule

Milestone numbers match `AGENTS.md`. Its "(D1)", "(D2)"... labels are from the old schedule; the dates here win.

| Day | Date | Goals | Exit criteria (by 9 PM CT) |
|---|---|---|---|
| **1** | **Wed Oct 28 → all night** | **Setup (morning):** owner tasks 1–6. Spike one Spark image call + one SAM call on an engine-bay photo. **M0 + M1:** scaffold, schemas, CI; event store with hash chain and blobs. **Overnight, M2 complete in the sim:** allowlist, parsers, fingerprinting, `Elm327Sim` (3 personalities, all scenarios), Classic, BLE, Wi-Fi and Replay transports, Mode 22 allowlist. **Start M3:** `bayes.py`, `physics.py`, KP packs. | Both API calls work; repo public; `docs/RULES.md` confirmed; M0/M1/M2-sim acceptance green |
| **2** | **Thu Oct 29 → all night** | **Daytime, read-only car work:** M2 on Ford #1 over Classic (and BLE/Wi-Fi if you have those adapters). Record the baseline `.mmrec` (healthy, warm idle, 2,500 rpm hold). Benchmark PID rate. Test Mode 22 reads if the adapter is genuine. **Finish M3:** planner, evaluator, trace, stop rule, replay determinism. **M4 GATE pulled forward:** live Ford → backend → WS → next-test card → JSON report. **Overnight, M5 vision:** keyframe selector, Spark component/anomaly JSON, SAM image masks, SAM video tracking, instrument reading. | **GATE by Thu midnight.** Sim scenarios converge (true hypothesis #1, p ≥ 0.8 in ≤ 4 tests); end-to-end works on the Ford |
| **3** | **Fri Oct 30 → sleep** | **Finish M5** against real engine-bay and multimeter photos. **Upload mode:** PDF manuals (spec extraction with citations), scan-tool PDFs/CSVs, photo/video upload. **Wonder review pass.** Queue M6 groundwork for Muse overnight. | M5 acceptance (≥ 7/10 overlays, 8/10 meter readings); a PDF spec lands as a cited `SpecRange` |
| **4** | **Sat Oct 31 → sleep** | **Planted-fault recording day** (rested, daylight, driveway, cool engine), using the safety notes in `docs/SPEC.md` §8–9: MAF unplugged, IAT unplugged, unmetered air leak, coil unplugged (≤ 60 s idle), ECT unplugged on 2 vehicles. Capture engine-bay clips for SAM video, plus demo B-roll. **M6 starts:** L1/L2/L3 styles, tools questionnaire, safety gate checklists. | ≥ 5 `.mmrec` bundles saved, each converging correctly in replay |
| **5** | **Sun Nov 1 → sleep** | **Finish M6:** report HTML/PDF, web Replay + Report viewer **deployed** on a cloud host. Budget guard. **M7:** voice proxy + "log …"/"next test" commands, judge mode, `docs/JUDGES.md`. | Public URL replays a real session with zero API spend; L1 vs L3 differ on the same session; voice "next test" works |
| **6** | **Mon Nov 2 → all night** | **Daytime: shoot demo footage** (rested; script in `docs/research/` §9). **Overnight:** every acceptance test green, polish, signed release APK with simulator mode, start the video edit. | All acceptance tests green; footage captured; APK on a GitHub Release |
| **7** | **Tue Nov 3 → sleep** | **Feature freeze at noon.** Finish the video (≤ 2:45). README (architecture diagram, Meta model map, how to run sim/replay, safety statement, prior-experience statement). Draft submission. Bug fixes only. | Draft submission saved; video uploaded (public link) |
| **8** | **Wed Nov 4** | Buffer. Fresh-browser smoke test of the public URL and a fresh-install test of the APK. Rotate API keys. **Submit by 3 PM CT.** | Submitted |

Reality check for Muse Code: each milestone starts with `/plan`, the plan is saved to `.agents/plans/`, and you
approve it before implementation. Keep android/, backend/ and web/ on separate subagents only when they don't
touch the same schemas that day.

## Owner tasks (only you can do these)

Day 1 morning, in this order:

1. [x] Discord `#rules` / `#faqs`: confirm the pre-existing-code rule, the 3-minute video, judging weights, and whether
       judges need to run it. Record answers in `docs/RULES.md` with date and link.
2. [x] Claim the $150 Meta credits (the form wasn't out on Oct 26; check again).
3. [x] `meta-set-key`, open a new terminal, then `meta-smoke`.
4. [x] `muse login`, then `cd ~/mmc && muse` and trust the folder when asked. Contributor mode is already off
       in local settings; check that your account/subscription page agrees (subscriptions default it on).
5. [x] Publish the repo: `cd ~/mmc && gh repo create JeremyDoege89/master-mechanic-copilot --public --source . --remote origin --push`
       (`.env` is already gitignored and the first commit was scanned for secrets).
6. [x] Set a spend alert in the Meta console; remember it **doesn't stop billing**, so the backend budget guard matters.

Later:

7. [ ] Thu Oct 29: car time for the baseline recording (Ford #1, phone paired to the ELM327).
8. [ ] Sat Oct 31: planted-fault recording session, 2 vehicles, daylight.
9. [x] Cloud host: **Modal**, live Oct 29 at https://jeremydoege89--mmc-fastapi-app.us-east.modal.run (replay-only,
       $0 public spend; smoke test passes). Original note: cloud host for the judging period is **Modal** (sponsor credits you were awarded). Before then, put the
       Modal token in place: `uv tool install modal && modal token set --token-id <id> --token-secret <secret>`.
       Baseten key (also awarded): save to `~/.config/baseten/api.env` as `BASETEN_API_KEY=...` (chmod 600).
10. [ ] Mon Nov 2: demo footage (driveway, CEL on, voice-over).
11. [ ] Wed Nov 4: fill in the submission form (Indie track; themes "Build for Your World" + "Push the Frontier").

## Rules guardrails (until Discord confirms otherwise)

- **Only work done Oct 26 – Nov 4 counts.** Don't copy anything from GwDiag, xtool-re, PSI, BlueLink Bridge or other
  pre-event projects into this repo. Muse Code re-implements from the spec, and the README states prior domain
  experience.
- **Every AI call is a Meta model** through `https://api.meta.ai/v1`, Standard-mode IDs only (`muse-spark-1.3`,
  never `-contributor`).
- **Keep future proprietary assets out** (verified-fix data, adapter tricks): organizers get a perpetual licence to
  the submission.

## Demo assets checklist

- [ ] 4+ real `.mmrec` bundles (healthy, MAF unplugged, vacuum leak, coil unplugged), also used as CI fixtures
- [ ] 10 engine-bay photos + 10 multimeter photos (M5 acceptance set)
- [ ] Screen recording of the D3 gate run
- [ ] Demo video ≤ 3:00 (target 2:45), public link
- [x] Deployed Replay & Report URL with a 60-second judge path in `docs/JUDGES.md` (live; JUDGES.md written, final pass in M7)
- [ ] Signed release APK with simulator mode on a GitHub Release

## Risks to watch

| Risk | Early warning | Response |
|---|---|---|
| ELM327 clone misbehaves on the Ford | Init fails or `?` on core commands Thu | Fall back to the limited-clone profile; buy/borrow an OBDLink if needed |
| Gate slips | No end-to-end by Thu midnight | Fri goes to the core first; take the fallback order only for what's still unbuilt Sun night |
| Fatigue | Mistakes piling up, or a car task on a no-sleep day | Follow the sleep and safety rules above; let Muse grind while you sleep |
| API credits run out | Budget guard > $80 by Sun | Replay serves cached outputs only; stop live keyframe analysis in dev |
| Rules forbid something in the plan | Discord answer on Day 1 | Change the plan the same day and log it in `docs/DECISIONS.md` |
| Weather on Sat | Rain forecast | Move recording to Fri afternoon or Sun morning |

## Change log

- **2026-10-28 (later):** Full scope restored at the owner's call: three all-nighters (Wed, Thu, Mon) instead of
  cuts. The gate moved up to Thu, planted faults stay on rested Saturday, and the old cuts became a fallback order only.

- **2026-10-28:** Plan created from the 2026-10-26 research. Re-baselined to 8 days (setup slipped 2 days); cut
  Mode 22, BLE and Wi-Fi up front; moved Wonder review, SAM video and PDF upload to stretch.
