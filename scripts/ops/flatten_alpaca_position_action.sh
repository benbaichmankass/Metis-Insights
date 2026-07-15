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

# The working Alpaca creds are the ones the ict-web-api service authenticates
# with — it loads the root-owned /etc/ict-trader/web-api.env EnvironmentFile in
# addition to repo .env. The repo .env copy (synced from Actions secrets by
# sync-vm-secrets) can be empty/stale for the ALPACA_*_LIVE pair, which makes
# alpaca_client_for() return None → "could not read the live Alpaca position".
# So prefer web-api.env's known-good ALPACA_* values (sudo-read, mirroring the
# prop-report.yml pattern). Non-secret presence diagnostic only — never echoes
# a value. Override only when web-api.env carries a non-empty value.
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
