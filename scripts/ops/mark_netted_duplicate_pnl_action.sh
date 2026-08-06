#!/usr/bin/env bash
# Tier-2 operator action: mark journal rows carrying a DUPLICATED netted broker PnL
# (BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS, operator-approved 2026-08-06).
#
# Wraps scripts/ops/mark_netted_duplicate_pnl.py, which stamps
# `notes.exit_price_source = "netted_duplicate_unattributed"` (a FABRICATED source,
# so `pnl_is_trustworthy` refuses the row) and preserves the original under
# `notes.pre_remediation_exit_price_source`.
#
# WHY THIS ACTION EXISTS. The script shipped un-run: `system-actions` had no
# allowlisted entry, and the live journal lives on the trader VM where the only
# sanctioned mutation path is this workflow. Until it runs, 31 `bybit_1` rows still
# read MEASURED and feed the fidelity calibration set and the ML label builders.
#
# WHAT IT DOES NOT DO. It never rewrites `pnl`. There is no defensible per-row
# value: the broker record's magnitude belongs to the netted POSITION, and
# splitting it now — after the close, with no per-row fill to anchor to — would be
# the proration assumption dressed as a correction. Marking the number
# untrustworthy and leaving it visible is the honest operation (the same contract
# `provenance.py`'s UNMEASURED_MARKER holds for an anchorless close). It also
# restarts no service and touches no order path.
#
# ALWAYS STATE THE POPULATION. The selection is biased toward UNDER-marking on
# purpose: a cluster is `(account_id, symbol, ROUND(pnl,2))` with 2+ closed
# non-backtest rows, and is SUSPECT only when quantities differ by more than
# `--qty-spread` (default 1.5x) AND |pnl| clears `--min-abs-pnl` (default $1.00).
# Without the spread filter the raw count called 236/408 real-money rows
# "clustered"; with it, 79 rows totalling $45.52 — mostly still false positives at
# scalp size. Marking a correct row costs real information, so the defaults decline
# to touch the low-|pnl| tail. Quote the filtered figure or neither.
#
# DRY-RUN by default, and the dry run opens the DB `mode=ro` so a selection bug
# cannot write. Pass `apply: true` in the issue body to mark. Idempotent — a
# re-run finds the rows already marked and reports "to mark: 0", preserving the
# true original source captured on the first pass.
#
# Optional issue-body knobs (all plumbed through system-actions.yml):
#   apply:        true            -> WRITE (Tier-2). Default/absent -> dry run.
#   account:      bybit_1         -> restrict to one account (review scope)
#                                   NOTE the key is `account:`. `account_id:` is NOT parsed
#                                   (system-actions.yml matches ^account:) and is silently
#                                   ignored -> the run widens to ALL accounts.
set -euo pipefail

SCRIPT_NAME="mark_netted_duplicate_pnl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

heal_devnull || true

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/mark_netted_duplicate_pnl.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "mark-netted-duplicate-pnl" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "mark-netted-duplicate-pnl" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

APPLY_FLAG=""
case "${ACTION_APPLY:-}" in
    true|True) APPLY_FLAG="--apply" ;;
    *)         APPLY_FLAG="" ;;
esac

ACCOUNT_FLAG=""
if [ -n "${ACCOUNT_ID:-}" ]; then
    ACCOUNT_FLAG="--account ${ACCOUNT_ID}"
fi

if [ -n "${APPLY_FLAG}" ]; then
    log "Running mark_netted_duplicate_pnl.py --apply (Tier-2 DB write) on ${DB_PATH} …"
else
    log "Running mark_netted_duplicate_pnl.py DRY RUN (pass apply: true to write) on ${DB_PATH} …"
fi
echo
echo "===== mark_netted_duplicate_pnl.py ${APPLY_FLAG:-(dry run)} ${ACCOUNT_FLAG} ====="
set +e
# shellcheck disable=SC2086  # ACCOUNT_FLAG/APPLY_FLAG are deliberately word-split
python3 "${PY_SCRIPT}" --db "${DB_PATH}" ${ACCOUNT_FLAG} ${APPLY_FLAG}
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "mark-netted-duplicate-pnl" "failed" \
        "{\"apply\": \"${ACTION_APPLY:-}\", \"account\": \"${ACCOUNT_ID:-}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: marker exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "mark-netted-duplicate-pnl" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\", \"account\": \"${ACCOUNT_ID:-}\"}" >/dev/null || true
log "mark-netted-duplicate-pnl complete (apply=${ACTION_APPLY:-false})."
exit 0
