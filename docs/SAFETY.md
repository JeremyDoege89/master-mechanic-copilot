# Safety — on-vehicle work (M4 gate pass, solo owner)

The owner works **alone**. No passenger runs the phone. Every rule below is
written for one person doing all of it.

## Non-negotiables

- Vehicle in **Park** (automatic) or **Neutral** (manual) with the **parking
  brake set**, wheels chocked, engine work done **outdoors only — never in a
  garage** (CO kills in enclosed spaces, door open or not).
- **Phone mounted** (windshield/dash mount) and **logging started before any
  rpm hold**. **Never touch the phone while holding rpm** — eyes on gauges,
  mirrors, and the engine bay.
- **No hands, sleeves, tools, or rags near belts, fans, or hot exhaust.**
  Electric fans can start with the key on, engine running or not.
- Key off before plugging/unplugging anything under the hood or dash.
- Read-only access only: the app never writes to the ECU. Clearing codes is a
  separate gated flow and is **never** offered as a fix.

## Abort immediately on any of these

- Flashing check-engine light, sudden roughness, or stall during a hold.
- Fuel / coolant / burning smell, any smoke or steam.
- Temperature gauge climbing past normal or a temp warning.
- Anything falls toward the belt/fan path — key off, retrieve only after the
  bay is still.

## M4 gate pass (the manual Ford check)

1. Outdoors, Park/Neutral + parking brake + chocks. Mount phone, open the app,
   confirm the backend URL in Settings (Tailscale address of the dev host).
2. Pair the ELM327 (Classic), connect, confirm the fingerprint/profile card
   shows a genuine/good-clone profile.
3. Read VIN + DTCs. Confirm the phone shows only the **last 6 of the VIN**.
4. Log warm idle 2 min (fast PID group running, recorder writing .mmrec).
5. One steady **2,500 rpm hold, 60 s**, then release. Never touch the phone
   during the hold.
6. Open the next-test card, run **at most 2** suggested tests. Any test with a
   safety gate shows its checklist first — complete it before the test unlocks.
7. Fetch the JSON report, save a screen recording of the whole pass.

## M2 real-vehicle acceptance bundle (still required)

- VIN, DTC list, and **at least 10 PIDs** logged to a **.mmrec bundle over
  Classic** (init + fingerprint + fast/slow PID groups + DTC read).
- Copy the bundle off the phone into the backend fixtures so CI replays it:

  ```sh
  adb pull /sdcard/Android/data/dev.dreamz.mmc/files/*.mmrec backend/tests/fixtures/sessions/
  scripts/replay_check.sh backend/tests/fixtures/sessions/*.mmrec
  ```

  (If the app stored the bundle under its cache dir instead, pull from
  `/data/data/dev.dreamz.mmc/cache/` on a rooted device, or use the in-app
  Upload on the Report screen, which posts the same bytes to
  `POST /sessions/{id}/blobs`.)
- Verify the new bundle with `scripts/replay_check.sh` before relying on it.
