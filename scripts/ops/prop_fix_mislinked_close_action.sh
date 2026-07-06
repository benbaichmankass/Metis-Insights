#!/usr/bin/env bash
# system-action wrapper: repair the 2026-07-06 mis-linked ETH prop CLOSE
# (BL-20260706-PROP-CLOSE-MISLINK) — one-shot prop-journal hygiene.
#
# Runs scripts/ops/prop_fix_mislinked_close.py on the live VM. DRY-RUN by
# default — prints the guarded 3-op plan (relink the close fill to the real
# position ticket; close that ticket filled→closed; restore the phantom ticket
# closed→expired) WITHOUT writing. Only writes when ACTION_APPLY is true, and
# the Python takes a timestamped DB backup first.
#
# Background: before PR #5744, a prop CLOSE with no explicit ticket_id linked to
# the newest open-status ticket — a never-placed `emitted` SIGNAL rather than the
# `filled` POSITION. The 2026-07-06 ETH close (prop_fills id 17) hit the emitted
# ticket prop-manual-849ece101a3c instead of the filled position ticket
# prop-manual-5bc393741ec4, marking a phantom closed and leaving the real
# position open. #5744 stops recurrence; this repairs the rows already written.
#
# Env (passed by system-actions.yml):
#   ACTION_APPLY - "true" to write (DB backup taken first); else = dry-run report
#
# Idempotent & guarded: every op only fires when its expected current value
# holds, so re-running after apply is a clean no-op. Touches only prop_fills /
# prop_tickets (the prop journal is isolated from real-money/paper KPIs); never
# a trades row, never an exchange position.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

DB_PATH="$(runtime_db_path)"
PY_SCRIPT="${REPO_DIR}/scripts/ops/prop_fix_mislinked_close.py"
ACTION_APPLY="${ACTION_APPLY:-}"

# The exact ETH incident (hardcoded — one-shot repair, like the options-artifact
# superseder). The Python guards each op on its expected current value.
RELINK_FILL="17"
FROM_TICKET="prop-manual-849ece101a3c"   # emitted signal the close wrongly hit
TO_TICKET="prop-manual-5bc393741ec4"     # the real filled position

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: repair helper not present at ${PY_SCRIPT}. Did the VM pull latest main?"
    record_audit "fix-prop-mislinked-close" "error" \
        "{\"reason\": \"helper missing\", \"path\": \"${PY_SCRIPT}\"}" >/dev/null || true
    exit 1
fi
if [ ! -f "${DB_PATH}" ]; then
    log "ERROR: trade_journal.db not present at ${DB_PATH}."
    record_audit "fix-prop-mislinked-close" "error" \
        "{\"reason\": \"db missing\", \"path\": \"${DB_PATH}\"}" >/dev/null || true
    exit 1
fi

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--db "${DB_PATH}"
      --relink-fill "${RELINK_FILL}"
      --relink-from-ticket "${FROM_TICKET}"
      --relink-to-ticket "${TO_TICKET}"
      --close-ticket "${TO_TICKET}"
      --restore-ticket "${FROM_TICKET}"
      --restore-status "expired")

case "${ACTION_APPLY}" in
  true|True)
    echo ">>> fix-prop-mislinked-close: APPLY mode — will write (DB backup taken first)."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> fix-prop-mislinked-close: DRY-RUN (set apply: true to write)."
    ;;
esac

"${PY}" "${PY_SCRIPT}" "${ARGS[@]}"
