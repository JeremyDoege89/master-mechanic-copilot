# Judges: the 60-second path

Open the deployed site with `?judge=1`. Everything below replays a
recorded-style investigation of a **simulated** Ford (vacuum leak) with
**real recorded AI outputs** — replay costs $0 and makes no live model
calls. A second demo (unplugged coil) loads the same way. Real Ford
bundles drop in by file upload; nothing below needs one.

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

Notes: every probability is folded from the event log by `backend/mmc/engine/`;
LLM outputs are validated JSON that never set posteriors. Bundles are built
offline by `scripts/make_demo_bundle.py` from the recorded demo cache
(`scripts/record_demo_cache.py`, owner-run once); regeneration is
byte-identical (`--check`).
