#!/usr/bin/env bash
# Tier-2 operator action: backfill realised PnL on monitor-closed
# trades that recorded gross PnL (from the deleted ``_compute_close_pnl``
# formula) instead of Bybit's authoritative net.
#
# Wraps scripts/ops/backfill_monitor_closed_pnl.py --apply. The
# Python script prints a preview of every row it touches before
# writing, so a single invocation gives both preview and write. The
# UPDATE is guarded by ``notes NOT LIKE '%bybit_closed_pnl%'`` so
# re-running is a no-op once the notes get the stamp.
#
# What this is for: companion to PR #1409
# (claude/bybit-only-pnl). PR #1409 deleted ``_compute_close_pnl``
# and wired ``_sweep_pending_pnl_from_bybit`` into ``run_monitor_tick``
# so new monitor closes get reconciled against Bybit truth within a
# couple of ticks. The new sweep only matches ``pnl IS NULL``, so it
# can't fix historical rows where the old code wrote a (wrong) gross
# value. This action does that one-shot fix. Visible example as of
# 2026-05-18: trade #1540, closed via tp_cross at pnl=+$1.03 gross
# vs Bybit-net of ~+$0.57.
#
# Idempotent. Safe to re-run — rows that already carry
# ``notes.bybit_closed_pnl`` are skipped by the SQL guard.
#
# What this script does NOT touch:
#   - rows with COALESCE(is_backtest,0)=1
#   - rows with status != 'closed'
#   - rows whose notes already contain bybit_closed_pnl (already
#     correct from the reconciler or live sweep)
#   - rows older than Bybit's 7-day closed-pnl retention window
#     (the original gross PnL is the only number available; listed
#     in the script output for operator awareness)
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="backfill_monitor_closed_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # exchange creds for account_closed_pnl_for_trade
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_monitor_closed_pnl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-monitor-closed-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-monitor-closed-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Pre-snapshot: count of rows the helper considers candidates.
pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND COALESCE(is_backtest,0)=0 \
       AND (notes IS NULL OR notes NOT LIKE '%bybit_closed_pnl%') \
       AND datetime(created_at) >= datetime('now', '-7 days');" 2>/dev/null || echo "?")"
log "Pre-backfill candidate count (closed + no bybit_closed_pnl stamp + <7d old): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "Nothing to backfill — exiting clean."
    record_audit "backfill-monitor-closed-pnl" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to backfill ====="
    exit 0
fi

echo
echo "===== backfill_monitor_closed_pnl.py --apply ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

# Post-snapshot: how many rows still lack the stamp. Non-zero means
# Bybit couldn't provide the close fill for some rows (likely qty
# filter mismatched or the trade really did open outside the 7-day
# window despite the SQL filter, e.g. clock skew).
post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND COALESCE(is_backtest,0)=0 \
       AND (notes IS NULL OR notes NOT LIKE '%bybit_closed_pnl%') \
       AND datetime(created_at) >= datetime('now', '-7 days');" 2>/dev/null || echo "?")"
log "Post-backfill candidate count: ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "backfill-monitor-closed-pnl" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: backfill helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "backfill-monitor-closed-pnl" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Backfill complete. ${pre_count} → ${post_count} candidate rows."
exit 0
