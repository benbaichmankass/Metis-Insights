#!/usr/bin/env bash
# system-action wrapper: flatten one Bybit exchange position (one-shot, guarded).
#
# Runs scripts/ops/flatten_bybit_position.py on the live VM. DRY-RUN by default;
# only places the close when ACTION_APPLY is true. The Bybit sibling of
# flatten_ib_position_action.sh — gives a web/PM session the ability to flatten
# a real-money Bybit position (e.g. to clear an account before a
# different-account key rotation) without a human at the exchange terminal.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. bybit_2)    [required]
#   ACTION_SYMBOL - bot symbol to flatten (e.g. BTCUSDT)          [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run    [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env): Bybit creds (BYBIT_API_KEY_*/
# BYBIT_API_SECRET_*) + BYBIT_TESTNET, so this one-shot ops client authenticates
# to Bybit exactly like ict-trader-live.service does — not from a bare SSH
# shell. load_runtime_secrets sources .env in full (set -a).
load_runtime_secrets

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> flatten-bybit-position: APPLY mode — will place the reduce-only close on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> flatten-bybit-position: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/flatten_bybit_position.py "${ARGS[@]}"
