# VWAP Mean Reversion Strategy

## Overview
A statistical strategy that trades price deviations from VWAP (Volume Weighted Average Price) expecting reversion to the mean.

## Core Logic
- Calculate session VWAP from opening price
- Identify price deviation threshold (standard deviations)
- Long when price > 2 std devs below VWAP
- Short when price > 2 std devs above VWAP
- Exit on VWAP touch or time-based stop

## Parameters
- **VWAP Period**: Session-based (reset daily)
- **Entry Threshold**: 2.0 standard deviations
- **Take Profit**: VWAP or 1.5x deviation
- **Stop Loss**: 3.0 standard deviations
- **Position Size**: 1% account risk
- **Max Trades**: 3 per session

## Timeframes
- Primary: 5-minute candles
- VWAP calculation: Session (daily reset)
- Backtest period: 30 days minimum

## Filters
- Only trade during high volume periods
- Skip first 15 minutes of session
- Require minimum ATR for volatility

## Status
- [ ] Backtesting complete
- [ ] Dry-run validation on small live account
- [ ] Live deployment ready
- [x] Strategy scaffold created
