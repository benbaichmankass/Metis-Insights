#!/usr/bin/env bash
# Tier-2 system-action: ONE minimum-size round trip on velotrade_1 over the
# DXtrade REST API (lane VELOTRADE-API-EXEC, 2026-10-05; operator decision
# relayed by manager session_01MM8o5js6TcDFeNAPBY4Ntv, verbatim "Build it +
# pre-approve test"). Plan: docs/research/velotrade-api-executor-design-2026-10-05.md § 4.
#
# DRY unless BOTH `apply: live` AND `confirm_account: velotrade_1`: the dry run
# logs in, reads, builds the IF-THEN body and sends no order. A live run is
# ONE-SHOT (scripts/prop/velotrade_api_roundtrip.py writes a latch before its
# first send and refuses a second run). Stop rule: any non-200 or read-back
# mismatch -> Bulk Close ETHUSD, verify flat, stop.
#
# Dispatched by the system-actions workflow (issue body):
#   action: velotrade-api-roundtrip
#   reason: <audit note>                       (required, Tier-2)
#   apply: live                                (optional; anything else = dry)
#   confirm_account: velotrade_1               (required with apply: live)
#
# Exit codes pass through: 0 passed (or dry) and flat, 3 stop rule tripped and
# verified flat, 4 stop rule tripped and NOT verified flat, 5 refused before any
# send, 2 no credentials, 1 environment / login.

set -euo pipefail

SCRIPT_NAME="velotrade_api_roundtrip_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Export exactly the keys the script names; values are never echoed.
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

LIVE_FLAG=""
if [ "${ACTION_APPLY:-}" = "live" ]; then
    if [ "${ACTION_CONFIRM_ACCOUNT:-}" != "velotrade_1" ]; then
        log "refused: apply=live needs confirm_account: velotrade_1 (got '${ACTION_CONFIRM_ACCOUNT:-}')"
        exit 5
    fi
    LIVE_FLAG="--live"
    log "LIVE round trip: 0.01 ETHUSD IF-THEN bracket, modify SL, close, reconcile (one-shot)"
else
    log "DRY round trip: login + reads + IF-THEN body built; no order sent"
fi
set +e
( cd "${REPO_DIR}" && python3 scripts/prop/velotrade_api_roundtrip.py ${LIVE_FLAG} )
rc=$?
set -e
log "velotrade-api-roundtrip: exit ${rc}"
exit "${rc}"
