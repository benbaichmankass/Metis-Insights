#!/usr/bin/env bash
set -e
cd /home/ubuntu/ict-trading-bot
export PYTHONPATH=/home/ubuntu/ict-trading-bot
python3 -u -B ./src/bot/telegram_query_bot.py
