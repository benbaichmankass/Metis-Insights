#!/usr/bin/env bash
# ============================================
# BACKUP LOGS SCRIPT
# Copies curated daily log summaries into the repo
# and pushes them to GitHub
# Usage: bash scripts/backup_logs.sh
# Or schedule via cron: 0 0 * * * bash /home/ubuntu/YOUR_REPO/scripts/backup_logs.sh
# ============================================

set -e

REPO_DIR="/home/ubuntu/YOUR_REPO"
RUNTIME_LOGS="$REPO_DIR/runtime_logs"
SUMMARY_LOGS="$REPO_DIR/logs"

echo "===== LOG BACKUP STARTED: $(date) ====="

cd $REPO_DIR
mkdir -p $SUMMARY_LOGS

TODAY=$(date +%Y-%m-%d)

if [ -f "$RUNTIME_LOGS/bot.log" ]; then
    cp "$RUNTIME_LOGS/bot.log" "$SUMMARY_LOGS/daily_${TODAY}.log"
    echo ">>> Copied bot.log to logs/daily_${TODAY}.log"
else
    echo ">>> No bot.log found, skipping copy"
fi

source .venv/bin/activate
git add logs/
git commit -m "chore: daily log backup ${TODAY}" || echo ">>> Nothing new to commit"
git push origin main

echo "===== LOG BACKUP COMPLETE: $(date) ====="
