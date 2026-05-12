#!/usr/bin/env bash
# =============================================================================
# Auto-install / refresh systemd units from deploy/ (S-018).
#
# The autonomous-deploy contract says: an operator pushes a commit
# from anywhere with `git push`, the VM picks it up via
# ict-git-sync.timer, services come up running the new code. New
# systemd units (e.g. deploy/ict-smoke-once.service shipped in S-017)
# used to require a manual `sudo cp ... && sudo systemctl daemon-reload`
# on the VM — defeating the promise. This script closes that gap.
#
# Behaviour:
#   - For every `deploy/*.service` and `deploy/*.timer` that's NOT a
#     systemd template (i.e. doesn't contain `@`), compare against
#     `/etc/systemd/system/<name>`. If different (or missing), copy
#     the new version in.
#   - Installs unit drop-ins from deploy/dropins/ for units that need
#     them (see drop-in section below for the explicit mapping).
#   - `daemon-reload` ONCE at the end if any change happened.
#   - DOES NOT enable / start / restart anything. The regular
#     `deploy_pull_restart.sh` flow handles restarts of the long-
#     running units.
#   - Idempotent — second run with no changes is a no-op.
#
# Wiring: called from scripts/deploy_pull_restart.sh after a HEAD-
# advancing pull, before the service-restart step.
#
# Required: passwordless sudo for `cp`, `systemctl daemon-reload` and
# `chmod` (already granted in the existing deploy environment).
# =============================================================================

set -uo pipefail

REPO_DIR=${REPO_DIR:-/home/ubuntu/ict-trading-bot}
SYSTEMD_DIR=${SYSTEMD_DIR:-/etc/systemd/system}

if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
else
    SUDO=(sudo)
fi

cd "$REPO_DIR"

changed=0

shopt -s nullglob
for unit_path in deploy/*.service deploy/*.timer; do
    unit_name=$(basename "$unit_path")
    # Skip systemd template units (e.g. claude-vm-runner@.service) —
    # those are installed once by the bootstrap script and don't get
    # mass-refreshed.
    if [[ "$unit_name" == *@* ]]; then
        continue
    fi

    target="$SYSTEMD_DIR/$unit_name"

    if [ ! -e "$target" ] || ! cmp -s "$unit_path" "$target"; then
        echo ">>> install_systemd_units: $unit_name → $target"
        "${SUDO[@]}" cp "$unit_path" "$target"
        "${SUDO[@]}" chmod 0644 "$target"
        changed=1
    fi
done
shopt -u nullglob

# ---------------------------------------------------------------------------
# Install unit drop-ins from deploy/dropins/.
#
# Listed explicitly (one variable per drop-in) so installs are transparent
# and auditable. Each drop-in is idempotently compared before copying.
#
# Why the watchdog needs its own drop-in:
#   check_heartbeat.py resolves DEFAULT_HEARTBEAT at module load time using
#   DATA_DIR. Without a drop-in, the watchdog inherits only .env (which has
#   no DATA_DIR after the fix-data-dir strip), falls back to
#   <repo>/runtime_logs/heartbeat.txt, and perpetually reads a stale
#   heartbeat even when the trader is healthy (2026-05-12 incident).
#   No service restart needed after installing the drop-in — the watchdog
#   is a oneshot fired by its timer; the next tick picks up the new env.
# ---------------------------------------------------------------------------
_WATCHDOG_DROPIN_SRC="${REPO_DIR}/deploy/dropins/watchdog-data-dir.conf"
_WATCHDOG_DROPIN_DST="${SYSTEMD_DIR}/ict-liveness-watchdog.service.d/data-dir.conf"
if [ -f "${_WATCHDOG_DROPIN_SRC}" ]; then
    if [ ! -e "${_WATCHDOG_DROPIN_DST}" ] || ! cmp -s "${_WATCHDOG_DROPIN_SRC}" "${_WATCHDOG_DROPIN_DST}"; then
        echo ">>> install_systemd_units: dropin watchdog-data-dir.conf → ${_WATCHDOG_DROPIN_DST}"
        "${SUDO[@]}" mkdir -p "$(dirname "${_WATCHDOG_DROPIN_DST}")"
        "${SUDO[@]}" cp "${_WATCHDOG_DROPIN_SRC}" "${_WATCHDOG_DROPIN_DST}"
        "${SUDO[@]}" chmod 0644 "${_WATCHDOG_DROPIN_DST}"
        changed=1
    fi
fi

if [ "$changed" -eq 1 ]; then
    echo ">>> install_systemd_units: daemon-reload"
    if ! "${SUDO[@]}" systemctl daemon-reload 2>&1; then
        # Test environments / containers without PID1 systemd can't
        # daemon-reload. Log loudly but don't fail the deploy — the
        # files are in place and the next real systemd start picks
        # them up automatically.
        echo ">>> install_systemd_units: WARN daemon-reload failed (no systemd in this env?)"
    fi
else
    echo ">>> install_systemd_units: nothing to refresh."
fi

# Auto-enable + start any timers shipped under deploy/. Service units
# are left alone — they're either oneshots fired by their timer, or
# long-running units managed by deploy_pull_restart.sh's restart step.
# Idempotent: enable --now on an already-enabled-and-active timer is
# a no-op.
#
# Why this exists: ict-liveness-watchdog.timer (2026-05-11 silent-
# failure incident) needs to start the moment the file lands on the VM.
# Before this step the operator had to SSH and run `systemctl enable
# --now ict-liveness-watchdog.timer` by hand, defeating the autonomous-
# deploy contract this script was added for.
shopt -s nullglob
for timer_path in deploy/*.timer; do
    timer_name=$(basename "$timer_path")
    if [[ "$timer_name" == *@* ]]; then
        continue
    fi
    if "${SUDO[@]}" systemctl is-enabled "$timer_name" >/dev/null 2>&1 \
        && "${SUDO[@]}" systemctl is-active "$timer_name" >/dev/null 2>&1; then
        continue
    fi
    echo ">>> install_systemd_units: enable --now $timer_name"
    if ! "${SUDO[@]}" systemctl enable --now "$timer_name" 2>&1; then
        echo ">>> install_systemd_units: WARN could not enable $timer_name (no systemd? not yet installed?)"
    fi
done
shopt -u nullglob

exit 0
