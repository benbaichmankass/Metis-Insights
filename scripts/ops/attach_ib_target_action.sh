#!/usr/bin/env bash
# system-action wrapper: attach the declared take-profit to a target-naked IB
# position, joining the stop's EXISTING OCA group (BL-20260816-COVERAGE-IS-ONE-SIDED).
#
# Cancels nothing: the stop stays armed throughout, and IBKR cancels it when
# the target fills (ocaType=1). DRY-RUN unless ACTION_APPLY is true.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. ib_paper)  [required]
#   ACTION_SYMBOL - bot symbol (e.g. MGC)                        [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run   [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

cd "${REPO_DIR}"
load_runtime_secrets
export IB_PROBE_TIMEOUT_S="${IB_PROBE_TIMEOUT_S:-0}"

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> attach-ib-target: APPLY — placing the declared TP on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply) ;;
  *)
    echo ">>> attach-ib-target: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}" ;;
esac

exec "${PY}" scripts/ops/attach_ib_target.py "${ARGS[@]}"
