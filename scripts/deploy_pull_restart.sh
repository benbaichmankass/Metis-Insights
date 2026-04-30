#!/usr/bin/env bash
# ============================================
# DEPLOY PULL RESTART SCRIPT
# Run on Oracle VM to sync to origin/main and restart services.
#
# IMPORTANT: The VM is a read-only mirror of origin/main. This script uses
# `git fetch && git reset --hard origin/main` rather than `git pull` so any
# accidental local commits or dirty working tree on the VM are wiped out
# on the next sync. Never commit on the VM — always commit through GitHub.
#
# Usage: bash scripts/deploy_pull_restart.sh
#
# Verify locally before merging:
#   bash -n scripts/deploy_pull_restart.sh
#   shellcheck scripts/deploy_pull_restart.sh
# ============================================

set -euo pipefail

REPO_DIR="/home/ubuntu/ict-trading-bot"

# ---------------------------------------------------------------------------
# Detect sudo capability once at startup and build a reusable helper array.
# Running as root: no sudo needed. Otherwise require NOPASSWD sudo for systemctl.
# ---------------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
    SYSTEMCTL=(systemctl)
elif sudo -n systemctl --version >/dev/null 2>&1; then
    SYSTEMCTL=(sudo systemctl)
else
    echo "ERROR: Cannot invoke systemctl. Grant passwordless sudo for systemctl:" >&2
    echo "       ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl" >&2
    exit 1
fi

echo "===== DEPLOY STARTED: $(date) ====="

cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Capture current HEAD before we sync. We use this to decide whether to
# re-install dependencies. Service restart always runs so a manual
# `git reset --hard` (or any other state drift) cannot leave the running
# Python processes pinned to stale code.
# ---------------------------------------------------------------------------
PRE_SYNC_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo ">>> Pre-sync HEAD: ${PRE_SYNC_HEAD}"

echo ">>> Fetching latest from origin..."
git fetch --prune origin

echo ">>> Hard-resetting to origin/main (VM is a read-only mirror)..."
git reset --hard origin/main

POST_SYNC_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo ">>> Post-sync HEAD: ${POST_SYNC_HEAD}"

# ---------------------------------------------------------------------------
# S-020: Telegram ping fanout, state-file driven.
#
# We compare against the LAST-NOTIFIED head (persisted in
# runtime_logs/notify_state.txt), NOT this run's PRE_SYNC_HEAD. Why:
# during S-019 debugging the operator manually `git reset --hard`d to
# advance HEAD outside the timer's window. PRE_SYNC_HEAD only remembers
# this run, so the next tick sees pre==post and skipped the ping —
# CP-2026-04-30-15 (#226) was permanently lost. The state file fixes
# that: as long as POST_SYNC_HEAD differs from what we last pinged for,
# we ping, regardless of how HEAD got there.
#
# Auto-ping test flag: if runtime_flags/auto_ping_test.flag exists, we
# force a notify_on_pull run with --force-checkpoint, which emits a
# checkpoint ping even if the diff doesn't naturally include
# CHECKPOINT_LOG.md. The flag file is consumed (deleted) on success.
#
# Failures here are logged but do NOT abort the deploy: a broken ping
# channel must not break the deploy channel. The state file is updated
# only on success, so the next tick retries.
# ---------------------------------------------------------------------------
NOTIFY_STATE_DIR="${REPO_DIR}/runtime_logs"
NOTIFY_STATE_FILE="${NOTIFY_STATE_DIR}/notify_state.txt"
AUTO_PING_TEST_FLAG="${REPO_DIR}/runtime_flags/auto_ping_test.flag"
mkdir -p "${NOTIFY_STATE_DIR}"
LAST_NOTIFIED_HEAD=$(cat "${NOTIFY_STATE_FILE}" 2>/dev/null || true)
# Bootstrap: on first run after this fix lands the state file is absent.
# notify_on_pull.py treats "unknown" as a hard short-circuit (no diff,
# no blocker scan), so we'd silently miss the very first checkpoint
# ping. Default to HEAD~1 so the merge commit's diff (which includes
# CHECKPOINT_LOG.md when this PR lands) actually fires a ping.
if [ -z "${LAST_NOTIFIED_HEAD}" ]; then
    LAST_NOTIFIED_HEAD=$(git rev-parse HEAD~1 2>/dev/null || echo "unknown")
    echo ">>> No notify_state.txt — bootstrapping with HEAD~1=${LAST_NOTIFIED_HEAD:0:7}"
fi

NOTIFY_ARGS=(--pre "${LAST_NOTIFIED_HEAD}" --post "${POST_SYNC_HEAD}")
if [ -f "${AUTO_PING_TEST_FLAG}" ]; then
    echo ">>> auto_ping_test.flag detected — adding --force-checkpoint"
    NOTIFY_ARGS+=(--force-checkpoint)
fi

if [ "${LAST_NOTIFIED_HEAD}" != "${POST_SYNC_HEAD}" ] || [ -f "${AUTO_PING_TEST_FLAG}" ]; then
    echo ">>> Sending Telegram pings (last_notified=${LAST_NOTIFIED_HEAD:0:7} -> head=${POST_SYNC_HEAD:0:7})..."
    if /usr/bin/python3 scripts/notify_on_pull.py "${NOTIFY_ARGS[@]}"; then
        echo ">>> Pings dispatched."
        echo "${POST_SYNC_HEAD}" > "${NOTIFY_STATE_FILE}"
        if [ -f "${AUTO_PING_TEST_FLAG}" ]; then
            rm -f "${AUTO_PING_TEST_FLAG}"
            echo ">>> Consumed auto_ping_test.flag."
        fi
    else
        echo ">>> WARNING: notify_on_pull exited nonzero — leaving state file untouched so next tick retries."
    fi
else
    echo ">>> notify state already at HEAD (${POST_SYNC_HEAD:0:7}); no pings to send."
fi

# ---------------------------------------------------------------------------
# Restart only when HEAD actually moved during THIS run.
#
# Originally we restarted unconditionally on every 5-minute git-sync tick,
# reasoning that a no-op restart is cheap. That broke the S-014.5
# Telegram-dispatched VM runner: a /vm invocation that lands within ~30 s
# of the next git-sync tick gets killed by the bot restart (the wrapper
# subprocess is in the bot's cgroup and dies with it).
#
# We now restart ONLY when the new HEAD differs from the pre-sync HEAD.
# Trade-off: if an operator does a manual `git reset --hard` to a different
# revision and the timer happens not to advance HEAD on its next tick,
# the running Python processes will hold the previous in-memory copy
# until the next deploy. That is a rare path and is handled by a manual
# `sudo systemctl restart ict-trader-live ict-telegram-bot`.
#
# Defense in depth: even when HEAD advances, skip the restart if any
# claude-vm-runner@*.service unit is currently active — the next
# git-sync tick (5 min) will pick up the change with no /vm in flight.
# ---------------------------------------------------------------------------
if [ "${PRE_SYNC_HEAD}" = "${POST_SYNC_HEAD}" ]; then
    echo ">>> No new commits in this pull. Skipping dependency install and service restart."
    echo "===== DEPLOY COMPLETE: $(date) ====="
    exit 0
fi

echo ">>> Code changed (${PRE_SYNC_HEAD:0:7} -> ${POST_SYNC_HEAD:0:7}). Installing/updating dependencies..."
/usr/bin/python3 -m pip install -r requirements.txt --quiet

# ---------------------------------------------------------------------------
# S-018 fix: auto-refresh systemd units from deploy/.
#
# Closes the gap that caused operator frustration: new .service / .timer
# files used to require manual `sudo cp ... && systemctl daemon-reload`.
# The installer is idempotent (compares each unit against /etc/systemd
# /system; only copies + reloads on diff) and never restarts anything —
# the existing flow below handles restarts for long-running units.
# ---------------------------------------------------------------------------
echo ">>> Refreshing systemd units from deploy/..."
if bash "${REPO_DIR}/scripts/install_systemd_units.sh"; then
    echo ">>> Systemd units in sync."
else
    echo ">>> WARNING: install_systemd_units.sh exited nonzero — see journal."
fi

if "${SYSTEMCTL[@]}" list-units 'claude-vm-runner@*.service' --state=active --no-legend 2>/dev/null | grep -q .; then
    echo ">>> A claude-vm-runner unit is active — deferring service restart to the next sync tick to avoid killing an in-flight /vm invocation."
    echo "===== DEPLOY COMPLETE (restart deferred): $(date) ====="
    exit 0
fi

echo ">>> Restarting services..."
"${SYSTEMCTL[@]}" restart ict-trader-live.service
"${SYSTEMCTL[@]}" restart ict-telegram-bot.service

# ---------------------------------------------------------------------------
# S-017 T7: one-shot smoke trigger. If a sandbox/operator session committed
# `runtime_flags/run_smoke_once.flag`, fire the smoke now via the
# ict-smoke-once.service oneshot unit. The unit's wrapper deletes the
# flag after running so a no-op re-pull does not refire.
#
# Per CLAUDE.md "Autonomous live-trading rule": this fires without
# per-trade operator confirmation. Safety is enforced by the qty cap
# in scripts/smoke_test_trade.py + ALLOW_LIVE_TRADING in the env.
# ---------------------------------------------------------------------------
if [ -f "${REPO_DIR}/runtime_flags/run_smoke_once.flag" ]; then
    if [ -f /etc/systemd/system/ict-smoke-once.service ]; then
        echo ">>> Smoke trigger flag detected — starting ict-smoke-once.service"
        "${SYSTEMCTL[@]}" start ict-smoke-once.service || true
    else
        echo ">>> Smoke trigger flag detected but ict-smoke-once.service is not installed."
        echo ">>> Operator: copy deploy/ict-smoke-once.service to /etc/systemd/system/ and 'systemctl daemon-reload'."
    fi
fi

echo ">>> Service status:"
"${SYSTEMCTL[@]}" status ict-trader-live.service --no-pager
"${SYSTEMCTL[@]}" status ict-telegram-bot.service --no-pager

echo "===== DEPLOY COMPLETE: $(date) ====="
