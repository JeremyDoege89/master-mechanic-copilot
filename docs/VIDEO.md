# Demo video plan — Master Mechanic Copilot (2:45)

Shoot: **Monday daytime, rested** — after Sunday sleep, before the Monday all-nighter edit. Hard cap 3:00, target **2:45** (165 s). Solo builder: phone on Ford + laptop + screen recorder. Story rule throughout: never show vision alone — every camera beat is tied to a hypothesis/test state, so this never reads as a “simple image analyzer.”

## 1. Timed script (2:45)

Criteria key: **I** = Impact · **M** = Meta tooling · **D** = Demo · **T** = Technical depth

| Time | On screen | Voice-over line | Serves |
|---|---|---|---|
| 0:00–0:08 | Black, then title card | “A check-engine light tells you *what* broke. Nothing tells you what to test next.” | I · D |
| 0:08–0:20 | Driveway Ford, CEL lit on dash | “This is my Ford. The light is real, the adapter is read-only, and no code gets guessed.” | I · D |
| 0:20–0:40 | Phone: live OBD PIDs + DTC | “The phone reads live OBD — codes, freeze frame, fuel trims — straight into an event log.” | D · T |
| 0:40–1:05 | Hypothesis bars + next-test card | “Suspects get probabilities from code, not the AI. Here’s the single most valuable next test for my tools.” | T · I |
| 1:05–1:30 | Camera bay view, SAM masks | “Spark sees the bay, SAM 3.1 outlines the part — then that photo becomes evidence for the engine.” | M · D |
| 1:30–1:45 | Hands in frame, phone untouched | “Hands busy? I just say ‘next test’ — Voice Transcribe moves me on, hands-free.” | M · D |
| 1:45–2:00 | Bars converge, trace opens | “Trims at idle versus 2,500 — watch the leak suspect pass 0.8. Tap the trace: expected, measured, why.” | T · D |
| 2:00–2:12 | Safety checklist blocks test | “Any risky test stops here. Checklist first, in code — the test stays locked until I confirm it.” | T · I |
| 2:12–2:30 | Report page, Wonder section | “The report shows every test, every hash — plus one Wonder review, clearly labelled as AI review.” | T · M |
| 2:30–2:38 | Laptop: judge replay, “$0” badge | “Judges replay this whole investigation in the browser. No car, no keys, zero dollars.” | D · T |
| 2:38–2:45 | Meta model map, then pitch | “A master mechanic in your pocket that shows its work.” | M · I |

Narration notes:

- 1:45 beat: freeze 1 s on the trace detail — likelihood ratio + expected-vs-measured + plain-English reason. Hold the “manual page range flips the voltage call” badge on screen here as a 2-second insert if the Ford clip allows; it proves spec-sourced expected values, not generic bands.
- 2:30 beat mirrors the live judge flow exactly (Play → trim evidence → trace → keyframe overlay → safety → Build report) so the video and the `?judge=1` site tell the same story.
- End card (2:43–2:45, no VO): repo URL + deployed replay URL + “Replay costs $0 — no live model calls.”

## 2. Shot list by location

### A. Driveway / car (phone camera or second phone, 1080p30)

Pre-load: ELM327 paired, app installed, session started, backend reachable, dash mount fitted, phone charged + power bank in car.

- A1 Wide: Ford in driveway, hood closed, daylight, you walk in. (Hook B-roll, 5 s usable.)
- A2 Dash close-up: CEL illuminated, key-on. Hold 3 s steady.
- A3 Hands plugging in adapter (key off), then key on. No hands near belts/fan once running.
- A4 Over-shoulder: phone mounted, live PID strip moving at warm idle.
- A5 Engine bay (engine off or idling, camera on tripod/phone clamp, never hand-held over belts): intake boot / air-filter housing for the SAM beat.
- A6 Hands-free beat: both hands visibly on the intake boot area (engine off for the take if needed), phone in mount behind you; say “next test.”
- B-roll: badge/grille, OBD port, exhaust (outdoors), multimeter on battery (engine off), hood closing. Shoot 10 s each, use 1–2 s cuts.

### B. Phone screen recording (1080p, record *while* shooting A)

Use one, test before the real take:

```sh
# Option 1 — scrcpy (mirrors + records, best for framing)
scrcpy --record=phone_take1.mp4 --max-size=1080 --video-bit-rate=8M

# Option 2 — on-device
adb shell screenrecord --size 1080x1920 --bit-rate 8000000 /sdcard/mmc_take1.mp4
adb pull /sdcard/mmc_take1.mp4
```

Settings: Do Not Disturb on, brightness locked high, Tailscale/backend URL set, demo session pre-warmed (one dry run so PIDs are flowing). Record separate clean passes for: connect/fingerprint, live PIDs, next-test card, SAM overlay, safety checklist, voice “next test.” Narrate live only as a guide track — real VO is recorded later.

### C. Laptop screen recording (1080p)

Pre-load: deployed Replay URL with `?judge=1`, `sim-vacuum-leak.mmrec` ready on **Play the investigation**, report tab open in a second tab, browser zoom 110%, notifications off.

```sh
# OBS (preferred): Display Capture 1920x1080, 30 fps, MP4/H.264
# or ffmpeg x11grab:
ffmpeg -video_size 1920x1080 -framerate 30 -f x11grab -i :0.0 -c:v libx264 -pix_fmt yuv420p laptop_take1.mp4
```

Capture: Play at 2× → trim evidence crossing 0.8 → trace point → evidence-drawer keyframe (SAM masks + corroborated badge) → safety checklist → **Build report** → Wonder section + reproducibility hashes + $0 badge. Record the Meta model map as a static slide from the README table.

## 3. Safety notes for filming (SPEC §8 + on-vehicle checklist)

- Engine running **outdoors only** — never in a garage, door open or not. Park/Neutral, parking brake set, wheels chocked.
- No planted faults and no hands near a running engine on a tired day. Monday shoot is rested by plan; if you did not sleep, shoot screen recordings only and use the fallback cut.
- Hands, hair, sleeves, leads and the phone stay clear of fans and belts. Electric fans can start with key on. Tripod/clamp for bay shots; key off before plugging/unplugging anything.
- Phone mounted before any RPM hold; never touch the phone while holding revs. A second person as safety spotter/camera operator if at all possible — if solo, locked-off tripod shots only.
- Abort on: flashing CEL, fuel/coolant/burning smell, smoke/steam, temp climbing, anything dropping toward the belt path.
- Show the safety checklist in the video exactly as the app enforces it (code gate, not a prompt). Do not film code-clearing as a “fix.”

## 4. Voice-over + edit plan

VO: record indoors after the shoot, phone/laptop mic 15 cm away, quiet room (closet with clothes works), one beat per file (`vo_0108.wav` …). Speak slower than feels natural; leave 0.5 s silence at each end. Re-record any beat with car noise under it — driveway audio is guide track only.

Edit (pick one, both free):

- **Fast path:** OBS-recorded takes + VO in Shotcut/Kdenlive, single timeline, hard cuts only.
- **ffmpeg path:** trim/concat, then lay VO under picture:

```sh
ffmpeg -i picture.mp4 -i vo.wav -c:v copy -c:a aac -shortest mmc_demo.mp4
ffmpeg -i mmc_demo.mp4 -vf subtitles=captions.srt -c:a copy mmc_demo_final.mp4
```

Captions: burn in an `.srt` (also upload it as a sidecar). Every VO line captioned; on-screen text additionally labels “Live Ford,” “SAM 3.1 mask,” “Deterministic engine — AI never sets probabilities,” “$0 replay.” Music: none, or a single licence-free track kept at −25 LUFS under VO (e.g. CC0/Free Music Archive with licence noted in the description). Export H.264, 1080p, ≤ 2:45, check the full file once at 1× with sound on before uploading.

## 5. Fallback cut (no Ford footage ready)

Same script, same timecodes, every visual from the simulator bundles (`sim-vacuum-leak.mmrec` primary, `sim-coil-unplugged.mmrec` second):

- 0:08–0:40: judge-site landing + **Play the investigation** at 2× replaces the driveway/CEL/phone beats.
- 1:05–2:00: recorded SAM overlay keyframe, trace point and safety checklist from the replay, unchanged.
- Permanent lower-third for the whole video: **“Simulated Ford — recorded AI outputs, replayed at $0.”** Say it in VO at 0:08: “Filmed from our simulator bundles — the same investigation judges can replay.”
- Close still lands the pitch and the Meta model map. Labelled honestly, this cut loses no judging criterion except live-car impact — and it is always shippable.
