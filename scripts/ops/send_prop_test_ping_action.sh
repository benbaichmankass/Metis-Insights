#!/usr/bin/env bash
# Tier-1 system-action: fire ONE test prop-account ticket through the real
# ping path (FCM push + the prop Telegram bot).
#
# Exercises the Breakout prop "trade flow" up to and including the
# notification — ruleset resolution, per-account leg + sizing, ticket render,
# and the prop_signal fan-out — using a SYNTHETIC, clearly-labelled order.
# It calls the emitter directly (not the execute path), so NOTHING is
# journaled and no exchange socket is opened. Safe to run repeatedly.
#
# Dispatched by the system-actions workflow (issue body:
#   action: send-prop-test-ping
#   symbol: SOLUSDT          (optional, default SOLUSDT)
#   strategy: trend_donchian_sol   (optional, default trend_donchian_sol)
# ). The workflow threads these as ACTION_SYMBOL / ACTION_STRATEGY env vars.
#
# Exit codes: 0 = ticket emitted (at least one leg attempted), 1 = failure.

set -euo pipefail

SCRIPT_NAME="send_prop_test_ping_action"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

# Load the runtime .env so the prop bot token (TELEGRAM_PROP_BOT_TOKEN /
# TELEGRAM_CLAUDE_BOT_TOKEN), TELEGRAM_CHAT_ID, and the FCM creds are present —
# the same environment the live trader emits prop signals under.
load_runtime_env

TEST_PING="${REPO_DIR}/scripts/prop/send_test_ping.py"
SYMBOL="${ACTION_SYMBOL:-SOLUSDT}"
STRATEGY="${ACTION_STRATEGY:-trend_donchian_sol}"

if [ ! -f "${TEST_PING}" ]; then
    log "ERROR: ${TEST_PING} not found."
    record_audit "send-prop-test-ping" "error" '{"reason": "send_test_ping.py missing"}' >/dev/null || true
    exit 1
fi

log "Emitting TEST prop ticket (symbol=${SYMBOL} strategy=${STRATEGY})."
if /usr/bin/python3 "${TEST_PING}" --symbol "${SYMBOL}" --strategy "${STRATEGY}"; then
    record_audit "send-prop-test-ping" "ok" \
        "{\"symbol\": \"${SYMBOL}\", \"strategy\": \"${STRATEGY}\"}" >/dev/null || true
    log "send-prop-test-ping: ticket emitted (check the prop Telegram bot + Android)."
    exit 0
else
    record_audit "send-prop-test-ping" "failed" \
        "{\"symbol\": \"${SYMBOL}\", \"strategy\": \"${STRATEGY}\"}" >/dev/null || true
    log "ERROR: send_test_ping.py returned nonzero."
    exit 1
fi
