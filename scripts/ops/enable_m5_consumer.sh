#!/usr/bin/env bash
# Tier-2 operator action: activate the M5 strategy-testing consumer
# on the Telegram bot.
#
# Sets ``M5_CONSUMER_ENABLED=1`` in
# /home/ubuntu/ict-trading-bot/.env (the EnvironmentFile that
# ict-telegram-bot.service reads on start) so
# ``install_comms_handlers`` auto-installs the BacktestConsumer pass
# in CommsPoller.poll_once. Then restarts the unit so the new env
# propagates to the running process.
#
# Idempotent. Safe to re-run.
#
# Symmetric companion: scripts/ops/disable_m5_consumer.sh.
#
# What this is for: M5 — Strategy Testing Workflow shipped 2026-05-10
# across PRs #637/#639/#640/#689 and dashboard #12. The consumer is
# OFF by default everywhere (so dev/CI checkouts never auto-run
# backtests); this action is the canonical activation path on the
# live VM. Operator runbook:
# docs/runbooks/strategy-testing.md.
#
# What this script does NOT touch:
#   - strategy parameters (config/strategies.yaml)
#   - account configs (config/accounts.yaml)
#   - risk caps (config/risk.yaml)
#   - the live/dry-run mode flag
# Those remain Tier-3 PRs.
set -euo pipefail

SCRIPT_NAME="enable_m5_consumer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-telegram-bot.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="M5_CONSUMER_ENABLED"
ENV_VALUE="1"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "enable-m5-consumer" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — don't kill an in-flight /vm runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "enable-m5-consumer" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist on this VM. Cannot toggle ${ENV_KEY}."
    record_audit "enable-m5-consumer" "error" \
        "{\"reason\": \"env file missing\", \"path\": \"${ENV_FILE}\"}" >/dev/null || true
    exit 1
fi

# Snapshot current value (if any) for the audit record.
pre_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Pre-toggle value of ${ENV_KEY}: '${pre_value:-<unset>}'"

# Idempotent in-place edit. If the line exists, replace it; else append.
# Use a tmp file + atomic rename so a crash mid-edit doesn't leave
# the .env half-written.
tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "${tmp_env}"' EXIT

if grep -qE "^${ENV_KEY}=" "${ENV_FILE}"; then
    sed -E "s|^${ENV_KEY}=.*|${ENV_KEY}=${ENV_VALUE}|" "${ENV_FILE}" > "${tmp_env}"
else
    cp "${ENV_FILE}" "${tmp_env}"
    {
        echo ""
        echo "# M5 — Strategy Testing Workflow consumer activation."
        echo "# Set by scripts/ops/enable_m5_consumer.sh."
        echo "${ENV_KEY}=${ENV_VALUE}"
    } >> "${tmp_env}"
fi

# Preserve original ownership + mode.
chown --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
chmod --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
mv "${tmp_env}" "${ENV_FILE}"
trap - EXIT

post_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Post-toggle value of ${ENV_KEY}: '${post_value}'"

if [ "${post_value}" != "${ENV_VALUE}" ]; then
    log "ERROR: post-edit verification failed. Expected '${ENV_VALUE}', got '${post_value}'."
    record_audit "enable-m5-consumer" "error" \
        "{\"reason\": \"verify mismatch\", \"expected\": \"${ENV_VALUE}\", \"actual\": \"${post_value}\"}" \
        >/dev/null || true
    exit 1
fi

# Restart the bot so the new env propagates to the running process.
pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"

log "Restarting ${UNIT}…"
"${SYSTEMCTL[@]}" restart "${UNIT}"

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    if [ "${post_state}" = "active" ]; then
        break
    fi
    sleep 2
done
log "Post-restart state of ${UNIT}: ${post_state}"

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

if [ "${post_state}" = "active" ]; then
    record_audit "enable-m5-consumer" "ok" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "M5 consumer ENABLED. /test <strategy> in Telegram now runs the closed loop."
    log "Watch: tail -f ${REPO_DIR}/runtime_logs/validation.jsonl"
    exit 0
else
    record_audit "enable-m5-consumer" "failed" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s after env toggle."
    log "Manual rollback: scripts/ops/disable_m5_consumer.sh"
    exit 1
fi
