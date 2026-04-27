#!/usr/bin/env bash
# ============================================
# DEPLOY PULL RESTART SCRIPT
# Run on Oracle VM to pull latest code and restart services
# Usage: bash scripts/deploy_pull_restart.sh
# ============================================

set -e

REPO_DIR="/home/ubuntu/ict-trading-bot"
VENV="$REPO_DIR/.venv"

echo "===== DEPLOY STARTED: $(date) ====="

cd $REPO_DIR

echo ">>> Pulling latest from GitHub..."
git pull origin main

echo ">>> Installing/updating dependencies..."
source $VENV/bin/activate
pip install -r requirements.txt --quiet

echo ">>> Restarting services..."
sudo systemctl restart ict-trader-live.service
sudo systemctl restart ict-telegram-bot.service

echo ">>> Service status:"
sudo systemctl status ict-trader-live.service --no-pager
sudo systemctl status ict-telegram-bot.service --no-pager

echo "===== DEPLOY COMPLETE: $(date) ====="
