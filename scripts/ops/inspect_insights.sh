#!/usr/bin/env bash
# Tier-1 read-only diagnostic: dump the AI Analyst's current state.
#
# Reports:
#   - ls -la of runtime_logs/insights/ (cache file list + sizes + mtimes)
#   - head of each cache file (first ~30 lines of JSON) so the operator
#     can see what the latest summary / recent / strategy / health
#     narratives actually say
#   - sqlite count + recent rows of insights_history (last 24h)
#   - sqlite sum of insights_usage.estimated_cost_usd for the current
#     calendar month + per-endpoint split + budget value from
#     INSIGHTS_MONTHLY_BUDGET_USD env
#   - systemctl is-enabled + is-active for the timer + service
#   - journalctl tail of ict-insights-generator.service (last 50 lines)
#
# No DB writes. No live-trading side effects. Mirrors the
# inspect-closed-pnl pattern: read-only audit + comment-back via the
# system-actions workflow.
#
# Operator invokes via system-actions issue:
#   action: inspect-insights
#   reason: <text>
set -euo pipefail

SCRIPT_NAME="inspect_insights"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_env  # picks up DATA_DIR + INSIGHTS_MONTHLY_BUDGET_USD from .env
DB_PATH="$(runtime_db_path)"
# Mirror src.utils.paths.runtime_logs_dir resolution: env override first,
# DATA_DIR-anchored second, repo-local fallback.
if [ -n "${RUNTIME_LOGS_DIR:-}" ]; then
    RUNTIME_LOGS="${RUNTIME_LOGS_DIR}"
elif [ -n "${DATA_DIR:-}" ] && [ -d "${DATA_DIR}/runtime_logs" ]; then
    RUNTIME_LOGS="${DATA_DIR}/runtime_logs"
else
    RUNTIME_LOGS="${REPO_DIR}/runtime_logs"
fi
INSIGHTS_DIR="${RUNTIME_LOGS}/insights"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    SYSTEMCTL=(systemctl)
fi

echo "=== AI ANALYST — CACHE DIR ==="
echo "path: ${INSIGHTS_DIR}"
if [ -d "${INSIGHTS_DIR}" ]; then
    ls -la "${INSIGHTS_DIR}" 2>&1 | sed 's/^/  /'
else
    echo "  (directory does not exist yet — generator has not written a cache)"
fi

echo ""
echo "=== AI ANALYST — CACHE FILE SAMPLES (first ~30 lines each) ==="
if [ -d "${INSIGHTS_DIR}" ]; then
    for f in "${INSIGHTS_DIR}"/*.json; do
        [ -f "${f}" ] || continue
        name="$(basename "${f}")"
        echo "--- ${name} ---"
        head -n 30 "${f}" 2>/dev/null | sed 's/^/  /'
        echo ""
    done
fi

echo "=== AI ANALYST — HISTORY TABLE ==="
echo "db: ${DB_PATH}"
if [ -f "${DB_PATH}" ]; then
    # Total + last-24h count
    total=$(sqlite3 "file:${DB_PATH}?mode=ro" -readonly \
        "SELECT COUNT(*) FROM insights_history" 2>/dev/null || echo "(error)")
    last_24h=$(sqlite3 "file:${DB_PATH}?mode=ro" -readonly \
        "SELECT COUNT(*) FROM insights_history WHERE generated_at >= datetime('now', '-1 day')" 2>/dev/null || echo "(error)")
    echo "total rows: ${total}"
    echo "rows in last 24h: ${last_24h}"

    echo ""
    echo "most recent 10 rows:"
    sqlite3 "file:${DB_PATH}?mode=ro" -readonly -header -column \
        "SELECT id, generated_at, endpoint, strategy_name, model_id, grade,
                substr(summary_md, 1, 60) AS snippet
         FROM insights_history
         ORDER BY datetime(generated_at) DESC
         LIMIT 10" 2>&1 | sed 's/^/  /'
else
    echo "  (DB not found — generator has not run + table not created)"
fi

echo ""
echo "=== AI ANALYST — USAGE TABLE (current calendar month) ==="
if [ -f "${DB_PATH}" ]; then
    month_start=$(date -u +%Y-%m-01T00:00:00+00:00)
    echo "month_start: ${month_start}"
    echo "INSIGHTS_MONTHLY_BUDGET_USD env: ${INSIGHTS_MONTHLY_BUDGET_USD:-<unset, default 5.00>}"
    spent=$(sqlite3 "file:${DB_PATH}?mode=ro" -readonly \
        "SELECT COALESCE(printf('%.4f', SUM(estimated_cost_usd)), '0.0000')
         FROM insights_usage WHERE ts >= '${month_start}'" 2>/dev/null || echo "(error)")
    tokens=$(sqlite3 "file:${DB_PATH}?mode=ro" -readonly \
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
         FROM insights_usage WHERE ts >= '${month_start}'" 2>/dev/null || echo "(error)")
    calls=$(sqlite3 "file:${DB_PATH}?mode=ro" -readonly \
        "SELECT COUNT(*) FROM insights_usage WHERE ts >= '${month_start}'" 2>/dev/null || echo "(error)")
    echo "spent so far: \$${spent}"
    echo "tokens: ${tokens}"
    echo "calls:  ${calls}"

    echo ""
    echo "per-endpoint:"
    sqlite3 "file:${DB_PATH}?mode=ro" -readonly -header -column \
        "SELECT endpoint, status, COUNT(*) AS calls,
                COALESCE(printf('%.4f', SUM(estimated_cost_usd)), '0.0000') AS spent_usd,
                COALESCE(SUM(input_tokens), 0) AS in_tok,
                COALESCE(SUM(output_tokens), 0) AS out_tok
         FROM insights_usage WHERE ts >= '${month_start}'
         GROUP BY endpoint, status
         ORDER BY endpoint, status" 2>&1 | sed 's/^/  /'
fi

echo ""
echo "=== AI ANALYST — TIMER + SERVICE STATE ==="
for unit in ict-insights-generator.timer ict-insights-generator.service; do
    enabled=$("${SYSTEMCTL[@]}" is-enabled "${unit}" 2>/dev/null || echo "unknown")
    active=$("${SYSTEMCTL[@]}" is-active "${unit}" 2>/dev/null || echo "unknown")
    echo "${unit}: is-enabled=${enabled}, is-active=${active}"
done

echo ""
echo "next + last fire:"
"${SYSTEMCTL[@]}" list-timers --all ict-insights-generator.timer --no-legend 2>/dev/null | head -1 | sed 's/^/  /' || true

echo ""
echo "=== AI ANALYST — JOURNAL TAIL (last 50 lines) ==="
"${SYSTEMCTL[@]}" status ict-insights-generator.service --no-pager 2>/dev/null | tail -5 | sed 's/^/  /' || true
echo ""
"${SYSTEMCTL[@]}" cat ict-insights-generator.service >/dev/null 2>&1 || true
journalctl -u ict-insights-generator.service -n 50 --no-pager 2>&1 | tail -50 | sed 's/^/  /' || true

record_audit "inspect-insights" "success" \
    "{\"db_path\": \"${DB_PATH}\", \"insights_dir\": \"${INSIGHTS_DIR}\"}" \
    >/dev/null || true
exit 0
