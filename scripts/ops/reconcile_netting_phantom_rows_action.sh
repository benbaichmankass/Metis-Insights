#!/usr/bin/env bash
# Tier-2 operator action: close the bybit_1 netting phantom-open rows
# (BL-20260731-W1-JOURNAL-EXCHANGE-DIVERGENCE-MAP, operator-approved
# 2026-08-01).
#
# Wraps scripts/ops/reconcile_netting_phantom_rows.py. The one-way-netting
# partial-close class left four ict_scalp journal rows "open" on bybit_1
# whose exchange share no longer exists; the same-moment check (vm-diag
# #8218 vs trainer-diag #8227, both 2026-08-01) shows the remaining
# pairs-sleeve rows alone match the exchange EXACTLY, so closing exactly
# these four (status closed + reconcile_status superseded + pnl left
# UNMEASURED, full provenance under notes.netting_phantom_reconcile)
# makes journal == exchange with no orphan re-adoption.
#
# DRY-RUN by default; pass apply: true (issue body) to write. The helper
# refuses any row whose live values no longer match the pinned signature,
# so the action is idempotent and safe against a since-changed DB.
# Validated 2026-08-01 on a throwaway DB: dry-run matched, --apply
# repaired, re-run refused all (already-closed), mismatched size refused.
#
# What this does NOT touch: any row outside the 4 hard-coded ids; the
# BNBUSDT pairs-row surplus (root-cause item, pairs sleeve owns it); the
# running ict-trader-live.service (no restart required).
set -euo pipefail

SCRIPT_NAME="reconcile_netting_phantom_rows"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/reconcile_netting_phantom_rows.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: reconcile helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "reconcile-netting-phantom-rows" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "reconcile-netting-phantom-rows" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# ACTION_APPLY gates dry-run (default) vs the real write.
APPLY_FLAG=""
case "${ACTION_APPLY:-}" in
    true|True) APPLY_FLAG="--apply" ;;
    *)         APPLY_FLAG="" ;;
esac

if [ -n "${APPLY_FLAG}" ]; then
    log "Running reconcile_netting_phantom_rows.py --apply (Tier-2 DB write) on ${DB_PATH} …"
else
    log "Running reconcile_netting_phantom_rows.py DRY RUN (pass apply: true to write) on ${DB_PATH} …"
fi
echo
echo "===== reconcile_netting_phantom_rows.py ${APPLY_FLAG:-(dry run)} ====="

set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" ${APPLY_FLAG}
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "reconcile-netting-phantom-rows" "failed" \
        "{\"apply\": \"${ACTION_APPLY:-}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: reconcile helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "reconcile-netting-phantom-rows" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\"}" >/dev/null || true
log "reconcile-netting-phantom-rows complete (apply=${ACTION_APPLY:-false})."
exit 0
