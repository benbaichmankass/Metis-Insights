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
#   3 global endpoints. This slow tier refreshes each strategy narrative.
#   Both run on gemini-2.0-flash (~1,500 RPD free-tier cap). To stay inside
#   the free tier with a ~48-strategy fleet, this cycle fires at 120-min
#   cadence (see ict-insights-generator-strategies.timer) AND only refreshes
#   ACTIVE strategies (recent/open trades) rather than all configured — so a
#   dormant strategy the analyst has nothing to say about doesn't burn a call.
#   Fast (3 × 96 = ~290/day) + active-strategy slow (bounded well under the cap
#   at 120-min) stays comfortably free-tier.
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
configured = [
    name for name in strategies
    if isinstance(name, str) and name.replace("_", "").isalnum() and name.islower()
]

# Active-strategies-only: restrict the slow tier to strategies that actually
# have something to analyze — those with a recent trade OR an open position —
# so a dormant strategy doesn't spend a free-tier Gemini call every cycle.
# "Recent" = seen among the last INSIGHTS_STRATEGY_ACTIVE_TRADES (default 2000)
# non-backtest trades by insertion order (rowid) — format-agnostic, no reliance
# on the epoch-ms-vs-ISO closed_at ambiguity. Fail-OPEN to all configured on any
# DB error (safe: the 120-min cadence bounds even the all-strategies worst case
# under the free-tier RPD).
import os
try:
    k = int(os.environ.get("INSIGHTS_STRATEGY_ACTIVE_TRADES", "2000"))
except Exception:
    k = 2000
active = None
try:
    import sqlite3
    from src.utils.paths import trade_journal_db_path
    dbp = str(trade_journal_db_path())
    con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5)
    try:
        recent = con.execute(
            "SELECT strategy_name FROM trades "
            "WHERE strategy_name IS NOT NULL AND COALESCE(is_backtest,0)=0 "
            "ORDER BY id DESC LIMIT ?",
            (k,),
        ).fetchall()
        openrows = con.execute(
            "SELECT DISTINCT strategy_name FROM trades "
            "WHERE strategy_name IS NOT NULL AND COALESCE(is_backtest,0)=0 "
            "AND status='open'"
        ).fetchall()
    finally:
        con.close()
    active = {r[0] for r in recent if r and r[0]} | {r[0] for r in openrows if r and r[0]}
except Exception:
    active = None

if active:
    out = [n for n in configured if n in active]
    if not out:            # join missed everything — don't go silent
        out = configured
else:
    out = configured       # fail-open
for name in out:
    print(name)
PY
)

for name in "${STRATEGIES[@]}"; do
    run_strategy "$name"
done

echo "run_insights_strategies_cycle: done (${#STRATEGIES[@]} active strategies) [slow tier]"
