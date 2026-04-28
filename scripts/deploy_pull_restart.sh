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
# Only run pip install when HEAD actually moved. Restart services
# unconditionally — the running Python processes hold the in-memory copy of
# the previous revision, and a no-op restart is cheap (~5 s).
# ---------------------------------------------------------------------------
if [ "${PRE_SYNC_HEAD}" != "${POST_SYNC_HEAD}" ]; then
    echo ">>> Code changed (${PRE_SYNC_HEAD:0:7} -> ${POST_SYNC_HEAD:0:7}). Installing/updating dependencies..."
    /usr/bin/python3 -m pip install -r requirements.txt --quiet
else
    echo ">>> No new commits. Skipping dependency install."
fi

echo ">>> Restarting services..."
"${SYSTEMCTL[@]}" restart ict-trader-live.service
"${SYSTEMCTL[@]}" restart ict-telegram-bot.service

echo ">>> Service status:"
"${SYSTEMCTL[@]}" status ict-trader-live.service --no-pager
"${SYSTEMCTL[@]}" status ict-telegram-bot.service --no-pager

echo "===== DEPLOY COMPLETE: $(date) ====="
