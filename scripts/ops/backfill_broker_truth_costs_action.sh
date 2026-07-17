#!/usr/bin/env bash
# Tier-2 operator action: upgrade cleanly-attributable closed trades from a
# fixed-model cost ESTIMATE to BROKER-TRUTH fees (Slice B / B2, MB-20260629-ALLOC-COSTCAP).
#
# Wraps scripts/ops/backfill_broker_truth_costs.py. Joins trade_journal.db::trades
# to the exchange-fills store (runtime_state/exchange_fills.sqlite) by the
# trades.broker_order_id join key (Slice-B/B0) and FIFO-attributes each round
# trip's fees. Writes fee_taker_usd + fee_maker_usd + cost_source='broker' ONLY
# for CLEAN attributions (both legs matched, unambiguous, USD fees); ambiguous
# (netted) / entry-only / non-USD trades keep their estimate.
#
# Observability-only: writes only fee_taker/maker + cost_source; NEVER touches
# pnl, funding_paid_usd, the order path, or any live-trading state. Idempotent +
# non-destructive: overwrites an 'estimate' with 'broker' but never an existing
# 'broker' row, so a re-run only picks up newly-attributable trades. Does NOT
# restart any service. PREREQ: run pull-exchange-fills (populate the store) and
# backfill-broker-order-id (populate the join key) first.
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (default) → prints the coverage report (clean/ambiguous/…), no write.
#   2. --apply           → commits the broker-truth UPDATEs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
# Canonical, DATA_DIR-anchored fills-store path (holds both the fills and the
# exchange_funding table the sweep reads). Passed explicitly so the sweep
# targets the SAME absolute file the puller wrote + the systemd reader uses —
# a fresh SSH wrapper shell doesn't inherit systemd's DATA_DIR, so the python
# child would otherwise resolve runtime_state/ repo-relative and report "fills
# store not found" (BL-20260717-FILLS-STORE-PATH-SPLIT).
FILLS_DB="$(fills_store_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_broker_truth_costs.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-broker-truth-costs" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-broker-truth-costs" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== backfill_broker_truth_costs.py (DRY-RUN coverage report) ====="
echo "fills store: ${FILLS_DB}"
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --fills-db "${FILLS_DB}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run exited ${dry_code} — NOT applying."
    record_audit "backfill-broker-truth-costs" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_broker_truth_costs.py --apply (COMMIT) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --fills-db "${FILLS_DB}" --apply
apply_code=$?
set -e
if [ "${apply_code}" -ne 0 ]; then
    log "ERROR: apply exited ${apply_code}."
    record_audit "backfill-broker-truth-costs" "failed" \
        "{\"phase\": \"apply\", \"exit_code\": ${apply_code}}" >/dev/null || true
    exit "${apply_code}"
fi

record_audit "backfill-broker-truth-costs" "ok" "{\"db\": \"${DB_PATH}\"}" >/dev/null || true
log "backfill-broker-truth-costs complete."
