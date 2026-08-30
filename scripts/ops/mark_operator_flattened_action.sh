#!/usr/bin/env bash
# Tier-2 operator action: mark specific CLOSED trades as operator-flattened, so
# exit analyses can exclude them.
#
# WHY THIS EXISTS. The flatten scripts deliberately leave the journal row to the
# trader's reconciler ("close-on-disappear"). The reconciler then stamps
# exit_reason='reconciler_filled' — a correct description of HOW the row closed
# and a misleading one about WHY. The row becomes indistinguishable from an
# ordinary close the reconciler happened to book, so perExitPath buckets it with
# genuine strategy exits and the exit-refinement corpus reads its entry->exit
# geometry as evidence about the strategy's exit quality, when a human chose the
# exit time for a reason unrelated to the market.
#
# close-stranded-journal-row already owns the operator-flatten convention, but it
# hard-filters WHERE status='open' — it is the tool for when the reconciler
# FAILS. This is the missing half for when it SUCCEEDS.
#
# Wraps scripts/ops/mark_operator_flattened.py. Self-test as a PRECONDITION,
# then a dry-run plan, then --apply. Single transaction; partial failure rolls
# back. Idempotent (a row already carrying notes.closed_by_operator is skipped).
#
# What this does NOT touch:
#   - any monetary field (pnl, pnl_percent, exit_price)
#   - notes.exit_price_source — the PRICE's provenance and the CLOSE's CAUSE are
#     different questions, and clobbering a broker-truth stamp with
#     'operator_flatten_fill' is precisely the defect recorded in
#     BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED
#   - rows that are not status='closed', backtest rows, or ids that do not exist
#     (each is REFUSED, and nothing is written if ANY id is bad)
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="mark_operator_flattened"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/mark_operator_flattened.py"

TRADE_IDS="${ACTION_TRADE_ID:-}"
REASON_TEXT="${ACTION_REASON:-}"

if [ -z "${TRADE_IDS// }" ]; then
    log "ERROR: ACTION_TRADE_ID is empty — this action never derives its targets."
    record_audit "mark-operator-flattened" "error" \
        "{\"reason\": \"no trade_id\"}" >/dev/null || true
    exit 1
fi
if ! printf '%s' "${TRADE_IDS}" | grep -Eq '^[0-9]+(,[0-9]+)*$'; then
    log "ERROR: trade_id '${TRADE_IDS}' invalid (want digits, comma-separated)."
    record_audit "mark-operator-flattened" "error" \
        "{\"reason\": \"bad trade_id format\"}" >/dev/null || true
    exit 1
fi
if [ -z "${REASON_TEXT// }" ]; then
    log "ERROR: ACTION_REASON is empty — the whole point is recording WHY."
    record_audit "mark-operator-flattened" "error" \
        "{\"reason\": \"no reason text\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "mark-operator-flattened" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "mark-operator-flattened" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== PRECONDITION: mark_operator_flattened.py --self-test ====="
python3 "${PY_SCRIPT}" --self-test

echo
echo "===== BEFORE (the rows as they stand) ====="
sqlite3 -header -column "${DB_PATH}" \
    "SELECT id, account_id, symbol, status, exit_reason, pnl \
     FROM trades WHERE id IN (${TRADE_IDS});" || true

echo
echo "===== DRY-RUN plan ====="
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" \
    --trade-ids "${TRADE_IDS}" --reason "${REASON_TEXT}"

echo
echo "===== APPLY ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" \
    --trade-ids "${TRADE_IDS}" --reason "${REASON_TEXT}" --apply
exit_code=$?
set -e

echo
echo "===== AFTER ====="
sqlite3 -header -column "${DB_PATH}" \
    "SELECT id, account_id, symbol, status, exit_reason, pnl \
     FROM trades WHERE id IN (${TRADE_IDS});" || true

if [ "${exit_code}" -ne 0 ]; then
    record_audit "mark-operator-flattened" "failed" \
        "{\"trade_ids\": \"${TRADE_IDS}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "mark-operator-flattened" "ok" \
    "{\"trade_ids\": \"${TRADE_IDS}\"}" >/dev/null || true
log "Mark complete for trade ids ${TRADE_IDS}."
exit 0
