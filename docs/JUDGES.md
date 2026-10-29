# Judges: the 60-second path

Open the deployed site with `?judge=1`. Everything below replays a
recorded-style investigation of a **simulated** Ford (vacuum leak) with
**real recorded AI outputs** — replay costs $0 and makes no live model
calls. A second demo (unplugged coil) loads the same way. Real Ford
bundles drop in by file upload; nothing below needs one.

Demo bundles: `sim-vacuum-leak.mmrec` (primary) and
`sim-coil-unplugged.mmrec` (second) — both load from the Replay page
(`Play the investigation` / `Play the coil demo instead`).

Voice is Android-only: hands-free `next test` and log-by-voice run in the
phone app, so judges should not hunt for a microphone on the replay site.

Timed script (each step names the observable payoff):

- 0:00 — Landing. Read the two sentences: simulated Ford, vacuum leak;
  every suspect probability comes from deterministic code while the AI
  only reads photos, manuals and sounds.
- 0:05 — Press **Play the investigation** (autoplays at 2×).
- 0:15 — Watch the fuel-trim evidence move `vacuum_leak_post_maf` past
  0.8. Open its trace point: the likelihood ratio, expected-vs-measured
  and the plain-language reason appear.
- 0:30 — Keyframe in the evidence drawer (open it): the recorded SAM
  overlay masks plus the corroborated second-opinion badge. The photo
  bytes themselves are not bundled, so shapes render without the photo.
  Each candidate shows its confidence; the highest-confidence mask is the
  match and the rest render faint as other candidates.
- 0:40 — Manual-PDF badge: the book page range flips a voltage outcome
  (generic bands read 14.75 V running as overcharging; the recorded
  13.5–14.8 V page reads it normal). The engine-sound row is labelled
  signal processing, not AI.
- 0:50 — Safety checklist on the gated next test (display only; replay
  stays read-only).
- 0:55 — **Build report**: conclusion, the Wonder "AI review" section,
  reproducibility hashes. Note the $0 spend badge.

Landing checklist (same wording as the page):

1. Press Play — the fuel-trim evidence moves the air-leak suspect past 0.8.
2. Open its trace point — the likelihood ratio and expected-vs-measured appear.
3. Keyframe in the evidence drawer — recorded overlay plus corroborated badge.
4. Manual-PDF badge — the book range flips a voltage outcome.
5. Build report — conclusion, AI review, reproducibility hashes, $0 spend.

## How this was built

Built by Muse Code during the event (muse-wonder-1.0 plans and reviews,
muse-spark-1.3 builds; prompts and runs are in .agents/).

Spark 1.3 reasons and reads · SAM 3.1 masks · Voice Transcribe hears
(Android only) · Wonder reviews once per report · Spark Open via Baseten
corroborates and falls back — none set probabilities.

Notes: every probability is folded from the event log by `backend/mmc/engine/`;
LLM outputs are validated JSON that never set posteriors. Bundles are built
offline by `scripts/make_demo_bundle.py` from the recorded demo cache
(`scripts/record_demo_cache.py`, owner-run once); regeneration is
byte-identical (`--check`).
