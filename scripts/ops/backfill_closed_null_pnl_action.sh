#!/usr/bin/env bash
# Tier-2 operator action: backfill exit_price + realised PnL on
# closed trades where the reconciler's fallback path stamped
# status='closed' + exit_reason='reconciler_filled' without computing
# PnL (pnl=NULL, often exit_price=NULL too).
#
# Wraps scripts/ops/backfill_closed_null_pnl.py --apply. The Python
# script prints a preview of every row it touches before writing and
# carries the same Bybit-closed-pnl recovery as backfill_orphan_pnl.py
# (they share _plan_row / _apply_updates / _warn_if_silent_credential_failure).
# The UPDATE is guarded by ``WHERE pnl IS NULL`` so re-running is a
# no-op once the rows are filled.
#
# What this is for: the 2026-06-04 reporting-cleanup sprint made the
# READ side honest (/api/bot/trades/closed emits realizedPnl: null
# instead of coercing to 0; /api/bot/performance excludes pnl IS NULL
# from aggregates). This action retroactively recovers the historical
# rows so the dashboard's Live/Demo segment shows real PnL where
# Bybit still has the record.
#
# Idempotent. Safe to re-run.
#
# What this script does NOT touch:
#   - rows with COALESCE(is_backtest,0)=1
#   - rows with status != 'closed'
#   - rows where pnl IS NOT NULL (already populated)
#   - rows where Bybit no longer has the closed-pnl record
#     (>7-day window). These remain as-is and are listed in the
#     script output for operator follow-up.
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="backfill_closed_null_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # exchange creds for account_closed_pnl_for_trade
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_closed_null_pnl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-closed-null-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-closed-null-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Pre-backfill candidate count (closed + pnl IS NULL + non-backtest): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "Nothing to backfill — exiting clean."
    record_audit "backfill-closed-null-pnl" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to backfill ====="
    exit 0
fi

echo
echo "===== backfill_closed_null_pnl.py --apply ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Post-backfill candidate count: ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "backfill-closed-null-pnl" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: backfill helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "backfill-closed-null-pnl" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Backfill complete. ${pre_count} → ${post_count} candidate rows."
exit 0
