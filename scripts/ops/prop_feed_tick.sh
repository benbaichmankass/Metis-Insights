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
#   - BACKOFF, NEVER A STOP (NO-HALT, operator directive 2026-10-09: "There
#     is no halting."; PI-20261009-72XUJX8U-0002). Until then a `tripped`
#     marker stopped every later login until a person re-armed it, and the
#     executor stopped with it. Now a failure is retried on a CAPPED backoff
#     kept in ${STATE_DIR}/backoff (key=value: consecutive_failures,
#     next_attempt_at [epoch], next_attempt_at_utc, delay_s, last_exit,
#     reason, since, flagged, flagged_at). Repeated failed CREDENTIAL logins
#     can lock a prop account, so the retries back off instead of hammering:
#       * failures 1..PROP_FEED_MAX_FAILURES-1 (default 3): retried on the
#         next 5-min tick, as before;
#       * from the MAX_FAILURES-th consecutive failure: next attempt after
#         PROP_FEED_BACKOFF_BASE_S (300) doubling per further failure, capped
#         at PROP_FEED_BACKOFF_CAP_S (7200 = 2 h);
#       * a feasibility stop (exit 4: challenge, CAPTCHA, 2FA, rejected
#         login, login timeout, missing creds) goes straight to the cap: a
#         refused login is retried every 2 h, never every 5 min.
#     ONE red-flag ping (and audit record) when the feed first enters that
#     backoff (MAX_FAILURES-th failure, a feasibility stop, or the relogin
#     ceiling below), ONE "recovered" ping when a later tick succeeds. Ticks
#     in between log one line and exit 0 without logging in. A stale
#     `tripped` marker left by the old code is ignored and removed with a log
#     line. `breakout-login-check apply: reset-feed` clears the backoff early
#     (after its own check passes); it is never required.
#   - SESSION REUSE (operator decision 2026-09-28 "Reuse saved session"):
#     passes --storage-state ${STATE_DIR}/session_state.json, so a tick opens
#     the terminal on the session the last good tick saved and only does a
#     credential login when that session is no longer accepted (the check
#     prints `session: reused` / `session: relogin`). Each relogin bumps a
#     per-UTC-day counter and writes an audit record carrying it, so how often
#     the feed really logs in is visible. The state file is a CREDENTIAL
#     EQUIVALENT: 0600 in a 0700 directory, never printed, dumped, pinged or
#     committed. The one-off system-action does NOT pass it (always fresh).
#   - RELOGIN CEILING (manager review of #13504, 2026-09-28): a credential
#     login that SUCCEEDS exits 0 and would never enter the failure backoff,
#     so if session reuse silently never works (e.g. the terminal keeps its
#     auth where Playwright's storage_state does not look, such as
#     sessionStorage) the feed would log in 288 times a day unnoticed. Every
#     credential ATTEMPT (`session: login_attempt`, printed before the
#     submit, so rejected and timed-out ones count too) bumps a per-UTC-day
#     counter. While that count is at or above PROP_FEED_MAX_RELOGINS_PER_DAY
#     (default 12, i.e. on average one fresh login per 2 h: far above what a
#     working saved session needs, far below the 288/day of no reuse at all)
#     every tick that runs puts the feed back on the CAPPED backoff (one red
#     flag per episode), so for the rest of that UTC day it runs at most
#     once per cap interval instead of every 5 min. It is a slow-down, not a
#     stop (NO-HALT). The counter resets at 00:00 UTC; `reset-feed` does NOT
#     clear it, so a same-day reset buys at most one early tick, never a
#     fresh 12. A corrupt or unwritable relogin counter is treated as AT the
#     ceiling (fails closed, onto the backoff).
#     Separately, the first time a UTC day ends with ticks but ZERO
#     `session: reused`, it pings ONCE (ever; marker `noreuse-pinged`), since
#     that is the signature of reuse not working at all.
#   - VENV IS NOT BUILT HERE: the tick uses the isolated venv the system-action
#     builds (~/.cache/metis-prop-browser), never the trader's Python, and never
#     pip-installs on a 5-minute cadence. No venv → environment failure.
#
# Output is only what breakout_login_check.py prints, which it redacts
# (credentials, e-mails, token runs, URL paths/queries). --dump-dir is NOT
# passed, so nothing about the page is written to disk.
#
# Exit: 0 ok / backoff-skip / lock-busy; otherwise the python exit code
# (3 unparsed, 4 feasibility, 5 environment, 124 timeout, 1 other).

set -euo pipefail

SCRIPT_NAME="prop_feed_tick"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

ACCOUNT="${PROP_FEED_ACCOUNT:-breakout_1}"
BASE="${PROP_BROWSER_BASE:-${HOME}/.cache/metis-prop-browser}"
VENV="${BASE}/venv"
# breakout_1 keeps the state dir it has always had; any other prop account
# (the ict-prop-feed@<account> template, TRADEIFY-WIRE 2026-09-30) gets its own,
# the SAME dir breakout-login-check uses for that account, so its saved session
# and backoff state are never shared with breakout_1's.
if [ "${ACCOUNT}" = "breakout_1" ]; then
    STATE_DIR="${BASE}/feed"
    RESET_HINT="breakout-login-check apply: reset-feed"
else
    STATE_DIR="${BASE}/accounts/${ACCOUNT}/feed"
    RESET_HINT="breakout-login-check account: ${ACCOUNT} apply: reset-feed"
fi
SESSION_STATE="${STATE_DIR}/session_state.json"
BACKOFF_FILE="${STATE_DIR}/backoff"
# Left by the pre-NO-HALT code; ignored and removed (see BACKOFF above).
LEGACY_TRIP_FILE="${STATE_DIR}/tripped"
LEGACY_FAILS_FILE="${STATE_DIR}/consecutive_failures"
# breakout_1 keeps the shared lock (its executor flock -n's the same file).
# Any other account locks its OWN file, so its feed can never make breakout_1's
# feed or real-money executor skip a tick (manager review of #14663).
if [ "${ACCOUNT}" = "breakout_1" ]; then
    LOCK_FILE="${BASE}/login.lock"
else
    LOCK_FILE="${BASE}/accounts/${ACCOUNT}/login.lock"
fi
MAX_FAILURES="${PROP_FEED_MAX_FAILURES:-3}"
MAX_RELOGINS="${PROP_FEED_MAX_RELOGINS_PER_DAY:-12}"
TIMEOUT_S="${PROP_FEED_TIMEOUT_S:-150}"
BACKOFF_BASE_S="${PROP_FEED_BACKOFF_BASE_S:-300}"
BACKOFF_CAP_S="${PROP_FEED_BACKOFF_CAP_S:-7200}"
# systemd timers fire within AccuracySec (default 1 min) of their slot, so a
# next attempt due within this many seconds counts as due now.
BACKOFF_SLACK_S="${PROP_FEED_BACKOFF_SLACK_S:-60}"
for v in MAX_FAILURES MAX_RELOGINS BACKOFF_BASE_S BACKOFF_CAP_S BACKOFF_SLACK_S; do
    case "${!v}" in ''|*[!0-9]*) log "${v}='${!v}' is not a plain integer; using the default"
        case "${v}" in
            MAX_FAILURES) MAX_FAILURES=3 ;; MAX_RELOGINS) MAX_RELOGINS=12 ;;
            BACKOFF_BASE_S) BACKOFF_BASE_S=300 ;; BACKOFF_CAP_S) BACKOFF_CAP_S=7200 ;;
            BACKOFF_SLACK_S) BACKOFF_SLACK_S=60 ;;
        esac ;;
    esac
done
TICK_START="$(date -u +%s)"
PING_PY="${PROP_FEED_PING_PY:-/usr/bin/python3}"

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"
if [ "${ACCOUNT}" = "breakout_1" ]; then
    FEED_KEYS="BREAKOUT_DX_USERNAME BREAKOUT_DX_PASSWORD"
elif ! FEED_KEYS="$(cd "${REPO_DIR}" && python3 scripts/prop/prop_env_keys.py "${ACCOUNT}" | awk '{print $1, $2}')" \
        || [ -z "${FEED_KEYS// }" ]; then
    log "account ${ACCOUNT}: no entry in config/prop_platforms.yaml — not logging in"
    exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    log "another breakout login (tick or system-action) holds ${LOCK_FILE}; skipping this tick"
    exit 0
fi

# A `tripped` marker from the pre-NO-HALT code no longer stops anything.
if [ -e "${LEGACY_TRIP_FILE}" ]; then
    log "ignoring and removing a stale trip marker from the old code ($(head -c 300 "${LEGACY_TRIP_FILE}" 2>/dev/null | tr -d '\n')); the feed retries on a capped backoff instead (NO-HALT)"
    rm -f "${LEGACY_TRIP_FILE}" || log "could not remove ${LEGACY_TRIP_FILE} (ignored either way)"
fi
rm -f "${LEGACY_FAILS_FILE}" 2>/dev/null || true

bo_get() {
    # Args: key. Prints its value from the backoff state (empty when absent).
    [ -f "${BACKOFF_FILE}" ] || return 0
    sed -n "s/^${1}=//p" "${BACKOFF_FILE}" 2>/dev/null | tail -n1
}
as_int() { case "${1}" in ''|*[!0-9]*) echo 0 ;; *) echo "$((10#${1}))" ;; esac; }
iso() { date -u -d "@${1}" +%Y-%m-%dT%H:%M:%SZ; }

BO_FAILS="$(as_int "$(bo_get consecutive_failures)")"
BO_NEXT="$(as_int "$(bo_get next_attempt_at)")"
BO_FLAGGED="$(bo_get flagged)"; [ "${BO_FLAGGED}" = "1" ] || BO_FLAGGED=0
BO_FLAGGED_AT="$(bo_get flagged_at)"
BO_SINCE="$(bo_get since)"
if [ "${BO_NEXT}" -gt $((TICK_START + BACKOFF_SLACK_S)) ]; then
    log "backing off: ${BO_FAILS} consecutive failure(s), last: $(bo_get reason); next login attempt at $(iso "${BO_NEXT}") (in $((BO_NEXT - TICK_START))s). Retrying automatically; to retry sooner: ${RESET_HINT}"
    exit 0
fi

write_backoff() {
    # Args: consecutive_failures delay_s rc reason. Atomic tmp+mv, with a
    # direct write as the fallback; a state that cannot be written at all is
    # logged, and the next tick then retries without backing off.
    local n="$1" delay="$2" rc="$3" reason="$4" at
    at=$((TICK_START + delay))
    reason="$(printf '%s' "${reason}" | tr -d '\r\n' | tr '"' "'")"
    [ -n "${BO_SINCE}" ] || BO_SINCE="$(iso "${TICK_START}")"
    local body
    body="$(printf 'consecutive_failures=%s\nnext_attempt_at=%s\nnext_attempt_at_utc=%s\ndelay_s=%s\nlast_exit=%s\nreason=%s\nsince=%s\nflagged=%s\nflagged_at=%s\nupdated_at=%s\n' \
        "${n}" "${at}" "$(iso "${at}")" "${delay}" "${rc}" "${reason}" "${BO_SINCE}" "${BO_FLAGGED}" "${BO_FLAGGED_AT}" "$(iso "$(date -u +%s)")")"
    if ! { printf '%s\n' "${body}" > "${BACKOFF_FILE}.tmp" && mv -fT "${BACKOFF_FILE}.tmp" "${BACKOFF_FILE}"; } 2>/dev/null; then
        rm -f "${BACKOFF_FILE}.tmp" 2>/dev/null || true
        printf '%s\n' "${body}" > "${BACKOFF_FILE}" 2>/dev/null \
            || log "CANNOT write the backoff state ${BACKOFF_FILE}; the next tick will retry without backing off"
    fi
}

red_flag() {
    # Args: rc reason delay_s n. ONE ping per backoff episode; flagged=1 in
    # the state suppresses the repeat until a success clears it.
    local rc="$1" reason="$2" delay="$3" n="$4"
    if [ "${BO_FLAGGED}" = "1" ]; then
        log "red flag already raised at ${BO_FLAGGED_AT:-?} for this episode; not pinging again"
        return 0
    fi
    BO_FLAGGED=1
    BO_FLAGGED_AT="$(iso "$(date -u +%s)")"
    log "RED FLAG: ${reason} (exit ${rc}); backing off, next login attempt in ${delay}s (cap ${BACKOFF_CAP_S}s)"
    record_audit "prop-feed" "backoff_red_flag" \
        "{\"account\": \"${ACCOUNT}\", \"exit\": ${rc}, \"consecutive_failures\": ${n}, \"next_attempt_in_s\": ${delay}, \"reason\": \"${reason}\"}" >/dev/null || true
    "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority high \
        --kind state_change \
        --why "the 5-min ${ACCOUNT} account_status feed keeps failing; it is retrying on a capped backoff" \
        "[prop-feed] ${ACCOUNT} feed BACKING OFF: ${reason} (exit ${rc}, ${n} consecutive failure(s)). It keeps retrying: next login attempt in ${delay}s, backoff capped at ${BACKOFF_CAP_S}s. One-time flag; a recovery notice follows when a tick succeeds. To retry sooner: ${RESET_HINT}." \
        >/dev/null 2>&1 || log "ping enqueue failed (backoff state + audit record still written)"
}

record_failure() {
    # Args: rc reason. Never stops the feed: it sets WHEN the next login is
    # attempted, and raises the one red flag on entering the backoff.
    local rc="$1" reason="$2" n delay=0
    n=$((BO_FAILS + 1))
    if [ "${rc}" = "4" ]; then
        delay="${BACKOFF_CAP_S}"
        reason="feasibility stop (challenge/CAPTCHA/2FA/login rejected/login timeout/no creds): ${reason}; retried at the backoff cap, never every tick"
    elif [ "${n}" -ge "${MAX_FAILURES}" ]; then
        delay="${BACKOFF_BASE_S}"
        local k=$((n - MAX_FAILURES))
        while [ "${k}" -gt 0 ] && [ "${delay}" -lt "${BACKOFF_CAP_S}" ]; do
            delay=$((delay * 2)); k=$((k - 1))
        done
        [ "${delay}" -gt "${BACKOFF_CAP_S}" ] && delay="${BACKOFF_CAP_S}"
        reason="${n} consecutive failures, last: ${reason}"
    fi
    log "failure ${n} (red flag at ${MAX_FAILURES}): ${reason} (exit ${rc}); next login attempt in ${delay}s"
    if [ "${rc}" = "4" ] || [ "${n}" -ge "${MAX_FAILURES}" ]; then
        red_flag "${rc}" "${reason}" "${delay}" "${n}"
    fi
    write_backoff "${n}" "${delay}" "${rc}" "${reason}"
}

ceiling_backoff() {
    # Args: rc reason. The relogin ceiling: back off to the cap (a slow-down,
    # never a stop); a failed read still counts as a failure.
    local rc="$1" reason="$2" n=0
    [ "${rc}" = "0" ] || n=$((BO_FAILS + 1))
    red_flag "${rc}" "${reason}" "${BACKOFF_CAP_S}" "${n}"
    write_backoff "${n}" "${BACKOFF_CAP_S}" "${rc}" "${reason}"
    log "relogin ceiling: next tick that may log in at $(iso $((TICK_START + BACKOFF_CAP_S)))"
}

record_success() {
    # One "recovered" ping when a flagged episode ends; the state is dropped.
    if [ "${BO_FLAGGED}" = "1" ]; then
        log "RECOVERED: the feed succeeded after ${BO_FAILS} consecutive failure(s) (backing off since ${BO_SINCE:-?}, flagged ${BO_FLAGGED_AT:-?})"
        record_audit "prop-feed" "recovered" \
            "{\"account\": \"${ACCOUNT}\", \"after_failures\": ${BO_FAILS}, \"since\": \"${BO_SINCE}\"}" >/dev/null || true
        "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority normal \
            --kind state_change \
            --why "the ${ACCOUNT} account_status feed is posting again" \
            "[prop-feed] ${ACCOUNT} feed RECOVERED: account_status posted after ${BO_FAILS} consecutive failure(s) (backing off since ${BO_SINCE:-?})." \
            >/dev/null 2>&1 || log "ping enqueue failed (audit record still written)"
    fi
    rm -f "${BACKOFF_FILE}" 2>/dev/null || log "could not remove ${BACKOFF_FILE}"
}

if [ ! -x "${VENV}/bin/python" ] || ! "${VENV}/bin/python" -c "import playwright" >/dev/null 2>&1; then
    record_failure 5 "isolated browser venv missing at ${VENV} (run breakout-login-check once to build it)"
    exit 5
fi

# Exactly the keys the check needs, from the VM .env; values never echoed.
if [ -f "${REPO_DIR}/.env" ]; then
    for ckey in ${FEED_KEYS} DASHBOARD_API_TOKEN; do
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

# Layout canary (TRADEIFY-GOLIVE, manager-approved 2026-10-01): every account
# but breakout_1 asks the check for a click-free `layout_watchlist:` line.
# breakout_1's feed is unchanged.
CANARY_ARG=""
[ "${ACCOUNT}" != "breakout_1" ] && CANARY_ARG="--layout-canary"

# The check's own (already redacted) output goes to the journal AND to a
# scratch copy, read back only for its `session:` line.
OUT="$(mktemp "${STATE_DIR}/.tick-out.XXXXXX")"
trap 'rm -f "${OUT}"' EXIT
set +e
( cd "${REPO_DIR}" && timeout --kill-after=15 "${TIMEOUT_S}" \
    env PYTHONUNBUFFERED=1 "${VENV}/bin/python" -u scripts/prop/breakout_login_check.py \
    --account "${ACCOUNT}" --emit-status --symbols= \
    --storage-state "${SESSION_STATE}" ${CANARY_ARG} ) 2>&1 | tee "${OUT}"
rc=${PIPESTATUS[0]}
set -e

bump() {
    # Args: file. Increments the integer in it; the new value lands in BUMP_N.
    # Returns non-zero (and sets BUMP_ERR) when the counter is CORRUPT
    # (present but not a plain integer) or cannot be WRITTEN (e.g. disk full).
    # Deliberately not called inside $(...): a subshell would swallow both the
    # failure and set -e.
    local f="$1" n=0
    BUMP_N=0; BUMP_ERR=""
    if [ -e "${f}" ] && [ ! -f "${f}" ]; then
        BUMP_ERR="corrupt (not a regular file)"; return 1
    fi
    if [ -f "${f}" ]; then
        n="$(tr -d '[:space:]' < "${f}" 2>/dev/null)" || { BUMP_ERR="unreadable"; return 1; }
        case "${n}" in
            '') n=0 ;;
            *[!0-9]*) BUMP_ERR="corrupt (non-numeric)"; return 1 ;;
        esac
    fi
    n=$((10#${n} + 1))
    if ! printf '%s\n' "${n}" > "${f}.tmp" 2>/dev/null || ! mv -fT "${f}.tmp" "${f}" 2>/dev/null; then
        rm -f "${f}.tmp" 2>/dev/null || true
        BUMP_ERR="write failed"; return 1
    fi
    BUMP_N="${n}"
}

day="$(date -u +%Y%m%d)"
# Roll over any previous UTC day first: a day that had ticks but no reused
# session pings once (ever), then its counters are dropped.
for tfile in "${STATE_DIR}"/ticks-*; do
    [ -e "${tfile}" ] || continue
    d="${tfile##*/ticks-}"
    [ "${d}" = "${day}" ] && continue
    if [ ! -f "${STATE_DIR}/reused-${d}" ] && [ ! -f "${STATE_DIR}/noreuse-pinged" ]; then
        touch "${STATE_DIR}/noreuse-pinged"
        log "UTC day ${d} ended with $(cat "${tfile}") tick(s) and ZERO reused sessions"
        record_audit "prop-feed" "no_reuse_day" \
            "{\"account\": \"${ACCOUNT}\", \"day_utc\": \"${d}\", \"ticks\": $(cat "${tfile}" 2>/dev/null || echo 0)}" >/dev/null || true
        "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority normal \
            --kind state_change \
            --why "session reuse may not be working: no tick that day reused a saved session" \
            "[prop-feed] ${ACCOUNT}: UTC day ${d} had ticks but no reused session. One-time notice; the ${MAX_RELOGINS}/day relogin ceiling still applies." \
            >/dev/null 2>&1 || log "ping enqueue failed (audit record still written)"
    fi
done
find "${STATE_DIR}" -maxdepth 1 \( -name 'ticks-*' -o -name 'reused-*' -o -name 'relogins-*' \) \
    ! -name "*-${day}" -delete 2>/dev/null || true

# ticks/reused feed only the one-time zero-reuse notice: best-effort.
bump "${STATE_DIR}/ticks-${day}" || log "ticks counter: ${BUMP_ERR} (best-effort; does not move the backoff)"
if grep -q '^session: reused$' "${OUT}"; then
    bump "${STATE_DIR}/reused-${day}" || log "reused counter: ${BUMP_ERR} (best-effort; does not move the backoff)"
    log "session: reused"
fi
read_counter() {
    # Args: file. Its integer lands in COUNT_N (0 when absent); returns
    # non-zero (and sets COUNT_ERR) when it is present but corrupt.
    local f="$1" n=0
    COUNT_N=0; COUNT_ERR=""
    if [ -e "${f}" ] && [ ! -f "${f}" ]; then COUNT_ERR="corrupt (not a regular file)"; return 1; fi
    if [ -f "${f}" ]; then
        n="$(tr -d '[:space:]' < "${f}" 2>/dev/null)" || { COUNT_ERR="unreadable"; return 1; }
        case "${n}" in '') n=0 ;; *[!0-9]*) COUNT_ERR="corrupt (non-numeric)"; return 1 ;; esac
    fi
    COUNT_N=$((10#${n}))
}

# The relogin counter is the ceiling's only input, so it FAILS CLOSED: a
# counter that is corrupt or cannot be written puts the feed on the capped
# backoff rather than silently reading as 0 or sticking at 1. Never a stop.
CEILING_REASON=""
RELOGINS_FILE="${STATE_DIR}/relogins-${day}"
if grep -q '^session: login_attempt$' "${OUT}"; then
    if ! bump "${RELOGINS_FILE}"; then
        CEILING_REASON="relogin counter ${BUMP_ERR} (${RELOGINS_FILE}) — failing closed onto the backoff"
    else
        rn="${BUMP_N}"
        ok=false; grep -q '^session: relogin$' "${OUT}" && ok=true
        log "session: credential login attempt ${rn}/${MAX_RELOGINS} today (UTC ${day}, succeeded=${ok})"
        record_audit "prop-feed" "relogin" \
            "{\"account\": \"${ACCOUNT}\", \"relogins_today\": ${rn}, \"day_utc\": \"${day}\", \"succeeded\": ${ok}, \"exit\": ${rc}}" >/dev/null || true
    fi
fi
# Whether or not THIS tick logged in, a UTC day already at the ceiling keeps
# the feed on the capped backoff until the counter rolls over at 00:00 UTC.
if [ -z "${CEILING_REASON}" ]; then
    if ! read_counter "${RELOGINS_FILE}"; then
        CEILING_REASON="relogin counter ${COUNT_ERR} (${RELOGINS_FILE}) — failing closed onto the backoff"
    elif [ "${COUNT_N}" -ge "${MAX_RELOGINS}" ]; then
        CEILING_REASON="relogin ceiling: ${COUNT_N} credential logins today (UTC ${day}, ceiling ${MAX_RELOGINS}) — session reuse is not holding"
    fi
fi

# Layout canary: ONE ping when the watchlist goes from present to MISSING
# (marker `layout-missing`), one log line when it comes back. Never moves
# the backoff and never changes its exit code; no line = nothing decided.
LAYOUT_MARK="${STATE_DIR}/layout-missing"
layout_line="$(grep -m1 '^layout_watchlist: ' "${OUT}" || true)"
case "${layout_line}" in
    "layout_watchlist: MISSING"*)
        if [ ! -f "${LAYOUT_MARK}" ]; then
            # An empty marker still suppresses the repeat ping.
            printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${layout_line}" > "${LAYOUT_MARK}" 2>/dev/null \
                || : > "${LAYOUT_MARK}" 2>/dev/null \
                || log "CANNOT write ${LAYOUT_MARK}; the next MISSING tick will ping again"
            log "LAYOUT: ${layout_line} (first tick missing; pinging once)"
            record_audit "prop-feed" "layout_watchlist_missing" \
                "{\"account\": \"${ACCOUNT}\"}" >/dev/null || true
            "${PING_PY}" "${REPO_DIR}/scripts/send_ping.py" --target claude --priority high \
                --kind state_change \
                --why "the terminal layout lost its watchlist; no ticket can open until it is back" \
                "[prop-feed] ${ACCOUNT}: the watchlist widget is MISSING from the terminal (no Symbol/Bid/Ask table). Ticket openers have nothing to act on. Fix: breakout-login-check account: ${ACCOUNT} apply: add-watchlist-widget." \
                >/dev/null 2>&1 || log "ping enqueue failed (marker + audit record still written)"
        fi ;;
    "layout_watchlist: ok"*)
        if [ -f "${LAYOUT_MARK}" ]; then
            rm -f "${LAYOUT_MARK}"
            log "LAYOUT: watchlist back (${layout_line})"
        fi ;;
esac

if [ -n "${CEILING_REASON}" ]; then
    ceiling_backoff "${rc}" "${CEILING_REASON}"
    [ "${rc}" = "0" ] && log "ok (account_status posted; on the relogin-ceiling backoff)"
    exit "${rc}"
fi

case "${rc}" in
    0) record_success; log "ok (account_status posted)"; exit 0 ;;
    3) record_failure 3 "login ok but part of the read did not parse" ;;
    4) record_failure 4 "the check printed a feasibility stop" ;;
    5) record_failure 5 "environment (chromium/playwright unusable)" ;;
    124|137) record_failure "${rc}" "hard timeout after ${TIMEOUT_S}s" ;;
    *) record_failure "${rc}" "error" ;;
esac
exit "${rc}"
