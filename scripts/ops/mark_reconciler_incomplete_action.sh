#!/usr/bin/env bash
# Tier-2 operator action: re-stamp ``exit_reason='reconciler_incomplete'``
# on closed trades that still have ``pnl IS NULL`` after the backfill
# attempts (typically because Bybit's closed-pnl record didn't map 1:1
# to the bot's trade row, e.g. demo merge-mode position closes).
#
# Wraps scripts/ops/mark_reconciler_incomplete.py --apply. Single UPDATE
# in one transaction; partial failure rolls back.
#
# What this is for: makes the trade-row label match wire-side honesty.
# The 2026-06-04 reporting-cleanup sprint already made the READ side
# treat ``pnl IS NULL`` as "PnL unknown" — this action just makes the
# stored ``exit_reason`` agree.
#
# Idempotent. Safe to re-run.
#
# What this script does NOT touch:
#   - rows with COALESCE(is_backtest,0)=1
#   - rows with status != 'closed'
#   - rows where pnl IS NOT NULL (already known)
#   - rows where exit_reason != 'reconciler_filled' (other close paths)
#   - any other column (only exit_reason changes)
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="mark_reconciler_incomplete"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/mark_reconciler_incomplete.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "mark-reconciler-incomplete" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "mark-reconciler-incomplete" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND exit_reason='reconciler_filled' \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Pre-mark candidate count (closed + pnl IS NULL + exit_reason='reconciler_filled'): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "Nothing to mark — exiting clean."
    record_audit "mark-reconciler-incomplete" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to mark ====="
    exit 0
fi

echo
echo "===== mark_reconciler_incomplete.py --apply ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND exit_reason='reconciler_filled' \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Post-mark candidate count: ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "mark-reconciler-incomplete" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "mark-reconciler-incomplete" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Mark complete. ${pre_count} → ${post_count} candidate rows."
exit 0
