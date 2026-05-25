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
# ). The workflow threads these as ACTION_MESSAGE / ACTION_PRIORITY /
# ACTION_TARGET env vars.
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

if [ ! -f "${SEND_PING}" ]; then
    log "ERROR: ${SEND_PING} not found."
    record_audit "send-ping" "error" '{"reason": "send_ping.py missing"}' >/dev/null || true
    exit 1
fi

log "Enqueuing ${PRIORITY} ping to ${TARGET} bot (${#MESSAGE} chars)."
if /usr/bin/python3 "${SEND_PING}" \
        --target "${TARGET}" \
        --priority "${PRIORITY}" \
        "${MESSAGE}"; then
    record_audit "send-ping" "ok" \
        "{\"target\": \"${TARGET}\", \"priority\": \"${PRIORITY}\", \"chars\": ${#MESSAGE}}" >/dev/null || true
    log "send-ping queued — bot drains within ~5 s."
    exit 0
else
    record_audit "send-ping" "failed" \
        "{\"target\": \"${TARGET}\", \"priority\": \"${PRIORITY}\"}" >/dev/null || true
    log "ERROR: send_ping.py returned nonzero."
    exit 1
fi
