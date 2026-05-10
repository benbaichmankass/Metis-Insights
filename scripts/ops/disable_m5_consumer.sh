#!/usr/bin/env bash
# Tier-2 operator action: deactivate the M5 strategy-testing consumer.
#
# Symmetric companion of scripts/ops/enable_m5_consumer.sh — sets
# ``M5_CONSUMER_ENABLED=0`` in /home/ubuntu/ict-trading-bot/.env and
# restarts ict-telegram-bot.service so the consumer un-installs from
# CommsPoller.poll_once.
#
# Idempotent. Safe to re-run.
#
# After running, ``/test <strategy>`` artifacts will still be minted
# (the dispatch path is independent of the consumer) but they sit
# pending in comms/requests/ until the gate is re-enabled or they
# expire per their TTL. The kill switch documented in
# docs/runbooks/strategy-testing.md.
set -euo pipefail

SCRIPT_NAME="disable_m5_consumer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-telegram-bot.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="M5_CONSUMER_ENABLED"
ENV_VALUE="0"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "disable-m5-consumer" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — don't kill an in-flight /vm runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "disable-m5-consumer" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist on this VM. Cannot toggle ${ENV_KEY}."
    record_audit "disable-m5-consumer" "error" \
        "{\"reason\": \"env file missing\", \"path\": \"${ENV_FILE}\"}" >/dev/null || true
    exit 1
fi

pre_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Pre-toggle value of ${ENV_KEY}: '${pre_value:-<unset>}'"

tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "${tmp_env}"' EXIT

if grep -qE "^${ENV_KEY}=" "${ENV_FILE}"; then
    sed -E "s|^${ENV_KEY}=.*|${ENV_KEY}=${ENV_VALUE}|" "${ENV_FILE}" > "${tmp_env}"
else
    cp "${ENV_FILE}" "${tmp_env}"
    {
        echo ""
        echo "# M5 — Strategy Testing Workflow consumer kill-switch."
        echo "# Set by scripts/ops/disable_m5_consumer.sh."
        echo "${ENV_KEY}=${ENV_VALUE}"
    } >> "${tmp_env}"
fi

chown --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
chmod --reference="${ENV_FILE}" "${tmp_env}" 2>/dev/null || true
mv "${tmp_env}" "${ENV_FILE}"
trap - EXIT

post_value="$(grep -E "^${ENV_KEY}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
log "Post-toggle value of ${ENV_KEY}: '${post_value}'"

if [ "${post_value}" != "${ENV_VALUE}" ]; then
    log "ERROR: post-edit verification failed. Expected '${ENV_VALUE}', got '${post_value}'."
    record_audit "disable-m5-consumer" "error" \
        "{\"reason\": \"verify mismatch\", \"expected\": \"${ENV_VALUE}\", \"actual\": \"${post_value}\"}" \
        >/dev/null || true
    exit 1
fi

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
    record_audit "disable-m5-consumer" "ok" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "M5 consumer DISABLED. /test <strategy> artifacts will sit pending until re-enabled."
    exit 0
else
    record_audit "disable-m5-consumer" "failed" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s after env toggle."
    exit 1
fi
