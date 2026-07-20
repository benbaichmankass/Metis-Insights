#!/usr/bin/env bash
# Tier-2 operator action: honest-null repair of the Jun-2026 netted-position
# misattribution rows (BL-20260720-ICTSCALP-PASTSTOP-EXITS +
# BL-20260720-PAPER-PNL-CROSSWRITE).
#
# Wraps scripts/ops/repair_netted_misattributed_rows.py. Several journal
# trades shared one netted Bybit position; each position-level bracket fire
# flattened everything but closed only the newest row, and the phantom-open
# siblings were later mis-resolved with OTHER trades' closed-pnl records or
# stale mark prices. The stored pnl/exit_price on the 8 target rows are not
# measurements of those trades; the repair nulls them with full provenance
# preserved under notes.netted_repair and stamps
# exit_reason='netted_misattributed' so analytics can filter explicitly.
#
# DRY-RUN by default; pass apply: true (issue body) to write. The helper
# refuses any row whose current values no longer match the expected corrupt
# signature, so the action is idempotent and safe against a since-changed DB.
# Validated 2026-07-20 on the trainer's synced copy: dry-run 8/8 matched;
# --apply on a throwaway copy repaired 8/8 and re-ran 0/8 (issue #7125).
#
# What this does NOT touch: any row outside the 8 hard-coded ids; the running
# ict-trader-live.service (no restart required).
set -euo pipefail

SCRIPT_NAME="repair_netted_rows"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/repair_netted_misattributed_rows.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: repair helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "repair-netted-rows" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "repair-netted-rows" "error" \
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
    log "Running repair_netted_misattributed_rows.py --apply (Tier-2 DB write) on ${DB_PATH} …"
else
    log "Running repair_netted_misattributed_rows.py DRY RUN (pass apply: true to write) on ${DB_PATH} …"
fi
echo
echo "===== repair_netted_misattributed_rows.py ${APPLY_FLAG:-(dry run)} ====="

set +e
python3 "${PY_SCRIPT}" --db "${DB_PATH}" ${APPLY_FLAG}
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "repair-netted-rows" "failed" \
        "{\"apply\": \"${ACTION_APPLY:-}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: repair helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "repair-netted-rows" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\"}" >/dev/null || true
log "repair-netted-rows complete (apply=${ACTION_APPLY:-false})."
exit 0
