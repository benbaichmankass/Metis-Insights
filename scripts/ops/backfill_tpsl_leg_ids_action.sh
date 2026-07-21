#!/usr/bin/env bash
# system-action wrapper: backfill trades.sl_order_id/tp_order_id for one
# already-open Bybit partial-tpsl position (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP
# structural-fix gap — pre-existing open trades never got a tracked leg id).
#
# Runs scripts/ops/backfill_tpsl_leg_ids.py on the live VM. DRY-RUN by
# default; only writes when ACTION_APPLY is true.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. bybit_2)    [required]
#   ACTION_SYMBOL - bot symbol to backfill (e.g. XRPUSDT)         [required]
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
    echo ">>> backfill-tpsl-leg-ids: APPLY mode — will write sl_order_id/tp_order_id for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> backfill-tpsl-leg-ids: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/backfill_tpsl_leg_ids.py "${ARGS[@]}"
