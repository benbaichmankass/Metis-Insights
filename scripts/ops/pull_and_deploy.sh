#!/usr/bin/env bash
# Tier-2 operator action: sync main onto the VM and restart services.
#
# Thin wrapper around `scripts/deploy_pull_restart.sh` (the canonical,
# already-deployed deploy script that the `ict-git-sync` timer also
# invokes). Adds the system-actions audit conventions:
#
#   - capture pre-sync HEAD + post-sync HEAD
#   - record an audit JSON via record_audit() so the diag relay can
#     surface the run from runtime_logs/operator_actions/
#   - defer if any claude-vm-runner@*.service is currently active
#     (deploy_pull_restart.sh has its own guard for the systemd
#     restart, but we want the wrapper to short-circuit BEFORE it
#     attempts the git fetch — same defensive shape as restart_bot.sh)
#
# Pre/post checks:
#   - capture is-active state of ict-trader-live.service before
#   - run scripts/deploy_pull_restart.sh (which fetches origin/main,
#     hard-resets the worktree, optionally reinstalls deps, and bounces
#     the live trader + Telegram bot units)
#   - poll up to 60 s for `is-active` to return "active" (deploy can
#     take a beat longer than a bare restart because of dep install)
#   - dump the last 30 journal lines so the operator can spot crashes
#
# This script never edits accounts.yaml, strategy params, or risk caps;
# it only deploys code that has ALREADY been merged onto main through
# the standard PR + Tier gating. The Tier-3 merge gates upstream remain
# the canonical authorization for any behaviour change shipped here.

set -euo pipefail

SCRIPT_NAME="pull_and_deploy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

require_systemctl

UNIT="ict-trader-live.service"
DEPLOY_SCRIPT="${REPO_DIR}/scripts/deploy_pull_restart.sh"

if [ ! -f "${DEPLOY_SCRIPT}" ]; then
    log "ERROR: ${DEPLOY_SCRIPT} not found on this VM. Has the repo been cloned?"
    record_audit "pull-and-deploy" "error" '{"reason": "deploy script missing"}' >/dev/null || true
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    log "ERROR: passwordless sudo for systemctl is required (see deploy_pull_restart.sh)."
    record_audit "pull-and-deploy" "error" '{"reason": "sudo unavailable"}' >/dev/null || true
    exit 1
fi

# Defense in depth — same guard as restart_bot.sh. deploy_pull_restart.sh
# also checks at restart time, but we want the wrapper to refuse the
# whole sync rather than fetch + reset and then defer the restart, which
# would leave the worktree on new code while the systemd unit still ran
# the old code.
if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service unit is active. Refusing to deploy mid-runner."
    record_audit "pull-and-deploy" "deferred" '{"reason": "vm-runner active"}' >/dev/null || true
    exit 3
fi

# ---------------------------------------------------------------------------
# BL-20260821-DEPLOY-WRAPPER-ASSERTS-A-BOUNCE-IT-DID-NOT-DO
#
# The closing line used to infer the outcome from PRE_HEAD vs POST_HEAD and
# assert "${UNIT} bounced" whenever they matched. That inference cannot be
# correct, because deploy_pull_restart.sh does NOT drive its restart decision
# off this script's PRE_HEAD. It drives it off runtime_logs/deployed_sha.txt
# (its RUNTIME_BASE) and off the CONTENT of the diff, so BOTH branches lied:
#
#   PRE_HEAD == POST_HEAD  usually means NOTHING was restarted -- its
#                          "Running processes already deployed; nothing to
#                          deploy" early exit -- yet we printed "bounced".
#   PRE_HEAD != POST_HEAD  can ALSO mean nothing was restarted, when every
#                          changed path is docs/tests/.claude/.github
#                          (BL-20260529-002). "active" was then true only
#                          because the unit had never stopped.
#
# Four real outcomes -- restarted / skipped-already-deployed / skipped-non-
# runtime / deferred-vm-runner -- collapsed into two sentences derived from a
# signal that determines none of them. Every "deployed and verified" claim
# that quoted this line rested on it.
#
# So MEASURE the asserted fact instead of inferring it: a unit that was bounced
# has a NEW start timestamp and a NEW MainPID. Three states, never collapsed --
# bounced / not_bounced / unknown -- because an unreadable systemctl must not
# read as "did not restart". Two read-only `systemctl show` calls; this changes
# nothing about what the deploy DOES.
# ---------------------------------------------------------------------------
unit_start_fingerprint() {
    # "<ActiveEnterTimestampMonotonic> <MainPID>" for ${UNIT}, or "" if either
    # read fails. `show -p X` (not `--value`) so this works on older systemd.
    local stamp pid
    stamp="$("${SYSTEMCTL[@]}" show -p ActiveEnterTimestampMonotonic "${UNIT}" 2>/dev/null | cut -d= -f2- || true)"
    pid="$("${SYSTEMCTL[@]}" show -p MainPID "${UNIT}" 2>/dev/null | cut -d= -f2- || true)"
    if [ -z "${stamp}" ] || [ -z "${pid}" ]; then
        return 0
    fi
    printf '%s %s' "${stamp}" "${pid}"
}

cd "${REPO_DIR}"
PRE_HEAD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
PRE_UNIT_STATE="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
PRE_UNIT_FP="$(unit_start_fingerprint)"
log "Pre-deploy HEAD: ${PRE_HEAD}"
log "Pre-deploy state of ${UNIT}: ${PRE_UNIT_STATE}"

echo "===== running deploy_pull_restart.sh ====="
# Don't capture into a variable — let stdout/stderr stream so the
# workflow's run-log shows progress in real time.
if ! bash "${DEPLOY_SCRIPT}"; then
    rc=$?
    POST_HEAD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    POST_UNIT_STATE="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    record_audit "pull-and-deploy" "failed" \
        "{\"pre_head\": \"${PRE_HEAD}\", \"post_head\": \"${POST_HEAD}\", \"pre_unit\": \"${PRE_UNIT_STATE}\", \"post_unit\": \"${POST_UNIT_STATE}\", \"deploy_exit\": ${rc}}" >/dev/null || true
    log "ERROR: deploy_pull_restart.sh exited ${rc}."
    exit "${rc}"
fi

# Re-heal /dev/null before the post-deploy reads: the OCI host-agent strip can
# land DURING the deploy_pull_restart.sh run just above (healthy at our entry,
# clobbered partway through — the 2026-08-02 pull-and-deploy log had `git
# rev-parse … 2>/dev/null` and `is-active … 2>/dev/null` here all EACCESing,
# yielding "Post-deploy HEAD: unknown"; BL-20260730-DEVNULL-DEPLOY-REDIRECT-FRAGILITY recurrence).
heal_devnull || true
POST_HEAD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

# Verify post-state. Allow up to 60 s for systemd to settle (deploy
# can take longer than a bare restart because of pip install).
deadline=$(( $(date +%s) + 60 ))
POST_UNIT_STATE="unknown"
while [ "$(date +%s)" -lt "${deadline}" ]; do
    heal_devnull || true  # re-heal — the strip can recur inside this 60s poll
    POST_UNIT_STATE="$("${SYSTEMCTL[@]}" is-active "${UNIT}" 2>/dev/null || echo "unknown")"
    if [ "${POST_UNIT_STATE}" = "active" ]; then
        break
    fi
    sleep 2
done
POST_UNIT_FP="$(unit_start_fingerprint)"
if [ -z "${PRE_UNIT_FP}" ] || [ -z "${POST_UNIT_FP}" ]; then
    # We could not look. NOT the same as "it did not restart".
    BOUNCE_STATE="unknown"
    BOUNCE_MSG="${UNIT} restart state UNKNOWN (could not read its start stamp)"
elif [ "${PRE_UNIT_FP}" != "${POST_UNIT_FP}" ]; then
    BOUNCE_STATE="bounced"
    BOUNCE_MSG="${UNIT} restarted"
else
    BOUNCE_STATE="not_bounced"
    BOUNCE_MSG="${UNIT} NOT restarted (left running)"
fi
log "Post-deploy HEAD: ${POST_HEAD}"
log "Post-deploy state of ${UNIT}: ${POST_UNIT_STATE}"
log "Post-deploy restart of ${UNIT}: ${BOUNCE_STATE}"

echo
echo "===== post-deploy journalctl (last 30 lines) ====="
journalctl -u "${UNIT}" -n 30 --no-pager 2>/dev/null || true

if [ "${POST_UNIT_STATE}" = "active" ]; then
    record_audit "pull-and-deploy" "ok" \
        "{\"pre_head\": \"${PRE_HEAD}\", \"post_head\": \"${POST_HEAD}\", \"pre_unit\": \"${PRE_UNIT_STATE}\", \"post_unit\": \"${POST_UNIT_STATE}\", \"bounce_state\": \"${BOUNCE_STATE}\"}" >/dev/null || true
    if [ "${PRE_HEAD}" = "${POST_HEAD}" ]; then
        log "Deploy finished — HEAD unchanged at ${POST_HEAD}; ${BOUNCE_MSG}."
    else
        log "Deploy finished — HEAD ${PRE_HEAD} → ${POST_HEAD}; ${BOUNCE_MSG}."
    fi
    exit 0
else
    record_audit "pull-and-deploy" "failed" \
        "{\"pre_head\": \"${PRE_HEAD}\", \"post_head\": \"${POST_HEAD}\", \"pre_unit\": \"${PRE_UNIT_STATE}\", \"post_unit\": \"${POST_UNIT_STATE}\", \"bounce_state\": \"${BOUNCE_STATE}\"}" >/dev/null || true
    log "ERROR: ${UNIT} did not return to 'active' within 60 s post-deploy."
    exit 1
fi
