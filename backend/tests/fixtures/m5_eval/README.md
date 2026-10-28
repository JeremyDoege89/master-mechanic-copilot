# m5_eval — owner photo drop (gitignored except this README)

Drop here later (owner task, PLAN.md):
- `engine_bay_01..10.jpg` — 10 engine-bay photos ( varied light/angles )
- `meter_01..10.jpg` — 10 multimeter/gauge photos with known readings
- `labels.json` — `{"engine_bay_01.jpg": {"concepts": [...]},
  "meter_01.jpg": {"value": 12.42, "units": "V"}}`

Score: `python scripts/eval_m5.py backend/tests/fixtures/m5_eval/` (live,
costs a few cents) or `python scripts/eval_m5.py --offline` ($0, recorded
fixtures only). Verdicts: overlays ≥7/10 (≥1 correct labelled mask,
human-judged), meters 8/10 (±1 last digit).
