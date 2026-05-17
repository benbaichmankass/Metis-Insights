#!/usr/bin/env bash
# Tier-2 operator action: backfill realised PnL on closed trades that
# have pnl=NULL.
#
# Wraps scripts/ops/backfill_pnl_nulls.py --apply. The Python script
# prints the proposed updates BEFORE writing, so a single invocation
# gives both the preview (in the wrapper output) and the write. The
# UPDATE statement is guarded by ``WHERE pnl IS NULL`` so re-running
# is a no-op once the rows are filled.
#
# What this is for: the 2026-05-10 layer-2 health review surfaced 38
# trades that closed via tp_cross with status='closed' + exit_price set
# but pnl=NULL because the monitor close path never wrote PnL. PR #739
# fixed the live writer; this action is the canonical way to backfill
# the historical nulls without SSH-ing the VM.
#
# Idempotent. Safe to re-run — rows that already have pnl set are
# skipped by the SQL guard.
#
# What this script does NOT touch:
#   - rows with status='rejected' (they never filled — NULL is correct)
#   - rows with COALESCE(is_backtest,0)=1
#   - rows missing entry_price / exit_price / position_size
#   - any column other than pnl + pnl_percent
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="backfill_pnl_nulls"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # exchange creds for account_closed_pnl_for_trade
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_pnl_nulls.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-pnl-nulls" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-pnl-nulls" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# Pre-snapshot: count of rows the helper considers candidates.
pre_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND exit_price IS NOT NULL \
       AND entry_price IS NOT NULL \
       AND position_size IS NOT NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Pre-backfill candidate count (closed + pnl IS NULL + complete inputs): ${pre_count}"

if [ "${pre_count}" = "0" ]; then
    log "Nothing to backfill — exiting clean."
    record_audit "backfill-pnl-nulls" "noop" \
        "{\"pre_count\": 0}" >/dev/null || true
    echo
    echo "===== nothing to backfill ====="
    exit 0
fi

echo
echo "===== backfill_pnl_nulls.py --apply ====="
# The script's own output carries the preview of every row it touches
# (capped at first 10 in the output, with a "and N more" tail). We
# echo both stdout + stderr so the wrapper's audit comment is the
# whole story.
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --apply
exit_code=$?
set -e

# Post-snapshot: how many candidates remain (should be 0 unless rows
# were skipped for degenerate inputs).
post_count="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE status='closed' \
       AND pnl IS NULL \
       AND exit_price IS NOT NULL \
       AND entry_price IS NOT NULL \
       AND position_size IS NOT NULL \
       AND COALESCE(is_backtest,0)=0;" 2>/dev/null || echo "?")"
log "Post-backfill candidate count (should be 0): ${post_count}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "backfill-pnl-nulls" "failed" \
        "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: backfill helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "backfill-pnl-nulls" "ok" \
    "{\"pre_count\": ${pre_count}, \"post_count\": \"${post_count}\"}" \
    >/dev/null || true
log "Backfill complete. ${pre_count} → ${post_count} candidate rows."
exit 0
