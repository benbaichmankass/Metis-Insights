#!/bin/bash
# M15 WS-C — fetch Bybit alt-perp candles for the BTC-roster generalization
# sweep (ETH/SOL/BNB/XRP/ADA/LINK/AVAX USDT linear perpetuals).
#
# Runs detached on the trainer VM. Public V5 kline endpoint, no auth.
# 15m feeds the 1h/2h/4h/15m resampled families; 5m feeds ict_scalp.
# A --start-date before a symbol's listing is safe: Bybit returns bars
# from listing onward (fetch_backtest_candles pages forward).
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true
mkdir -p data

for SYM in ETHUSDT SOLUSDT BNBUSDT XRPUSDT ADAUSDT LINKUSDT AVAXUSDT; do
  echo "=== fetch ${SYM} 15m ==="
  python3 scripts/ops/fetch_backtest_candles.py --symbol "$SYM" --interval 15 \
    --start-date 2020-01-01 --output "data/${SYM}_15m.csv" || echo "FETCH_FAILED ${SYM} 15m"
  echo "=== fetch ${SYM} 5m ==="
  python3 scripts/ops/fetch_backtest_candles.py --symbol "$SYM" --interval 5 \
    --start-date 2020-01-01 --output "data/${SYM}_5m.csv" || echo "FETCH_FAILED ${SYM} 5m"
done

echo "WS_C_FETCH_DONE"
ls -la data/*USDT_*.csv
