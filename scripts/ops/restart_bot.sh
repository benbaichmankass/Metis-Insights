#!/usr/bin/env bash
# Tier-2 operator action: restart the live trader systemd unit.
#
# Mirrors the restart guard in scripts/deploy_pull_restart.sh:
# defers if any claude-vm-runner@*.service unit is currently active
# (a /vm invocation in flight would be killed by the restart).
#
# Pre/post checks:
#   - capture is-active state of the unit before restart
#   - issue `systemctl restart ict-trader-live.service`
#   - poll up to 30 s for `is-active` to return "active"
#   - dump the last 30 journal lines so the operator can spot crashes
#
# This script never touches strategy config, risk caps, or the
# per-account live/dry-run mode flag. Restart-only.

set -euo pipefail

SCRIPT_NAME="restart_bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required (see deploy_pull_restart.sh)."
    record_audit "restart-bot-service" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — borrowed from deploy_pull_restart.sh. Don't
# kill an in-flight /vm runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "restart-bot-service" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"
echo "===== pre-restart status ====="
"${SYSTEMCTL[@]}" status "${UNIT}" --no-pager -n 5 || true

log "Restarting ${UNIT}…"
"${SYSTEMCTL[@]}" restart "${UNIT}"

# Verify post-state. Allow up to 30 s for systemd to settle.
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
    record_audit "restart-bot-service" "ok" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\"}" >/dev/null || true
    log "Restart succeeded."
    exit 0
else
    record_audit "restart-bot-service" "failed" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\"}" >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 30 s."
    exit 1
fi
