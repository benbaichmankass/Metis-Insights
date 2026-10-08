#!/usr/bin/env bash
# system-action wrapper: re-issue ONE suppressed prop ticket as `emitted`
# (PROP-REISSUE, 2026-10-08). Tier 2.
#
# Runs scripts/ops/prop_ticket_reissue.py on the live VM. DRY-RUN by default:
# prints the ticket row before and as it would be after, writes nothing. Only
# writes when ACTION_APPLY is true, after an SQLite backup of the journal.
#
# Allowed: ONE `suppressed` ticket whose blocking ticket is terminal, on a
# `mode: live` account, younger than the max age, rebuilt with emission's own
# sizing so the executor's intake (entry band, expiry, rule guards) decides
# placement. Everything else is refused (exit 3) with the reason.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID       - the prop account (e.g. tradeify_1)         (required)
#   ACTION_TICKET_ID - the ticket id (prop-manual-<hex>)          (required)
#   ACTION_APPLY     - "true" to write (backup first); else dry-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/prop_ticket_reissue.py"
ACCOUNT_ID="${ACCOUNT_ID:-}"
ACTION_TICKET_ID="${ACTION_TICKET_ID:-}"
ACTION_APPLY="${ACTION_APPLY:-}"

if [ -z "${ACCOUNT_ID}" ] || [ -z "${ACTION_TICKET_ID}" ]; then
    log "ERROR: prop-ticket-reissue needs ACCOUNT_ID and ACTION_TICKET_ID."
    exit 1
fi
if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper not present at ${PY_SCRIPT}. Did the VM pull latest main?"
    record_audit "prop-ticket-reissue" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "prop-ticket-reissue" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--db "${DB_PATH}" --account "${ACCOUNT_ID}" --ticket-id "${ACTION_TICKET_ID}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> prop-ticket-reissue: APPLY mode — will write (DB backup taken first)."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> prop-ticket-reissue: DRY-RUN (set apply: true to write)."
    ;;
esac

cd "${REPO_DIR}"
set +e
"${PY}" "${PY_SCRIPT}" "${ARGS[@]}"
rc=$?
set -e
record_audit "prop-ticket-reissue" "$([ "${rc}" -eq 0 ] && echo ok || echo refused)" \
    "{\"account\": \"${ACCOUNT_ID}\", \"ticket_id\": \"${ACTION_TICKET_ID}\", \"apply\": \"${ACTION_APPLY}\", \"rc\": ${rc}}" >/dev/null || true
exit "${rc}"
