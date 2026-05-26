#!/usr/bin/env bash
# Tier-2 operator action: disable the mobile-push notifier (M12 S1).
#
# Sets ``MOBILE_PUSH_ENABLED=0`` in /home/ubuntu/ict-trading-bot/.env
# and restarts ict-trader-live.service so the trader stops calling
# publish_event() on trade closes. The router at /api/bot/devices/*
# stays available — devices can still register / be revoked — only the
# notifier fan-out is silenced. Push notifications resume the moment
# enable_mobile_push.sh is re-run.
#
# Idempotent. Safe to re-run.
#
# Symmetric companion: scripts/ops/enable_mobile_push.sh.
set -euo pipefail

SCRIPT_NAME="disable_mobile_push"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="MOBILE_PUSH_ENABLED"
ENV_VALUE="0"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "disable-mobile-push" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "disable-mobile-push" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist on this VM. Cannot toggle ${ENV_KEY}."
    record_audit "disable-mobile-push" "error" \
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
        echo "# M12 S1 — mobile push notifier disabled."
        echo "# Set by scripts/ops/disable_mobile_push.sh."
        echo "${ENV_KEY}=${ENV_VALUE}"
    } >> "${tmp_env}"
fi

mv "${tmp_env}" "${ENV_FILE}"
chown ubuntu:ubuntu "${ENV_FILE}" 2>/dev/null || true
chmod 600 "${ENV_FILE}" 2>/dev/null || true
trap - EXIT

log "Set ${ENV_KEY}=${ENV_VALUE} in ${ENV_FILE}."

log "Restarting ${UNIT}..."
"${SYSTEMCTL[@]}" daemon-reload
"${SYSTEMCTL[@]}" restart "${UNIT}"

sleep 2
if "${SYSTEMCTL[@]}" is-active --quiet "${UNIT}"; then
    log "✓ ${UNIT} active after restart. Mobile push is now silenced."
    record_audit "disable-mobile-push" "success" \
        "{\"unit\": \"${UNIT}\", \"prev_value\": \"${pre_value:-unset}\", \"new_value\": \"${ENV_VALUE}\"}" >/dev/null || true
    exit 0
else
    log "ERROR: ${UNIT} did not come back active after restart."
    record_audit "disable-mobile-push" "error" \
        "{\"unit\": \"${UNIT}\", \"reason\": \"unit inactive after restart\"}" >/dev/null || true
    exit 1
fi
