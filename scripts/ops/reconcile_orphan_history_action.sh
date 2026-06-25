#!/usr/bin/env bash
# system-action wrapper: historical orphan-flap reconciliation (one-shot).
#
# Runs scripts/ops/reconcile_orphan_history.py on the live VM. DRY-RUN by
# default — prints the full per-cluster plan (which rows would be KEPT as the
# canonical, which VOID-flagged as superseded phantom duplicates) WITHOUT
# writing. Only writes when ACTION_APPLY is true, and takes a timestamped DB
# backup first.
#
# Orphan-flap hardening item #5 (operator directive 2026-06-24): collapse the
# phantom flap duplicates a position left behind so every physical position is
# ONE reconciled row, and no trade rests silently in an orphan state.
#
# Env (passed by system-actions.yml):
#   ACTION_APPLY - "true" to write (backup taken first); else = dry-run report
#
# WHY apply is gated: the live apply void-flags rows on a real-money bybit_2
# cluster (and the alpaca_paper / ib_paper orphans). Run dry-run first, eyeball
# the plan, then apply with operator approval.
#
# Idempotent. Safe to re-run — rows already reconcile_status='superseded' (or
# already in their target reconcile state) are skipped by the SQL guard. Pure
# journal hygiene: never closes/opens an exchange position, never deletes a row.
set -euo pipefail

SCRIPT_NAME="reconcile_orphan_history"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/reconcile_orphan_history.py"
ACTION_APPLY="${ACTION_APPLY:-}"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: reconcile helper not present at ${PY_SCRIPT}. Did the VM pull latest main?"
    record_audit "reconcile-orphan-history" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "reconcile-orphan-history" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=()
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> reconcile-orphan-history: APPLY mode — will write (DB backup taken first)."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> reconcile-orphan-history: DRY-RUN (set apply: true to write)."
    ;;
esac

echo
echo "===== reconcile_orphan_history.py ${ARGS[*]} ====="
set +e
TRADE_JOURNAL_DB="${DB_PATH}" "${PY}" "${PY_SCRIPT}" "${ARGS[@]}"
exit_code=$?
set -e

if [ "${exit_code}" -ne 0 ]; then
    record_audit "reconcile-orphan-history" "failed" \
        "{\"apply\": \"${ACTION_APPLY}\", \"exit_code\": ${exit_code}}" >/dev/null || true
    log "ERROR: reconcile helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "reconcile-orphan-history" "ok" \
    "{\"apply\": \"${ACTION_APPLY}\"}" >/dev/null || true
log "reconcile-orphan-history complete (apply=${ACTION_APPLY:-false})."
exit 0
