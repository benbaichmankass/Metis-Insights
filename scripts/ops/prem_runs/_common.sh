#!/usr/bin/env bash
# scripts/ops/prem_runs/_common.sh — shared setup for the prem-tier run kit.
#
# Sourced by 01/02/03_*.sh. Resolves repo/venv/data paths, builds a
# timestamped output dir, and exposes throttle + logging helpers so the runs
# YIELD to the operator's in-flight sweep on a shared VM. Nothing here mutates
# live config, the order path, or any DB — these are Tier-1 evidence runs.
#
# Environment knobs (all overridable):
#   REPO_ROOT   — repo checkout            (default: this file's repo)
#   VENV_DIR    — virtualenv               (default: $REPO_ROOT/.venv)
#   DATA_5M     — full-history 5m BTC CSV/parquet (REQUIRED for 01/02)
#   DATA_2H     — 2h BTC CSV for the apples-to-apples reproduce single-strat
#                 (optional; derived from DATA_5M by backtest_system resample
#                  when absent — see 01_reproduce_check.sh)
#   DATA_SPX    — SPX/MES OHLCV for the re-tune (REQUIRED for 03)
#   OUT_ROOT    — where run artifacts land  (default: $REPO_ROOT/runtime_logs/prem_runs)
#   NICE        — set to 0 to disable nice/ionice throttling (default: throttled)
set -euo pipefail

_THIS="${BASH_SOURCE[0]}"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$_THIS")/../../.." && pwd)}"
VENV_DIR="${VENV_DIR:-$REPO_ROOT/.venv}"
OUT_ROOT="${OUT_ROOT:-$REPO_ROOT/runtime_logs/prem_runs}"
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Python: prefer the venv if present, else system python3.
if [[ -x "$VENV_DIR/bin/python" ]]; then
  PY="$VENV_DIR/bin/python"
else
  PY="$(command -v python3)"
fi

# THROTTLE — run heavy work at the lowest CPU+IO priority so the operator's
# sweep keeps the foreground. `nice -n 19` (lowest CPU) + `ionice -c3` (idle IO).
# ionice may be absent (non-Linux / minimal image) — degrade gracefully.
throttle() {
  if [[ "${NICE:-1}" == "0" ]]; then
    "$@"
  elif command -v ionice >/dev/null 2>&1; then
    nice -n 19 ionice -c3 "$@"
  else
    nice -n 19 "$@"
  fi
}

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2; }
die()  { log "FATAL: $*"; exit 1; }

require_file() {
  local var="$1" path="${!1:-}"
  [[ -n "$path" ]] || die "\$$var is unset — point it at the data file (see header)."
  [[ -f "$path" ]] || die "\$$var=$path does not exist on this host."
}

# Best-effort operator ping when notify_run.sh is present (no-op off-VM).
notify() {
  local action="$1" code="$2" detail="${3:-}"
  local n="$REPO_ROOT/scripts/ops/notify_run.sh"
  [[ -x "$n" ]] && "$n" "$action" "$code" "prem_runs/$RUN_TS" "$detail" 2>/dev/null || true
}

mk_outdir() {
  local name="$1"
  local d="$OUT_ROOT/${RUN_TS}_${name}"
  mkdir -p "$d"
  printf '%s\n' "$d"
}

export REPO_ROOT VENV_DIR PY OUT_ROOT RUN_TS
cd "$REPO_ROOT"
