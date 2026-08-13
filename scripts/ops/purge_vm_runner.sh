#!/usr/bin/env bash
# Tier-2 operator action: remove the dead claude-vm-runner subsystem from the
# live VM — INCLUDING its passwordless-root sudoers grant — while PRESERVING
# the `ufw` grant that shares the same file.
#
# ── WHY ──────────────────────────────────────────────────────────────────────
#
# `/etc/sudoers.d/claude-vm-runner` grants:
#
#     ubuntu ALL=(root) NOPASSWD: /usr/local/bin/claude-vm-dispatch
#
# The wrapper's ONLY caller — the Telegram `/vm` + `/vm_write` surface — was
# deleted in PR #1933 on 2026-05-25. The 2026-08-13 full-system audit found the
# grant still installed on the live money VM three months later, with zero call
# sites in `src/` (`BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT`). This is
# the zombie class at its most expensive: an installer, a unit and eight
# defensive "is a runner active?" checks all still present, so a consistency
# pass reads it as alive. Nothing exercises it, so nothing would notice if the
# wrapper were replaced.
#
# ── THE ORDERING IS THE SAFETY PROPERTY, NOT A STYLE CHOICE ──────────────────
#
# The obvious remediation (`rm /etc/sudoers.d/claude-vm-runner`) would ALSO drop
# the `ufw` grant that lives in the same file, and `ufw` is live: the
# system-actions / vm-net-fix workflows use it to reopen dashboard ingress after
# the VM-local firewall blocks TCP/8001 across a reboot (#537 / #542 / #545).
# Losing it breaks that auto-recovery SILENTLY — the failure only surfaces at the
# next outage, which is the worst possible time to discover it.
#
# So this script INSTALLS THE REPLACEMENT FIRST and verifies it, and only then
# removes anything. If the replacement cannot be installed or fails `visudo -c`,
# it aborts having changed NOTHING. A partially-applied sudoers change on the
# money VM is not an acceptable intermediate state.
#
# ── IDEMPOTENT ───────────────────────────────────────────────────────────────
#
# Every step is a no-op when already applied, so this is safe to run blind and
# safe to re-run. A VM that was never bootstrapped with the runner exits 0 with
# a "nothing to purge" report.
#
# ── WHAT THIS DOES NOT TOUCH ─────────────────────────────────────────────────
#
#   - ict-trader-live.service / ict-web-api.service (the live stack)
#   - /etc/sudoers.d/ict-system-actions (the tiered-action grant, separate file)
#   - Strategy params, accounts, risk caps, or any order-path file
#
# `set -e` is required by the operator-action wrapper conformance guard
# (tests/ops/test_system_actions_workflow.py). The removal steps below are
# each wrapped in an `if` so a failure is RECORDED and still reaches the
# post-state assertion at the end, rather than aborting the script silently
# before it can report what state the VM was actually left in.
set -euo pipefail

SCRIPT_NAME="purge_vm_runner"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ops/_lib.sh
source "${SCRIPT_DIR}/_lib.sh"

REPO_DIR="${REPO_DIR:-/home/ubuntu/ict-trading-bot}"
OLD_SUDOERS="/etc/sudoers.d/claude-vm-runner"
NEW_SUDOERS="/etc/sudoers.d/ict-ufw"
NEW_SRC="${REPO_DIR}/deploy/ict-ufw.sudoers"
DISPATCH="/usr/local/bin/claude-vm-dispatch"
RUNNER_UNIT="/etc/systemd/system/claude-vm-runner@.service"


# ── Existence checks under /etc/sudoers.d MUST run as root ───────────────────
#
# 2026-08-13, found by this script's OWN post-state block on its first live run
# (issue #8993). The script runs as `ubuntu`; /etc/sudoers.d is root-only, so a
# plain `[ -f /etc/sudoers.d/x ]` returns FALSE for every file in there whether
# or not it exists. The run reported, in the same breath:
#
#     [ok] installed /etc/sudoers.d/ict-ufw     <- sudo install succeeded
#     [FAIL] MISSING: /etc/sudoers.d/ict-ufw    <- unprivileged [ -f ] said no
#     [ok] ufw grant resolves                   <- ...yet the grant works
#
# `sudo -n -l` resolving is PROOF that some file in that directory grants ufw,
# so the existence check was provably wrong rather than ambiguously wrong.
#
# The dangerous half is not the false FAIL, it is the false PASS: the removal
# step read "already absent" for the dead grant and therefore never tried to
# remove it. A check that cannot distinguish "absent" from "I am not allowed to
# look" reports the second as the first -- the collapsed-state class, in the
# assertion that exists to prove this action worked.
#
# `sudo test -f` asks the question with the privilege needed to answer it. It
# is a THREE-state answer, and the caller must branch on all three:
#   0 -> present · 1 -> genuinely absent · 2 -> could not determine
priv_exists() {
    local path="$1"
    if ! sudo -n test -e "${path}" 2>/dev/null; then
        # Distinguish "sudo worked and the file is not there" from "sudo itself
        # could not run" -- otherwise a broken sudo reads as a clean absence.
        if sudo -n true 2>/dev/null; then return 1; fi
        return 2
    fi
    return 0
}

changed=0

log "==> purge-vm-runner (BL-20260813-VM-RUNNER-ZOMBIE-SUDOERS-ROOT-GRANT)"
log "    old sudoers : ${OLD_SUDOERS}"
log "    replacement : ${NEW_SUDOERS} (ufw only)"

# ── Step 0: refuse to run if a runner instance is somehow ACTIVE ─────────────
# It should be impossible (no caller exists), but "should be impossible" is what
# this whole item is about. If one IS active, something we do not understand is
# starting it — that is a finding, not a thing to purge underneath.
if systemctl list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    log "ABORT: a claude-vm-runner@*.service instance is ACTIVE."
    log "       Nothing in the repo can start one, so this contradicts the premise"
    log "       of the purge. Investigate before removing anything."
    exit 3
fi

# ── Step 1: INSTALL THE REPLACEMENT FIRST (the ufw grant must never lapse) ───
if [ ! -f "${NEW_SRC}" ]; then
    log "ABORT: ${NEW_SRC} not found — cannot install the ufw replacement."
    log "       Refusing to remove the old grant without its successor in place."
    exit 1
fi
TMP="$(mktemp)"
cp "${NEW_SRC}" "${TMP}"
if ! sudo visudo -cf "${TMP}" >/dev/null; then
    log "ABORT: replacement sudoers failed 'visudo -c'. NOTHING changed."
    rm -f "${TMP}"; exit 1
fi
if sudo install -m 0440 -o root -g root "${TMP}" "${NEW_SUDOERS}"; then
    log "  [ok] installed ${NEW_SUDOERS} (ufw grant preserved)"
    changed=1
else
    log "ABORT: could not install ${NEW_SUDOERS}. NOTHING removed."
    rm -f "${TMP}"; exit 1
fi
rm -f "${TMP}"

# ── Step 2: PROVE the ufw grant still resolves BEFORE removing the old file ──
# `sudo -n -l` asks "may I, without a password?" without running ufw. If this
# cannot confirm the grant, we keep the old file — a live recovery path is worth
# more than a tidy sudoers.d.
if ! sudo -n -l /usr/sbin/ufw >/dev/null 2>&1; then
    log "ABORT: ufw grant does not resolve after installing ${NEW_SUDOERS}."
    log "       LEAVING ${OLD_SUDOERS} IN PLACE — the recovery path stays intact."
    log "       (The dead dispatch grant is still present; re-run once fixed.)"
    exit 1
fi
log "  [ok] verified: ufw still resolves passwordless via the new file"

# ── Step 3: now, and only now, remove the dead grant + its artifacts ─────────
priv_exists "${OLD_SUDOERS}"; _old_state=$?
if [ "${_old_state}" -eq 0 ]; then
    if sudo rm -f "${OLD_SUDOERS}"; then
        log "  [ok] removed ${OLD_SUDOERS} (dispatch root grant GONE)"; changed=1
    else
        log "  [WARN] could NOT remove ${OLD_SUDOERS} — the post-state check will FAIL"
    fi
elif [ "${_old_state}" -eq 1 ]; then
    log "  [--] ${OLD_SUDOERS} genuinely absent (verified as root)"
else
    log "ABORT: cannot determine whether ${OLD_SUDOERS} exists (sudo unavailable)."
    log "       Refusing to report a purge that was never verified."
    exit 1
fi

if [ -e "${DISPATCH}" ]; then
    if sudo rm -f "${DISPATCH}"; then
        log "  [ok] removed ${DISPATCH}"; changed=1
    else
        log "  [WARN] could NOT remove ${DISPATCH} — the post-state check will FAIL"
    fi
else
    log "  [--] ${DISPATCH} already absent"
fi

if [ -e "${RUNNER_UNIT}" ]; then
    sudo systemctl disable --now 'claude-vm-runner@*.service' >/dev/null 2>&1 || true
    if sudo rm -f "${RUNNER_UNIT}"; then
        log "  [ok] removed ${RUNNER_UNIT}"; changed=1
    else
        log "  [WARN] could NOT remove ${RUNNER_UNIT} — the post-state check will FAIL"
    fi
    sudo systemctl daemon-reload || true
    sudo systemctl reset-failed 2>/dev/null || true
else
    log "  [--] ${RUNNER_UNIT} already absent"
fi

# ── Step 4: post-state, asserted rather than assumed ─────────────────────────
log "==> POST-STATE"
fail=0
for p in "${OLD_SUDOERS}" "${DISPATCH}" "${RUNNER_UNIT}"; do
    priv_exists "${p}"; _st=$?
    case "${_st}" in
        0) log "  [FAIL] still present: ${p}"; fail=1 ;;
        1) log "  [ok] absent (verified as root): ${p}" ;;
        *) log "  [FAIL] UNDETERMINED: ${p} — could not check as root; this is NOT an absence"; fail=1 ;;
    esac
done
priv_exists "${NEW_SUDOERS}"; _new_st=$?
case "${_new_st}" in
    0) log "  [ok] present (verified as root): ${NEW_SUDOERS}" ;;
    1) log "  [FAIL] MISSING: ${NEW_SUDOERS}"; fail=1 ;;
    *) log "  [FAIL] UNDETERMINED: ${NEW_SUDOERS} — could not check as root"; fail=1 ;;
esac
if sudo -n -l /usr/sbin/ufw >/dev/null 2>&1; then
    log "  [ok] ufw grant resolves — the #537/#542/#545 recovery path is intact"
else
    log "  [FAIL] ufw grant does NOT resolve — recovery path is BROKEN, fix now"; fail=1
fi

if [ "${fail}" -ne 0 ]; then log "==> purge-vm-runner: FAILED post-state check"; exit 1; fi
if [ "${changed}" -eq 0 ]; then log "==> nothing to purge — already clean"; fi
log "==> purge-vm-runner: OK"
exit 0
