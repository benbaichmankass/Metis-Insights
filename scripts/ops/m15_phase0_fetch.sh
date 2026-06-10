#!/bin/bash
# M15 Phase 0 — fetch the candidate-market datasets from Dukascopy.
#
# Runs detached on the trainer VM from the m15-phase0 worktree
# (docs/research/market-alternatives-2026-06-10.md §6, Phase 0).
# Output CSVs land in ./data/ in the shape scripts/backtest_*.py expect.
#
# Universe:
#   FX 15m (2019->now)      — EURUSD, GBPUSD, XAUUSD (harnesses resample
#                             to 1h/2h/4h via --timeframe/--resample)
#   ETF CFD 5m RTH (2019->) — QQQ, SPY (the intraday equities leg)
#   Daily (2010->now)       — QQQ, SPY, GLD, copper (the daily baseline)
set -u
cd "$(dirname "$0")/../.."
source /home/ubuntu/ict-trading-bot/.venv/bin/activate 2>/dev/null \
  || source /home/ubuntu/ict-trading-bot/venv/bin/activate 2>/dev/null || true
mkdir -p data

fetch() {
  echo "=== fetch $* ==="
  python3 scripts/ops/fetch_dukascopy_ohlcv.py "$@" || echo "FETCH_FAILED $*"
}

fetch --instrument INSTRUMENT_FX_MAJORS_EUR_USD --interval 15m --start 2019-01-01 --output data/EURUSD_15m.csv
fetch --instrument INSTRUMENT_FX_MAJORS_GBP_USD --interval 15m --start 2019-01-01 --output data/GBPUSD_15m.csv
fetch --instrument INSTRUMENT_FX_METALS_XAU_USD --interval 15m --start 2019-01-01 --output data/XAUUSD_15m.csv
fetch --instrument INSTRUMENT_ETF_CFD_US_QQQ_US_USD --interval 5m --start 2019-01-01 --rth-only --output data/QQQ_5m_rth.csv
fetch --instrument INSTRUMENT_ETF_CFD_US_SPY_US_USD --interval 5m --start 2019-01-01 --rth-only --output data/SPY_5m_rth.csv
fetch --instrument INSTRUMENT_ETF_CFD_US_QQQ_US_USD --interval 1d --start 2010-01-01 --output data/QQQ_1d.csv
fetch --instrument INSTRUMENT_ETF_CFD_US_SPY_US_USD --interval 1d --start 2010-01-01 --output data/SPY_1d.csv
fetch --instrument INSTRUMENT_ETF_CFD_US_GLD_US_USD --interval 1d --start 2010-01-01 --output data/GLD_1d.csv
fetch --instrument INSTRUMENT_CMD_METALS_COPPER_CMD_USD --interval 1d --start 2010-01-01 --output data/COPPER_1d.csv

echo "FETCH_ALL_DONE"
ls -la data/*.csv
