#!/usr/bin/env bash
# Tier-2 operator action: REVERT the 2026-05-18 dispatch of
# backfill-monitor-closed-pnl (issue #1411).
#
# Wraps scripts/ops/revert_backfill_monitor_closed_pnl.py --apply.
# The Python script restores pnl from notes.original_pnl (preserved
# as audit trail by the original backfill), recomputes pnl_percent
# from the gross formula, and derives exit_price algebraically from
# the gross PnL equation. Returns each row to its pre-backfill
# state. Audit-stamps notes with reverted_at / reverted_by.
#
# Why: the backfill output (transcript on issue #1411) showed many
# distinct trades collapsing to identical pnl values and one trade
# swinging from +$10.48 to -$0.17 — signature of
# account_closed_pnl_for_trade matching the wrong Bybit record. The
# matching bug needs a separate investigation before any retry; in
# the meantime the dashboard returns to its (known-wrong-but-
# consistent) gross-PnL state. The matching-bug investigation will
# follow as a separate PR; do not re-dispatch the original backfill
# until that lands.
#
# Idempotent. The WHERE filter targets only rows still carrying
# notes.backfilled_by='backfill_monitor_closed_pnl_script'; the
# revert removes that stamp, so re-runs find nothing.
set -euo pipefail

SCRIPT_NAME="revert_backfill_monitor_closed_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# No exchange creds needed — the revert reads only from local notes.
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/revert_backfill_monitor_closed_pnl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: revert helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "revert-backfill-monitor-closed-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "revert-backfill-monitor-closed-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Pre-snapshot: count of rows still carrying the backfill stamp.
pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE notes LIKE '%\"backfilled_by\": \"backfill_monitor_closed_pnl_script\"%' \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Pre-revert candidate count (rows carrying backfill stamp): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "No backfill-stamped rows — nothing to revert."
    record_audit "revert-backfill-monitor-closed-pnl" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to revert ====="
    exit 0
fi

echo
echo "===== revert_backfill_monitor_closed_pnl.py --apply ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE notes LIKE '%\"backfilled_by\": \"backfill_monitor_closed_pnl_script\"%' \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Post-revert candidate count: ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "revert-backfill-monitor-closed-pnl" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: revert helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "revert-backfill-monitor-closed-pnl" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Revert complete. ${pre_count} → ${post_count} stamped rows."
exit 0
