#!/usr/bin/env bash
# Tier-2 operator action: START the live trader systemd unit.
#
# The symmetric companion to scripts/ops/stop_bot.sh, which exists so a repair
# needing the trader ABSENT for a bounded window has a dispatch path. This is
# the half that CLOSES that window, and it is deliberately a separate action:
# a stop whose restart is bundled into it cannot be held open for the work the
# stop was taken for.
#
# ⚠️ WARNS IF THE LIVENESS WATCHDOG IS STILL PAUSED. While it is paused the
# genuine dead-man switch is OFF -- a trader that dies after this start gets no
# alert and no auto-restart. This WARNS rather than refuses, because resume is
# its own action (`resume-autoheal`) and the operator may legitimately sequence
# it after confirming the trader is heartbeating. But "started" and "protected"
# are different states and this says which one you have.
#
# Reconciles systemd units before starting, for the same reason restart_bot.sh
# does: a merged unit-file change can sit on disk un-applied when git-sync has
# already advanced HEAD, so a manual lifecycle action always reconciles first.
#
# What this script does NOT touch: strategy params, account mode flags, risk
# caps, or any order. Start-only.

set -euo pipefail

SCRIPT_NAME="start_bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
WATCHDOG_TIMER="ict-liveness-watchdog.timer"

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "start-bot-service" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

ERR_SINK="$(mktemp "${TMPDIR:-/tmp}/start_bot_err.XXXXXX" 2>&1)" || ERR_SINK="${TMPDIR:-/tmp}/start_bot_err.$$"
trap 'rm -f "${ERR_SINK}"' EXIT

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
log "Pre-start state of ${UNIT}: ${pre_state}"

INSTALL_UNITS="${SCRIPT_DIR}/../install_systemd_units.sh"
if [ -f "${INSTALL_UNITS}" ]; then
    log "Reconciling systemd units (install_systemd_units.sh) before start…"
    if bash "${INSTALL_UNITS}"; then
        log "Units reconciled."
    else
        log "WARNING: install_systemd_units.sh exited nonzero — starting on the currently-installed units."
    fi
fi

log "Starting ${UNIT}…"
"${SYSTEMCTL[@]}" start "${UNIT}"
heal_devnull || true

deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
    if [ "${post_state}" = "active" ]; then
        break
    fi
    sleep 2
done
log "Post-start state of ${UNIT}: ${post_state}"

wd_active="$("${SYSTEMCTL[@]}" is-active "${WATCHDOG_TIMER}" 2>>"${ERR_SINK}" || echo "unknown")"
if [ "${wd_active}" != "active" ]; then
    log "WARNING: ${WATCHDOG_TIMER} is '${wd_active}' — the liveness dead-man switch is still OFF."
    log "         Run resume-autoheal once the trader is confirmed heartbeating."
fi

echo
echo "===== post-start journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>>"${ERR_SINK}" || true

if [ "${post_state}" = "active" ]; then
    record_audit "start-bot-service" "ok" \
        "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"watchdog\": \"${wd_active}\"}" >/dev/null || true
    log "Start succeeded."
    exit 0
fi

record_audit "start-bot-service" "failed" \
    "{\"pre\": \"${pre_state}\", \"post\": \"${post_state}\", \"watchdog\": \"${wd_active}\"}" >/dev/null || true
log "ERROR: ${UNIT} did not reach 'active' within 30 s."
exit 1
