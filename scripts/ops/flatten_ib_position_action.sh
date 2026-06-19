#!/usr/bin/env bash
# system-action wrapper: flatten one IB exchange position (one-shot, guarded).
#
# Runs scripts/ops/flatten_ib_position.py on the live VM. DRY-RUN by default;
# only places the close when ACTION_APPLY is true. BL-20260618-RECONCILE-DUP
# residual cleanup (the stranded ib_paper MGC short).
#
# Env (passed by system-actions.yml):
#   ACCOUNT_ID    - account_id in accounts.yaml (e.g. ib_paper)   [required]
#   ACTION_SYMBOL - bot symbol to flatten (e.g. MGC)              [required]
#   ACTION_APPLY  - "true" to execute; anything else = dry-run    [optional]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"  # sets REPO_DIR (canonical /home/ubuntu/ict-trading-bot)

cd "${REPO_DIR}"

# Inherit the live trader's runtime env (.env): IB connection tuning +
# exchange creds, so this one-shot ops client connects to the IB gateway
# exactly like ict-trader-live.service does — not from a bare SSH shell.
# Without it the ops client ran the IB liveness probe at the default 5s and
# tripped the circuit breaker on the isolated (cross-host) gateway, where
# reqCurrentTime false-trips even on a healthy connection (the #4004 apply
# failure). load_runtime_secrets sources .env in full (set -a).
load_runtime_secrets

# Isolated-gateway escape hatch: skip the false-tripping post-connect
# liveness probe (sanctioned for the cross-host relay topology — the gateway
# is on its own VM so it can't starve the trader, and IB_FETCH_TIMEOUT_S still
# bounds each call). Honour an explicit .env value if present; else default to
# skip. The flatten's own position read already proves the gateway is live.
export IB_PROBE_TIMEOUT_S="${IB_PROBE_TIMEOUT_S:-0}"

ACCOUNT_ID="${ACCOUNT_ID:?ACCOUNT_ID required}"
ACTION_SYMBOL="${ACTION_SYMBOL:?ACTION_SYMBOL required}"
ACTION_APPLY="${ACTION_APPLY:-}"

PY="${REPO_DIR}/.venv/bin/python3"
[ -x "${PY}" ] || PY="python3"

ARGS=(--account "${ACCOUNT_ID}" --symbol "${ACTION_SYMBOL}")
case "${ACTION_APPLY}" in
  true|True)
    echo ">>> flatten-ib-position: APPLY mode — will place the close on ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ARGS+=(--apply)
    ;;
  *)
    echo ">>> flatten-ib-position: DRY-RUN (set apply: true to execute) for ${ACCOUNT_ID}/${ACTION_SYMBOL}"
    ;;
esac

exec "${PY}" scripts/ops/flatten_ib_position.py "${ARGS[@]}"
