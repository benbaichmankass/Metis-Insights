#!/usr/bin/env bash
# =============================================================================
# AI Analyst — one refresh cycle (M13 S1).
#
# Triggered by ict-insights-generator.timer every ~10 minutes. Drives
# the generator CLI through every cache file the router serves:
#
#   1. global endpoints  → summary, recent, health
#   2. per-strategy      → strategy/<name> for every strategy declared
#                          in config/strategies.yaml
#
# Each call is independent — a single failure does NOT short-circuit
# the cycle. The generator itself handles the kill switch
# (`INSIGHTS_ENABLED=0`), the monthly cost gate, and persistence; this
# script is just the systemd-side driver.
#
# Idempotent: re-running mid-cycle just overwrites in-flight caches
# with fresh content. Atomic writes inside the generator ensure
# readers always see a complete file.
# =============================================================================

set -uo pipefail

REPO_DIR=${REPO_DIR:-/home/ubuntu/ict-trading-bot}
PYTHON=${PYTHON:-/usr/bin/python3}

# Source the canonical .env so the generator picks up ANTHROPIC_API_KEY,
# INSIGHTS_ENABLED (if set), INSIGHTS_MONTHLY_BUDGET_USD (if set), and
# the DATA_DIR + TRADE_JOURNAL_DB pointers.
if [ -f "$REPO_DIR/.env" ]; then
    # shellcheck disable=SC1090,SC1091
    set -o allexport
    . "$REPO_DIR/.env"
    set +o allexport
fi

cd "$REPO_DIR"

# Short-circuit the cycle when the analyst is disabled. The generator
# would no-op anyway, but exiting here keeps the journal tidy and
# avoids touching the strategy-list parse path.
if [ "${INSIGHTS_ENABLED:-1}" = "0" ] \
    || [ "${INSIGHTS_ENABLED:-1}" = "false" ] \
    || [ "${INSIGHTS_ENABLED:-1}" = "no" ]; then
    echo "run_insights_cycle: INSIGHTS_ENABLED=${INSIGHTS_ENABLED}, skipping cycle"
    exit 0
fi

run_endpoint() {
    local endpoint="$1"
    shift
    echo ">>> insights: $endpoint $*"
    if ! "$PYTHON" -m src.runtime.insights generate \
        --endpoint "$endpoint" "$@"; then
        # Generator returns 0 even on Anthropic errors (it logs +
        # records an `error` usage row). A non-zero here means the
        # CLI itself blew up — bad arg, missing module, etc. We log
        # and continue rather than abort the cycle.
        echo "run_insights_cycle: $endpoint failed (continuing)"
    fi
}

# M13 S2 cadence split (2026-05-26): this wrapper drives the FAST tier
# only — the 3 global endpoints (summary, recent, health). Per-strategy
# narratives moved to the SLOW tier (60-min cadence) in
# run_insights_strategies_cycle.sh. The split lets the operator pin a
# better, lower-RPD-limit model on the strategy endpoint (gemini-2.5-flash)
# while keeping the headline narrative fresh every 15 min on 2.0-flash.
#
# When INSIGHTS_RUN_ALL=1 is set (manual one-off / ops debugging) the
# wrapper falls back to the legacy "all 9 endpoints in one cycle"
# behaviour — useful for first-cycle smoke after `enable-insights-
# generator` so the operator sees every cache file populated at once.
run_endpoint summary
run_endpoint recent
run_endpoint health

if [ "${INSIGHTS_RUN_ALL:-0}" = "1" ] || [ "${INSIGHTS_RUN_ALL:-0}" = "true" ]; then
    mapfile -t STRATEGIES < <(
        "$PYTHON" - <<'PY'
import sys
try:
    import yaml
except ModuleNotFoundError:
    sys.exit(0)
from pathlib import Path
p = Path("config/strategies.yaml")
if not p.exists():
    sys.exit(0)
try:
    data = yaml.safe_load(p.read_text()) or {}
except Exception:
    sys.exit(0)
strategies = data.get("strategies") or {}
if not isinstance(strategies, dict):
    sys.exit(0)
for name in strategies:
    if isinstance(name, str) and name.replace("_", "").isalnum() and name.islower():
        print(name)
PY
    )
    for name in "${STRATEGIES[@]}"; do
        run_endpoint strategy --strategy "$name"
    done
    echo "run_insights_cycle: done (${#STRATEGIES[@]} strategies + 3 global endpoints) [INSIGHTS_RUN_ALL]"
else
    echo "run_insights_cycle: done (3 global endpoints) [fast tier]"
fi
