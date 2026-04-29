#!/usr/bin/env bash
# Operator status snapshot — S-012 PR C6 update.
#
# Reflects the post-S-012 single-process architecture:
#   * Trader runs via systemd unit ict-trader-live.service →
#     `python3 -u -B -m src.main`. Process name to grep: src.main.
#   * Telegram bot runs via systemd unit ict-telegram-bot.service →
#     `python3 -u -B -m src.bot.telegram_query_bot`. Process name to
#     grep: src.bot.telegram_query_bot.
#
# This script does not start anything; it only reports state.

set -u
cd ~/ict-trading-bot 2>/dev/null || cd /home/ubuntu/ict-trading-bot

echo "=== TMUX ==="
tmux ls 2>/dev/null || echo "No tmux sessions"

echo "=== TRADER (src.main) ==="
ps -ef | grep "[s]rc\.main" | grep -v -E "telegram|test" || echo "No trader process"

echo "=== TELEGRAM BOT (src.bot.telegram_query_bot) ==="
ps -ef | grep "[s]rc\.bot\.telegram_query_bot" || echo "No telegram process"

echo "=== SYSTEMD UNITS ==="
for unit in ict-trader-live.service ict-telegram-bot.service \
            ict-heartbeat.service ict-git-sync.timer; do
    state=$(systemctl is-active "$unit" 2>/dev/null || echo "unknown")
    echo "  $unit: $state"
done

echo "=== GIT ==="
git status --short

echo "=== RECENT LOGS (last 10 min) ==="
find . -name "*.log" -mmin -10 2>/dev/null | head -5

echo "=== TRADER LIVE OUTPUT (last 10 lines from journalctl) ==="
journalctl -u ict-trader-live -n 10 --no-pager 2>/dev/null \
    || tmux capture-pane -pt trader 2>/dev/null | tail -n 10 \
    || echo "(journal + tmux trader pane unavailable)"
