#!/usr/bin/env bash
# Shared PASS/FAIL/SKIP rows for scripts/acceptance_sweep.sh (M7 review #4).
#
# Every command-based row is decided by the underlying command's EXIT CODE
# (plus its failure summary as evidence), never by grepping output text for
# a word like "passed" — "1 failed, 19 passed" exits non-zero and must FAIL.
# Sourced, never executed: `source scripts/sweep_lib.sh`.

row() { printf '%-42s | %-4s | %s\n' "$1" "$2" "$3"; }
pass() { row "$1" "PASS" "$2"; }
fail() { row "$1" "FAIL" "$2"; FAIL=1; }
skip() { row "$1" "SKIP" "$2"; }

# Run "$@" (one command); PASS iff it exits 0. Evidence is the command's own
# tail: the last line on success, the last three lines on failure (which for
# pytest/gradle carry the failure counts, never a grepped pass word).
run_row() {
  local label="$1"; shift
  local log
  log=$(mktemp /tmp/mmc-sweep-row-XXXXXX.log)
  if "$@" >"$log" 2>&1; then
    pass "$label" "$(tail -1 "$log" | head -c 200)"
  else
    fail "$label" "$(tail -3 "$log" | tr '\n' ' ' | head -c 300)"
  fi
  rm -f "$log"
}
