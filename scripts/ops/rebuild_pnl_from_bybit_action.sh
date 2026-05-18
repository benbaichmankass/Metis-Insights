#!/usr/bin/env bash
# Tier-2 operator action: one-shot DB rebuild from Bybit ground truth.
#
# Wraps scripts/ops/rebuild_pnl_from_bybit.py --apply. Iterates Bybit's
# closed-pnl records and rewrites every matched DB row's pnl /
# exit_price / pnl_percent / notes to agree with Bybit. Stamps notes
# with `rebuilt_at` + `rebuilt_by` + `pre_rebuild_pnl` (audit trail).
#
# Why: 2026-05-18 audit (#1429) revealed 92 of 122 matched DB rows
# have |diff| > $0.01 vs Bybit, plus 11 stuck rows from #1419 the
# revert couldn't undo (notes truncated). This is the canonical
# recovery: don't try to revert, just overwrite from ground truth.
#
# Idempotent. Re-runs find no changes.
#
# What this script does NOT touch:
#   - Backtest rows
#   - Open rows
#   - Rows older than 7 days (Bybit retention)
#   - Rows that don't match a Bybit record (likely partial-fills /
#     retried opens / DB-only state)
#
# Tier-2 (DB mutation, no live-trading side effects).
set -euo pipefail

SCRIPT_NAME="rebuild_pnl_from_bybit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/rebuild_pnl_from_bybit.py"

ACCOUNT="${ACCOUNT_ID:-}"
DAYS="${ACTION_DAYS:-7}"

if [ -z "${ACCOUNT}" ]; then
    log "ERROR: rebuild-pnl-from-bybit requires 'account: <id>' in issue body"
    record_audit "rebuild-pnl-from-bybit" "error" \
        "{\"reason\": \"missing account\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    record_audit "rebuild-pnl-from-bybit" "error" \
        "{\"reason\": \"helper missing\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "rebuild-pnl-from-bybit" "error" \
        "{\"reason\": \"db missing\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== rebuild_pnl_from_bybit.py --account ${ACCOUNT} --days ${DAYS} --apply ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" \
    --account "${ACCOUNT}" --days "${DAYS}" --apply
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "rebuild-pnl-from-bybit" "failed" \
        "{\"account\": \"${ACCOUNT}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "rebuild-pnl-from-bybit" "ok" \
    "{\"account\": \"${ACCOUNT}\", \"days\": ${DAYS}}" >/dev/null || true
exit 0
