#!/usr/bin/env bash
# ============================================
# DEPLOY PULL RESTART SCRIPT
# Run on Oracle VM to pull latest code and restart services
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

echo ">>> Pulling latest from GitHub..."
PULL_OUTPUT=$(git pull origin main 2>&1)
echo "$PULL_OUTPUT"

if echo "$PULL_OUTPUT" | grep -q "Already up to date"; then
    echo ">>> No new commits. Skipping deploy."
    exit 0
fi

echo ">>> Installing/updating dependencies..."
/usr/bin/python3 -m pip install -r requirements.txt --quiet

echo ">>> Restarting services..."
"${SYSTEMCTL[@]}" restart ict-trader-live.service
"${SYSTEMCTL[@]}" restart ict-telegram-bot.service

echo ">>> Service status:"
"${SYSTEMCTL[@]}" status ict-trader-live.service --no-pager
"${SYSTEMCTL[@]}" status ict-telegram-bot.service --no-pager

echo "===== DEPLOY COMPLETE: $(date) ====="
