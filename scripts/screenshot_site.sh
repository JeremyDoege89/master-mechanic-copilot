#!/usr/bin/env bash
# Screenshot the public judge site (OWNER-RUN on a host with google-chrome;
# NOT in the sandbox). Usage: bash scripts/screenshot_site.sh <modal-url>
# Takes URL-addressable shots into /tmp/mmc-shots/ and prints the two
# stateful shots (mid-replay, drawer open, report) for one manual pass:
# press Play, open the evidence drawer, build the report.
set -euo pipefail
URL="${1:?usage: scripts/screenshot_site.sh <modal-url>}"
URL="${URL%/}"
OUT="${OUT:-/tmp/mmc-shots}"
need() { command -v "$1" >/dev/null 2>&1 || { echo "shots: need $1 on PATH"; exit 2; }; }
need google-chrome
need curl
need python3
mkdir -p "$OUT"

shot() { # $1 name $2 url $3 size $4 extra-flags
  echo "shots: $1"
  google-chrome --headless=new --hide-scrollbars --screenshot="$OUT/$1.png" \
    --window-size="$3" ${4:-} "$2" >/dev/null 2>&1
}

shot landing-1440 "$URL/?judge=1" "1440,900"
shot landing-phone "$URL/?judge=1" "390,844"
shot landing-dark "$URL/?judge=1" "1440,900" "--force-dark-mode"
shot replay-plain "$URL/" "1440,900"

echo "shots: replay + report endpoints answer (report page itself is manual)"
SID=$(curl -fsS -H 'Content-Type: application/json' \
  -d '{"fixture":"sim-vacuum-leak.mmrec"}' "$URL/replay" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
curl -fsS -X POST "$URL/sessions/$SID/report" >/dev/null
echo "shots: done in $OUT (session $SID)"
echo "shots: STILL MANUAL — open $URL/?judge=1, press Play, screenshot mid-replay;"
echo "shots: open the evidence drawer, screenshot; build the report, screenshot its page."
echo "shots: paste all back: the plan is not done until they show the §2 layout."
