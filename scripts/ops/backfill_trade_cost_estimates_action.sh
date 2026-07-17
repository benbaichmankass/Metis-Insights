#!/usr/bin/env bash
# Tier-2 operator action: backfill the fixed-model round-trip cost ESTIMATE onto
# uncosted historical closed trades (MB-20260629-ALLOC-COSTCAP).
#
# Wraps scripts/ops/backfill_trade_cost_estimates.py. The live close path stamps
# trades.fee_taker_usd + cost_source='estimate' on every close (M18 P0a), but
# that writer only went live recently — trades that closed BEFORE it have no
# cost at all (as of 2026-07-17: 86/798 costed, 712 uncosted). A net-R label
# over the historical book is therefore missing cost for ~89% of trades. This
# one-shot backfill applies the SAME pure estimator the live writer uses to every
# uncosted closed non-backtest row, giving the whole book a consistent modelled
# cost.
#
# Observability-only: writes only fee_taker_usd + cost_source; NEVER touches pnl,
# the order path, or any live-trading state. Idempotent + non-destructive: skips
# any row already carrying a cost (cost_source set OR fee_taker_usd present), so
# it never overwrites broker truth / a prior estimate and a re-run is a no-op.
# Does NOT restart any service. Does NOT populate funding_paid_usd / fee_maker_usd
# (those need the broker-truth writer, a separate follow-up).
#
# Two invocations in one run for a self-documenting audit trail:
#   1. dry-run (default) → prints candidate/would-write/skipped counts, no write.
#   2. --apply           → commits the UPDATEs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/backfill_trade_cost_estimates.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: backfill helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "backfill-trade-costs" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "backfill-trade-costs" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

echo
echo "===== backfill_trade_cost_estimates.py (DRY-RUN preview) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}"
dry_code=$?
set -e
if [ "${dry_code}" -ne 0 ]; then
    log "ERROR: dry-run preview exited ${dry_code} — NOT applying."
    record_audit "backfill-trade-costs" "failed" \
        "{\"phase\": \"dry-run\", \"exit_code\": ${dry_code}}" >/dev/null || true
    exit "${dry_code}"
fi

echo
echo "===== backfill_trade_cost_estimates.py --apply (COMMIT) ====="
set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" --apply
apply_code=$?
set -e
if [ "${apply_code}" -ne 0 ]; then
    log "ERROR: apply exited ${apply_code}."
    record_audit "backfill-trade-costs" "failed" \
        "{\"phase\": \"apply\", \"exit_code\": ${apply_code}}" >/dev/null || true
    exit "${apply_code}"
fi

record_audit "backfill-trade-costs" "ok" "{\"db\": \"${DB_PATH}\"}" >/dev/null || true
log "backfill-trade-costs complete."
