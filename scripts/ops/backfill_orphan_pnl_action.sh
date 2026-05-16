#!/usr/bin/env bash
# Tier-2 operator action: backfill exit_price + realised PnL on
# orphaned trades that closed via Bybit V5 broker-side SL/TP and
# were watchdog-orphaned with exit_price=NULL.
#
# Wraps scripts/ops/backfill_orphan_pnl.py --apply. The Python
# script prints a preview of every row it touches before writing,
# so a single invocation gives both preview and write. The UPDATE
# is guarded by ``WHERE status='orphaned'`` so re-running is a
# no-op once the rows are filled.
#
# What this is for: companion to PR #1299
# (claude/exit-price-from-closed-pnl). PR #1299 makes new orphans
# impossible by sourcing the real exit fill from Bybit V5
# /v5/position/closed-pnl as the trade closes. This action
# retroactively applies the same recovery to the cluster of orphans
# left behind by the pre-#1268 UUID-orderid bug (trade ids 1450 +
# 1454-1466 on bybit_2 vwap) — and to any other orphan within
# Bybit's 7-day closed-pnl retention window.
#
# Idempotent. Safe to re-run — rows that already moved to
# status='closed' are skipped by the SQL guard.
#
# What this script does NOT touch:
#   - rows with COALESCE(is_backtest,0)=1
#   - rows with status != 'orphaned'
#   - rows with exit_reason != 'stuck_strategy_watchdog'
#     (other orphan reasons need different recovery)
#   - rows where Bybit no longer has the closed-pnl record
#     (>7-day window). These remain orphaned and are listed in
#     the script output for operator follow-up.
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="backfill_orphan_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_orphan_pnl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-orphan-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-orphan-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Pre-snapshot: count of rows the helper considers candidates.
pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='orphaned' \
       AND exit_reason='stuck_strategy_watchdog' \
       AND exit_price IS NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Pre-backfill candidate count (orphaned + stuck_strategy_watchdog + NULL exit_price): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "Nothing to backfill — exiting clean."
    record_audit "backfill-orphan-pnl" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to backfill ====="
    exit 0
fi

echo
echo "===== backfill_orphan_pnl.py --apply ====="
# The script's own output carries the preview of every row it
# touches (capped at first 20 in the output, with a "and N more"
# tail) plus a "skipped" section for rows where Bybit had no
# matching record. We echo both stdout + stderr so the audit
# comment is the whole story.
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

# Post-snapshot: how many orphan rows remain. Non-zero means Bybit
# couldn't provide the close fill for some rows (likely 7-day
# window expired or qty filter mismatched).
post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='orphaned' \
       AND exit_reason='stuck_strategy_watchdog' \
       AND exit_price IS NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Post-backfill candidate count: ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "backfill-orphan-pnl" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: backfill helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "backfill-orphan-pnl" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Backfill complete. ${pre_count} → ${post_count} candidate rows."
exit 0
