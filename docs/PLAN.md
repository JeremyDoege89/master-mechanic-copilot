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

## Where we are (Oct 28 AM)

| Item | State |
|---|---|
| Toolchain on dreamz-code (`setup-dreamz-code.sh`) | Done Oct 27: Muse Code, uv/Python 3.12, Node 24, JDK 21, Android SDK + CLI, ffmpeg, gh |
| GitHub CLI login | Done (`JeremyDoege89`) |
| Meta Model API key (`meta-set-key`, `meta-smoke`) | **Not done** |
| Muse Code sign-in + Contributor mode off | **Not done** |
| Rules confirmed in Discord | **Not done** (`docs/RULES.md` is all secondhand) |
| Public repo | **Not created** (local folder `~/mmc` only, not yet a git repo) |
| Code | None yet. All code is written during the event by Muse Code. |

We are two days behind the research schedule: its D1 (setup) and D2 (event store + ELM driver) haven't happened.
The schedule below absorbs that by cutting scope up front instead of hoping to catch up.

## Scope changes from the slip (decided now, not at the gate)

- **Cut before starting:** Mode 22 Ford DIDs, BLE and Wi-Fi transports (Classic RFCOMM + Sim + Replay only).
- **Stretch only (build if ahead on Sun Nov 1):** Muse Wonder report review, SAM *video* tracking (single-image SAM
  stays), PDF manual upload.
- **Never cut:** simulator, replay, event log + hash chain, report, explanation trace, safety gates, allowlist.

## Schedule

Milestone numbers match `AGENTS.md`. Its "(D1)", "(D2)"... labels are from the old schedule; the dates here win.

| Day | Date | Goals | Exit criteria (by 9 PM CT) |
|---|---|---|---|
| **1** | **Wed Oct 28** | **Setup sprint (morning):** owner tasks 1–6 below. Create the public repo from `~/mmc`. Spike one Spark image call + one SAM call on an engine-bay photo. **Then M0 + M1:** scaffold, schemas, `.env.example`, CI; event store with hash chain and blobs. Start M2 sim-only: allowlist, parsers, `Elm327Sim`. | Both API calls work; repo public; `docs/RULES.md` rows confirmed; M0/M1 acceptance green; `ElmAllowlistTest` green |
| **2** | **Thu Oct 29** | **M2 on the car:** Classic transport, init + fingerprint, VIN/DTCs/PIDs. **Record a baseline `.mmrec` on Ford #1** (healthy, warm idle, 2,500 rpm hold). Benchmark PID rate. **Start M3:** `bayes.py`, `physics.py`, `ford_gen_v1.yaml` (6 fault families). | Phone reads VIN, DTCs and live PIDs from the Ford; baseline bundle saved; Bayes update unit tests pass |
| **3** | **Fri Oct 30** | **Finish M3:** planner (EIG/cost, filters, stop rule), evaluator, trace, replay determinism test. **M4 GATE:** live Ford → backend → state over WS → next-test card → JSON report. | **GATE.** Sim scenarios converge (true hypothesis #1, p ≥ 0.8 in ≤ 4 tests). If the end-to-end isn't working by 9 PM, also cut voice and all SAM work except a single still-image overlay, and spend Sat on the core. |
| **4** | **Sat Oct 31** | **Planted-fault recording day** (daylight, driveway, cool engine), using the safety notes in `docs/SPEC.md` §8–9: MAF unplugged, IAT unplugged, unmetered air leak, coil unplugged (≤ 60 s idle) on 2 vehicles. Take engine-bay photos and multimeter photos for M5. **M5 vision** (keyframe selector, Spark component/anomaly JSON, SAM masks, instrument reading) built against those photos. | ≥ 4 `.mmrec` bundles saved, each converging correctly in replay; overlay on a real engine-bay photo |
| **5** | **Sun Nov 1** | **M6:** L1/L2/L3 styles, tools questionnaire, safety gate checklists, report HTML, web Replay + Report viewer **deployed** on a cloud host (not the home server). Budget guard. Stretch items if ahead. | Public URL replays a real session with zero API spend (`REPLAY_USE_CACHED_LLM=true`); L1 vs L3 instructions differ on the same session |
| **6** | **Mon Nov 2** | **M7:** voice proxy + "log …"/"next test" commands, judge mode, `docs/JUDGES.md`, polish. **Shoot demo footage** (script in `docs/research/` §9). Signed release APK with simulator mode. **Feature freeze 9 PM.** | All acceptance tests green; footage captured; APK on a GitHub Release |
| **7** | **Tue Nov 3** | Edit the video to ≤ 2:45. README (what it is, architecture diagram, Meta model map, how to run sim/replay, safety statement, prior-experience statement). Draft submission. Bug fixes only. | Draft submission saved; video uploaded (public link) |
| **8** | **Wed Nov 4** | Buffer. Fresh-browser smoke test of the public URL and a fresh-install test of the APK. Rotate API keys. **Submit by 3 PM CT.** | Submitted |

Reality check for Muse Code: each milestone starts with `/plan`, the plan is saved to `.agents/plans/`, and you
approve it before implementation. Keep android/, backend/ and web/ on separate subagents only when they don't
touch the same schemas that day.

## Owner tasks (only you can do these)

Day 1 morning, in this order:

1. [ ] Discord `#rules` / `#faqs`: confirm the pre-existing-code rule, the 3-minute video, judging weights, and whether
       judges need to run it. Record answers in `docs/RULES.md` with date and link.
2. [ ] Claim the $150 Meta credits (the form wasn't out on Oct 26; check again).
3. [ ] `meta-set-key`, open a new terminal, then `meta-smoke`.
4. [ ] `muse` → sign in → trust `~/mmc` → **turn Contributor mode off** (subscriptions default to it on).
5. [ ] Create the public GitHub repo (name it; the folder is `~/mmc`) and push the docs. Check `.env` is gitignored first.
6. [ ] Set a spend alert in the Meta console; remember it **doesn't stop billing**, so the backend budget guard matters.

Later:

7. [ ] Thu Oct 29: car time for the baseline recording (Ford #1, phone paired to the ELM327).
8. [ ] Sat Oct 31: planted-fault recording session, 2 vehicles, daylight.
9. [ ] Sun Nov 1: pick and pay for the cloud host for the judging period.
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
- [ ] Deployed Replay & Report URL with a 60-second judge path in `docs/JUDGES.md`
- [ ] Signed release APK with simulator mode on a GitHub Release

## Risks to watch

| Risk | Early warning | Response |
|---|---|---|
| ELM327 clone misbehaves on the Ford | Init fails or `?` on core commands Thu | Fall back to the limited-clone profile; buy/borrow an OBDLink if needed |
| Gate slips Fri | No end-to-end by 9 PM Oct 30 | Apply the gate cuts above; Sat becomes core + recording only |
| API credits run out | Budget guard > $80 by Sun | Replay serves cached outputs only; stop live keyframe analysis in dev |
| Rules forbid something in the plan | Discord answer on Day 1 | Change the plan the same day and log it in `docs/DECISIONS.md` |
| Weather on Sat | Rain forecast | Move recording to Fri afternoon or Sun morning |

## Change log

- **2026-10-28:** Plan created from the 2026-10-26 research. Re-baselined to 8 days (setup slipped 2 days); cut
  Mode 22, BLE and Wi-Fi up front; moved Wonder review, SAM video and PDF upload to stretch.
