#!/usr/bin/env bash
# Tier-2 system-action: READ-ONLY login check for a prop account's web
# terminal (probe step 1 — docs/research/prop-automation-options-2026-09-27.md § 4).
#
# Logs in to breakout_1's DXtrade terminal (app.breakoutprop.com) with
# BREAKOUT_DX_USERNAME / BREAKOUT_DX_PASSWORD from the VM .env, reads balance,
# equity, open positions and working orders, and prints them. It has NO code
# path that clicks an order control (scripts/prop/breakout_login_check.py).
# A challenge / CAPTCHA / 2FA / rejected login ends it with
# "feasibility: <reason>" and exit 4 — reported, never worked around.
#
# Browser: Playwright Chromium, headless, default configuration, installed
# into an ISOLATED venv under ~/.cache/metis-prop-browser (never the trader's
# Python). No stealth plugin, fingerprint change or fake jitter.
#
# Dispatched by the system-actions workflow (issue body):
#   action: breakout-login-check
#   reason: <audit note>                     (required, Tier-2)
#   account: breakout_1                      (optional, default breakout_1)
#   apply: install-deps,emit-status          (optional, comma-separated)
#     install-deps — also run `playwright install-deps chromium` (sudo apt:
#                    the shared libraries Chromium needs). Only needed if a
#                    run ends with "environment: chromium failed to launch".
#     emit-status  — post ONE account_status through POST /api/bot/prop/report.
#                    Default OFF.
#
# Exit codes pass through from the python script: 0 ok, 3 login ok but part of
# the read did not parse, 4 feasibility stop, 5 environment, 1 other.

set -euo pipefail

SCRIPT_NAME="breakout_login_check_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

load_runtime_env

ACCOUNT="${ACCOUNT_ID:-breakout_1}"
APPLY="${ACTION_APPLY:-}"
case ",${APPLY}," in *",install-deps,"*) WANT_DEPS=1 ;; *) WANT_DEPS=0 ;; esac
case ",${APPLY}," in *",emit-status,"*) WANT_EMIT=1 ;; *) WANT_EMIT=0 ;; esac

# Export exactly the keys the check needs from the VM .env. Values are never
# echoed (no `set -x`, no print); the python side prints set/MISSING only.
CHECK_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN"
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in ${CHECK_KEYS}; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi

# Isolated browser venv. --system-site-packages so the repo's own deps (yaml)
# resolve from the same system Python the trader uses, without installing
# anything into it.
PW_VERSION="1.48.0"
BASE="${HOME}/.cache/metis-prop-browser"
VENV="${BASE}/venv"
export PLAYWRIGHT_BROWSERS_PATH="${BASE}/browsers"
mkdir -p "${BASE}"
if [ ! -x "${VENV}/bin/python" ]; then
    log "Creating isolated browser venv at ${VENV}"
    /usr/bin/python3 -m venv --system-site-packages "${VENV}"
fi
if ! "${VENV}/bin/python" -c "import playwright, sys; from importlib.metadata import version; sys.exit(0 if version('playwright') == '${PW_VERSION}' else 1)" 2>/dev/null; then
    log "Installing playwright==${PW_VERSION} into the isolated venv"
    "${VENV}/bin/pip" install --quiet --disable-pip-version-check "playwright==${PW_VERSION}"
fi
log "Ensuring Playwright Chromium is present (${PLAYWRIGHT_BROWSERS_PATH})"
"${VENV}/bin/python" -m playwright install chromium >/dev/null
if [ "${WANT_DEPS}" = "1" ]; then
    log "Installing Chromium system libraries (apt, via playwright install-deps)"
    sudo -n env PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH}" \
        "${VENV}/bin/python" -m playwright install-deps chromium
fi

ARGS=(--account "${ACCOUNT}" --dump-dir "${BASE}/last-run")
[ "${WANT_EMIT}" = "1" ] && ARGS+=(--emit-status)

log "Running read-only login check (account=${ACCOUNT} emit_status=${WANT_EMIT})"
set +e
( cd "${REPO_DIR}" && "${VENV}/bin/python" scripts/prop/breakout_login_check.py "${ARGS[@]}" )
rc=$?
set -e

case "${rc}" in
    0) outcome="ok" ;;
    3) outcome="login_ok_read_unparsed" ;;
    4) outcome="feasibility_stop" ;;
    5) outcome="environment" ;;
    *) outcome="error" ;;
esac
record_audit "breakout-login-check" "${outcome}" \
    "{\"account\": \"${ACCOUNT}\", \"exit\": ${rc}, \"emit_status\": ${WANT_EMIT}}" >/dev/null || true
log "breakout-login-check: ${outcome} (exit ${rc})"
exit "${rc}"
