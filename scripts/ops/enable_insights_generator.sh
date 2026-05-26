#!/usr/bin/env bash
# Tier-2 operator action: enable + start the AI Analyst generator timer (M13 S1).
#
# Runs `systemctl daemon-reload && systemctl enable --now
# ict-insights-generator.timer` on the live VM. The timer fires the
# matching .service (oneshot) every 10 minutes, which runs
# scripts/ops/run_insights_cycle.sh → the generator CLI → refreshes
# runtime_logs/insights/*.json files the /api/bot/insights/* router serves.
#
# This is the *autonomous* activation path for the Ship-Autonomously
# Rule (docs/CLAUDE-RULES-CANONICAL.md). The previous manual SSH
# instruction in docs/runbooks/insights.md was an anti-pattern.
#
# Idempotent: re-running on an already-enabled timer is a no-op.
# Symmetric companion: scripts/ops/disable_insights_generator.sh.
#
# What this script does NOT touch:
#   - Strategy parameters (config/strategies.yaml)
#   - Account configs (config/accounts.yaml)
#   - Risk caps (config/risk_caps.yaml)
#   - The live/dry-run mode flag
#   - The trader service itself (ict-trader-live.service)
# Those remain Tier-3 PRs.
#
# The soft kill switch (`INSIGHTS_ENABLED=0` in /home/ubuntu/ict-trading-bot/.env)
# remains independent — set it to disable the generator's calls without
# stopping the timer; this script controls whether the timer runs at all.

set -euo pipefail

SCRIPT_NAME="enable_insights_generator"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

TIMER_UNIT="ict-insights-generator.timer"
SERVICE_UNIT="ict-insights-generator.service"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "enable-insights-generator" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Confirm the unit files were installed by scripts/install_systemd_units.sh
# (which runs as part of pull-and-deploy). If they're missing, the
# preceding deploy step didn't run yet — instruct the operator to run
# pull-and-deploy first rather than copying files from this script.
for unit in "${TIMER_UNIT}" "${SERVICE_UNIT}"; do
    target="/etc/systemd/system/${unit}"
    if [ ! -e "${target}" ]; then
        log "ERROR: ${target} not found. Run pull-and-deploy first to install the unit files."
        record_audit "enable-insights-generator" "error" \
            "{\"reason\": \"unit file missing\", \"path\": \"${target}\"}" >/dev/null || true
        exit 1
    fi
done

log "Reloading systemd daemon..."
"${SYSTEMCTL[@]}" daemon-reload

# Capture the pre-state so the audit row records the diff.
pre_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
pre_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-state: ${TIMER_UNIT} is-enabled=${pre_enabled}, is-active=${pre_active}"

log "Enabling + starting ${TIMER_UNIT}..."
"${SYSTEMCTL[@]}" enable --now "${TIMER_UNIT}"

# Brief sanity check.
sleep 2
post_enabled="$("${SYSTEMCTL[@]}" is-enabled "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"
post_active="$("${SYSTEMCTL[@]}" is-active "${TIMER_UNIT}" 2>/dev/null || echo "unknown")"

if [ "${post_enabled}" = "enabled" ] && [ "${post_active}" = "active" ]; then
    log "✓ ${TIMER_UNIT} is-enabled=enabled, is-active=active"
    # Show the next scheduled fire so the operator can verify cadence.
    "${SYSTEMCTL[@]}" list-timers --all "${TIMER_UNIT}" --no-legend 2>/dev/null | head -1 || true
    record_audit "enable-insights-generator" "success" \
        "{\"timer\": \"${TIMER_UNIT}\", \"pre_enabled\": \"${pre_enabled}\", \"pre_active\": \"${pre_active}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 0
else
    log "ERROR: ${TIMER_UNIT} did not reach enabled+active (is-enabled=${post_enabled}, is-active=${post_active})."
    record_audit "enable-insights-generator" "error" \
        "{\"timer\": \"${TIMER_UNIT}\", \"post_enabled\": \"${post_enabled}\", \"post_active\": \"${post_active}\"}" \
        >/dev/null || true
    exit 1
fi
