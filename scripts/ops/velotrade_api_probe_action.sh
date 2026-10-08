#!/usr/bin/env bash
# Tier-2 system-action: READ-ONLY probe of Velotrade's DXtrade REST API
# (lane VELOTRADE-API-PROBE, 2026-10-05; pipeline PI-20261005-APBY4NTV-0004).
#
# Logs in to https://dx.velotrade.com/dxsca-web/login with
# VELOTRADE_DX_USERNAME / VELOTRADE_DX_PASSWORD (optional VELOTRADE_DX_DOMAIN)
# from the VM .env, then GETs users, portfolio, metrics and instruments, and
# logs out. scripts/prop/velotrade_api_probe.py hard-refuses every request that
# is not GET, POST /login or POST /logout, and never touches /orders. Secrets
# and account codes are redacted from the output.
#
# Dispatched by the system-actions workflow (issue body):
#   action: velotrade-api-probe
#   reason: <audit note>                       (required, Tier-2)
#   account_id: velotrade_1 | tradeify_1       (optional, default velotrade_1)
#
# DXTRADE-PROBE-ACCOUNT (2026-10-08): ACCOUNT_ID is a FIXED choice, re-validated here
# and again by argparse ``choices`` in the probe; the host + env prefix come from
# config/prop_platforms.yaml. TRADEIFY_DX_* may be absent from the VM .env: the probe
# then reports ``read_state: env_absent`` (exit 2), which is not a login failure.
#
# Exit codes pass through: 0 login accepted + reads done, 3 login rejected
# (a measurement), 2 no credentials, 1 environment.

set -euo pipefail

SCRIPT_NAME="velotrade_api_probe_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Export exactly the keys the probe names; values are never echoed.
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in VELOTRADE_DX_USERNAME VELOTRADE_DX_PASSWORD VELOTRADE_DX_DOMAIN \
                TRADEIFY_DX_USERNAME TRADEIFY_DX_PASSWORD TRADEIFY_DX_DOMAIN; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi

PROBE_ACCOUNT="${ACCOUNT_ID:-velotrade_1}"
case "${PROBE_ACCOUNT}" in
    velotrade_1|tradeify_1) : ;;
    *) log "refused: account '${PROBE_ACCOUNT}' (allowed: velotrade_1, tradeify_1)"; exit 1 ;;
esac
log "read-only DXtrade REST probe for ${PROBE_ACCOUNT} (login + GET reads + logout; no order endpoint)"
set +e
( cd "${REPO_DIR}" && python3 scripts/prop/velotrade_api_probe.py --account "${PROBE_ACCOUNT}" )
rc=$?
set -e
log "velotrade-api-probe: exit ${rc}"
exit "${rc}"
