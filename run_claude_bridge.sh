#!/usr/bin/env bash
set -e
cd /home/ubuntu/ict-trading-bot
export PYTHONPATH=/home/ubuntu/ict-trading-bot
python3 -u -B -m src.bot.claude_bridge
