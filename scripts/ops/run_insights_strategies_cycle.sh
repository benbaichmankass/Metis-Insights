#!/usr/bin/env bash
# =============================================================================
# AI Analyst — per-strategy refresh cycle (M13 S2, SLOW tier).
#
# Triggered by ict-insights-generator-strategies.timer every 60 minutes.
# Refreshes runtime_logs/insights/strategy_<name>.json for every strategy
# declared in config/strategies.yaml.
#
# Why a separate timer:
#   The fast tier (run_insights_cycle.sh, 15-min cadence) refreshes the
#   3 global endpoints on gemini-2.0-flash (1500 RPD free-tier cap).
#   Per-strategy narratives use gemini-2.5-flash for higher quality at
#   the cost of a tighter 500 RPD free-tier cap — so they fire less
#   often. 6 strategies × 24 cycles/day = 144 calls/day, comfortably
#   under the 500 cap.
#
# Each call is independent — a single failure does NOT short-circuit
# the cycle. The generator itself handles the kill switch
# (`INSIGHTS_ENABLED=0`), the monthly cost gate (template mode bypasses
# it), and persistence; this script is just the systemd-side driver.
# =============================================================================

set -uo pipefail

REPO_DIR=${REPO_DIR:-/home/ubuntu/ict-trading-bot}
PYTHON=${PYTHON:-/usr/bin/python3}

if [ -f "$REPO_DIR/.env" ]; then
    # shellcheck disable=SC1090,SC1091
    set -o allexport
    . "$REPO_DIR/.env" || true
    set +o allexport
fi

cd "$REPO_DIR"

if [ "${INSIGHTS_ENABLED:-1}" = "0" ] \
    || [ "${INSIGHTS_ENABLED:-1}" = "false" ] \
    || [ "${INSIGHTS_ENABLED:-1}" = "no" ]; then
    echo "run_insights_strategies_cycle: INSIGHTS_ENABLED=${INSIGHTS_ENABLED}, skipping cycle"
    exit 0
fi

run_strategy() {
    local name="$1"
    echo ">>> insights: strategy $name"
    if ! "$PYTHON" -m src.runtime.insights generate \
        --endpoint strategy --strategy "$name"; then
        echo "run_insights_strategies_cycle: strategy=$name failed (continuing)"
    fi
}

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
    run_strategy "$name"
done

echo "run_insights_strategies_cycle: done (${#STRATEGIES[@]} strategies) [slow tier]"
