#!/usr/bin/env bash
# system-action wrapper: cancel ONE resting IB order by id.
#
# Runs scripts/ops/cancel_ib_order.py on the live VM. DRY-RUN by default;
# only cancels when ACTION_APPLY is true. Closes the per-order cancel gap
# (BL-20260816-NO-PER-ORDER-IB-CANCEL): before this, the only cancels
# available were flatten-ib-position (which PLACES an order) and
# reqGlobalCancel (which strips every protective stop on the account).
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID       - account_id in accounts.yaml (e.g. ib_paper)  [required]
#   ACTION_ORDER_ID  - IB orderId to cancel                         [one of]
#   ACTION_PERM_ID   - IB permId to cancel (account-stable)         [one of]
#   ACTION_APPLY     - "true" to execute; anything else = dry-run   [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env) so this one-shot client
# connects to the IB gateway exactly like ict-trader-live.service does —
# same reasoning as flatten_ib_position_action.sh (a bare SSH shell lacks
# the IB tuning and tripped the breaker on the isolated cross-host gateway).
load_runtime_secrets

# Isolated-gateway escape hatch: the post-connect liveness probe false-trips
# on a cold socat-relay flow. Honour an explicit .env value if present; else
# default to skip. IB_FETCH_TIMEOUT_S still bounds every call, and this
# script's own account-wide order read proves the gateway is live.
export IB_PROBE_TIMEOUT_S="${IB_PROBE_TIMEOUT_S:-0}"

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_ORDER_ID="${ACTION_ORDER_ID:-}"
ACTION_PERM_ID="${ACTION_PERM_ID:-}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}")
if [ -n "${ACTION_ORDER_ID// }" ]; then
  ARGS+=(--order-id "${ACTION_ORDER_ID}")
  TARGET="order ${ACTION_ORDER_ID}"
else
  ARGS+=(--perm-id "${ACTION_PERM_ID}")
  TARGET="perm ${ACTION_PERM_ID}"
fi

case "${ACTION_APPLY}" in
  true|True)
    echo ">>> cancel-ib-order: APPLY mode — will cancel ${TARGET} on ${ACCOUNT_ID}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> cancel-ib-order: DRY-RUN (set apply: true to execute) for ${TARGET} on ${ACCOUNT_ID}"
    ;;
esac

exec "${PY}" scripts/ops/cancel_ib_order.py "${ARGS[@]}"
