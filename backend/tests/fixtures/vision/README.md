# vision fixtures

`spark_components_01.json` is **LIVE**: a real `muse-spark-1.3` response
captured 2026-10-28 (usage 1142 in / 1499 out incl. 1428 reasoning tokens,
ledger cost $0.00779825 from returned usage). Key-free `call_record()`
output — no headers, no key, image bytes as a length stub.

`sam_stream_battery.jsonl` and `sam_stream_air_filter_housing.jsonl` are
**LIVE** captured SAM 3.1 Responses streams (one JSON object per line) on
the real CC0 photo `../images/engine_bay_02.jpg`:
- `..._battery.jsonl`: concept "battery" found 3 objects (completed,
  usage `images_processed=1`, no token counts).
- `..._air_filter_housing.jsonl`: concept "air filter housing" found
  nothing — empty text, status completed, usage `images_processed=1`.
  Empty is a normal "not found", not an error; replay with
  `mmc.meta.sam.parse_image_events` + `result_from_image`.

`instrument_01.json` is still **PROVISIONAL**: hand-shaped from SPEC §7 /
AGENTS.md (flagged `"PROVISIONAL": true`) until the owner supplies
multimeter photos. Do not treat it as a live capture.
