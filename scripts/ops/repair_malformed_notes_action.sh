#!/usr/bin/env bash
# Tier-2 operator action: repair legacy malformed-JSON blobs in
# trade_journal.db (BL-20260618-CLOSEDFLAT-MALFORMED-JSON /
# BL-20260709-MALFORMED-NOTES-LEGACY-REPAIR).
#
# Wraps scripts/ops/repair_malformed_notes.py. Before the write-side was
# migrated to dump_capped (RISK-1 Task 2, PR #6037), json.dumps(payload)[:N]
# could persist INVALID JSON into trades.notes and
# order_packages.{signal_logic,meta}. The write-path is now fixed
# (INV-6 recent=0), but a legacy backlog of rows with json_valid(col)=0
# remains; this action rewrites each into a valid, length-bounded envelope
# that salvages the intact load-bearing keys and preserves the raw original
# under _original_truncated. Idempotent by construction (a repaired row has
# json_valid=1, so a re-run never re-touches it).
#
# DRY-RUN by default (prints the per-column count + a sample); pass
# apply: true (issue body) to perform the write. Mirrors the dry-run/apply
# contract of the supersede-*-artifacts actions.
#
# What this does NOT touch:
#   - rows already valid (json_valid=1)
#   - any column outside trades.notes / order_packages.{signal_logic,meta}
#   - the running ict-trader-live.service (no restart required)
set -euo pipefail

SCRIPT_NAME="repair_malformed_notes"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/repair_malformed_notes.py"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: repair helper not present at ${PY_SCRIPT}. Did the VM pull the latest main?"
    record_audit "repair-malformed-notes" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi

if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "repair-malformed-notes" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

# ACTION_APPLY gates dry-run (default) vs the real write. Only an explicit
# true/True performs the repair; anything else (blank/false) is a dry run.
APPLY_FLAG=""
case "${ACTION_APPLY:-}" in
    true|True) APPLY_FLAG="--apply" ;;
    *)         APPLY_FLAG="" ;;
esac

if [ -n "${APPLY_FLAG}" ]; then
    log "Running repair_malformed_notes.py --apply (Tier-2 DB write) on ${DB_PATH} …"
    echo
    echo "===== repair_malformed_notes.py --apply ====="
else
    log "Running repair_malformed_notes.py DRY RUN (counts only; pass apply: true to write) on ${DB_PATH} …"
    echo
    echo "===== repair_malformed_notes.py (dry run) ====="
fi

set +e
TRADE_JOURNAL_DB="${DB_PATH}" python3 "${PY_SCRIPT}" ${APPLY_FLAG}
exit_code=$?
set -e

remaining="$(sqlite3 "${DB_PATH}" \
    "SELECT COUNT(*) FROM trades \
     WHERE notes IS NOT NULL AND notes != '' AND json_valid(notes)=0;" 2>/dev/null || echo "?")"
log "Post-run malformed trades.notes count (json_valid=0): ${remaining}"

if [ "${exit_code}" -ne 0 ]; then
    record_audit "repair-malformed-notes" "failed" \
        "{\"apply\": \"${ACTION_APPLY:-}\", \"remaining_trades_notes\": \"${remaining}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "ERROR: repair helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "repair-malformed-notes" "ok" \
    "{\"apply\": \"${ACTION_APPLY:-}\", \"remaining_trades_notes\": \"${remaining}\"}" \
    >/dev/null || true
log "repair-malformed-notes complete (apply=${ACTION_APPLY:-false})."
exit 0
