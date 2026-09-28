#!/usr/bin/env bash
# Tier-2 system-action: READ-ONLY measurement of Breakout's proprietary
# terminal (dashboard → "Open Terminal"), lane PROP-TERM 2026-09-28.
# Spec: docs/research/prop-automation-options-2026-09-27.md § 2.4.
#
# Runs scripts/prop/breakout_terminal_probe.py, which has NO code path that
# places, modifies, cancels or closes an order. Stages:
#   landing (always)  — load the probe's login_url (config/prop_platforms.yaml
#                       `probes.breakout_terminal`, default app.breakoutprop.com)
#                       and print its redacted shape. No credentials used.
#   login             — apply: login. ONE credential attempt with the env-var
#                       NAMES the probe entry lists; on the dashboard, press
#                       "Open Terminal" (navigation only) and read the terminal.
#   probe-ticket      — apply: login,probe-ticket. Open the order form ONLY if
#                       one-click trading reads OFF, record its shape, type
#                       nothing, close it.
# A challenge / CAPTCHA / emailed code / 2FA / rejected login / no account /
# canvas-only ticket ends with "feasibility: <reason>" and exit 4 — reported,
# never worked around.
#
# Dispatched by the system-actions workflow (issue body):
#   action: breakout-terminal-probe
#   reason: <audit note>                       (required, Tier-2)
#   apply: login | login,probe-ticket          (optional; default landing only)
#   symbol: <VENUE_SYMBOL>                     (optional, probe-ticket only; default BTCUSD)
#
# Reuses the isolated browser venv the breakout-login-check action builds
# (~/.cache/metis-prop-browser) and takes the SAME flock as the DXtrade feed,
# so a probe never runs a browser while a feed tick is logging in. It never
# touches the DXtrade feed's saved session.
#
# Exit codes pass through from the python script: 0 done, 3 reached the
# terminal but part of the read did not parse, 4 feasibility stop,
# 5 environment, 1 other.

set -euo pipefail

SCRIPT_NAME="breakout_terminal_probe_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_env

APPLY="${ACTION_APPLY:-}"
SYMBOL="${ACTION_SYMBOL:-BTCUSD}"
case ",${APPLY}," in *",login,"*) WANT_LOGIN=1 ;; *) WANT_LOGIN=0 ;; esac
case ",${APPLY}," in *",probe-ticket,"*) WANT_TICKET=1 ;; *) WANT_TICKET=0 ;; esac
if [ "${WANT_TICKET}" = "1" ] && [ "${WANT_LOGIN}" != "1" ]; then
    log "apply: probe-ticket needs login (apply: login,probe-ticket)"
    exit 1
fi
if ! printf '%s' "${SYMBOL}" | grep -Eq '^[A-Za-z0-9._-]{1,20}$'; then
    log "symbol '${SYMBOL}' invalid (allowed: A-Z a-z 0-9 . _ -, 1-20 chars)"
    exit 1
fi

# Export exactly the credential keys the probe entry names. Values are never
# echoed (no `set -x`, no print); the python side prints set/MISSING only.
PROBE_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD"
if [ "${WANT_LOGIN}" = "1" ] && [ -f "${REPO_DIR}/.env" ]; then
    for ckey in ${PROBE_KEYS}; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi

BASE="${HOME}/.cache/metis-prop-browser"
VENV="${BASE}/venv"
export PLAYWRIGHT_BROWSERS_PATH="${BASE}/browsers"
if [ ! -x "${VENV}/bin/python" ] || ! "${VENV}/bin/python" -c "import playwright" >/dev/null 2>&1; then
    log "environment: isolated browser venv missing at ${VENV} (run breakout-login-check once to build it)"
    record_audit "breakout-terminal-probe" "environment" "{\"exit\": 5, \"stage\": \"venv_missing\"}" >/dev/null || true
    exit 5
fi

exec 9>"${BASE}/login.lock"
if ! flock -w 200 9; then
    log "environment: a scheduled feed tick still holds ${BASE}/login.lock after 200s"
    exit 5
fi

ARGS=()
[ "${WANT_LOGIN}" = "1" ] && ARGS+=(--login)
[ "${WANT_TICKET}" = "1" ] && ARGS+=(--probe-ticket "${SYMBOL}")

log "Running read-only terminal probe (login=${WANT_LOGIN} probe_ticket=${WANT_TICKET})"
set +e
( cd "${REPO_DIR}" && "${VENV}/bin/python" scripts/prop/breakout_terminal_probe.py "${ARGS[@]}" )
rc=$?
set -e

case "${rc}" in
    0) outcome="ok" ;;
    3) outcome="terminal_read_unparsed" ;;
    4) outcome="feasibility_stop" ;;
    5) outcome="environment" ;;
    *) outcome="error" ;;
esac
record_audit "breakout-terminal-probe" "${outcome}" \
    "{\"exit\": ${rc}, \"login\": ${WANT_LOGIN}, \"probe_ticket\": ${WANT_TICKET}}" >/dev/null || true
log "breakout-terminal-probe: ${outcome} (exit ${rc})"
exit "${rc}"
