#!/usr/bin/env bash
# Smoke-test a deployed MMC public URL (M6 §5).
# Usage: scripts/modal_smoke.sh <url>
# Curls /health, /budget, the judge replay, and the report .json/.html/.pdf,
# asserting 200s everywhere and zero spend delta after the replay.
set -euo pipefail
URL="${1:?usage: scripts/modal_smoke.sh <url>}"
URL="${URL%/}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "smoke: need $1 on PATH"; exit 2; }; }
need curl
need python3

get() { curl -fsS -o "$2" -w "%{http_code}" "$URL$1"; }

echo "smoke: health"
code=$(get /health /tmp/mmc-smoke-health.json)
[ "$code" = "200" ] || { echo "smoke FAIL: /health -> $code"; exit 1; }
grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' /tmp/mmc-smoke-health.json \
  || { echo "smoke FAIL: /health body:"; cat /tmp/mmc-smoke-health.json; exit 1; }

echo "smoke: budget before"
code=$(get /budget /tmp/mmc-smoke-budget-0.json)
[ "$code" = "200" ] || { echo "smoke FAIL: /budget -> $code"; exit 1; }

echo "smoke: judge replay (sim-healthy.mmrec)"
code=$(curl -fsS -o /tmp/mmc-smoke-replay.json -w "%{http_code}" \
  -H 'Content-Type: application/json' -d '{"fixture":"sim-healthy.mmrec"}' "$URL/replay")
[ "$code" = "200" ] || { echo "smoke FAIL: /replay -> $code"; cat /tmp/mmc-smoke-replay.json; exit 1; }

echo "smoke: state + report"
SID=$(python3 -c "import json; print(json.load(open('/tmp/mmc-smoke-replay.json'))['session_id'])")
code=$(get "/sessions/$SID/state" /tmp/mmc-smoke-state.json)
[ "$code" = "200" ] || { echo "smoke FAIL: state -> $code"; exit 1; }
code=$(curl -fsS -o /tmp/mmc-smoke-report.json -w "%{http_code}" \
  -X POST "$URL/sessions/$SID/report")
[ "$code" = "200" ] || { echo "smoke FAIL: report -> $code"; cat /tmp/mmc-smoke-report.json; exit 1; }
RID=$(python3 -c "import json; print(json.load(open('/tmp/mmc-smoke-report.json'))['report_id'])")
for suffix in json html pdf; do
  code=$(get "/reports/$RID.$suffix" "/tmp/mmc-smoke-report.$suffix")
  [ "$code" = "200" ] || { echo "smoke FAIL: report.$suffix -> $code"; exit 1; }
done
python3 -c "d=open('/tmp/mmc-smoke-report.pdf','rb').read(); assert d.startswith(b'%PDF'), 'not a PDF'; print('smoke: pdf bytes', len(d))"

echo "smoke: budget after (spend must be 0)"
code=$(get /budget /tmp/mmc-smoke-budget-1.json)
[ "$code" = "200" ] || { echo "smoke FAIL: /budget -> $code"; exit 1; }
python3 - <<'EOF'
import json
b0 = json.load(open('/tmp/mmc-smoke-budget-0.json'))
b1 = json.load(open('/tmp/mmc-smoke-budget-1.json'))
for provider in ('meta', 'baseten'):
    s0, s1 = b0[provider]['spend_usd'], b1[provider]['spend_usd']
    assert s1 == s0, f"{provider} spend moved {s0} -> {s1} during a $0 replay"
    print(f"smoke: {provider} spend_usd: {s1}")
pub = b1.get('public_live')
if pub is not None:
    print(f"smoke: public demo spend {pub['spend_usd']}/{pub['cap_usd']}")
print('smoke OK')
EOF
