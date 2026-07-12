#!/usr/bin/env python3
"""Research harness: Donchian channel-breakout trend (net-of-fee).

Committed, self-contained re-implementation of the Donchian trend engine the
overnight 2026-06-01 campaign ran from /tmp (which is ephemeral on the trainer
VM). Entry is a Donchian breakout — long when the close prints above the prior
N-bar highest-high, short below the prior N-bar lowest-low — and exit is the
SAME ATR Chandelier trail / SL-first intrabar / opposite-signal flip / timeout
machinery as research_momentum.py, with the SAME 7.5 bps round-trip fee model
and the SAME JSON schema, so its output ranks alongside the momentum + pullback
harnesses and reproduces the report's numbers.

Primary use (2026-06-01): validate `mes_trend_long_1d` (the execution: shadow
daily long-only diversifier, PR #2522) on NATIVE MES daily bars pulled from
IBKR — params donchian=30 / atr-stop=2.5 / trail=4.0 / long-only — instead of
the SPX500-CFD proxy the campaign used. Calibrate first by running this engine
on the SPX parquet and confirming it reproduces the proxy result (~+5..7 R OOS,
long-only) before trusting the MES figure.

Reads OHLCV from CSV / Parquet / JSONL (the IBKR pull writes JSONL rows of
{ts,open,high,low,close,volume}). Research only (Tier-1); no live-path touch.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
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
    if path.endswith('.parquet'):
        df = pd.read_parquet(path)
    elif path.endswith('.jsonl'):
        rows = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        df = pd.DataFrame(rows)
    else:
        df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    # the IBKR pull writes the timestamp column as `ts`; normalise to `timestamp`
    if 'timestamp' not in cols and 'ts' in cols:
        df = df.rename(columns={cols['ts']: 'timestamp'})
        cols = {c.lower(): c for c in df.columns}
    for need in ['timestamp', 'open', 'high', 'low', 'close']:
        if need in cols and cols[need] != need:
            df = df.rename(columns={cols[need]: need})
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
    for col in ('open', 'high', 'low', 'close'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return (df.dropna(subset=['timestamp', 'open', 'high', 'low', 'close'])
              .sort_values('timestamp').reset_index(drop=True))


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


def _signal(df: pd.DataFrame, donchian: int) -> pd.Series:
    """+1 long / -1 short / 0 flat — Donchian channel breakout.

    Long when the close exceeds the prior `donchian`-bar highest-high; short
    when it breaks the prior `donchian`-bar lowest-low. The channel uses bars
    strictly BEFORE the current one (shift(1)) so the signal is causal.
    """
    c = df['close']
    upper = df['high'].rolling(donchian, min_periods=donchian).max().shift(1)
    lower = df['low'].rolling(donchian, min_periods=donchian).min().shift(1)
    long_sig = (c > upper).astype(int)
    short_sig = (c < lower).astype(int)
    return long_sig - short_sig


def backtest(df: pd.DataFrame, donchian: int, atr_p: int, atr_stop: float,
             trail_mult: float, timeout: int, long_only: bool,
             min_confidence: float = 0.0,
             stale_exit_bars: int = 0, stale_exit_below_r: float = 0.0,
             bank_frac: float = 0.0, bank_at_r: float = 1.0,
             giveback_min_mfe_r: float = 0.0, giveback_r: float = 1.0
             ) -> List[Trade]:
    atr = _atr(df, atr_p)
    sig = _signal(df, donchian)
    # Donchian channel (causal — prior N bars) for the breakout-depth
    # confidence gate, mirroring the LIVE trend_donchian unit: confidence =
    # clamp((break depth past the channel) / ATR, 0, 1); skip entries below
    # ``min_confidence`` (the unit's 0.30 gate that drops the shallow breaks
    # "where the strategy bleeds"). Without this the engine takes every
    # breakout and is NOT a faithful test of the filtered live strategy.
    upper = df["high"].rolling(donchian, min_periods=donchian).max().shift(1)
    lower = df["low"].rolling(donchian, min_periods=donchian).min().shift(1)
    n = len(df)
    trades: List[Trade] = []
    pos = None
    warm = max(donchian, atr_p) + 1
    for i in range(warm, n):
        bar = df.iloc[i]
        hi, lo, cl = float(bar['high']), float(bar['low']), float(bar['close'])
        if pos is not None:
            a = float(atr.iloc[i]) or 0.0
            # M20 partial-TP bank lever (0=off, byte-identical): bank
            # `bank_frac` of the position at entry + bank_at_r × risk (rung
            # fill at the rung price), remainder keeps trailing. Checked
            # BEFORE the stop for the bar only when the rung is on the
            # profit side — a bar that hits both is credited conservatively
            # (stop for the remainder, rung for the banked part only if the
            # rung price was actually touched).
            if bank_frac > 0.0 and not pos.get('banked'):
                if pos['direction'] == 'long':
                    rung = pos['entry'] + bank_at_r * pos['risk']
                    if hi >= rung:
                        pos['banked'] = True
                else:
                    rung = pos['entry'] - bank_at_r * pos['risk']
                    if lo <= rung:
                        pos['banked'] = True
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
            opp = (sig.iloc[i] == -1 and pos['direction'] == 'long') or \
                  (sig.iloc[i] == 1 and pos['direction'] == 'short')
            tmo = timeout > 0 and (i - pos['entry_i']) >= timeout
            # M20 stale-stop lever (default 0 = off, byte-identical): cut a
            # position that is still below `stale_exit_below_r` open-R after
            # `stale_exit_bars` bars — the conditional chop-cut, checked at
            # close, never pre-empting the intrabar stop (hit wins below).
            r_close = ((cl - pos['entry']) / pos['risk']
                       if pos['direction'] == 'long'
                       else (pos['entry'] - cl) / pos['risk'])
            stale = (stale_exit_bars > 0
                     and (i - pos['entry_i']) >= stale_exit_bars
                     and not hit and r_close < stale_exit_below_r)
            # M20 giveback-stop lever (0=off, byte-identical): once the trade
            # has SEEN >= giveback_min_mfe_r R of open profit (peak basis),
            # exit at close when it has given back >= giveback_r R from that
            # peak — "grab the PnL" instead of riding the full retrace. An
            # R-based lock, distinct from the price/ATR chandelier trail.
            gb = False
            if giveback_min_mfe_r > 0.0 and not hit:
                peak_r = ((pos['peak'] - pos['entry']) / pos['risk']
                          if pos['direction'] == 'long'
                          else (pos['entry'] - pos['peak']) / pos['risk'])
                gb = (peak_r >= giveback_min_mfe_r
                      and (peak_r - r_close) >= giveback_r)
            if hit or opp or tmo or stale or gb:
                # Weighted R when a rung was banked: bank_frac realized at
                # +bank_at_r, remainder at the exit r.
                if pos.get('banked'):
                    r = bank_frac * bank_at_r + (1.0 - bank_frac) * r
                trades.append(Trade(
                    pos['direction'], pos['entry'], pos['sl_init'], pos['risk'],
                    exit_px if hit else cl,
                    'trail_stop' if hit else ('flip' if opp else (
                        'timeout' if tmo else (
                            'stale_stop' if stale else 'giveback_stop'))),
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
                # Breakout-depth confidence gate (mirrors the live unit).
                if min_confidence > 0.0:
                    if direction == 'long':
                        depth = (cl - float(upper.iloc[i])) / a
                    else:
                        depth = (float(lower.iloc[i]) - cl) / a
                    conf = min(max(depth, 0.0), 1.0)
                    if conf < min_confidence:
                        continue
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
    peak = cum = mdd = 0.0
    for r in net:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        'strategy': 'trend_donchian', 'symbol': params['symbol'],
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
    p = argparse.ArgumentParser(description='Donchian channel-breakout trend backtest (net-of-fee).')
    p.add_argument('--data', default=os.environ.get('BACKTEST_DATA_PATH', 'data/backtest_candles.csv'))
    p.add_argument('--timeframe', default='1d')
    p.add_argument('--symbol', default='MES')
    p.add_argument('--resample', default=None)
    p.add_argument('--start', default=None)
    p.add_argument('--end', default=None)
    p.add_argument('--donchian', type=int, default=30)
    p.add_argument('--atr-period', type=int, default=14)
    p.add_argument('--atr-stop-mult', type=float, default=2.5)
    p.add_argument('--trail-mult', type=float, default=4.0)
    p.add_argument('--timeout-bars', type=int, default=0)
    p.add_argument('--long-only', action='store_true')
    p.add_argument('--min-confidence', type=float, default=0.0,
                   help='breakout-depth/ATR gate, mirrors the live unit (0.30 live)')
    p.add_argument('--stale-exit-bars', type=int, default=0,
                   help='M20 exit lever: close at bar N after entry when open R is '
                        'below --stale-exit-below-r (0=off, legacy behaviour).')
    p.add_argument('--stale-exit-below-r', type=float, default=0.0,
                   help='Threshold R for --stale-exit-bars (default 0.0).')
    p.add_argument('--bank-frac', type=float, default=0.0,
                   help='M20 partial-TP ladder lever: fraction of the position '
                        'banked at +bank_at_r R (0=off, legacy behaviour).')
    p.add_argument('--bank-at-r', type=float, default=1.0,
                   help='R-multiple of the bank rung for --bank-frac (default 1.0).')
    p.add_argument('--giveback-min-mfe-r', type=float, default=0.0,
                   help='M20 giveback-stop lever: arm once peak open profit '
                        'reaches this many R (0=off, legacy behaviour).')
    p.add_argument('--giveback-r', type=float, default=1.0,
                   help='R given back from the peak that triggers the exit '
                        '(default 1.0).')
    p.add_argument('--emit-trades', default=None, metavar='PATH',
                   help='Write per-trade JSONL (entry_time/direction/net_r/'
                        'entry/sl/exit_time/exit_reason) for the M20 E0 '
                        'exit-head dataset builder.')
    p.add_argument('--json', dest='json_out', default=None)
    a = p.parse_args(argv)

    df = _load(a.data)
    if a.resample:
        df = _resample(df, a.resample)
    if a.start:
        df = df[df['timestamp'] >= pd.Timestamp(a.start, tz='UTC')].reset_index(drop=True)
    if a.end:
        df = df[df['timestamp'] <= pd.Timestamp(a.end, tz='UTC')].reset_index(drop=True)

    trades = backtest(df, a.donchian, a.atr_period, a.atr_stop_mult,
                      a.trail_mult, a.timeout_bars, a.long_only, a.min_confidence,
                      a.stale_exit_bars, a.stale_exit_below_r,
                      a.bank_frac, a.bank_at_r,
                      a.giveback_min_mfe_r, a.giveback_r)
    params = {'symbol': a.symbol, 'timeframe': a.timeframe, 'donchian': a.donchian,
              'atr_stop_mult': a.atr_stop_mult, 'trail_mult': a.trail_mult,
              'long_only': a.long_only}
    if a.stale_exit_bars:
        params['stale_exit_bars'] = a.stale_exit_bars
        params['stale_exit_below_r'] = a.stale_exit_below_r
    if a.bank_frac:
        params['bank_frac'] = a.bank_frac
        params['bank_at_r'] = a.bank_at_r
    if a.emit_trades:
        Path(a.emit_trades).parent.mkdir(parents=True, exist_ok=True)
        with open(a.emit_trades, 'w', encoding='utf-8') as fh:
            for t in trades:
                fh.write(json.dumps({
                    'strategy': 'trend_donchian', 'symbol': a.symbol,
                    'entry_time': str(t.entry_time),
                    'exit_time': str(t.exit_time),
                    'direction': t.direction, 'entry': t.entry, 'sl': t.sl,
                    'gross_r': t.r_multiple,
                    'net_r': round(t.r_multiple - _fee_r(t), 4),
                    'exit_reason': t.outcome}, default=str) + '\n')
    out = summarize(trades, params, df)
    line = (f"trend_donchian — {a.symbol} {a.timeframe} dc={a.donchian} tm={a.trail_mult} "
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
