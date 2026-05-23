#!/usr/bin/env bash
# Tier-2 operator action: ENABLE the signals SQL dual-write.
#
# Sets ``SIGNAL_DUAL_WRITE_DISABLED=false`` in
# /home/ubuntu/ict-trading-bot/.env (the EnvironmentFile that
# ict-trader-live.service reads on start) so
# ``src/utils/signal_audit_logger.py::_dual_write_to_db`` hydrates the
# ``trade_journal.db::signals`` table on every signal eval (S-034
# cutover). Then restarts the trader so the new env propagates.
#
# Idempotent. Safe to re-run. Symmetric companion:
# scripts/ops/disable_signal_dual_write.sh.
#
# WHY THIS IS GATED: the dual-write opens a SQLite write per signal
# eval on the LIVE trading hot path. It was opted out via this env flag
# to avoid pipeline lag; the JSONL (signal_audit.jsonl) remains the
# source of truth either way. Re-enable only when the SQL ``signals``
# table is actually needed (e.g. Data Explorer browsing) and the
# per-eval write cost is acceptable. Watch the next few ticks for
# tick-time regressions after enabling.
#
# What this script does NOT touch: strategy params, account configs,
# risk caps, or the live/dry-run mode flag (all Tier-3).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
ENV_FILE="${REPO_DIR}/.env"
ENV_KEY="SIGNAL_DUAL_WRITE_DISABLED"
ENV_VALUE="false"
ACTION="enable-signal-dual-write"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "${ACTION}" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — don't kill an in-flight /vm runner.
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

# Idempotent atomic in-place edit (tmp file + rename).
tmp_env="$(mktemp "${ENV_FILE}.XXXXXX")"
trap 'rm -f "${tmp_env}"' EXIT

if grep -qE "^${ENV_KEY}=" "${ENV_FILE}"; then
    sed -E "s|^${ENV_KEY}=.*|${ENV_KEY}=${ENV_VALUE}|" "${ENV_FILE}" > "${tmp_env}"
else
    cp "${ENV_FILE}" "${tmp_env}"
    {
        echo ""
        echo "# Signals SQL dual-write (S-034). Set by scripts/ops/enable_signal_dual_write.sh."
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
    log "Signals SQL dual-write ENABLED. trade_journal.db::signals now hydrates per eval."
    log "Rollback: scripts/ops/disable_signal_dual_write.sh"
    exit 0
else
    record_audit "${ACTION}" "failed" \
        "{\"pre_value\": \"${pre_value:-<unset>}\", \"post_value\": \"${post_value}\", \"pre_state\": \"${pre_state}\", \"post_state\": \"${post_state}\"}" \
        >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s after env toggle."
    log "Manual rollback: scripts/ops/disable_signal_dual_write.sh"
    exit 1
fi
