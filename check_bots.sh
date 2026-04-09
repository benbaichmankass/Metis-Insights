#!/usr/bin/env bash
cd ~/ict-trading-bot
echo "=== TMUX ==="
tmux ls 2>/dev/null || echo "No tmux sessions"
echo "=== TRADER ==="
ps aux | grep -i automated_trading_loop.py | grep -v grep || echo "No trader process"
echo "=== TELEGRAM ==="
ps aux | grep -i telegram_query_bot.py | grep -v grep || echo "No telegram process"
echo "=== GIT ==="
git status --short
echo "=== RECENT LOGS ==="
find . -name "*.log" -mmin -10 2>/dev/null | head -5
