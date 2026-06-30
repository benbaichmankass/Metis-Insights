#!/usr/bin/env bash
# Tier-2 operator action: reset today's daily_risk_state row for one account (or all).
#
# Deletes the row(s) from trade_journal.db::daily_risk_state for today (UTC)
# so the RiskManager rebuilds from scratch on the next tick — clearing any
# INTRADAY_DRAWDOWN or DAILY_LOSS breach.
#
# Usage (via reset-daily-risk-state issue):
#   reason: <why>
#   account: bybit_1          # optional — omit to reset ALL accounts
#
# Exit codes:
#   0 — success (rows deleted, or none matched)
#   1 — DB not found or python3 unavailable

set -euo pipefail

SCRIPT_NAME="reset_daily_risk_state"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB="$(runtime_db_path)"
if [ ! -f "${DB}" ]; then
    log "ERROR: trade_journal.db not found at ${DB}"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    log "ERROR: python3 not available"
    exit 1
fi

TODAY="$(date -u +%Y-%m-%d)"

if [ -n "${ACCOUNT_ID:-}" ]; then
    log "Resetting daily_risk_state for account=${ACCOUNT_ID} date=${TODAY} …"
    RESULT="$(python3 - "${DB}" "${ACCOUNT_ID}" "${TODAY}" <<'PYEOF'
import sys, sqlite3
db, account_id, today = sys.argv[1], sys.argv[2], sys.argv[3]
conn = sqlite3.connect(db)
cur = conn.execute(
    "DELETE FROM daily_risk_state WHERE account_id=? AND date=?",
    (account_id, today)
)
conn.commit()
print(cur.rowcount)
conn.close()
PYEOF
)"
    log "Rows deleted: ${RESULT}"
    record_audit "${SCRIPT_NAME}" "ok" "{\"account_id\":\"${ACCOUNT_ID}\",\"date\":\"${TODAY}\",\"rows_deleted\":${RESULT:-0}}"
else
    log "No account_id specified — resetting ALL accounts for date=${TODAY} …"
    RESULT="$(python3 - "${DB}" "${TODAY}" <<'PYEOF'
import sys, sqlite3
db, today = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
cur = conn.execute("DELETE FROM daily_risk_state WHERE date=?", (today,))
conn.commit()
print(cur.rowcount)
conn.close()
PYEOF
)"
    log "Rows deleted: ${RESULT}"
    record_audit "${SCRIPT_NAME}" "ok" "{\"account_id\":\"ALL\",\"date\":\"${TODAY}\",\"rows_deleted\":${RESULT:-0}}"
fi

log "Done. RiskManager will rebuild daily_risk_state on the next tick."
exit 0
