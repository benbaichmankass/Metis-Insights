#!/usr/bin/env bash
# Tier-1 read-only diagnostic: dump Bybit V5 closed-pnl records for
# the search window of a single trade row.
#
# Wraps scripts/ops/inspect_closed_pnl.py. Reads the trade by id
# from trade_journal.db, queries Bybit /v5/position/closed-pnl with
# the same parameters the matcher uses, and prints every record
# Bybit returned plus what the pre-fix and post-fix matchers would
# select.
#
# No DB writes. No live-trading side effects.
#
# Operator invokes via operator-actions issue with body:
#   action: inspect-closed-pnl
#   reason: <text>
#   trade_id: <int>   (default: 1540 — the canonical incident-#1411 example)
set -euo pipefail

SCRIPT_NAME="inspect_closed_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets  # exchange creds for the Bybit call
DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/inspect_closed_pnl.py"

# trade_id is plumbed from the operator-actions workflow as
# ACTION_TRADE_ID (added in the same PR as this script). Default 1540.
TRADE_ID="${ACTION_TRADE_ID:-1540}"

if ! [[ "${TRADE_ID}" =~ ^[0-9]+$ ]]; then
    log "ERROR: trade_id must be a positive integer; got '${TRADE_ID}'"
    record_audit "inspect-closed-pnl" "error" \
        "{\"reason\": \"bad trade_id\", \"trade_id\": \"${TRADE_ID}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    record_audit "inspect-closed-pnl" "error" \
        "{\"reason\": \"helper missing\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db missing at ${DB_PATH}"
    record_audit "inspect-closed-pnl" "error" \
        "{\"reason\": \"db missing\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== inspect_closed_pnl.py --trade-id ${TRADE_ID} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" --trade-id "${TRADE_ID}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "inspect-closed-pnl" "failed" \
        "{\"trade_id\": ${TRADE_ID}, \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "inspect-closed-pnl" "ok" \
    "{\"trade_id\": ${TRADE_ID}}" >/dev/null || true
exit 0
