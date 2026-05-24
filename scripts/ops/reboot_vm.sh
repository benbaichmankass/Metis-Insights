#!/usr/bin/env bash
# Tier-2 LAST-RESORT operator action: reboot the VM.
#
# Use only when:
#   - `restart-bot-service` has already been tried and the trader
#     still won't come back, OR
#   - a kernel/networking-level issue is suspected, OR
#   - explicitly directed by the operator.
#
# Why this needs special care:
#   - a reboot drops every active SSH session, including any in-flight
#     /vm runner, the bot, the web-api, and the trader itself
#   - if the systemd units don't auto-start cleanly on boot, recovery
#     requires manual intervention (Oracle Cloud Console — see
#     docs/audit/sprint-013-deployment-runbook.md)
#
# Mechanism:
#   - capture pre-reboot state to runtime_logs/operator_actions/
#   - call `shutdown -r +1` so we have a 60 s window to abort if
#     something looks wrong; the workflow waits for SSH to return.
#   - the workflow's "Wait for VM to come back" step polls SSH for up
#     to 5 minutes after the call.
#
# Required sudoers (one-time manual setup — see
# docs/claude/system-actions.md § "VM sudoers setup"):
#   ubuntu ALL=(ALL) NOPASSWD: /sbin/shutdown -r *
#
# This script never edits config, code, or runtime flags.

set -euo pipefail

SCRIPT_NAME="reboot_vm"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
elif sudo -n /sbin/shutdown --help >/dev/null 2>&1 || sudo -n true 2>/dev/null; then
    SUDO=(sudo)
else
    log "ERROR: passwordless sudo for /sbin/shutdown is required."
    log "       Add: ubuntu ALL=(ALL) NOPASSWD: /sbin/shutdown -r *"
    record_audit "reboot-vm" "error" '{"reason": "sudo shutdown unavailable"}' >/dev/null || true
    exit 1
fi

log "Capturing pre-reboot snapshot…"
{
    echo "===== uptime ====="
    uptime
    echo
    echo "===== systemctl is-active (canonical units) ====="
    for u in ict-trader-live.service ict-web-api.service ict-telegram-bot.service; do
        printf '%-32s %s\n' "${u}" \
            "$(systemctl is-active "${u}" 2>/dev/null || echo unknown)"
    done
    echo
    echo "===== last 10 journal lines (ict-trader-live) ====="
    journalctl -u ict-trader-live.service -n 10 --no-pager 2>/dev/null || true
} >&2

# Persist the snapshot inside the repo so the diag relay can fetch
# it after the reboot completes.
record_audit "reboot-vm" "scheduled" "{\"delay_min\": 1}" >/dev/null || true

log "Scheduling reboot in 1 minute (shutdown -r +1)…"
"${SUDO[@]}" /sbin/shutdown -r +1 "system-actions: reboot-vm requested via GitHub Actions" \
    || {
        log "ERROR: shutdown command failed."
        record_audit "reboot-vm" "error" '{"reason": "shutdown call failed"}' >/dev/null || true
        exit 1
    }

# Print confirmation. SSH session will drop ~60 s from now.
log "Reboot scheduled. SSH will drop momentarily; the workflow handles reconnect."
exit 0
