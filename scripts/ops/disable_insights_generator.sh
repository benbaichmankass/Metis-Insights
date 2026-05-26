#!/usr/bin/env bash
# Tier-2 operator action: disable + stop the AI Analyst generator timer (M13 S1).
#
# Runs `systemctl disable --now ict-insights-generator.timer` on the
# live VM. After this fires, the timer no longer schedules new runs;
# any in-flight cycle (the matching .service is oneshot) completes
# naturally — the script does NOT kill an active run mid-cycle.
#
# This is the hard disable — for a soft disable (timer still scheduled
# but each fire exits immediately), set `INSIGHTS_ENABLED=0` in
# /home/ubuntu/ict-trading-bot/.env instead. The runbook
# (docs/runbooks/insights.md) documents both.
#
# Symmetric companion: scripts/ops/enable_insights_generator.sh.
# Idempotent: re-running on an already-disabled timer is a no-op.

set -euo pipefail

SCRIPT_NAME="disable_insights_generator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

TIMER_UNIT="ict-insights-generator.timer"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "disable-insights-generator" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

pre_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
pre_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-state: ${TIMER_UNIT} is-enabled=${pre_enabled}, is-active=${pre_active}"

if [ "${pre_enabled}" = "unknown" ] && [ "${pre_active}" = "unknown" ]; then
    log "Note: ${TIMER_UNIT} not found on this VM. Nothing to disable."
    record_audit "disable-insights-generator" "noop" \
        "{\"timer\": \"${TIMER_UNIT}\", \"reason\": \"unit not installed\"}" \
        >/dev/null || true
    exit 0
fi

log "Disabling + stopping ${TIMER_UNIT}..."
"${SYSTEMCTL[@]}" disable --now "${TIMER_UNIT}" 2>&1 | sed 's/^/  /'

# Brief sanity check.
sleep 1
post_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "disabled")"
post_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "inactive")"

if [ "${post_active}" != "active" ]; then
    log "✓ ${TIMER_UNIT} stopped (is-enabled=${post_enabled}, is-active=${post_active})"
    record_audit "disable-insights-generator" "success" \
        "{\"timer\": \"${TIMER_UNIT}\", \"pre_enabled\": \"${pre_enabled}\", \"pre_active\": \"${pre_active}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 0
else
    log "ERROR: ${TIMER_UNIT} still active after disable (is-active=${post_active})."
    record_audit "disable-insights-generator" "error" \
        "{\"timer\": \"${TIMER_UNIT}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 1
fi
