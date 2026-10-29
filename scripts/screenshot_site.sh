#!/usr/bin/env bash
# Screenshot the public judge site (OWNER-RUN on a host with google-chrome;
# NOT in the sandbox). Usage: bash scripts/screenshot_site.sh <modal-url>
# Takes URL-addressable shots into /tmp/mmc-shots/. When node can resolve
# playwright-core (e.g. playwright-core@1.49.1 under the host scratchpad) the
# stateful shots (mid-replay, drawer open, report) are driven automatically;
# otherwise they stay a one-pass manual checklist (press Play, open the
# evidence drawer, build the report).
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

has_playwright=0
if command -v node >/dev/null 2>&1; then
  # playwright-core lives under the host scratchpad, not in the repo sandbox:
  # honour NODE_PATH plus a couple of conventional scratchpad locations.
  if NODE_PATH="${NODE_PATH:-}:$HOME/scratchpad/node_modules:/tmp/scratchpad/node_modules" \
      node -e "require('playwright-core')" >/dev/null 2>&1; then
    has_playwright=1
  fi
fi

if [ "$has_playwright" = "1" ]; then
  echo "shots: driving stateful shots with playwright-core + google-chrome"
  CHROME_BIN="$(command -v google-chrome)"
  URL="$URL" OUT="$OUT" CHROME_BIN="$CHROME_BIN" NODE_PATH="${NODE_PATH:-}:$HOME/scratchpad/node_modules:/tmp/scratchpad/node_modules" \
  node -e "
const { chromium } = require('playwright-core');
(async () => {
  const base = process.env.URL;
  const out = process.env.OUT;
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BIN,
    args: ['--no-sandbox', '--force-device-scale-factor=1'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('console', (m) => { if (m.type() === 'error') console.log('page:', m.text().slice(0, 200)); });
  // Judge runs must stay 4xx-free: print every >= 400 response (URL + status).
  page.on('response', (r) => { if (r.status() >= 400) console.log('http ' + r.status() + ' ' + r.url().slice(0, 220)); });
  await page.goto(base + '/?judge=1', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Play the investigation' }).click();
  // Let autoplay advance into the trim/sound evidence (2x speed).
  await page.waitForFunction(
    () => document.body.textContent.includes('Fuel trims:') || document.body.textContent.includes('Event seq 3'),
    { timeout: 30000 },
  ).catch(() => {});
  await page.waitForTimeout(9000);
  await page.screenshot({ path: out + '/mid.png' });
  // Evidence drawer is open by default in judge mode; ensure it is open.
  const drawerBtn = page.getByRole('button', { name: /Evidence \(/ });
  if ((await drawerBtn.getAttribute('aria-expanded')) === 'false') await drawerBtn.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: out + '/drawer.png', fullPage: true });
  // One-click report: prefer the conclusion banner / card button, then land on Report.
  const see = page.getByRole('button', { name: /See the report|Build report/ }).first();
  await see.click();
  await page.getByRole('heading', { name: 'Report' }).waitFor({ timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out + '/report.png', fullPage: true });
  await browser.close();
})().catch((e) => { console.error('stateful shots failed:', e.message); process.exit(1); });
"
  echo "shots: done in $OUT (landing + mid + drawer + report)"
else
  echo "shots: playwright-core not found for node (optional) — stateful shots stay manual"
  echo "shots: replay + report endpoints answer (report page itself is manual)"
  SID=$(curl -fsS -H 'Content-Type: application/json' \
    -d '{"fixture":"sim-vacuum-leak.mmrec"}' "$URL/replay" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
  curl -fsS -X POST "$URL/sessions/$SID/report" >/dev/null
  echo "shots: done in $OUT (session $SID)"
  echo "shots: STILL MANUAL — open $URL/?judge=1, press Play, screenshot mid-replay;"
  echo "shots: open the evidence drawer, screenshot; build the report, screenshot its page."
  echo "shots: paste all back: the plan is not done until they show the §2 layout."
fi
