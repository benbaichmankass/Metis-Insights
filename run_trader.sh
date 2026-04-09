#!/usr/bin/env bash
set -e
cd /home/ubuntu/ict-trading-bot
export PYTHONPATH=/home/ubuntu/ict-trading-bot
python3 -u -B ./src/core/automated_trading_loop.py
