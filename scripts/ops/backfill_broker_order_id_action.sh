#!/usr/bin/env bash
# Tier-2 operator action: backfill the trades.broker_order_id join key from the
# notes JSON blob (Slice B / B0, MB-20260629-ALLOC-COSTCAP).
#
# Wraps scripts/ops/backfill_broker_order_id.py. The broker's entry orderId has
# always ridden inside notes.trade_id; the new broker_order_id column
# (database._migrate_add_broker_order_id) promotes it to a first-class, indexed
# join key so the Slice-B broker-truth cost sweep can tie a trade to its
# exchange_fills rows EXACTLY. Forward rows get it at open; this one-shot fills
# the historical book.
#
# Observability-only: writes only broker_order_id; NEVER touches pnl, cost
# columns, the order path, or any live-trading state. Idempotent +
# non-destructive: fills only rows where broker_order_id IS NULL, so a re-run is
# a no-op. Does NOT restart any service. Writes NO cost (the fee/funding sweep
# that consumes this key is a separate follow-up).
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (default) → prints candidate/would-write/skipped counts, no write.
#   2. --apply           → commits the UPDATEs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_broker_order_id.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-broker-order-id" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-broker-order-id" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== backfill_broker_order_id.py (DRY-RUN preview) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run preview exited ${dry_code} — NOT applying."
    record_audit "backfill-broker-order-id" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_broker_order_id.py --apply (COMMIT) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --apply
apply_code=$?
set -e
if [ "${apply_code}" -ne 0 ]; then
    log "ERROR: apply exited ${apply_code}."
    record_audit "backfill-broker-order-id" "failed" \
        "{\"phase\": \"apply\", \"exit_code\": ${apply_code}}" >/dev/null || true
    exit "${apply_code}"
fi

record_audit "backfill-broker-order-id" "ok" "{\"db\": \"${DB_PATH}\"}" >/dev/null || true
log "backfill-broker-order-id complete."
