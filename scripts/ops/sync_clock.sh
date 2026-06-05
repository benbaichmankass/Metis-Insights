#!/usr/bin/env bash
# Tier-2 operator action: diagnose + correct live-VM clock drift.
#
# WHY (2026-06-05 incident): the live VM clock was ~6.5 s behind real
# time (pybit ErrCode 10002: req_timestamp vs server_timestamp), which
# exceeds Bybit's recv_window and forces signed-request retries — and the
# skew SURVIVED a full reboot, so NTP was not disciplining the clock.
#
# What this does (and its limits): the only NOPASSWD sudo on the VM is
# `systemctl` (see docs/claude/system-actions.md § 10), so this wrapper
# CANNOT `date -s` / `timedatectl set-ntp` / `chronyc makestep` directly.
# It can:
#   1. DIAGNOSE — timedatectl status + the NTP daemon's source/offset
#      (chronyc tracking/sources, or timedatectl timesync-status). These
#      reads need no sudo.
#   2. REMEDIATE within its means — `systemctl enable --now` + `restart`
#      the time daemon, which forces a fresh sync (and steps the clock on
#      the next successful poll if the daemon was merely stopped/disabled).
#
# Interpreting the output:
#   * "System clock synchronized: yes" + a small offset AFTER  -> fixed.
#   * Daemon active but sources unreachable / offset unchanged -> the NTP
#     egress (UDP 123) is almost certainly blocked at the OCI security
#     list. That is the one genuinely external step (cloud console): allow
#     UDP 123 egress, then re-run this action.
#
# Tier-2 (restarts a system service). No trade-path / config impact.

set -euo pipefail

SCRIPT_NAME="sync_clock"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required."
    record_audit "sync-clock" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# --- Identify the time daemon -------------------------------------------------
NTP_UNIT=""
USE_CHRONY=0
if command -v chronyc >/dev/null 2>&1; then
    USE_CHRONY=1
    for u in chrony.service chronyd.service; do
        if systemctl list-unit-files "${u}" >/dev/null 2>&1 \
           && systemctl list-unit-files "${u}" 2>/dev/null | grep -q "${u}"; then
            NTP_UNIT="${u}"; break
        fi
    done
fi
if [ -z "${NTP_UNIT}" ]; then
    if systemctl list-unit-files systemd-timesyncd.service 2>/dev/null | grep -q systemd-timesyncd; then
        NTP_UNIT="systemd-timesyncd.service"
        USE_CHRONY=0
    fi
fi

_offset_report() {
    if [ "${USE_CHRONY}" -eq 1 ]; then
        echo "--- chronyc tracking ---"; chronyc tracking 2>&1 || true
        echo "--- chronyc sources -v ---"; chronyc sources -v 2>&1 || true
    else
        echo "--- timedatectl timesync-status ---"; timedatectl timesync-status 2>&1 || true
    fi
}

echo "===== BEFORE — timedatectl status ====="
timedatectl status 2>&1 || true
echo
echo "===== BEFORE — NTP source/offset (daemon: ${NTP_UNIT:-<none found>}) ====="
_offset_report

if [ -z "${NTP_UNIT}" ]; then
    log "ERROR: no NTP daemon (chrony / systemd-timesyncd) found on the VM — cannot discipline the clock via systemctl. Needs an installed time daemon."
    record_audit "sync-clock" "error" '{"reason": "no ntp daemon found"}' >/dev/null || true
    exit 1
fi

echo
log "Enabling + (re)starting ${NTP_UNIT} to force a fresh NTP sync..."
"${SYSTEMCTL[@]}" enable --now "${NTP_UNIT}" 2>&1 | sed 's/^/  /' || true
"${SYSTEMCTL[@]}" restart "${NTP_UNIT}" 2>&1 | sed 's/^/  /' || true

# Give the daemon a moment to poll its source(s) and step/slew.
sleep 12

echo
echo "===== AFTER — timedatectl status ====="
timedatectl status 2>&1 || true
echo
echo "===== AFTER — NTP source/offset ====="
_offset_report

synced="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo "unknown")"
echo
log "NTPSynchronized=${synced} (unit=${NTP_UNIT})."
if [ "${synced}" = "yes" ]; then
    log "Clock is NTP-synchronized. If the offset above is small, the skew is corrected."
    record_audit "sync-clock" "success" "{\"ntp_unit\": \"${NTP_UNIT}\", \"synced\": \"yes\"}" >/dev/null || true
    exit 0
else
    log "WARNING: NTPSynchronized!=yes after restart. If sources show unreachable/no-data, NTP egress (UDP 123) is likely blocked at the OCI security list — allow it, then re-run sync-clock. (Not a wrapper failure; the daemon was (re)started.)"
    record_audit "sync-clock" "degraded" "{\"ntp_unit\": \"${NTP_UNIT}\", \"synced\": \"${synced}\"}" >/dev/null || true
    exit 0
fi
