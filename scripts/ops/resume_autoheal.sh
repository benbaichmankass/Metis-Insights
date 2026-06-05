#!/usr/bin/env bash
# Tier-2 operator action: resume the liveness-watchdog autoheal loop.
#
# Runs `systemctl daemon-reload && systemctl enable --now
# ict-liveness-watchdog.timer` on the live VM — the symmetric undo of
# scripts/ops/pause_autoheal.sh. Restores the per-minute dead-man switch
# (stale-heartbeat Telegram alert + auto-restart of ict-trader-live.service
# after the configured stale streak).
#
# Run this once the trader is confirmed healthy + heartbeating (so the
# watchdog won't immediately see a stale heartbeat and re-restart it).
# The watchdog's boot-grace does NOT apply here (this is not a host boot),
# so a still-stale heartbeat at resume time will autoheal on the next
# streak — verify the heartbeat is fresh first.
#
# Idempotent: re-running on an already-enabled timer is a no-op.
# Symmetric companion: scripts/ops/pause_autoheal.sh.
#
# What this script does NOT touch:
#   - Strategy parameters (config/strategies.yaml)
#   - Account configs / mode flag (config/accounts.yaml)
#   - Risk caps (config/risk_caps.yaml)
#   - ict-trader-live.service itself

set -euo pipefail

SCRIPT_NAME="resume_autoheal"
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
    record_audit "resume-autoheal" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

target="/etc/systemd/system/${TIMER_UNIT}"
if [ ! -e "${target}" ]; then
    log "ERROR: ${target} not found. Run pull-and-deploy first to install the unit files."
    record_audit "resume-autoheal" "error" \
        "{\"reason\": \"unit file missing\", \"path\": \"${target}\"}" >/dev/null || true
    exit 1
fi

log "Reloading systemd daemon..."
"${SYSTEMCTL[@]}" daemon-reload

pre_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
pre_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-state: ${TIMER_UNIT} is-enabled=${pre_enabled}, is-active=${pre_active}"

log "Enabling + starting ${TIMER_UNIT} (dead-man switch + autoheal restored)..."
"${SYSTEMCTL[@]}" enable --now "${TIMER_UNIT}"

# Brief sanity check.
sleep 2
post_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
post_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"

if [ "${post_enabled}" = "enabled" ] && [ "${post_active}" = "active" ]; then
    log "✓ ${TIMER_UNIT} is-enabled=enabled, is-active=active"
    "${SYSTEMCTL[@]}" list-timers --all "${TIMER_UNIT}" --no-legend 2>/dev/null | head -1 || true
    record_audit "resume-autoheal" "success" \
        "{\"timer\": \"${TIMER_UNIT}\", \"pre_enabled\": \"${pre_enabled}\", \"pre_active\": \"${pre_active}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 0
else
    log "ERROR: ${TIMER_UNIT} did not reach enabled+active (is-enabled=${post_enabled}, is-active=${post_active})."
    record_audit "resume-autoheal" "error" \
        "{\"timer\": \"${TIMER_UNIT}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 1
fi
