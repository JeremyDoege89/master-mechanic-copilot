# Judges: the 60-second path

Open the deployed site with `?judge=1`. Everything below replays a
recorded investigation with **real recorded AI outputs** — replay costs
$0 and makes no live model calls. Real-vehicle bundles list first when
the owner has exported one; simulated Fords follow, clearly marked. The
primary **Play the investigation** button prefers a real bundle when one
exists.

Demo bundles: `sim-vacuum-leak.mmrec` (primary until a real bundle
lands) and `sim-coil-unplugged.mmrec` (second) — plus any
`real-*.mmrec` the owner exports — all load from the Replay page
(`Play the investigation` / the fixture list / `Play the coil demo
instead`).

Voice is Android-only: hands-free `next test` and log-by-voice run in the
phone app, so judges should not hunt for a microphone on the replay site.

Timed script (each step names the observable payoff):

- 0:00 — Landing. Read the two sentences: recorded investigations, real
  AI outputs; every suspect probability comes from deterministic code
  while the AI only reads photos, manuals and sounds.
- 0:05 — Press **Play the investigation** (autoplays at 2×).
- 0:15 — Watch the fuel-trim evidence move `vacuum_leak_post_maf` past
  0.8. Open its trace point: the likelihood ratio, expected-vs-measured
  and the plain-language reason appear.
- 0:30 — Keyframe in the evidence drawer (open it): the engine-bay photo
  with recorded SAM candidate overlays plus the corroborated
  second-opinion badge. Photo bytes are bundled and served with the
  replay, so shapes render over the photo. Each candidate shows its own
  confidence; no candidate is claimed as the match — scores are SAM's
  own confidence, not a confirmed match.
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

## Turning a real session into a judge demo (owner)

No hand editing — one command per session after the drive:

1. Pull the phone recording (Recorder.kt `cacheDir/session.mmrec`) and
   verify it:
   `scripts/import_phone_recording.sh` — adb-pulls every `.mmrec` into
   `data/phone_recordings/`, runs `mmrec verify` + `replay_check.sh` on
   each, and prints the matching export command (session id from the
   recording's `meta.json`).
2. Export the finished backend session (live or upload mode, report with
   Wonder review already built):
   `scripts/export_session_bundle.py <session_id> --label "Mazda 3 2.0L, MAF unplugged" --out backend/tests/fixtures/sessions/real-maf-unplugged.mmrec`
   The bundle carries the hash-chained `events.jsonl`, the phone's
   adapter transcript, every referenced blob, the report with its Wonder
   review, and the cached model responses the session used — replay stays
   $0 and byte-identical. Full VIN never leaves `backend/mmc/vin.py`
   (`vin_last6` only in `meta.json`). The script refuses, naming the
   item, when any blob, report, or cached response is missing.
3. Verify and ship: `scripts/replay_check.sh` the new bundle, reload the
   judge landing (`?judge=1`) — the real bundle lists first with its
   label and **Play the investigation** prefers it. `modal_app` seeding
   copies new/changed bundles on deploy, so no other step is needed.

## How this was built

Built by Muse Code during the event (muse-wonder-1.0 plans and reviews,
muse-spark-1.3 builds; prompts and runs are in .agents/).

Spark 1.3 reasons and reads · SAM 3.1 masks · Voice Transcribe hears
(Android only) · Wonder reviews once per report · Spark Open via Baseten
corroborates and falls back — none set probabilities.

Notes: every probability is folded from the event log by `backend/mmc/engine/`;
LLM outputs are validated JSON that never set posteriors. Simulated bundles
are built offline by `scripts/make_demo_bundle.py` from the recorded demo
cache (`scripts/record_demo_cache.py`, owner-run once); regeneration is
byte-identical (`--check`). Real-vehicle bundles are exported by
`scripts/export_session_bundle.py` from finished live/upload sessions.
