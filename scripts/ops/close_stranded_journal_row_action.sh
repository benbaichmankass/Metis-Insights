#!/usr/bin/env bash
# system-action wrapper: close a STRANDED open journal row whose broker
# position is already flat (one-shot, guarded).
#
# Runs scripts/ops/close_stranded_journal_row.py on the live VM. DRY-RUN by
# default; only writes the close when ACTION_APPLY is true. Companion to
# flatten_alpaca_position_action.sh: the flatten flattens the BROKER position,
# this closes the stranded JOURNAL row that the reconciler can't close while the
# account is shelved to dry_run (account_open_positions gates a dry alpaca
# account to None → the reverse reconciler skips it → the row stays open in the
# UI). This makes a MODE-AGNOSTIC broker-flat read and, only if the broker is
# actually flat, marks the row closed. Never flips account mode, never touches
# the broker.
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID       - account_id in accounts.yaml (e.g. alpaca_live)  [required]
#   ACTION_SYMBOL    - bot symbol whose stranded row to close (e.g. IEF) [required]
#   ACTION_APPLY     - "true" to write; anything else = dry-run         [optional]
#   ACTION_EXIT_PRICE- the flatten fill price for local-compute pnl     [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env): Alpaca creds so the one-shot
# ops client can make the MODE-AGNOSTIC broker-flat read exactly like
# ict-trader-live.service does.
load_runtime_secrets

# The working Alpaca creds are the ones the ict-web-api service authenticates
# with — it loads the root-owned /etc/ict-trader/web-api.env EnvironmentFile in
# addition to repo .env. Prefer web-api.env's known-good ALPACA_* values
# (sudo-read, mirroring flatten_alpaca_position_action.sh). Non-secret presence
# diagnostic only — never echoes a value.
WEBAPI_ENV="/etc/ict-trader/web-api.env"
if sudo test -r "${WEBAPI_ENV}" 2>/dev/null; then
  for _v in ALPACA_API_KEY_ID_LIVE ALPACA_API_SECRET_KEY_LIVE \
            ALPACA_API_KEY_ID ALPACA_API_SECRET_KEY \
            ALPACA_API_KEY_ID_OPTIONS ALPACA_API_SECRET_KEY_OPTIONS; do
    _val="$(sudo grep -E "^${_v}=" "${WEBAPI_ENV}" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
    if [ -n "${_val}" ]; then
      export "${_v}=${_val}"
      echo ">>> creds: ${_v} sourced from web-api.env"
    elif [ -n "${!_v:-}" ]; then
      echo ">>> creds: ${_v} absent in web-api.env; using repo .env value"
    else
      echo ">>> creds: ${_v} absent in web-api.env AND empty/unset in repo .env"
    fi
    unset _val
  done
else
  echo ">>> creds: web-api.env not sudo-readable — using repo .env creds only"
fi

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"
ACTION_EXIT_PRICE="${ACTION_EXIT_PRICE:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
if [ -n "${ACTION_EXIT_PRICE// }" ]; then
  ARGS+=(--exit-price "${ACTION_EXIT_PRICE}")
fi
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> close-stranded-journal-row: APPLY mode — will close the stranded ${ACTION_SYMBOL} journal row on ${ACCOUNT_ID} (broker-flat verified first)"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> close-stranded-journal-row: DRY-RUN (set apply: true to write) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/close_stranded_journal_row.py "${ARGS[@]}"
