#!/usr/bin/env bash
# Run one Muse Code job headlessly from a prompt file and keep its log.
# Usage: .agents/muse-run.sh <name> [effort] [model]   (reads .agents/runs/<name>.prompt)
# Model by job: *-plan and *-review use muse-wonder-1.0 (strongest reasoning, priced for
# few, high-value calls); everything else (builds, fixes) uses muse-spark-1.3.
set -euo pipefail
cd "$(dirname "$0")/.."
name="$1"; effort="${2:-high}"
case "$name" in
  *-plan|*-review) default_model=muse-wonder-1.0 ;;
  *) default_model=muse-spark-1.3 ;;
esac
model="${3:-$default_model}"
prompt=".agents/runs/$name.prompt"
log=".agents/runs/$name.log"
[ -f "$prompt" ] || { echo "missing $prompt" >&2; exit 2; }
echo "== $name started $(date -Is) model=$model effort=$effort" > "$log"
status=0
muse exec --trust-workspace --model "$model" --reasoning-effort "$effort" --prompt-file "$prompt" >> "$log" 2>&1 || status=$?
echo "== $name finished $(date -Is) exit=$status" >> "$log"
tail -25 "$log"
exit $status
