#!/usr/bin/env bash
# Tier-1 system-action: send an immediate ping to the operator's Telegram.
#
# This is the autonomous "Claude wants to say something NOW" path. It does
# NOT deploy, pull, or restart anything — it just enqueues a message via
# scripts/send_ping.py, which the relevant bot drains within ~5 s:
#   target=claude → @claude_ict_comms_bot (default; Claude's update channel)
#   target=trader → @bict_trading_bot     (trade/system alerts)
#
# Dispatched by the system-actions workflow (issue body:
#   action: send-ping
#   message: <one-line message>
#   priority: <urgent|high|normal|low>   (optional, default normal)
#   target: <claude|trader>              (optional, default claude)
#   kind: <decision|state_change|lifecycle>   (optional; BLANK = passthrough)
#   why: <what CHANGED for the reader>        (required when kind is set)
#   unproven: <what this does NOT establish>  (optional, needs kind)
# ). The workflow threads these as ACTION_MESSAGE / ACTION_PRIORITY /
# ACTION_TARGET / ACTION_KIND / ACTION_WHY / ACTION_UNPROVEN env vars.
#
# ⚠️ PASSTHROUGH IS THE DEFAULT AND MUST STAY THE DEFAULT (2026-09-01).
# Until now this wrapper passed no `--kind`, so `send-ping` could only ever
# fire the passthrough shape and the three classes in
# src/runtime/claude_ping.py were unreachable from the action. That is the gap
# `kind:` closes. It is NOT a migration: the passthrough path carries the
# OPERATOR'S OWN WORDS -- a session saying something now, in its own voice --
# and Format B is a house style for MACHINE-generated events. Forcing an
# operator sentence into "headline / why" would rewrite what a human chose to
# say, so an empty `kind` runs the byte-for-byte previous command line.
#
# ⚠️ A WITHHELD PING IS NOT A SENT PING. `send_ping.py` exits 0 and prints
# `withheld: <reason>` when the class limiter suppresses one (a lifecycle ping
# inside the 300 s window). Recording that as `ok` would collapse "delivered"
# into "suppressed" -- precisely the distinction `claude_ping.admits()` returns
# a REASON to preserve. The audit below records it as its own status.
#
# Exit codes: 0 success, 1 validation / enqueue failure.

set -euo pipefail

SCRIPT_NAME="send_ping_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Export DATA_DIR (+ friends) so send_ping.py resolves the CANONICAL inbox
# (runtime_logs_dir() → $DATA_DIR/runtime_logs) — the same dir the bot
# drainers read. Without this, the action subprocess has no DATA_DIR (it's
# stripped from .env), send_ping falls back to the repo-relative inbox, and
# any drainer running with DATA_DIR (e.g. ict-claude-bridge) never sees the
# ping. (2026-05-25: claude-channel pings silently undelivered.)
load_runtime_env

SEND_PING="${REPO_DIR}/scripts/send_ping.py"

MESSAGE="${ACTION_MESSAGE:-}"
PRIORITY="${ACTION_PRIORITY:-normal}"
TARGET="${ACTION_TARGET:-claude}"
KIND="${ACTION_KIND:-}"
WHY="${ACTION_WHY:-}"
UNPROVEN="${ACTION_UNPROVEN:-}"

if [ -z "${MESSAGE// }" ]; then
    log "ERROR: send-ping requires a non-empty 'message'."
    record_audit "send-ping" "error" '{"reason": "empty message"}' >/dev/null || true
    exit 1
fi

case "${PRIORITY}" in
    urgent|high|normal|low) ;;
    *)
        log "WARN: invalid priority '${PRIORITY}', defaulting to normal."
        PRIORITY="normal"
        ;;
esac

case "${TARGET}" in
    claude|trader) ;;
    *)
        log "WARN: invalid target '${TARGET}', defaulting to claude."
        TARGET="claude"
        ;;
esac

# ⚠️ AN INVALID KIND IS A HARD ERROR, NEVER A FALLBACK TO PASSTHROUGH -- and
# that is deliberately UNLIKE the priority/target cases above, which warn and
# default. Those two pick a destination or a prefix; a wrong one is visible in
# the delivered message. `kind` selects the FORMAT and the RATE LIMITER, so
# silently degrading a misspelled `state-change` to passthrough would send an
# unformatted ping, record nothing against the limiter, and report success --
# a caller who asked for a gated, formatted ping would have no way to tell they
# did not get one. Refusing is the only honest direction.
if [ -n "${KIND// }" ]; then
    case "${KIND}" in
        decision|state_change|lifecycle) ;;
        *)
            log "ERROR: kind '${KIND}' is not a class (decision|state_change|lifecycle). Leave it blank for the passthrough shape."
            record_audit "send-ping" "error" \
                "{\"reason\": \"invalid kind\", \"kind\": \"${KIND}\"}" >/dev/null || true
            exit 1
            ;;
    esac
    if [ -z "${WHY// }" ]; then
        log "ERROR: kind='${KIND}' requires 'why' -- Format B's second line is the whole point."
        record_audit "send-ping" "error" \
            "{\"reason\": \"kind without why\", \"kind\": \"${KIND}\"}" >/dev/null || true
        exit 1
    fi
fi

if [ ! -f "${SEND_PING}" ]; then
    log "ERROR: ${SEND_PING} not found."
    record_audit "send-ping" "error" '{"reason": "send_ping.py missing"}' >/dev/null || true
    exit 1
fi

# The Format-B flags are appended ONLY when a kind was given, so the
# passthrough invocation is byte-for-byte what this script has always run.
PING_ARGS=(--target "${TARGET}" --priority "${PRIORITY}")
if [ -n "${KIND// }" ]; then
    PING_ARGS+=(--kind "${KIND}" --why "${WHY}")
    [ -n "${UNPROVEN// }" ] && PING_ARGS+=(--unproven "${UNPROVEN}")
fi

log "Enqueuing ${PRIORITY} ping to ${TARGET} bot (${#MESSAGE} chars, kind=${KIND:-passthrough})."

# stdout is CAPTURED rather than inherited because it carries the one fact the
# exit code cannot: `withheld: <reason>` on a rate-limited class. Still logged
# below, so nothing is hidden by capturing it.
set +e
PING_OUT="$(/usr/bin/python3 "${SEND_PING}" "${PING_ARGS[@]}" "${MESSAGE}" 2>&1)"
PING_RC=$?
set -e
[ -n "${PING_OUT}" ] && log "send_ping.py: ${PING_OUT}"

if [ "${PING_RC}" -ne 0 ]; then
    record_audit "send-ping" "failed" \
        "{\"target\": \"${TARGET}\", \"priority\": \"${PRIORITY}\", \"kind\": \"${KIND:-}\"}" >/dev/null || true
    log "ERROR: send_ping.py returned ${PING_RC}."
    exit 1
fi

# ⚠️ EXIT 0 IS NOT ENOUGH TO CLAIM A SEND. A suppressed class-ping also exits
# 0; only the absence of the `withheld:` marker distinguishes them.
if printf '%s' "${PING_OUT}" | grep -q '^withheld:'; then
    WITHHELD_REASON="$(printf '%s' "${PING_OUT}" | grep -m1 '^withheld:' | sed -E 's/^withheld:[[:space:]]*//' | tr -d '"\\')"
    record_audit "send-ping" "withheld" \
        "{\"target\": \"${TARGET}\", \"priority\": \"${PRIORITY}\", \"kind\": \"${KIND:-}\", \"reason\": \"${WITHHELD_REASON}\"}" >/dev/null || true
    log "send-ping WITHHELD by the class limiter: ${WITHHELD_REASON} — nothing was queued."
    exit 0
fi

record_audit "send-ping" "ok" \
    "{\"target\": \"${TARGET}\", \"priority\": \"${PRIORITY}\", \"kind\": \"${KIND:-passthrough}\", \"chars\": ${#MESSAGE}}" >/dev/null || true
log "send-ping queued — bot drains within ~5 s."
exit 0
