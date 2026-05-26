#!/usr/bin/env bash
# Tier-1 diagnostic: kick the AI Analyst's fast-tier cycle once,
# off the timer schedule, so the operator can verify a provider
# change (e.g. enabling the Gemini API) without waiting up to 15
# minutes for the next scheduled fire.
#
# The unit is a oneshot, so `systemctl start` runs the wrapper
# synchronously to completion; this script tails the journal +
# the most recent usage rows so the comment-back tells the
# operator exactly what happened.
#
# No DB writes, no order-path touch, no config mutation. The
# only side effect is one extra cycle's worth of cache-file +
# insights_history + insights_usage rows — same shape the timer
# itself produces.
#
# Operator invokes via system-actions issue:
#   action: kick-insights
#   reason: <text>
set -euo pipefail

SCRIPT_NAME="kick_insights"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_env
DB_PATH="$(runtime_db_path)"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    SYSTEMCTL=(systemctl)
fi

UNIT="ict-insights-generator.service"
echo "=== KICK-INSIGHTS — starting ${UNIT} now ==="
"${SYSTEMCTL[@]}" start "${UNIT}"
# Oneshot: `start` returns when the wrapper exits.
echo "${UNIT} completed."
echo ""
echo "=== KICK-INSIGHTS — journal tail (last 80 lines) ==="
journalctl -u "${UNIT}" -n 80 --no-pager 2>&1 | sed 's/^/  /' || true

echo ""
echo "=== KICK-INSIGHTS — 5 most recent usage rows ==="
if [ -f "${DB_PATH}" ]; then
    sqlite3 "file:${DB_PATH}?mode=ro" -readonly -header -column \
        "SELECT id, ts, endpoint, model_id, status,
                input_tokens, output_tokens,
                printf('%.4f', estimated_cost_usd) AS cost_usd
         FROM insights_usage
         ORDER BY id DESC LIMIT 5" 2>&1 | sed 's/^/  /'
fi

echo ""
echo "=== KICK-INSIGHTS — 5 most recent history rows ==="
if [ -f "${DB_PATH}" ]; then
    sqlite3 "file:${DB_PATH}?mode=ro" -readonly -header -column \
        "SELECT id, generated_at, endpoint, strategy_name, model_id, grade
         FROM insights_history
         ORDER BY id DESC LIMIT 5" 2>&1 | sed 's/^/  /'
fi

record_audit "kick-insights" "success" \
    "{\"unit\": \"${UNIT}\"}" >/dev/null || true
exit 0
