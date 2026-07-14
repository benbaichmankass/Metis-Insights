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

# State reads must never depend on /dev/null: the OCI host agent has stripped
# it to 0444 MID-RUN (first redirect of the run succeeded, later ones EACCESed
# — 2026-07-13 + 2026-07-14, BL-20260713-DEVNULL-RESTART-MISREPORT), which made
# `is-active … 2>/dev/null` fail and report post_state "unknown" on a restart
# that actually succeeded. All stderr suppression below goes to a temp sink.
ERR_SINK="$(mktemp "${TMPDIR:-/tmp}/restart_bot_err.XXXXXX" 2>&1)" || ERR_SINK="${TMPDIR:-/tmp}/restart_bot_err.$$"
trap 'rm -f "${ERR_SINK}"' EXIT

# Defense in depth — borrowed from deploy_pull_restart.sh. Don't
# kill an in-flight /vm runner.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>>"${ERR_SINK}" | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to restart ${UNIT} mid-runner."
    record_audit "restart-bot-service" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

pre_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
log "Pre-restart state of ${UNIT}: ${pre_state}"
echo "===== pre-restart status ====="
"${SYSTEMCTL[@]}" status "${UNIT}" --no-pager -n 5 || true

# Reconcile systemd units BEFORE the restart so the bounced trader picks up the
# latest deploy/*.service (e.g. resource directives) — closing the 2026-06-10
# deploy-guard gap. The normal deploy (deploy_pull_restart.sh) installs units
# only when HEAD MOVES during its run; when git-sync has already advanced HEAD,
# a subsequent pull-and-deploy sees "no new commits" and SKIPS the install +
# restart, so a merged unit-file change can sit on disk un-applied (the cgroup
# CPUWeight/IOWeight/Nice directives from #3232 hit exactly this). Re-running
# the idempotent installer here means a manual restart-bot-service always
# reconciles the installed units with the repo, then restarts. No-op when the
# units already match.
INSTALL_UNITS="${SCRIPT_DIR}/../install_systemd_units.sh"
if [ -f "${INSTALL_UNITS}" ]; then
    log "Reconciling systemd units (install_systemd_units.sh) before restart…"
    if bash "${INSTALL_UNITS}"; then
        log "Units reconciled (installed + daemon-reloaded if changed)."
    else
        log "WARNING: install_systemd_units.sh exited nonzero — restarting on the currently-installed units."
    fi
fi

log "Restarting ${UNIT}…"
"${SYSTEMCTL[@]}" restart "${UNIT}"

# The strip has landed mid-run right around this point on both observed
# incidents (possibly triggered by the daemon-reload/enable churn above), so
# re-heal /dev/null before the load-bearing post-state poll. Best-effort — the
# ERR_SINK reads below survive even if the heal itself fails.
heal_devnull || true

# Verify post-state. Allow up to 30 s for systemd to settle.
deadline=$(( $(date +%s) + 30 ))
post_state="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    post_state="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>>"${ERR_SINK}" || echo "unknown")"
    if [ "${post_state}" = "active" ]; then
        break
    fi
    sleep 2
done
log "Post-restart state of ${UNIT}: ${post_state}"

echo
echo "===== post-restart journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>>"${ERR_SINK}" || true

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
