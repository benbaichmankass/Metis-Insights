#!/usr/bin/env bash
# system-action wrapper: repair prop_fills rows that carry no direction and are
# therefore permanently unclosable (BL-20260820-PROP-FILL-DIRECTION-ADMISSION-GAP).
#
# Runs scripts/ops/repair_prop_fill_direction.py on the live VM. DRY-RUN by
# default; only writes when ACTION_APPLY is true, and the tool takes its own
# timestamped DB backup before any write.
#
# Touches prop_fills ONLY (the prop journal is isolated from the real-money /
# paper KPIs) — never a `trades` row, never an order, never an exchange position.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - restrict to one account_id (e.g. breakout_1)  [optional]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run    [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

ACCOUNT_ID="${ACCOUNT_ID:-}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=()
[ -n "${ACCOUNT_ID}" ] && ARGS+=(--account "${ACCOUNT_ID}")

case "${ACTION_APPLY}" in
  true|True)
    echo ">>> repair-prop-fill-direction: APPLY mode — will write prop_fills.direction for ${ACCOUNT_ID:-all accounts}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> repair-prop-fill-direction: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID:-all accounts}"
    ;;
esac

# Exit 1 means at least one candidate could NOT be resolved from its ticket —
# that row still reads open for ever, so it must not be reported as success.
exec "${PY}" scripts/ops/repair_prop_fill_direction.py "${ARGS[@]}"
