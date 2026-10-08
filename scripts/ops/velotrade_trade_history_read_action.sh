#!/usr/bin/env bash
# Tier-2 system-action: READ-ONLY Velotrade trade-history read (lane
# VELOTRADE-RECON, 2026-10-07; pipeline PI-20261006-ZRM27CBW-0001).
#
# Logs in over the DXtrade REST API and GETs /accounts/{a}/orders/history with a
# few query variants, printing status, counts, key NAMES and the parsed
# executions (price/qty/side/effect/time). No order endpoint is touched;
# secrets and account codes are redacted.
#
# Dispatched by the system-actions workflow (issue body):
#   action: velotrade-trade-history-read
#   reason: <audit note>                       (required, Tier-2)
#
# Exit: 0 read done, 3 a variant rejected (a measurement), 2 no credentials, 1 env.

set -euo pipefail

SCRIPT_NAME="velotrade_trade_history_read_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Export exactly the keys the probe names; values are never echoed.
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in VELOTRADE_DX_USERNAME VELOTRADE_DX_PASSWORD VELOTRADE_DX_DOMAIN; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi

log "read-only Velotrade REST trade-history read (login + GET orders/history + logout)"
set +e
( cd "${REPO_DIR}" && python3 scripts/prop/velotrade_trade_history_read.py )
rc=$?
set -e
log "velotrade-trade-history-read: exit ${rc}"
exit "${rc}"
