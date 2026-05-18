#!/usr/bin/env bash
# Tier-1 read-only diagnostic: strategy performance audit.
#
# Joins clean trade_journal.db (post-#1432 rebuild) with Bybit's
# closed-pnl ledger + execution fees and emits actionable
# breakdowns: by direction / hour-of-day / exit_reason /
# deviation_std bucket, plus R:R geometry, slippage, and fee drag.
#
# Triggered by the 2026-05-18 confirmation that the 18.25% win
# rate / -$44 7-day net is real (not accounting). With clean
# data we can now diagnose WHERE the strategy is failing.
#
# No DB writes. No live-trading side effects.
#
# Operator invokes via operator-actions issue with body:
#   action: strategy-performance-audit
#   reason: <text>
#   account: <id>     (required, e.g. bybit_2)
#   days: <int>       (optional, default 7)
set -euo pipefail

SCRIPT_NAME="strategy_performance_audit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/strategy_performance_audit.py"

ACCOUNT="${ACCOUNT_ID:-}"
DAYS="${ACTION_DAYS:-7}"

if [ -z "${ACCOUNT}" ]; then
    log "ERROR: strategy-performance-audit requires 'account: <id>' in issue body"
    record_audit "strategy-performance-audit" "error" \
        "{\"reason\": \"missing account\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    record_audit "strategy-performance-audit" "error" \
        "{\"reason\": \"helper missing\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "strategy-performance-audit" "error" \
        "{\"reason\": \"db missing\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== strategy_performance_audit.py --account ${ACCOUNT} --days ${DAYS} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" \
    --account "${ACCOUNT}" --days "${DAYS}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "strategy-performance-audit" "failed" \
        "{\"account\": \"${ACCOUNT}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "strategy-performance-audit" "ok" \
    "{\"account\": \"${ACCOUNT}\", \"days\": ${DAYS}}" >/dev/null || true
exit 0
