#!/usr/bin/env bash
# Run one Muse Code job headlessly from a prompt file and keep its log.
# Usage: .agents/muse-run.sh <name> [effort]   (reads .agents/runs/<name>.prompt)
set -euo pipefail
cd "$(dirname "$0")/.."
name="$1"; effort="${2:-high}"
prompt=".agents/runs/$name.prompt"
log=".agents/runs/$name.log"
[ -f "$prompt" ] || { echo "missing $prompt" >&2; exit 2; }
echo "== $name started $(date -Is)" > "$log"
muse exec --trust-workspace --reasoning-effort "$effort" --prompt-file "$prompt" >> "$log" 2>&1
status=$?
echo "== $name finished $(date -Is) exit=$status" >> "$log"
tail -25 "$log"
exit $status
