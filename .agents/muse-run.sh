#!/usr/bin/env bash
# Run one Muse Code job headlessly from a prompt file and keep its log.
# Usage: .agents/muse-run.sh <name> [effort] [model]   (reads .agents/runs/<name>.prompt)
# Model by job: *-plan and *-review use muse-wonder-1.0 (strongest reasoning); everything else
# (builds, fixes) uses muse-spark-1.3. Cost guards: compaction, tool-output cap, step cap (Oct 29: ~$134 spent unguarded).
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
COST_FLAGS=(--context-compaction-soft-threshold 0.35 --context-compaction-hard-threshold 0.5 --max-tool-output-bytes 20000 --max-model-steps "${MUSE_MAX_STEPS:-80}")
if [ "${MUSE_BACKEND:-meta}" = baseten ]; then
  # Meta open weights (Muse Spark Open) via Baseten; key from ~/.config/baseten/api.env, never printed.
  model=meta-models/muse-spark-open-1026
  echo "== backend=baseten model=$model max_steps=${MUSE_MAX_STEPS:-80}" >> "$log"
  ( set -a; . ~/.config/baseten/api.env; set +a; printf '%s' "$BASETEN_API_KEY" ) | muse exec --trust-workspace \
    -c 'endpoint_transport={"base_url":"https://inference.baseten.co/v1","auth":"bearer"}' \
    --base-url https://inference.baseten.co/v1 --api-key-stdin --model "$model" \
    "${COST_FLAGS[@]}" --reasoning-effort "$effort" --prompt-file "$prompt" >> "$log" 2>&1 || status=$?
else
  muse exec --trust-workspace --model "$model" "${COST_FLAGS[@]}" --reasoning-effort "$effort" --prompt-file "$prompt" >> "$log" 2>&1 || status=$?
fi
echo "== $name finished $(date -Is) exit=$status" >> "$log"
tail -25 "$log"
exit $status
