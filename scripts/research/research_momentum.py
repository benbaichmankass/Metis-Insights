#!/usr/bin/env python3
"""Research harness: time-series momentum / MA-cross trend (net-of-fee).

New-idea complement to the Donchian/pullback harnesses — a pure momentum
entry (no channel): signal from either an N-bar return sign (``tsmom``) or a
fast/slow SMA cross (``macross``). Exit is an ATR Chandelier trail with
conservative SL-first intrabar resolution, opposite-signal flip, and a
timeout backstop — the same fee-efficient runner profile that works on this
program. Net-of-fee (7.5 bps round-trip), long/short split, by-year, with the
SAME JSON schema as backtest_trend.py so the research sweep can rank it
alongside the others. Research only (Tier-1), reads OHLCV CSV/Parquet.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd

FEE_BPS_ROUNDTRIP = 7.5


@dataclass
class Trade:
    direction: str
    entry: float
    sl: float
    risk: float
    exit_price: float
    outcome: str
    r_multiple: float
    entry_time: Any
    exit_time: Any


def _load(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path) if path.endswith('.parquet') else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    for need in ['timestamp', 'open', 'high', 'low', 'close']:
        if need in cols and cols[need] != need:
            df = df.rename(columns={cols[need]: need})
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
    return df.dropna(subset=['timestamp']).reset_index(drop=True)


def _norm_rule(rule: str) -> str:
    r = rule.strip().lower()
    return r[:-1] + 'min' if (r.endswith('m') and not r.endswith('min')) else r


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.set_index('timestamp')
            .resample(_norm_rule(rule), label='right', closed='right')
            .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
            .dropna().reset_index())


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, lo, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([(h - lo), (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _signal(df: pd.DataFrame, strat: str, lookback: int, slow: int) -> pd.Series:
    """+1 long / -1 short / 0 flat, computed from data up to and incl. each bar."""
    c = df['close']
    if strat == 'tsmom':
        return (c > c.shift(lookback)).astype(int) - (c < c.shift(lookback)).astype(int)
    # macross
    fast_ma = c.rolling(lookback, min_periods=lookback).mean()
    slow_ma = c.rolling(slow, min_periods=slow).mean()
    return (fast_ma > slow_ma).astype(int) - (fast_ma < slow_ma).astype(int)


def backtest(df: pd.DataFrame, strat: str, lookback: int, slow: int, atr_p: int,
             atr_stop: float, trail_mult: float, timeout: int, long_only: bool) -> List[Trade]:
    atr = _atr(df, atr_p)
    sig = _signal(df, strat, lookback, slow)
    n = len(df)
    trades: List[Trade] = []
    pos = None  # dict(direction, entry, sl, risk, peak, entry_i, entry_time)
    warm = max(lookback, slow, atr_p) + 1
    for i in range(warm, n):
        bar = df.iloc[i]
        hi, lo, cl = float(bar['high']), float(bar['low']), float(bar['close'])
        if pos is not None:
            # update trailing stop from the favorable extreme
            a = float(atr.iloc[i]) or 0.0
            if pos['direction'] == 'long':
                pos['peak'] = max(pos['peak'], hi)
                trail = pos['peak'] - trail_mult * a
                pos['sl'] = max(pos['sl'], trail)
                hit = lo <= pos['sl']
                exit_px = pos['sl'] if hit else cl
                r = (exit_px - pos['entry']) / pos['risk']
            else:
                pos['peak'] = min(pos['peak'], lo)
                trail = pos['peak'] + trail_mult * a
                pos['sl'] = min(pos['sl'], trail)
                hit = hi >= pos['sl']
                exit_px = pos['sl'] if hit else cl
                r = (pos['entry'] - exit_px) / pos['risk']
            opp = (sig.iloc[i] == -1 and pos['direction'] == 'long') or (sig.iloc[i] == 1 and pos['direction'] == 'short')
            tmo = timeout > 0 and (i - pos['entry_i']) >= timeout
            if hit or opp or tmo:
                trades.append(Trade(pos['direction'], pos['entry'], pos['sl_init'], pos['risk'],
                                    exit_px if hit else cl,
                                    'trail_stop' if hit else ('flip' if opp else 'timeout'),
                                    round(r, 6), pos['entry_time'], bar['timestamp']))
                pos = None
        if pos is None:
            s = int(sig.iloc[i])
            if long_only and s < 0:
                s = 0
            if s != 0:
                a = float(atr.iloc[i]) or 0.0
                if a <= 0:
                    continue
                direction = 'long' if s > 0 else 'short'
                entry = cl
                sl = entry - atr_stop * a if direction == 'long' else entry + atr_stop * a
                risk = abs(entry - sl)
                if risk <= 0:
                    continue
                pos = {'direction': direction, 'entry': entry, 'sl': sl, 'sl_init': sl,
                       'risk': risk, 'peak': hi if direction == 'long' else lo,
                       'entry_i': i, 'entry_time': bar['timestamp']}
    return trades


def _fee_r(t: Trade) -> float:
    return (t.entry * (FEE_BPS_ROUNDTRIP / 10000.0)) / t.risk if t.risk else 0.0


def summarize(trades: List[Trade], params: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:
    longs = [t for t in trades if t.direction == 'long']
    shorts = [t for t in trades if t.direction == 'short']
    net = [t.r_multiple - _fee_r(t) for t in trades]
    wins = [t for t in trades if (t.r_multiple - _fee_r(t)) > 0]
    by_year: Dict[str, Any] = {}
    for t in trades:
        y = str(pd.Timestamp(t.exit_time).year)
        slot = by_year.setdefault(y, {'trades': 0, 'net_r': 0.0})
        slot['trades'] += 1
        slot['net_r'] = round(slot['net_r'] + (t.r_multiple - _fee_r(t)), 4)
    # running drawdown in R
    peak = cum = mdd = 0.0
    for r in net:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        'strategy': f"momentum_{params['strat']}", 'symbol': params['symbol'],
        'timeframe': params['timeframe'], 'params': params,
        'total_trades': len(trades), 'fee_bps_roundtrip': FEE_BPS_ROUNDTRIP,
        'data_start': str(df['timestamp'].iloc[0]) if len(df) else None,
        'data_end': str(df['timestamp'].iloc[-1]) if len(df) else None,
        'win_rate_pct': round(100 * len(wins) / len(trades), 2) if trades else 0,
        'net_total_r': round(sum(net), 4),
        'net_total_r_long': round(sum(t.r_multiple - _fee_r(t) for t in longs), 4),
        'net_total_r_short': round(sum(t.r_multiple - _fee_r(t) for t in shorts), 4),
        'net_expectancy_r': round(sum(net) / len(trades), 4) if trades else 0,
        'trades_long': len(longs), 'trades_short': len(shorts),
        'max_drawdown_r': round(mdd, 4), 'by_year': by_year,
    }


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description='Time-series momentum / MA-cross backtest (net-of-fee).')
    p.add_argument('--data', default=os.environ.get('BACKTEST_DATA_PATH', 'data/backtest_candles.csv'))
    p.add_argument('--timeframe', default='1d')
    p.add_argument('--symbol', default='BTCUSDT')
    p.add_argument('--resample', default=None)
    p.add_argument('--start', default=None)
    p.add_argument('--end', default=None)
    p.add_argument('--strategy', default='tsmom', choices=['tsmom', 'macross'])
    p.add_argument('--lookback', type=int, default=20)
    p.add_argument('--slow', type=int, default=50)
    p.add_argument('--atr-period', type=int, default=14)
    p.add_argument('--atr-stop-mult', type=float, default=2.5)
    p.add_argument('--trail-mult', type=float, default=4.0)
    p.add_argument('--timeout-bars', type=int, default=0)
    p.add_argument('--long-only', action='store_true')
    p.add_argument('--json', dest='json_out', default=None)
    a = p.parse_args(argv)

    df = _load(a.data)
    if a.resample:
        df = _resample(df, a.resample)
    if a.start:
        df = df[df['timestamp'] >= pd.Timestamp(a.start, tz='UTC')].reset_index(drop=True)
    if a.end:
        df = df[df['timestamp'] <= pd.Timestamp(a.end, tz='UTC')].reset_index(drop=True)

    trades = backtest(df, a.strategy, a.lookback, a.slow, a.atr_period,
                      a.atr_stop_mult, a.trail_mult, a.timeout_bars, a.long_only)
    params = {'strat': a.strategy, 'symbol': a.symbol, 'timeframe': a.timeframe,
              'lookback': a.lookback, 'slow': a.slow, 'atr_stop_mult': a.atr_stop_mult,
              'trail_mult': a.trail_mult, 'long_only': a.long_only}
    out = summarize(trades, params, df)
    line = (f"{out['strategy']} — {a.symbol} {a.timeframe} lb={a.lookback} tm={a.trail_mult} "
            f"lo={a.long_only}  trades={out['total_trades']} win={out['win_rate_pct']}% "
            f"net_r={out['net_total_r']} (long {out['net_total_r_long']}, short {out['net_total_r_short']})")
    print(line)
    if a.json_out:
        with open(a.json_out, 'w') as fh:
            json.dump(out, fh, indent=2)
        print(f"JSON -> {a.json_out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
