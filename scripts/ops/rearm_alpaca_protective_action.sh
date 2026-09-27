#!/usr/bin/env bash
# system-action wrapper: re-arm ONE GTC OCO protective on an Alpaca position,
# sized to the LIVE net qty, with SL/TP read from the journal (never caller-
# supplied). DRY-RUN by default; only cancels + places when ACTION_APPLY is true.
# Wraps scripts/ops/rearm_alpaca_protective.py (PI-20260926-HJPL5ABP-0001).
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. alpaca_paper)  [required]
#   ACTION_SYMBOL - bot symbol (e.g. SPY)                            [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run       [optional]
#
# Exit codes (mapped in notify_run.sh): 0 ok · 1 new OCO refused, OLD protection
# restored · 2 could not look · 3 placed but unverified · 4 refused by a guard
# (nothing cancelled) · 5 NAKED / cancel unsettled — urgent.
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
    echo ">>> rearm-alpaca-protective: APPLY — cancel the resting protective legs and place ONE GTC OCO for the net qty on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> rearm-alpaca-protective: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/rearm_alpaca_protective.py "${ARGS[@]}"
