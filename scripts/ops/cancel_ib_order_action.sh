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
#   ACTION_FORCE_PROTECTIVE - "true" to pass --force-protective     [optional]
#   ACTION_FORCE_CLIENT_ID  - "true" to pass --force-client-id      [optional]
#
# The two force vars exist because the python script's guards are documented
# as having explicit overrides, and this wrapper passed NEITHER -- so the
# action could not cancel the very class of order it was built for. The
# motivating row (BL-20260816-NO-PER-ORDER-IB-CANCEL) was a stranded MGC stop:
# protective AND trader-owned, i.e. blocked on both. Live-confirmed again
# 2026-08-17 on a duplicate MES stop (perm 166865400, clientId 597), which
# came back `action: refused` with both blockers listed and no way to proceed.
#
# THIS CHANGES NO GUARD. Both still default to refusing; the overrides remain
# opt-in per invocation, are absent unless the issue body asks for them, and
# each is echoed below so the run log states which refusal was waived. A guard
# you cannot reach from the only surface that invokes it is not a safe guard --
# it is an action that cannot do its job, and the pressure it creates is to
# reach for `flatten-ib-position`, which PLACES an order.
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

ACTION_FORCE_PROTECTIVE="${ACTION_FORCE_PROTECTIVE:-}"
ACTION_FORCE_CLIENT_ID="${ACTION_FORCE_CLIENT_ID:-}"

case "${ACTION_APPLY}" in
  true|True)
    echo ">>> cancel-ib-order: APPLY mode — will cancel ${TARGET} on ${ACCOUNT_ID}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> cancel-ib-order: DRY-RUN (set apply: true to execute) for ${TARGET} on ${ACCOUNT_ID}"
    ;;
esac

# Announce each waived refusal on its OWN line, whether or not it is set, so
# the run log answers "was this forced?" without the reader having to know the
# default. A silent absence and a silent presence look identical otherwise.
case "${ACTION_FORCE_PROTECTIVE}" in
  true|True)
    echo ">>> cancel-ib-order: --force-protective SET — this order is an exit; cancelling it strips a live position's protection"
    ARGS+=(--force-protective)
    ;;
  *) echo ">>> cancel-ib-order: --force-protective not set (a protective order will be REFUSED)" ;;
esac

case "${ACTION_FORCE_CLIENT_ID}" in
  true|True)
    echo ">>> cancel-ib-order: --force-client-id SET — will connect as the order's owning clientId; if that id is a LIVE trader session this evicts it"
    ARGS+=(--force-client-id)
    ;;
  *) echo ">>> cancel-ib-order: --force-client-id not set (a trader-band owner will be REFUSED)" ;;
esac

exec "${PY}" scripts/ops/cancel_ib_order.py "${ARGS[@]}"
