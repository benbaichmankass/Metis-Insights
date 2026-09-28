#!/usr/bin/env bash
# One tick of the scheduled breakout_1 account-status feed (W6-PROP-FEED,
# operator decision 2026-09-28 "Feed now on schedule",
# PI-20260927-D9R6QTDB-0002). Fired every 5 min by deploy/ict-prop-feed.timer.
#
# Runs the SAME read-only check the `breakout-login-check` system-action runs
# (scripts/prop/breakout_login_check.py): log in, read balance / equity /
# positions / orders, post ONE account_status through POST /api/bot/prop/report.
# It adds nothing to that path: no new click, fill or order control, and it
# passes --symbols='' so the passive instrument-spec read (#13416) is skipped.
#
# What this wrapper adds is the part a schedule needs and a one-off does not:
#   - LOCK: flock on the same file the system-action takes, so a tick never
#     overlaps another tick OR a manual `breakout-login-check` (two logins at
#     once would race each other's session).
#   - HARD TIMEOUT: `timeout` around the python run; the unit's
#     TimeoutStartSec is the outer backstop.
#   - BACKOFF / TRIP: a feasibility stop (exit 4 — challenge, CAPTCHA, 2FA,
#     login rejected, login timeout, missing creds) trips the feed on the FIRST
#     occurrence: retrying a rejected login is how an account gets locked, and a
#     challenge is reported, never worked around. Any other failure trips it
#     after PROP_FEED_MAX_FAILURES consecutive ticks (default 3). A tripped feed
#     sends ONE ping, writes an audit record, and every later tick exits 0
#     without logging in, until it is re-armed with
#       action: breakout-login-check
#       apply: reset-feed[,emit-status]
#   - SESSION REUSE (operator decision 2026-09-28 "Reuse saved session"):
#     passes --storage-state ${STATE_DIR}/session_state.json, so a tick opens
#     the terminal on the session the last good tick saved and only does a
#     credential login when that session is no longer accepted (the check
#     prints `session: reused` / `session: relogin`). Each relogin bumps a
#     per-UTC-day counter and writes an audit record carrying it, so how often
#     the feed really logs in is visible. The state file is a CREDENTIAL
#     EQUIVALENT: 0600 in a 0700 directory, never printed, dumped, pinged or
#     committed. The one-off system-action does NOT pass it (always fresh).
#   - VENV IS NOT BUILT HERE: the tick uses the isolated venv the system-action
#     builds (~/.cache/metis-prop-browser), never the trader's Python, and never
#     pip-installs on a 5-minute cadence. No venv → environment failure.
#
# Output is only what breakout_login_check.py prints, which it redacts
# (credentials, e-mails, token runs, URL paths/queries). --dump-dir is NOT
# passed, so nothing about the page is written to disk.
#
# Exit: 0 ok / tripped-skip / lock-busy; otherwise the python exit code
# (3 unparsed, 4 feasibility, 5 environment, 124 timeout, 1 other).

set -euo pipefail

SCRIPT_NAME="prop_feed_tick"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

ACCOUNT="${PROP_FEED_ACCOUNT:-breakout_1}"
BASE="${PROP_BROWSER_BASE:-${HOME}/.cache/metis-prop-browser}"
VENV="${BASE}/venv"
STATE_DIR="${BASE}/feed"
SESSION_STATE="${STATE_DIR}/session_state.json"
TRIP_FILE="${STATE_DIR}/tripped"
FAILS_FILE="${STATE_DIR}/consecutive_failures"
LOCK_FILE="${BASE}/login.lock"
MAX_FAILURES="${PROP_FEED_MAX_FAILURES:-3}"
TIMEOUT_S="${PROP_FEED_TIMEOUT_S:-150}"
PING_PY="${PROP_FEED_PING_PY:-/usr/bin/python3}"

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

if [ -f "${TRIP_FILE}" ]; then
    log "feed TRIPPED ($(head -c 300 "${TRIP_FILE}")); not logging in. Re-arm: breakout-login-check apply: reset-feed"
    exit 0
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "another breakout login (tick or system-action) holds ${LOCK_FILE}; skipping this tick"
    exit 0
fi

trip() {
    # Args: rc reason
    local rc="$1" reason="$2"
    printf '%s rc=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${rc}" "${reason}" > "${TRIP_FILE}"
    log "TRIPPING feed: ${reason} (exit ${rc})"
    record_audit "prop-feed" "tripped" \
        "{\"account\": \"${ACCOUNT}\", \"exit\": ${rc}, \"reason\": \"${reason}\"}" >/dev/null || true
    "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority high \
        --kind state_change \
        --why "the 5-min ${ACCOUNT} account_status feed stopped logging in" \
        "[prop-feed] ${ACCOUNT} feed TRIPPED: ${reason} (exit ${rc}). No further logins until re-armed (breakout-login-check apply: reset-feed)." \
        >/dev/null 2>&1 || log "ping enqueue failed (trip marker + audit record still written)"
}

record_failure() {
    # Args: rc reason
    local rc="$1" reason="$2" n=0
    [ -f "${FAILS_FILE}" ] && n="$(cat "${FAILS_FILE}" 2>/dev/null || echo 0)"
    case "${n}" in ''|*[!0-9]*) n=0 ;; esac
    n=$((n + 1))
    echo "${n}" > "${FAILS_FILE}"
    log "failure ${n}/${MAX_FAILURES}: ${reason} (exit ${rc})"
    if [ "${rc}" = "4" ]; then
        trip "${rc}" "feasibility stop (challenge/CAPTCHA/2FA/login rejected/login timeout/no creds) — never retried"
    elif [ "${n}" -ge "${MAX_FAILURES}" ]; then
        trip "${rc}" "${n} consecutive failures, last: ${reason}"
    fi
}

if [ ! -x "${VENV}/bin/python" ] || ! "${VENV}/bin/python" -c "import playwright" >/dev/null 2>&1; then
    record_failure 5 "isolated browser venv missing at ${VENV} (run breakout-login-check once to build it)"
    exit 5
fi

# Exactly the keys the check needs, from the VM .env; values never echoed.
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD DASHBOARD_API_TOKEN; do
        cval="$(grep -E "^${ckey}=" "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2-)" || true
        if [ -n "${cval}" ]; then
            cval="${cval%\"}"; cval="${cval#\"}"
            cval="${cval%\'}"; cval="${cval#\'}"
            export "${ckey}=${cval}"
        fi
    done
    unset cval
fi
export PLAYWRIGHT_BROWSERS_PATH="${BASE}/browsers"

# The check's own (already redacted) output goes to the journal AND to a
# scratch copy, read back only for its `session:` line.
OUT="$(mktemp "${STATE_DIR}/.tick-out.XXXXXX")"
trap 'rm -f "${OUT}"' EXIT
set +e
( cd "${REPO_DIR}" && timeout --kill-after=15 "${TIMEOUT_S}" \
    "${VENV}/bin/python" scripts/prop/breakout_login_check.py \
    --account "${ACCOUNT}" --emit-status --symbols= \
    --storage-state "${SESSION_STATE}" ) 2>&1 | tee "${OUT}"
rc=${PIPESTATUS[0]}
set -e

if grep -q '^session: relogin' "${OUT}"; then
    day="$(date -u +%Y%m%d)"
    rfile="${STATE_DIR}/relogins-${day}"
    rn=0
    [ -f "${rfile}" ] && rn="$(cat "${rfile}" 2>/dev/null || echo 0)"
    case "${rn}" in ''|*[!0-9]*) rn=0 ;; esac
    rn=$((rn + 1))
    echo "${rn}" > "${rfile}"
    find "${STATE_DIR}" -maxdepth 1 -name 'relogins-*' ! -name "relogins-${day}" -delete 2>/dev/null || true
    log "session: relogin (${rn} today, UTC ${day})"
    record_audit "prop-feed" "relogin" \
        "{\"account\": \"${ACCOUNT}\", \"relogins_today\": ${rn}, \"day_utc\": \"${day}\", \"exit\": ${rc}}" >/dev/null || true
elif grep -q '^session: reused' "${OUT}"; then
    log "session: reused"
fi

case "${rc}" in
    0) echo 0 > "${FAILS_FILE}"; log "ok (account_status posted)"; exit 0 ;;
    3) record_failure 3 "login ok but part of the read did not parse" ;;
    4) record_failure 4 "feasibility stop" ;;
    5) record_failure 5 "environment (chromium/playwright unusable)" ;;
    124|137) record_failure "${rc}" "hard timeout after ${TIMEOUT_S}s" ;;
    *) record_failure "${rc}" "error" ;;
esac
exit "${rc}"
