#!/usr/bin/env bash
# Tier-2 operator action: enable the mobile-push notifier (M12 S1).
#
# Sets ``MOBILE_PUSH_ENABLED=1`` in
# /home/ubuntu/ict-trading-bot/.env (the EnvironmentFile that
# ict-trader-live.service reads on start). The trader process'
# Database.update_trade() observer hook checks this flag at the
# publish-event call site, so flipping it here changes behaviour on
# the next ``status='closed'`` write — which means push notifications
# start arriving on the operator's phone after the next live trade close.
#
# Restarts ict-trader-live.service so the new env propagates to the
# running process. Safe to re-run; idempotent.
#
# Symmetric companion: scripts/ops/disable_mobile_push.sh.
#
# Prerequisites (operator must do these once, BEFORE first enable):
#   - FCM_SERVICE_ACCOUNT_JSON in the same .env (the Firebase service-
#     account JSON downloaded from Project Settings → Service Accounts)
#   - At least one device token registered via POST /api/bot/devices/register
#   - google-auth installed in the trader's venv (requirements.txt)
#
# What this script does NOT touch:
#   - Strategy parameters (config/strategies.yaml)
#   - Account configs (config/accounts.yaml)
#   - Risk caps (config/risk_caps.yaml)
#   - The live/dry-run mode flag
# Those remain Tier-3 PRs.
set -euo pipefail

SCRIPT_NAME="enable_mobile_push"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="MOBILE_PUSH_ENABLED"
ENV_VALUE="1"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "enable-mobile-push" "error" \
        '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — don't kill an in-flight /vm runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "enable-mobile-push" "deferred" \
        '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist on this VM. Cannot toggle ${ENV_KEY}."
    record_audit "enable-mobile-push" "error" \
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
        echo "# M12 S1 — mobile push notifier activation."
        echo "# Set by scripts/ops/enable_mobile_push.sh."
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

# Brief sanity check that the unit came back up.
sleep 2
if "${SYSTEMCTL[@]}" is-active --quiet "${UNIT}"; then
    log "✓ ${UNIT} active after restart."
    record_audit "enable-mobile-push" "success" \
        "{\"unit\": \"${UNIT}\", \"prev_value\": \"${pre_value:-unset}\", \"new_value\": \"${ENV_VALUE}\"}" >/dev/null || true
    exit 0
else
    log "ERROR: ${UNIT} did not come back active after restart."
    record_audit "enable-mobile-push" "error" \
        "{\"unit\": \"${UNIT}\", \"reason\": \"unit inactive after restart\"}" >/dev/null || true
    exit 1
fi
