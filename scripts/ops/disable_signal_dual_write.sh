#!/usr/bin/env bash
# Tier-2 operator action: DISABLE the signals SQL dual-write.
#
# Sets ``SIGNAL_DUAL_WRITE_DISABLED=true`` in
# /home/ubuntu/ict-trading-bot/.env so
# ``src/utils/signal_audit_logger.py::_dual_write_to_db`` short-circuits
# (the JSONL writer is unaffected — it stays the source of truth). Then
# restarts the trader so the new env propagates. This is the rollback /
# pipeline-lag escape hatch for enable_signal_dual_write.sh.
#
# Idempotent. Safe to re-run. Does NOT touch strategy params, account
# configs, risk caps, or the live/dry-run mode flag (all Tier-3).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="SIGNAL_DUAL_WRITE_DISABLED"
ENV_VALUE="true"
ACTION="disable-signal-dual-write"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "${ACTION}" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' \
        --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "${ACTION}" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

if [ ! -f "${ENV_FILE}" ]; then
    log "ERROR: ${ENV_FILE} does not exist on this VM. Cannot toggle ${ENV_KEY}."
    record_audit "${ACTION}" "error" \
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
        echo "# Signals SQL dual-write (S-034). Set by scripts/ops/disable_signal_dual_write.sh."
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
    record_audit "${ACTION}" "error" \
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
    [ "${post_state}" = "active" ] && break
    sleep 2
done
log "Post-restart state of ${UNIT}: ${post_state}"

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

if [ "${post_state}" = "active" ]; then
    record_audit "${ACTION}" "ok" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "Signals SQL dual-write DISABLED. JSONL remains the source of truth."
    exit 0
else
    record_audit "${ACTION}" "failed" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s after env toggle."
    exit 1
fi
