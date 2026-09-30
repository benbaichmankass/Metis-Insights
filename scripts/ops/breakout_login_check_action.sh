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
#     dump-tables  — READ-ONLY diagnostic of the positions / orders read path:
#                    print every extracted table (kind, headers, row count,
#                    first rows with ids masked) and how the readers classify
#                    it (live test #13987: a filled position read back as 0).
#     reset-feed   — re-arm the scheduled feed (deploy/ict-prop-feed.timer,
#                    scripts/ops/prop_feed_tick.sh) after it TRIPPED: clears
#                    its trip marker and failure count AFTER this check exits
#                    0, still under the shared lock. A failed check leaves it
#                    tripped.
#   Step-3 executor modes (PROP-EXEC 2026-09-28). Each REPLACES the login
#   check with ONE run of scripts/prop/prop_executor_tick.py --login reuse
#   (the feed's saved session; no credential login), under the same lock;
#   at most one of them per dispatch:
#     probe-ticket     — READ-ONLY: open the order ticket for SOLUSD (only if
#                        one-click trading reads OFF), record its shape,
#                        close it. Prints `feasibility: canvas_ticket` (exit
#                        4) if it is canvas-only. Types nothing.
#     executor-dry-run — walk the newest emitted ticket through intake →
#                        guards → (if it fits) fill + read back the form, then
#                        close it. No submit, no API write.
#     watched-click    — THE WATCHED STEP-3 TEST: one live cycle, at most one
#                        ticket, at executor.watched_click_max_lots (minimum
#                        size), confirmed by re-read, reported through
#                        POST /api/bot/prop/report. Refused unless
#                        PROP_EXECUTOR_MODE=live. Dispatch only with the
#                        operator watching.
#     round-trip-dry   — the END-TO-END test walked dry: read the quote, build
#                        a minimum-size ETHUSD market bracket (add `sol` for
#                        SOLUSD), fill + read back the form, locate the close
#                        control. Clicks nothing.
#     round-trip-live  — THE END-TO-END TEST (operator 2026-09-28): place that
#                        bracket, confirm entry + SL + TP by re-read, report
#                        `open`, CLOSE it at market, confirm flat by re-read,
#                        report `closed`. Refused unless PROP_EXECUTOR_MODE=live.
#                        Tell the operator before dispatching.
#     instrument-probe — READ-ONLY (PROP-ETH, 2026-09-29): for each symbol in
#                        `symbols:` (required, comma-separated venue symbols,
#                        e.g. BTCUSD,ADAUSD,AVAXUSD,XRPUSD), searches the
#                        watchlist/instrument search field and dumps whatever
#                        surfaces (digit-run-masked), then resets the field.
#                        Never opens the order ticket; never touches
#                        BUY/SELL/submit/the chart. See
#                        DXtradeAdapter.probe_instrument_details's docstring
#                        for why this reports STRUCTURE, not named fields.
#     instrument-search-dump — READ-ONLY (PROP-ETH-DOM, 2026-09-30): lists
#                        every visible input / combobox / searchbox /
#                        textbox / contenteditable / search-like button with
#                        its attributes and ancestor chain, nearest the
#                        watchlist first, so the search control's locator is
#                        derived from a measurement. Types, clicks and reads
#                        no value; skips the order ticket. No `symbols:`.
#     close-position   — locate the ONE existing position for the symbol (add
#                        `sol` for SOLUSD) and its row close control through
#                        the terminal's own flow, read back, click nothing.
#     close-position-live — CLOSE that position (row x -> "Close Position"
#                        modal, every step read back) and journal open +
#                        closed via POST /api/bot/prop/report. Refused
#                        unless PROP_EXECUTOR_MODE=live.
#   Executor timer (the go-live switch's second half; not a terminal run):
#     executor-enable-timer  — install deploy/opt-in/ict-prop-executor.timer
#                              and `systemctl enable --now` it. Go-live is
#                              THIS plus `set-env PROP_EXECUTOR_MODE=live`
#                              (service: none; the tick re-reads .env).
#     executor-clear-halt    — clear the executor's AUTO-REVERT latch
#                              (executor/halted). Manager/operator only; the
#                              issue's `reason:` is required and recorded with
#                              the prior latch reason; the file is moved aside.
#     executor-disable-timer — `systemctl disable --now` the timer. The instant
#                              revert is `set-env PROP_EXECUTOR_MODE=off`
#                              (which also stops reconciling in-flight
#                              `submitted` rows: containment is manual after).
#   Per-account feed timer (TRADEIFY-WIRE 2026-09-30; any account EXCEPT
#   breakout_1, whose feed is the non-templated ict-prop-feed.timer):
#     feed-enable-timer   — install deploy/ict-prop-feed@.service +
#                           deploy/opt-in/ict-prop-feed@.timer and
#                           `systemctl enable --now ict-prop-feed@<account>.timer`
#                           (read-only account_status every 5 min).
#     feed-disable-timer  — `systemctl disable --now` that instance.
#
# ACCOUNTS OTHER THAN breakout_1 (TRADEIFY-WIRE): the login env-var NAMES come
# from config/prop_platforms.yaml (scripts/prop/prop_env_keys.py; no entry =
# refused), the kill switch is PROP_EXECUTOR_MODE_<ACCOUNT> (never the global
# PROP_EXECUTOR_MODE), state lives under accounts/<account>/{feed,executor},
# the login check SAVES a session there (--storage-state) for the executor
# modes to reuse, and executor-enable/disable-timer refuse.
#
# Takes the same flock as the scheduled feed (${BASE}/login.lock; for a
# non-breakout account only around the venv/Chromium bootstrap, then its own
# ${BASE}/accounts/<account>/login.lock for the run), waiting up
# to 200 s, so a manual check never logs in while a scheduled tick is.
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

# Per-account state (TRADEIFY-WIRE, 2026-09-30). breakout_1 keeps the paths it
# has always had; any other prop account gets its own feed session + executor
# ledger/latch under accounts/<id>/, so one account's saved session, intent
# ledger or AUTO-REVERT latch can never be read as another's.
BASE="${HOME}/.cache/metis-prop-browser"
if [ "${ACCOUNT}" = "breakout_1" ]; then
    FEED_DIR="${BASE}/feed"
    X_STATE_DIR="${BASE}/executor"
    MODE_KEY="PROP_EXECUTOR_MODE"
else
    FEED_DIR="${BASE}/accounts/${ACCOUNT}/feed"
    X_STATE_DIR="${BASE}/accounts/${ACCOUNT}/executor"
    MODE_KEY="PROP_EXECUTOR_MODE_$(printf '%s' "${ACCOUNT}" | tr 'a-z' 'A-Z' | tr -c 'A-Z0-9\n' '_')"
fi
case ",${APPLY}," in *",install-deps,"*) WANT_DEPS=1 ;; *) WANT_DEPS=0 ;; esac
case ",${APPLY}," in *",emit-status,"*) WANT_EMIT=1 ;; *) WANT_EMIT=0 ;; esac
case ",${APPLY}," in *",reset-feed,"*) WANT_RESET=1 ;; *) WANT_RESET=0 ;; esac
case ",${APPLY}," in *",dump-tables,"*) WANT_TABLES=1 ;; *) WANT_TABLES=0 ;; esac
EXEC_MODE=""
for m in probe-ticket instrument-probe instrument-search-dump executor-dry-run watched-click round-trip-dry round-trip-live \
         close-position close-position-live \
         executor-enable-timer executor-disable-timer executor-clear-halt \
         feed-enable-timer feed-disable-timer; do
    case ",${APPLY}," in *",${m},"*)
        if [ -n "${EXEC_MODE}" ]; then
            log "apply: at most one executor mode per dispatch (got ${EXEC_MODE} and ${m})"
            exit 1
        fi
        EXEC_MODE="${m}" ;;
    esac
done
case ",${APPLY}," in *",sol,"*) RT_SYMBOL="SOLUSD" ;; *) RT_SYMBOL="ETHUSD" ;; esac
if [ "${EXEC_MODE}" = "executor-clear-halt" ]; then
    # Clear the executor's AUTO-REVERT latch (manager / operator decision,
    # 2026-09-28). Never cleared from inside the executor. Refuses without a
    # reason, without a latch, or on a latch with no recorded reason (that
    # needs a person to look first). The prior reason is logged and the
    # latch file is moved aside, never deleted.
    X_HALT="${X_STATE_DIR}/halted"
    if [ -z "${ACTION_REASON// }" ]; then
        log "executor-clear-halt: refused — a reason is required (who clears it and why)"
        exit 1
    fi
    if [ ! -f "${X_HALT}" ]; then
        log "executor-clear-halt: no latch set at ${X_HALT}; nothing to clear"
        exit 1
    fi
    prior="$(head -c 500 "${X_HALT}" | tr -d '\r')"
    if [ -z "${prior// }" ]; then
        log "executor-clear-halt: refused — the latch carries no recorded reason; inspect ${X_HALT} first"
        exit 1
    fi
    log "executor-clear-halt: prior latch: ${prior}"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    mv "${X_HALT}" "${X_HALT}.cleared-${stamp}"
    PRIOR="${prior}" ACTOR="${ACTION_ACTOR:-unknown}" ISSUE="${ACTION_ISSUE:-}" WHY="${ACTION_REASON}" \
        python3 -c 'import json,os,datetime;print(json.dumps({"ts":datetime.datetime.now(datetime.timezone.utc).isoformat(),"actor":os.environ["ACTOR"],"issue":os.environ["ISSUE"],"reason":os.environ["WHY"],"prior":os.environ["PRIOR"]}))' \
        >> "${X_STATE_DIR}/halt_clears.jsonl"
    log "executor-clear-halt: cleared by ${ACTION_ACTOR:-unknown} (issue #${ACTION_ISSUE:-?}); reason: ${ACTION_REASON}"
    record_audit "breakout-login-check" "executor-clear-halt" \
        "{\"account\": \"${ACCOUNT}\", \"moved_to\": \"halted.cleared-${stamp}\"}" >/dev/null || true
    exit 0
fi

TIMER_SRC="${REPO_DIR}/deploy/opt-in/ict-prop-executor.timer"
if { [ "${EXEC_MODE}" = "executor-enable-timer" ] || [ "${EXEC_MODE}" = "executor-disable-timer" ]; } \
        && [ "${ACCOUNT}" != "breakout_1" ]; then
    # ict-prop-executor.timer runs breakout_1 only. A second account's executor
    # timer is its go-live step and is not built here (TRADEIFY-WIRE PR C).
    log "${EXEC_MODE}: refused for ${ACCOUNT} — the executor timer is breakout_1's; a per-account executor timer is not built"
    exit 1
fi
if [ "${EXEC_MODE}" = "feed-enable-timer" ] || [ "${EXEC_MODE}" = "feed-disable-timer" ]; then
    # Per-account account_status feed (templated unit, TRADEIFY-WIRE). breakout_1
    # keeps its own non-templated ict-prop-feed.timer, untouched by this.
    if [ "${ACCOUNT}" = "breakout_1" ]; then
        log "${EXEC_MODE}: refused for breakout_1 — its feed is ict-prop-feed.timer (unchanged)"
        exit 1
    fi
    if ! sudo -n true >/dev/null 2>&1; then
        log "environment: ${EXEC_MODE} needs passwordless sudo"
        exit 5
    fi
    FEED_UNIT="ict-prop-feed@${ACCOUNT}"
    if [ "${EXEC_MODE}" = "feed-enable-timer" ]; then
        [ -f "${REPO_DIR}/deploy/opt-in/ict-prop-feed@.timer" ] || { log "missing deploy/opt-in/ict-prop-feed@.timer"; exit 1; }
        sudo -n install -m 0644 "${REPO_DIR}/deploy/ict-prop-feed@.service" /etc/systemd/system/ict-prop-feed@.service
        sudo -n install -m 0644 "${REPO_DIR}/deploy/opt-in/ict-prop-feed@.timer" /etc/systemd/system/ict-prop-feed@.timer
        sudo -n systemctl daemon-reload
        sudo -n systemctl enable --now "${FEED_UNIT}.timer"
    else
        sudo -n systemctl disable --now "${FEED_UNIT}.timer" 2>/dev/null || true
    fi
    state="$(systemctl is-active "${FEED_UNIT}.timer" 2>/dev/null || true)"
    log "${EXEC_MODE}: ${FEED_UNIT}.timer is now '${state}'"
    record_audit "breakout-login-check" "${EXEC_MODE}" \
        "{\"account\": \"${ACCOUNT}\", \"timer\": \"${state}\"}" >/dev/null || true
    exit 0
fi
if [ "${EXEC_MODE}" = "executor-enable-timer" ] || [ "${EXEC_MODE}" = "executor-disable-timer" ]; then
    if ! sudo -n true >/dev/null 2>&1; then
        log "environment: ${EXEC_MODE} needs passwordless sudo"
        exit 5
    fi
    if [ "${EXEC_MODE}" = "executor-enable-timer" ]; then
        [ -f "${TIMER_SRC}" ] || { log "missing ${TIMER_SRC}"; exit 1; }
        sudo -n install -m 0644 "${REPO_DIR}/deploy/ict-prop-executor.service" /etc/systemd/system/ict-prop-executor.service
        sudo -n install -m 0644 "${TIMER_SRC}" /etc/systemd/system/ict-prop-executor.timer
        sudo -n systemctl daemon-reload
        sudo -n systemctl enable --now ict-prop-executor.timer
    else
        sudo -n systemctl disable --now ict-prop-executor.timer 2>/dev/null || true
    fi
    state="$(systemctl is-active ict-prop-executor.timer 2>/dev/null || true)"
    log "${EXEC_MODE}: ict-prop-executor.timer is now '${state}' (the mode itself is PROP_EXECUTOR_MODE in .env; read it with get-env)"
    record_audit "breakout-login-check" "${EXEC_MODE}" \
        "{\"account\": \"${ACCOUNT}\", \"timer\": \"${state}\"}" >/dev/null || true
    exit 0
fi

# Export exactly the keys the check needs from the VM .env. Values are never
# echoed (no `set -x`, no print); the python side prints set/MISSING only.
# breakout_1's list is unchanged; any other account's credential + kill-switch
# NAMES come from config/prop_platforms.yaml (scripts/prop/prop_env_keys.py),
# and an account with no platform entry refuses rather than borrow a login.
if [ "${ACCOUNT}" = "breakout_1" ]; then
    CHECK_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN PROP_EXECUTOR_MODE"
else
    if ! ACCT_KEYS="$(cd "${REPO_DIR}" && python3 scripts/prop/prop_env_keys.py "${ACCOUNT}")" || [ -z "${ACCT_KEYS}" ]; then
        log "account ${ACCOUNT}: no entry in config/prop_platforms.yaml — refusing (no login borrowed from another account)"
        exit 1
    fi
    CHECK_KEYS="${ACCT_KEYS} DASHBOARD_API_TOKEN"
fi
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
VENV="${BASE}/venv"
# Overridable so tests can point the bootstrap at a stub interpreter instead
# of the real system Python.
PY3="${PY3:-/usr/bin/python3}"
export PLAYWRIGHT_BROWSERS_PATH="${BASE}/browsers"
mkdir -p "${BASE}"

# One login at a time: the scheduled feed takes the same lock. Taken BEFORE
# the venv/Chromium bootstrap so an install never swaps the browser build
# under a running tick.
exec 9>"${BASE}/login.lock"
if ! flock -w 200 9; then
    log "environment: a scheduled feed tick still holds ${BASE}/login.lock after 200s"
    exit 5
fi


# A venv whose `bin/python` exists but whose pip is missing or broken is not
# usable — that is exactly the partial state a prior `ensurepip`-missing
# failure leaves behind (issue #13210: `venv` created bin/python, then died
# before pip finished setting up).
venv_pip_ok() {
    local venv="$1"
    [ -x "${venv}/bin/python" ] || return 1
    [ -x "${venv}/bin/pip" ] || return 1
    "${venv}/bin/python" -m pip --version >/dev/null 2>&1
}

# Bootstraps `${1}` into a usable venv, installing the matching
# python3.X-venv apt package first if `ensurepip` is not importable (the
# Debian/Ubuntu split-package case `python3 -m venv` fails on otherwise).
ensure_venv() {
    local venv="$1"
    if venv_pip_ok "${venv}"; then
        return 0
    fi
    if [ -e "${venv}" ]; then
        log "Removing broken venv at ${venv} (bin/python present, pip not working)"
        rm -rf "${venv}"
    fi
    if ! "${PY3}" -c 'import ensurepip' >/dev/null 2>&1; then
        if ! sudo -n true >/dev/null 2>&1; then
            log "environment: python3-venv missing and cannot install (no passwordless sudo)"
            record_audit "breakout-login-check" "environment" \
                "{\"account\": \"${ACCOUNT}\", \"exit\": 5, \"stage\": \"venv_bootstrap\"}" >/dev/null || true
            exit 5
        fi
        local pyver installed_pkg=""
        pyver="$("${PY3}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
        local pkg
        for pkg in "python${pyver}-venv" "python3-venv"; do
            log "Installing ${pkg} via apt (ensurepip unavailable for venv creation)"
            if sudo -n apt-get install -y "${pkg}" >&2; then
                installed_pkg="${pkg}"
                break
            fi
        done
        if [ -z "${installed_pkg}" ]; then
            log "environment: python3-venv missing and cannot install"
            record_audit "breakout-login-check" "environment" \
                "{\"account\": \"${ACCOUNT}\", \"exit\": 5, \"stage\": \"venv_bootstrap\"}" >/dev/null || true
            exit 5
        fi
        log "Installed ${installed_pkg}"
    fi
    log "Creating isolated browser venv at ${venv}"
    "${PY3}" -m venv --system-site-packages "${venv}"
    if ! venv_pip_ok "${venv}"; then
        log "environment: python3-venv missing and cannot install"
        record_audit "breakout-login-check" "environment" \
            "{\"account\": \"${ACCOUNT}\", \"exit\": 5, \"stage\": \"venv_bootstrap\"}" >/dev/null || true
        exit 5
    fi
}

ensure_venv "${VENV}"
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

# A non-breakout account (TRADEIFY-WIRE) held the SHARED lock only for the
# venv/Chromium bootstrap above (the browser build is shared, so an install must
# never swap it under a running breakout_1 tick). Its terminal run now switches
# to its OWN lock, so a Tradeify check can never make breakout_1's feed or
# real-money executor (both flock -n on ${BASE}/login.lock) skip a tick.
if [ "${ACCOUNT}" != "breakout_1" ]; then
    ACCT_LOCK_DIR="${BASE}/accounts/${ACCOUNT}"
    mkdir -p "${ACCT_LOCK_DIR}" && chmod 700 "${ACCT_LOCK_DIR}"
    exec 9>&-
    exec 9>"${ACCT_LOCK_DIR}/login.lock"
    if ! flock -w 200 9; then
        log "environment: a ${ACCOUNT} feed tick still holds ${ACCT_LOCK_DIR}/login.lock after 200s"
        exit 5
    fi
fi

if [ "${EXEC_MODE}" = "instrument-probe" ] && [ -z "${ACTION_SYMBOLS// }" ]; then
    log "instrument-probe: refused — 'symbols:' is required (comma-separated venue symbols)"
    exit 1
fi

if [ -n "${EXEC_MODE}" ]; then
    # Reuse the FEED's saved session, never a second credential login: the
    # served client carries force_logout "You have logged in somewhere else"
    # (MEASURED 2026-09-28, PI-20260927-D9R6QTDB-0002), so a fresh login here
    # could log out the feed or the operator. Exit 6 = no reusable session:
    # re-dispatch after the feed's next tick (<= 5 min).
    EARGS=(--account "${ACCOUNT}" --login reuse --storage-state "${FEED_DIR}/session_state.json"
           --state-dir "${X_STATE_DIR}")
    case "${EXEC_MODE}" in
        probe-ticket)        EARGS+=(--probe-ticket "${PROBE_SYMBOL:-SOLUSD}") ;;
        instrument-probe)    EARGS+=(--instrument-probe "${ACTION_SYMBOLS}") ;;
        instrument-search-dump) EARGS+=(--instrument-search-dump) ;;
        executor-dry-run)    EARGS+=(--dry-run) ;;
        watched-click)       EARGS+=(--watched-click) ;;
        round-trip-dry)      EARGS+=(--round-trip "${RT_SYMBOL}") ;;
        round-trip-live)     EARGS+=(--round-trip "${RT_SYMBOL}" --live) ;;
        close-position)      EARGS+=(--close-position "${RT_SYMBOL}") ;;
        close-position-live) EARGS+=(--close-position "${RT_SYMBOL}" --live) ;;
    esac
    log "Running prop executor (${EXEC_MODE}, account=${ACCOUNT}, ${MODE_KEY}=${!MODE_KEY:-unset→read_only})"
    set +e
    ( cd "${REPO_DIR}" && "${VENV}/bin/python" scripts/prop/prop_executor_tick.py "${EARGS[@]}" )
    rc=$?
    set -e
    record_audit "breakout-login-check" "${EXEC_MODE}" \
        "{\"account\": \"${ACCOUNT}\", \"exit\": ${rc}, \"mode\": \"${EXEC_MODE}\"}" >/dev/null || true
    log "breakout-login-check ${EXEC_MODE}: exit ${rc}"
    exit "${rc}"
fi

ARGS=(--account "${ACCOUNT}" --dump-dir "${BASE}/last-run")
if [ "${ACCOUNT}" != "breakout_1" ]; then
    # A non-breakout account has no feed session until its feed runs, and the
    # executor modes above REUSE a saved session (never a second credential
    # login). So this check saves one: it resumes the saved session when it is
    # accepted and does ONE credential login otherwise (session: reused /
    # relogin). breakout_1's manual check stays a clean fresh login.
    mkdir -p "${FEED_DIR}" && chmod 700 "${FEED_DIR}"
    ARGS+=(--storage-state "${FEED_DIR}/session_state.json")
fi
[ "${WANT_EMIT}" = "1" ] && ARGS+=(--emit-status)
[ "${WANT_TABLES}" = "1" ] && ARGS+=(--dump-tables)

log "Running read-only login check (account=${ACCOUNT} emit_status=${WANT_EMIT})"
set +e
( cd "${REPO_DIR}" && "${VENV}/bin/python" scripts/prop/breakout_login_check.py "${ARGS[@]}" )
rc=$?
set -e

# reset-feed re-arms the scheduled feed ONLY on a clean check (exit 0), and
# still under the lock taken above (fd 9 stays open until this script exits),
# so a tick can never start between the proof and the re-arm. A failed check
# leaves the feed tripped.
if [ "${WANT_RESET}" = "1" ]; then
    if [ "${rc}" = "0" ]; then
        [ -f "${FEED_DIR}/tripped" ] && log "reset-feed: was tripped: $(head -c 300 "${FEED_DIR}/tripped")"
        rm -f "${FEED_DIR}/tripped" "${FEED_DIR}/consecutive_failures"
        log "reset-feed: feed re-armed (the check above passed)"
    else
        log "reset-feed: NOT re-armed — the check exited ${rc}; the feed stays tripped"
    fi
fi

case "${rc}" in
    0) outcome="ok" ;;
    3) outcome="login_ok_read_unparsed" ;;
    4) outcome="feasibility_stop" ;;
    5) outcome="environment" ;;
    *) outcome="error" ;;
esac
record_audit "breakout-login-check" "${outcome}" \
    "{\"account\": \"${ACCOUNT}\", \"exit\": ${rc}, \"emit_status\": ${WANT_EMIT}, \"reset_feed\": ${WANT_RESET}}" >/dev/null || true
log "breakout-login-check: ${outcome} (exit ${rc})"
exit "${rc}"
