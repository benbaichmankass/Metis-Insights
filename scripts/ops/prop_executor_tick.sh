#!/usr/bin/env bash
# One tick of the breakout_1 prop executor (step 3, PROP-EXEC 2026-09-28).
# Fired by deploy/opt-in/ict-prop-executor.timer, which is NOT installed or
# enabled by any deploy (scripts/install_systemd_units.sh only globs
# deploy/*.timer). Spec: docs/research/prop-automation-options-2026-09-27.md § 3.
#
# Runs scripts/prop/prop_executor_tick.py --login reuse: ONE cycle in
# PROP_EXECUTOR_MODE (off | read_only | live; default read_only when unset).
#
# ONE LOGIN AT A TIME, AND THE FEED OWNS LOGINS:
#   - takes the SAME flock as the feed tick and the breakout-login-check
#     system-action (${BASE}/login.lock); busy → this tick is skipped, never
#     queued behind a login;
#   - opens the terminal on the FEED's saved session
#     (${BASE}/feed/session_state.json) and never types credentials. If that
#     session is not accepted the tick exits 6 and waits for the feed's next
#     tick (<= 5 min) to log in again. So the executor adds ZERO credential
#     logins: the feed's relogin ceiling stays the only counter that matters.
#   - a TRIPPED feed means nobody is maintaining the session: the executor
#     does not run either.
#
# Alerts the cycle prints ({"alert": ...}) are pinged once per tick.
set -euo pipefail

SCRIPT_NAME="prop_executor_tick"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

ACCOUNT="${PROP_EXECUTOR_ACCOUNT:-breakout_1}"
BASE="${PROP_BROWSER_BASE:-${HOME}/.cache/metis-prop-browser}"
VENV="${BASE}/venv"
SESSION_STATE="${BASE}/feed/session_state.json"
STATE_DIR="${BASE}/executor"
LOCK_FILE="${BASE}/login.lock"
TIMEOUT_S="${PROP_EXECUTOR_TIMEOUT_S:-150}"
PING_PY="${PROP_EXECUTOR_PING_PY:-/usr/bin/python3}"

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

# Kill switch from the VM .env (values of the credential keys never echoed).
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in PROP_EXECUTOR_MODE DASHBOARD_API_TOKEN; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi
if [ "${PROP_EXECUTOR_MODE:-read_only}" = "off" ]; then
    log "PROP_EXECUTOR_MODE=off; not reading, not clicking"
    exit 0
fi

if [ -f "${BASE}/feed/tripped" ]; then
    log "feed is TRIPPED ($(head -c 200 "${BASE}/feed/tripped")); the executor does not run without a maintained session"
    exit 0
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "a feed tick or login-check holds ${LOCK_FILE}; skipping this executor tick"
    exit 0
fi

if [ ! -x "${VENV}/bin/python" ]; then
    log "isolated browser venv missing at ${VENV}"
    exit 5
fi
export PLAYWRIGHT_BROWSERS_PATH="${BASE}/browsers"

OUT="$(mktemp "${STATE_DIR}/.tick-out.XXXXXX")"
trap 'rm -f "${OUT}"' EXIT
set +e
( cd "${REPO_DIR}" && timeout --kill-after=15 "${TIMEOUT_S}" \
    env PYTHONUNBUFFERED=1 "${VENV}/bin/python" -u scripts/prop/prop_executor_tick.py \
    --account "${ACCOUNT}" --login reuse --storage-state "${SESSION_STATE}" \
    --state-dir "${STATE_DIR}" ) 2>&1 | tee "${OUT}"
rc=${PIPESTATUS[0]}
set -e

n_alerts="$(grep -c '^{"alert"' "${OUT}" || true)"
if [ "${n_alerts:-0}" -gt 0 ]; then
    first="$(grep -m1 '^{"alert"' "${OUT}" | head -c 300)"
    record_audit "prop-executor" "alert" \
        "{\"account\": \"${ACCOUNT}\", \"alerts\": ${n_alerts}, \"exit\": ${rc}}" >/dev/null || true
    "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority high \
        --kind state_change --why "the ${ACCOUNT} prop executor raised ${n_alerts} alert(s)" \
        "[prop-executor] ${ACCOUNT}: ${n_alerts} alert(s), exit ${rc}. First: ${first}" \
        >/dev/null 2>&1 || log "ping enqueue failed (audit record still written)"
fi
log "executor tick exit ${rc} (alerts=${n_alerts:-0})"
exit "${rc}"
