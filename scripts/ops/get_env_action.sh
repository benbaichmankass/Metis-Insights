#!/usr/bin/env bash
# Tier-1 read-only system-action: report the LIVE value of an allowlisted
# env var on the VM — the missing READ half of `set-env`.
#
# `set-env` could write an env var on the live VM and nothing could read one
# back. A Tier-3 order-path setting whose scope can be written from a session
# but never read is the write-without-a-reader asymmetry
# `provenance-consumer-guard` exists to catch, one level up at the ops surface
# (BL-20260810-CONVICTION-SIZING-APPLY-LIVE-VS-DOC).
#
# Reports BOTH the running process's environment (/proc/<MainPID>/environ —
# authoritative) and the unit's declared EnvironmentFiles (what the next
# restart picks up), and flags a disagreement as a pending restart.
#
# Dispatched by the system-actions workflow (issue body:
#   action: get-env
#   env_key: CONVICTION_SIZING_ACCOUNTS   (or ALL for every allowlisted key)
#   service: ict-trader-live              (optional; default ict-trader-live)
# ).
#
# Reads only: no socket, no write, no restart. Values of secret-NAMED keys are
# fingerprinted, never printed — this action's stdout is commented back onto a
# PUBLIC issue.
#
# Exit codes: 0 report produced, 1 validation / disallowed key or unit,
#             2 could not measure anything at all (ABSENT, not clean).
set -euo pipefail

SCRIPT_NAME="get_env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

PY_SCRIPT="${REPO_DIR}/scripts/ops/get_env.py"
KEY="${ENV_KEY:-}"
SERVICE="${ENV_SERVICE:-}"

if [ ! -f "${PY_SCRIPT}" ]; then
    log "ERROR: helper missing at ${PY_SCRIPT}"
    record_audit "get-env" "error" '{"reason": "helper missing"}' >/dev/null || true
    exit 1
fi

if [ -z "${KEY// }" ]; then
    log "ERROR: get-env requires 'env_key' (a name from --list-keys, or ALL)."
    echo
    echo "Allowlisted keys:"
    python3 "${PY_SCRIPT}" --list-keys || true
    record_audit "get-env" "error" '{"reason": "missing env_key"}' >/dev/null || true
    exit 1
fi

# Default to the trader; accept a bare name or a full unit name.
if [ -z "${SERVICE// }" ] || [ "${SERVICE}" = "none" ]; then
    SERVICE="ict-trader-live.service"
fi
case "${SERVICE}" in
    *.service) ;;
    *) SERVICE="${SERVICE}.service" ;;
esac

echo
echo "===== get_env.py --key ${KEY} --unit ${SERVICE} ====="
set +e
python3 "${PY_SCRIPT}" --key "${KEY}" --unit "${SERVICE}"
exit_code=$?
set -e

# The audit record carries the KEY and the OUTCOME, never a value — the same
# contract set_env.sh holds on the write side.
if [ "${exit_code}" -ne 0 ]; then
    record_audit "get-env" "failed" \
        "{\"env_key\": \"${KEY}\", \"unit\": \"${SERVICE}\", \"exit_code\": ${exit_code}}" \
        >/dev/null || true
    log "Helper exited ${exit_code}."
    exit "${exit_code}"
fi

record_audit "get-env" "ok" \
    "{\"env_key\": \"${KEY}\", \"unit\": \"${SERVICE}\"}" >/dev/null || true
exit 0
