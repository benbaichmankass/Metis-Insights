#!/usr/bin/env bash
# system-action wrapper: cancel accumulated stale Partial-tpsl legs on one
# Bybit symbol (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP stopgap).
#
# Runs scripts/ops/cancel_stale_tpsl_legs.py on the live VM. DRY-RUN by
# default; only cancels when ACTION_APPLY is true.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. bybit_2)    [required]
#   ACTION_SYMBOL - bot symbol to clean up (e.g. XRPUSDT)         [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run    [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env): Bybit creds so this one-shot
# ops client authenticates exactly like ict-trader-live.service does.
load_runtime_secrets

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> cancel-stale-tpsl-legs: APPLY mode — will cancel stale legs on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> cancel-stale-tpsl-legs: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/cancel_stale_tpsl_legs.py "${ARGS[@]}"
