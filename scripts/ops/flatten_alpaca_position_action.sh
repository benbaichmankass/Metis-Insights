#!/usr/bin/env bash
# system-action wrapper: flatten one Alpaca exchange position (one-shot, guarded).
#
# Runs scripts/ops/flatten_alpaca_position.py on the live VM. DRY-RUN by default;
# only places the close when ACTION_APPLY is true. The Alpaca sibling of
# flatten_bybit_position_action.sh / flatten_ib_position_action.sh — gives a
# web/PM session the ability to flatten a real-money Alpaca position (e.g. the
# ~2-week-flat IEF on alpaca_live whose protective bracket reserves the shares,
# so the operator's own in-app sell is rejected "insufficient qty available")
# without a human at the broker terminal.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. alpaca_live)  [required]
#   ACTION_SYMBOL - bot symbol to flatten (e.g. IEF)                [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run      [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env): Alpaca creds
# (ALPACA_API_KEY_ID* / ALPACA_API_SECRET_KEY*), so this one-shot ops client
# authenticates to Alpaca exactly like ict-trader-live.service does — not from a
# bare SSH shell. load_runtime_secrets sources .env in full (set -a).
load_runtime_secrets

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> flatten-alpaca-position: APPLY mode — will place the native flatten (cancels the reserving bracket first) on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    echo ">>> NOTE: Alpaca rejects the market close outside RTH (13:30-20:00 UTC) — run apply during regular hours."
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> flatten-alpaca-position: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/flatten_alpaca_position.py "${ARGS[@]}"
