#!/usr/bin/env bash
# system-action wrapper: read — and with apply, SWITCH — a Bybit symbol's VENUE
# position mode (one-way netting <-> hedge). One-shot, guarded, per-symbol.
#
# Runs scripts/ops/bybit_switch_position_mode.py on the live VM. REPORT-ONLY by
# default; only mutates when ACTION_APPLY is true AND ACTION_CONFIRM_ACCOUNT
# echoes ACCOUNT_ID. This is the venue half of T.2
# (BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN): src/runtime/
# bybit_position_mode.py makes the CODE hedge-capable and deliberately does not
# touch the venue, so nothing performed this step until now.
#
# ⚠️ ORDER OF OPERATIONS IS LOAD-BEARING. The venue mode and
# BYBIT_HEDGE_MODE_SYMBOLS must agree: a hedge venue with an empty allowlist
# sends no positionIdx (or 0) and Bybit REFUSES the order; a one-way venue with
# an armed allowlist sends 1/2 and Bybit refuses that too. Neither ordering is
# safe while the trader is running, so run this with the trader STOPPED
# (stop-bot-service -> this -> set-env -> start-bot-service). Then there is no
# refusal window because no orders are attempted.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID              - account_id in accounts.yaml (e.g. bybit_1)  [required]
#   ACTION_SYMBOL           - bot symbol (e.g. SOLUSDT)                   [required]
#   ACTION_POSITION_MODE           - "one_way" | "hedge"; omit to report only    [optional]
#   ACTION_CONFIRM_ACCOUNT  - must equal ACCOUNT_ID for apply             [apply only]
#   ACTION_APPLY            - "true" to execute; anything else = report   [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env) so this one-shot ops client
# authenticates to Bybit exactly like ict-trader-live.service does.
load_runtime_secrets

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_POSITION_MODE="${ACTION_POSITION_MODE:-}"
ACTION_CONFIRM_ACCOUNT="${ACTION_CONFIRM_ACCOUNT:-}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
[ -n "${ACTION_POSITION_MODE}" ] && ARGS+=(--mode "${ACTION_POSITION_MODE}")
[ -n "${ACTION_CONFIRM_ACCOUNT}" ] && ARGS+=(--confirm-account "${ACTION_CONFIRM_ACCOUNT}")

case "${ACTION_APPLY}" in
  true|True)
    echo ">>> switch-bybit-position-mode: APPLY — ${ACCOUNT_ID}/${ACTION_SYMBOL} -> ${ACTION_POSITION_MODE:-<none>}"
    echo ">>> the script re-reads the mode afterwards; an unverified switch exits non-zero"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> switch-bybit-position-mode: REPORT-ONLY for ${ACCOUNT_ID}/${ACTION_SYMBOL} (set apply: true to execute)"
    ;;
esac

exec "${PY}" scripts/ops/bybit_switch_position_mode.py "${ARGS[@]}"
