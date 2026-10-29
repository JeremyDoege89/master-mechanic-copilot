# M5O — Muse Spark Open (Baseten) + second-opinion vision + engine-sound DSP — plan
Source: AGENTS.md constraints 3/4/8, SPEC §4+§7, PLAN "Sponsor credits", DECISIONS (MetaClient/budget/SAM). Live Oct 29: Chat Completions (NOT Responses) `https://inference.baseten.co/v1`, model exactly `meta-models/muse-spark-open-1026`, key `BASETEN_API_KEY` from `~/.config/baseten/api.env` (never print/commit); always reasons (`reasoning_content` beside `content`; usage: prompt/completion + reasoning + cached tokens); text 3.3 s, image (`image_url` data-URL) 6.5 s on `engine_bay_02.jpg`; audio unavailable (`input_audio`/`audio_url` → `audio_tokens=0`, `file` → 400). Ledger prices assumed $1.25/M in, $4.25/M out (separate from Meta's $120 stop).

## 1. `backend/mmc/meta/spark_open.py` — sibling client (not subclass)
- `MetaClient` (`meta/client.py`) is Responses-shaped (`client.responses.create`); Spark Open is Chat Completions → new `SparkOpenClient` reusing helpers, not inheritance: `canonical_bytes`/`request_sha256`/`call_record`/`_redact`, `BudgetTracker`, lazy `openai` import, offline `transport` seam `(model, kwargs) -> response`, 429 policy (5 tries, exp backoff + jitter, honour Retry-After; other errors raise, as in `client.py`).
- Guards mirror `MetaClient.__init__`: refuse missing `BASETEN_API_KEY`, any pin ≠ `MODEL_SPARK_OPEN` default `meta-models/muse-spark-open-1026`, and `-contributor`/non-Meta IDs. Base URL from `BASETEN_BASE_URL`.
- Own ledger: `BASETEN_BUDGET_USD_HARD_STOP=80` (of $100 credit, $20 buffer); Meta's $120 tracker untouched. Add `budget.estimate_spark_open()` at Spark rates (cached billed as input until Baseten says otherwise — DECISIONS note) + Chat-usage mapping (`prompt_tokens/completion_tokens`, `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens`) via a `provider="baseten"` branch; pre-call `check()`, post-call `add(actual)`.
- Cache/replay: sha256(canonical request incl. model + messages, image bytes as sha256+len stub like `spark.py:_image_part`) under `DATA_DIR/llm_cache/baseten`. `REPLAY_USE_CACHED_LLM=true`: hit $0, miss raises `NoCachedResponse` (from `client.py`), never spends.
- Record: `call_record()` + `provider:"baseten"`, model pin, full `reasoning_content`, `content`, mapped `usage`, `cost_usd`, `cached`; appended via the existing `llm_call` event (no new event type). API: `chat_text(system,user)`, `chat_image(system,user,image_bytes,mime)` with `{"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}`.

## 2. Second-opinion vision — same schema, agreement only (never probabilities)
- `routes_vision.LiveVisionAnalyzer.analyze_keyframe`: run `spark.analyze_components` (Spark 1.3) + new `spark_open.analyze_components_open` (same `COMPONENTS_SYSTEM`, same `vision_components` validation + `components.BANNED_KEYS` scan) concurrently (`ThreadPoolExecutor(2)`); SAM overlays unchanged from Spark 1.3 concepts (primary may swap under §3).
- Merge `vision/components.py:merge_opinions(primary, second)` by normalised `sam_concept` slug (`_slug`): both name → `agreement="corroborated"`; one side only → `"single_source"`; same concept, conflicting anomaly type/region slug → `"disputed"`. Add optional `agreement` enum + `sources:[str]` to `packages/schemas/observation.json`; `to_observations` sets `confidence=max(both)` if corroborated else the namer's value. Only `confidence`/`agreement`/`sources` change — `sessions.compute_state` folds only `test_result`, asserted bit-identical in tests.
- Surface: keyframe response per-component `agreement` + `disagreements:[{name, named_by}]`; web SamOverlay/report badge reads those fields only.
- Latency budget (measure, don't assume): Spark 1.3 target <3 s (SPEC §7) vs Spark Open measured 6.5 s image → parallel wall ≈6.5 s breaks the target, so return Spark 1.3 immediately (`second_opinion_pending:true`) and patch agreement via WS diff when Open lands; 10 s per-model timeout, Open timeout → `single_source`, never blocks labels. Both latencies logged in call records.

## 3. Resilience fallback — text + image only
- New `meta/fallback.py:call_text_or_image(primary_fn, open_fn, *, kind)`: on `BudgetExceeded` or `MetaClientError` after 429 retries (5xx/timeout; non-429 already raises in `client.py`, no client change) retry once on `SparkOpenClient`; record carries `provider:"baseten"` + `fallback:{from:"meta", reason:"budget"|"error", detail:str(exc)[:200]}` in the `llm_call` event.
- Wire into `spark.analyze_components`, `spark.read_instrument`, agent text turns; <0.8 retake + user-confirm gate applies identically to fallback readings. SAM (`sam.py:call_stream` + parser grammar) and Voice Transcribe (no `meta/voice.py` yet, no substitute) get NO fallback and keep raising; test asserts the helper is unreachable from those sites (import graph).

## 4. Engine-sound DSP, no LLM — `backend/mmc/vision/engine_sound.py`
- Deps: `pyproject.toml` declares NO `numpy` (only transitive in `.venv`) → stdlib-only (`wave`/`array`/`math`, Goertzel targeted bins, no FFT lib); no new dep, no DECISIONS dep entry (add one only if profiling later forces numpy, per AGENTS.md §8).
- Input: WAV blob 3–10 s (stereo→mono, native rate) + live OBD `rpm` + `cylinders` (parsed from vehicle `engine`/vPIC; missing → `inconclusive`, never guessed). Firing freq (4-stroke) `f_fire=(rpm/60)*(cylinders/2)` (750 rpm V6 → 37.5 Hz).
- Detectors → `observation_added`, extractor `dsp:engine_sound_v1`, `value` holds the justifying numbers, `confidence` from margin: (a) misfire: envelope autocorrelation + Goertzel at `f_fire` dropout periodicity/depth → `acoustic:firing_dropout {f_fire_hz, dropout_depth, period_s}`; (b) squeal: narrowband peak 2–6 kHz, peak/median ≥ threshold ≥1 s → `acoustic:narrowband_tone {peak_hz, prominence_db, duration_s}`; (c) hiss: 4–8 kHz broadband ratio + spectral flatness at idle (idle/2500 pair when available, else idle-only flagged) → `acoustic:broadband_hiss {band_ratio, flatness}`.
- KP: loader already supports `outcomes` + `likelihoods` in [0.02,0.95] with uniform default → add ONE test `engine_sound_screen` (`kind:sensory`, L1, `engine_running_outdoors`, outcomes `[misfire_dropout, belt_bearing_tone, vacuum_hiss, normal, inconclusive]`, evaluator `dsp.engine_sound`, no cell >0.75) to both packs; posteriors move only via the normal `test_result` route through `engine/` (constraint 4).
- Spark Open audio stub: `spark_open.chat_audio()` raises `NotImplementedError` unless `SPARK_OPEN_AUDIO=true` (default `false`); even then sends nothing until Baseten confirms a shape.

## 5. Docs / env / README — both Meta paths
- AGENTS.md constraint 3 → "All AI calls use Meta models: Meta Model API (`https://api.meta.ai/v1`, Standard IDs only) OR Meta open-weight models via Baseten (`MODEL_SPARK_OPEN` pin); pin exact IDs from env, never `*-contributor`, never non-Meta." Add the four vars to its env block + `spark_open.py` line under Meta client rules (Chat Completions, own $80 ledger, `provider="baseten"`).
- SPEC §4: add **Muse Spark Open** row (`meta-models/muse-spark-open-1026` via Baseten — second-opinion vision + fallback narration; audio unavailable Oct 29; $1.25/$4.25 assumed) and repoint the stale "Spark Open post-hackathon" row here; README model map mirrors it (one line per Meta path).
- `.env.example`: `BASETEN_BASE_URL=https://inference.baseten.co/v1`, `MODEL_SPARK_OPEN=meta-models/muse-spark-open-1026`, `BASETEN_API_KEY=` (blank), `BASETEN_BUDGET_USD_HARD_STOP=80`, `SPARK_OPEN_AUDIO=false`. `docs/DECISIONS.md`: one entry (sibling client, cached-billing assumption, stdlib DSP).

## 6. `scripts/spike_spark_open.py` (owner runs outside sandbox)
- Mirrors `scripts/spike_meta_vision.py`: key from env/`~/.config/baseten/api.env` (never printed); text probe (fuel-trim question), image probe (`engine_bay_02.jpg`), audio probe (short WAV — records `audio_tokens=0`/400 as data, does not fail the spike); prints latency + mapped cost per probe, refuses fixtures containing credential markers, saves key-free records to `backend/tests/fixtures/spark_open/{text_01.json, image_01.json, audio_01.json}`.

## Tests per item (offline on fixtures; live only via spike)
- §1 `tests/test_spark_open.py`: fake transport → Chat shape, `reasoning_content` stored, usage→cost (reasoning/cached), wrong-model/contributor refusal, $80 stop refuses while $120 tracker unaffected, cache $0 + replay-miss `NoCachedResponse`, 429-then-success, no key/`sk-` in records.
- §2 `tests/test_second_opinion.py` (+routes): paired fixtures (agree / Open-only / disputed) → flags + confidence rule, posteriors bit-identical, Open timeout → `single_source` with labels still on time.
- §3 `tests/test_fallback.py`: `BudgetExceeded` + 5xx stub → Baseten record with `fallback.reason`; healthy primary → zero Baseten calls; SAM/ASR import isolation.
- §4 `tests/test_engine_sound.py`: in-test synthetic WAVs (stdlib `wave`+`math`: pulse train with dropped pulse, 3.5 kHz tone, shaped-noise hiss, clean idle) at known rpm/cylinders → each detector fires with expected numbers, clean → `normal`, no cylinders → `inconclusive`; both packs still load (sums 1±1e-9, cells in range).
- §5/§6: grep test asserts pins present + blank key in `.env.example`/AGENTS/SPEC/README; spike fixtures replay through `SparkOpenClient` at $0.

## Expected spend
Spike ≈$0.05; dev live ≈20 text + 20 image ≈$0.50; second opinion +≈$0.01–0.02/keyframe live (replay $0). Total M5O live <$2 of $100 credit; CI $0. Baseten ledger hard-stops at $80 regardless.

## Verification
```
python scripts/spike_spark_open.py   # owner, outside sandbox; writes fixtures/spark_open/
cd backend && uv run pytest -q tests/test_spark_open.py tests/test_second_opinion.py tests/test_fallback.py tests/test_engine_sound.py
cd backend && uv run pytest -q
scripts/replay_check.sh backend/tests/fixtures/sessions/*.mmrec
grep -rn "meta-models/muse-spark-open-1026" AGENTS.md docs/SPEC.md .env.example README.md
```
Non-goals: SAM/ASR fallback, Spark Open audio (flag off), Wonder changes, Android beyond badge fields, any probability path outside `engine/`, non-Meta models.

## Summary
Sibling Baseten Chat-Completions client reusing MetaClient cache/budget/retry/record machinery, own $80 stop.
Second-opinion vision merges on agreement/confidence only; Spark 1.3 answers first, Open corroborates async.
Text/image fall back to Spark Open on Meta budget/5xx with the event saying so; SAM and ASR never fall back.
Engine sound is stdlib DSP keyed to live-rpm firing frequency, reaching posteriors only via a new KP test.
Docs, env and README show both Meta paths; the owner-run spike seeds $0-replay fixtures for every test.

## Ambiguities resolved
Client: sibling not subclass (Responses vs Chat Completions). numpy: undeclared → stdlib-only, no dep entry.
KP: format already supports outcomes+likelihoods → one conservative test added. Latency (<3 s vs 6.5 s): progressive return + async corroboration, 10 s timeout. Observation: optional `agreement`/`sources` added (extra fields already tolerated).
