#!/usr/bin/env bash
# Tier-2 operator action: pause the liveness-watchdog autoheal loop.
#
# Runs `systemctl disable --now ict-liveness-watchdog.timer` on the live
# VM. The liveness watchdog (scripts/check_heartbeat.py, fired every 60 s
# by that timer) does TWO things: (1) Telegram-alerts on a stale
# heartbeat, and (2) auto-restarts ict-trader-live.service after the
# stale streak crosses --auto-restart-after. Stopping the timer pauses
# BOTH.
#
# WHY THIS EXISTS (2026-06-05 restart-loop incident): when the trader's
# first pipeline tick takes longer than the watchdog's autoheal window
# (e.g. a logged-out IB Gateway making every MES market-data fetch time
# out ~10 s, inflating the first tick past ~3 min), the watchdog keeps
# restarting the trader BEFORE it can complete a tick and write its first
# heartbeat — so the heartbeat stays permanently stale and the autoheal
# fires forever (a self-perpetuating restart loop). Pausing the autoheal
# lets the currently-running trader instance finish its slow first tick,
# enter the sleep loop, and start writing heartbeats every 60 s — which
# breaks the loop. Re-enable with resume_autoheal.sh once the trader is
# confirmed heartbeating.
#
# RISK while paused: the genuine dead-man switch is OFF. If the trader
# really dies during the pause, there is no alert and no auto-restart
# (the trader unit's own Restart=always still applies). Use only as a
# deliberate, temporary incident action and resume promptly.
#
# Symmetric companion: scripts/ops/resume_autoheal.sh.
# Idempotent: re-running on an already-stopped timer is a no-op.
#
# What this script does NOT touch:
#   - Strategy parameters (config/strategies.yaml)
#   - Account configs / mode flag (config/accounts.yaml)
#   - Risk caps (config/risk_caps.yaml)
#   - ict-trader-live.service (it keeps running; only the external
#     watchdog timer is stopped)

set -euo pipefail

SCRIPT_NAME="pause_autoheal"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

TIMER_UNIT="ict-liveness-watchdog.timer"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "pause-autoheal" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

pre_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
pre_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-state: ${TIMER_UNIT} is-enabled=${pre_enabled}, is-active=${pre_active}"

if [ "${pre_enabled}" = "unknown" ] && [ "${pre_active}" = "unknown" ]; then
    log "Note: ${TIMER_UNIT} not found on this VM. Nothing to pause."
    record_audit "pause-autoheal" "noop" \
        "{\"timer\": \"${TIMER_UNIT}\", \"reason\": \"unit not installed\"}" \
        >/dev/null || true
    exit 0
fi

log "Disabling + stopping ${TIMER_UNIT} (autoheal + stale-heartbeat alerts paused)..."
"${SYSTEMCTL[@]}" disable --now "${TIMER_UNIT}" 2>&1 | sed 's/^/  /'

# Brief sanity check.
sleep 1
post_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "disabled")"
post_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "inactive")"

if [ "${post_active}" != "active" ]; then
    log "✓ ${TIMER_UNIT} stopped (is-enabled=${post_enabled}, is-active=${post_active})"
    log "REMINDER: the dead-man switch is now OFF. Re-enable with resume-autoheal once the trader heartbeat is fresh."
    record_audit "pause-autoheal" "success" \
        "{\"timer\": \"${TIMER_UNIT}\", \"pre_enabled\": \"${pre_enabled}\", \"pre_active\": \"${pre_active}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 0
else
    log "ERROR: ${TIMER_UNIT} still active after disable (is-active=${post_active})."
    record_audit "pause-autoheal" "error" \
        "{\"timer\": \"${TIMER_UNIT}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 1
fi
