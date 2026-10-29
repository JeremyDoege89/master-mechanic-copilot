# M6B — Judge demo bundles + web UI design pass

Context: public Modal site is replay-only (`MMC_PUBLIC_MODE=replay`). Problems: (a) judge bundle
`sim-healthy.mmrec` holds ONE `session_started` event (verified in the zip) so judges see priors, not an
investigation; (b) `web/src/styles.css` is 39 lines of browser defaults. Goal: a real investigation judges
can play in 60 s for $0, on a styled UI. Non-goals: Android, new KP tests, live (paid) judge calls, Ford
bundles (file-drop later), any new npm dependency.

## 1. Deterministic demo bundles — `scripts/make_demo_bundle.py` (+ owner-run `scripts/record_demo_cache.py`)

1.1 Generator drives the REAL backend in-process (FastAPI `create_app()` + `TestClient`, non-public mode,
`REPLAY_USE_CACHED_LLM=true`), never hand-writes posteriors. Vehicle truth comes from the Python parity
fixture `backend/tests/fixtures/sim_scenarios.py` (`measured_for_test`/`snapshot`; same constants as Kotlin
`Elm327Sim.kt`, parity-tested) — the Android sim is not run. Per bundle, in order:
  1. `POST /sessions` {vehicle: sim Ford 2.0 L, `vin_last6` only, user_level L2, tools [multimeter,
     fuel pressure gauge], mode live} → `session_started` carries level+tools for the status bar.
  2. `adapter_fingerprinted` event (GENUINE personality profile from the sim snapshot: multi-PID, headers).
  3. DTC/PID evidence: `evidence_added` (dtc + obd_pid from `snapshot(scenario)`: vacuum → lean trims;
     coil → P0303 + Mode 06 counts 45 on cyl 3) + matching `observation_added` rows.
  4. Planner loop (≤4 performed tests): read `GET /state`, take `next_tests[0]`, if gated append
     `safety_ack` (checklist from `mmc/safety.py`), then `POST /tests/{ref}/result` with the fixture
     measured payload. Assert each step that the performed ref equals planner rank 1.
  5. Keyframe (step 2 of the loop): upload `backend/tests/fixtures/images/engine_bay_02.jpg` as blob →
     `POST /vision/keyframe` (Spark components + SAM overlays) → async second opinion merged
     (`corroborated`/`single_source` badges). Reuses live fixtures `vision/spark_components_01.json`
     (already names air filter + intake hose) and `vision/sam_stream_air_filter_housing.jsonl`.
  6. Manual-PDF decision: upload `fixtures/upload/upload_spec_test.pdf` → `POST /uploads/pdf/extract`
     (reuses live `spark_pdf_spec_01.json`, 3 SpecRanges, $0). The first performed test among
     {battery_voltage_rest_running, fuel_pressure_test} is submitted with its `spec_id`; measured values
     are chosen so the manual range FLIPS the KP-only outcome (e.g. rest 12.05 V: manual 11.5–12.6 →
     normal, generic 12.4–12.7 → low; evaluator manual > kp > physics). Assert `expected.source.kind ==
     manual_page` and flipped outcome in the log.
  7. Engine sound: generator synthesises a 6 s 44.1 kHz WAV deterministically (stdlib `wave`; hiss synth
     for vacuum, firing-dropout synth for coil — same recipes as `tests/test_engine_sound.py`) →
     `POST /vision/engine-sound` (pure DSP, $0, no LLM) → `engine_sound_screen` result via normal route.
  8. `POST /report`: Wonder review served from cache (see 1.3); `report_built` event embeds the review.
  Stop when top hypothesis == true hypothesis (`vacuum_leak_post_maf` / `ignition_misfire_cyl`) at
  p ≥ 0.8 within ≤4 tests (M3 already proves both converge for L1/L3; generator re-asserts for L2 and
  fails loudly otherwise — no scripted test order, no posterior fudging).
1.2 Output: `backend/tests/fixtures/sessions/sim-vacuum-leak.mmrec` + `sim-coil-unplugged.mmrec` via
`mmc/replay/mmrec.create` (transcript rows synthesised from the sim snapshot tx/rx; `meta.json` adds
`{scenario, personality: genuine, label: "simulated vehicle, real AI outputs", user_level, tools}`).
Byte-identical: sorted JSON, fixed blob bytes, two consecutive runs must give equal sha256 (test asserts).
`Replay.tsx` `JUDGE_FIXTURE` + `App.tsx` note + `scripts/modal_smoke.sh` switch to `sim-vacuum-leak.mmrec`
(coil bundle linked as second demo); `modal_app.seed_fixtures` already globs `*.mmrec` — no change.
Keep `sim-healthy.mmrec` for `replay_check.sh`. Small backend fix: report build in replay/public mode
reuses `wonder_review` from the latest `report_built` event when live Wonder is skipped ($0), so judges
see the review; otherwise the report page shows "AI review unavailable" on the demo.
1.3 Cache provenance — every model output is a REAL recorded live call; replay never samples:
  | Output | Source | New live call? |
  | Spark keyframe components | `fixtures/vision/spark_components_01.json` (live, $0.013) | no |
  | SAM overlay (air filter) | `fixtures/vision/sam_stream_*.jsonl` (live) | no |
  | PDF SpecRanges | `fixtures/upload/spark_pdf_spec_01.json` (live owner run) | no |
  | Spark Open second opinion on `engine_bay_02.jpg` | `record_demo_cache.py` → committed `backend/tests/fixtures/demo_cache/baseten/` | yes, once |
  | Wonder review ×2 bundles | `record_demo_cache.py` → committed `backend/tests/fixtures/demo_cache/` | yes, once each |
  `record_demo_cache.py` (owner-run, outside sandbox, keys from `~/.config/meta/model-api.env` /
  `~/.config/baseten/api.env`, never printed) makes ONLY the missing calls above, writes them in the
  existing call-record shape keyed by original-bytes sha256 (M5 cache-key rule), and prints per-call cost.
  Generator and replay resolve cache from `fixtures/demo_cache/` first, then existing fixtures; a cache
  miss raises `NoCachedResponse` (402 path) — the generator must never silently go live in CI.
  Expected one-time spend (SPEC §4/§7 prices: Spark $1.25/M in, $4.25/M out; SAM $2.50/1k images;
  Wonder $4/M in, $20/M out): second opinion ≈2.5k in + 0.4k out ≈ $0.005 (Baseten $100 credit);
  Wonder ≈15k in + 1.5k out ≈ $0.09 ×2 = $0.18; contingency one extra SAM concept $0.0025.
  Total Meta ≈ $0.19, Baseten ≈ $0.01; script hard-refuses above $1.00 cumulative. Judge replay: $0.

## 2. Web UI design pass (`web/src`, plain CSS, zero new deps — `package.json` unchanged)

2.1 Visual system in `styles.css` (rewrite, ~250 lines): CSS variables (`--bg/--surface/--ink/--muted/
--accent/--ok/--warn/--bad/--band`) with light default + dark via `prefers-color-scheme` and a manual
toggle (`data-theme` on `<html>`, persisted in `localStorage`); system-ui stack, 4 px spacing scale,
cards with 1 px border + 8 px radius. `report.html.j2` gets the same variables inline (standalone file).
2.2 Layout (`Replay.tsx` + `App.tsx`): CSS grid `280px 1fr 340px`, collapsing to one column <900 px.
  - Top status bar: product mark, vehicle (`vin_last6`), adapter profile, L1/L2/L3 switch, existing
    `BudgetBadge`, sim/real-AI label chip ("Simulated vehicle · real AI outputs").
  - Left: event timeline scrubber — vertical list of event rows (icon per type, seq, short label) bound
    to the existing `cursor`/`?upto_seq=` state; Play/Pause + speed stay, default speed 2.
  - Center: `HypothesisChart` extended in place (hand-rolled SVG, no lib): shaded p ≥ 0.8 "concluded"
    band + 0.5 line, per-hypothesis history already in the API; click/hover a point selects that trace
    entry and shows expected vs measured, LR + source, L-level text (data already in `Hypothesis.trace`).
  - Right: next-test card — instructions at selected level, EIG/cost line, safety checklist as
    checkboxes (display state only; replay stays read-only), blocked tests with reasons, still-needed list.
  - Evidence drawer (bottom, collapsible): evidence rows with badges — AI (Spark/Spark Open + agreement),
    DSP "Signal processing, not AI", manual (PDF page + hash), OBD/sim; image evidence renders the
    existing `SamOverlay` (photo + SVG mask/box + concept labels).
2.3 Judge landing (`?judge=1`): hero of exactly two sentences ("This is a recorded-style investigation of
a simulated Ford with a vacuum leak. Every suspect probability is computed by deterministic code — the
AI only reads photos, manuals and sounds; press play and watch it converge."), one primary button
"Play the investigation" → loads `sim-vacuum-leak.mmrec`, sets cursor 0 and auto-plays at 2× (today
`loadSession` parks at maxSeq — fix), plus quiet links: second bundle (coil), Report, `docs/JUDGES.md`.
Non-judge open/upload flows unchanged.
2.4 Report page: level summaries, trace and expected-vs-measured tables restyled with the same variables;
`@media print` in both `styles.css` and `report.html.j2`: white bg, no nav/drawer, tables avoid page-break
inside rows, badges become bordered text (print-safe without colour).

## 3. 60-second judge path — new `docs/JUDGES.md`, mirrored on the landing

Timed script (each step names the observable payoff): 0:00 landing, read the two sentences → 0:05 Play
(2× autoplay) → 0:15 watch trims evidence move `vacuum_leak_post_maf`, open its trace point (LR shown) →
0:30 keyframe in the evidence drawer (photo + SAM overlay + corroborated badge) → 0:40 manual-PDF badge
flips a voltage outcome; engine-sound row labelled DSP → 0:50 safety checklist on the gated next test →
0:55 Build report: conclusion, Wonder "AI review" section, reproducibility hashes → note $0 spend badge
and "simulated vehicle, real AI outputs; real Ford bundles drop in by file". Landing mirrors this as a
5-item checklist beside the Play button (same wording, no duplication drift: JUDGES.md is canonical).

## Tests (all offline) and verification

- `backend/tests/test_demo_bundles.py`: both bundles verify (`mmrec.verify`), ≥15 events each, hash chain
  valid, performed tests == planner rank-1 sequence, true hypothesis #1 p ≥ 0.8 in ≤4 tests, one manual-
  flipped outcome (`source.kind == manual_page`), one DSP sound outcome, keyframe evidence carries SAM
  overlay + second-opinion agreement, `report_built` embeds a Wonder review, trackers at $0 throughout,
  regeneration is byte-identical, cache-miss raises instead of calling live.
- Existing gates stay green: `test_replay_zero_spend`, `test_engine_scenarios`, `replay_check.sh`.
- Web: `npm run build` (tsc strict) + manual checks from §2 (theme toggle, <900 px layout, print preview).
- `scripts/screenshot_site.sh` (owner-run on host; `google-chrome` is NOT in the sandbox): takes a base
  URL, uses `google-chrome --headless=new --screenshot --window-size=` for landing (1440×900), mid-replay,
  evidence drawer open, report, phone (390×844) and dark landing into `/tmp/mmc-shots/`; the plan is not
  done until the owner pastes these back and they show the §2 layout (no browser-default styling).
- Commands: `cd backend && uv run pytest -q tests/test_demo_bundles.py tests/test_replay_zero_spend.py`
  · `scripts/replay_check.sh backend/tests/fixtures/sessions/*.mmrec` · `cd web && npm ci && npm run build`
  · `python3 scripts/make_demo_bundle.py --check` (regenerate to temp, diff sha256) · owner:
  `python3 scripts/record_demo_cache.py` (once, ≈$0.20) then `bash scripts/screenshot_site.sh <modal-url>`.

## Ambiguities resolved

- `docs/JUDGES.md` does not exist yet and there is no `backend/mmc/replay/bundle.py`: JUDGES.md is created
  by §3; the bundle path is `replay/mmrec.py` (`create`/`verify`) + `replay/runner.py` (`fold_rows`).
- Wonder on a replay-only public site: served from the `report_built` event's cached review (§1.2 fix),
  never a live judge-time call — keeps replay $0 and byte-identical.
- "Manual spec decides an outcome" uses the existing manual > kp > physics evaluator priority with a
  deliberately flipped voltage/pressure reading, asserted in tests — not a new spec feature.
- Keyframe reuses the one real engine-bay photo with live fixtures (`engine_bay_02.jpg`); no generated
  images as evidence (AGENTS.md ban) and no new photo shoot is on the critical path.
