#!/usr/bin/env bash
# pull-bybit-transaction-log — pull Bybit's OWN wallet ledger
# (/v5/account/transaction-log) into the venue-truth store on demand.
#
# WHY AN ON-DEMAND ACTION AND NOT JUST THE HOURLY TIMER (operator directive
# 2026-08-31). The timer runs --days 7, which can never close a HISTORICAL gap:
# bybit_2's authoritative wallet figure froze on 2026-07-13 when it came from a
# pasted CSV, leaving 48 days and 59 closed real-money trades unreconciled
# (BL-20260830-BROKER-TRUTH-LEDGER-STALE-59-REAL-MONEY-CLOSES-UNRECONCILED).
# Backfilling that needs ACTION_DAYS=60, which only this path can ask for.
#
# ⚠️ A DEEP WINDOW IS WALKED, NOT ASKED FOR IN ONE CALL. Bybit V5 caps the
# queryable RANGE at 7 days while retaining far more, so `startTime = now-60d`
# returns the 7-day slice at the START of the range — the window MOVES rather
# than widening, and the run reports success over almost nothing. The fills
# puller already paid for exactly this
# (BL-20260808-FILLS-WINDOW-TOO-SHORT-TO-REPAIR-HISTORY); the transaction-log
# puller walks the range in <= MAX_RANGE_DAYS chunks, asserted by
# tests/test_pull_bybit_transaction_log.py with a control proving a single call
# would have returned a slice.
#
# COST: ceil(days/7) requests PER ACCOUNT (plus pagination inside each chunk).
# `ACTION_DAYS=60` is ~9 chunks x 3 accounts. Keep deep pulls deliberate; the
# hourly timer stays at 7.
#
# Read-only on the exchange side. Idempotent — the store keys on the venue's own
# row id, so overlapping windows insert nothing and a re-run cannot move an
# account-level P&L figure. Touches NO service and NO trade_journal.db table.
set -euo pipefail

SCRIPT_NAME="pull_bybit_transaction_log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_secrets   # every account's BYBIT_API_KEY_* / _SECRET_* from .env

# Same DATA_DIR-anchored store the fills puller and the web-api reader use — a
# fresh SSH wrapper shell does not inherit the systemd DATA_DIR, so without this
# the python child resolves runtime_state/ repo-relative
# (BL-20260717-FILLS-STORE-PATH-SPLIT).
STORE_DB="$(fills_store_path)"

DAYS="${ACTION_DAYS:-7}"
case "${DAYS}" in
    ''|*[!0-9]*)
        log "ERROR: ACTION_DAYS='${DAYS}' is not a positive integer."
        exit 1
        ;;
esac
if [ "${DAYS}" -lt 1 ]; then
    log "ERROR: ACTION_DAYS='${DAYS}' must be >= 1."
    exit 1
fi

log "pulling bybit transaction log: days=${DAYS} store=${STORE_DB}"
"${REPO_DIR}/.venv/bin/python" -u "${REPO_DIR}/scripts/ops/pull_bybit_transaction_log.py" \
    --all-bybit-accounts --days "${DAYS}" --store-db "${STORE_DB}"
