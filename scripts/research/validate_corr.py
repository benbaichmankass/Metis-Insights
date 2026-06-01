#!/usr/bin/env python3
"""Pre-shadow validation: monthly net-R correlation / diversification matrix.

Emits per-trade net R for each cleared candidate (+ the live trend_donchian 2h
profile as a redundancy check vs the 1h variant), buckets to monthly net R, and
prints the Pearson correlation matrix. Low pairwise correlation => the shadow
sleeve is additive, not a re-skin of what's already live.
"""
import json
import subprocess
from pathlib import Path

import pandas as pd

REPO = '/home/ubuntu/ict-trading-bot'
PY = f'{REPO}/.venv/bin/python'
OUT = Path('/tmp/research/corr')
OUT.mkdir(parents=True, exist_ok=True)
BTC = '/tmp/btc5m.csv'
SPX = f'{REPO}/data/SPX500_1m.parquet'

RUNS = {
    'trend_1h':    ('/tmp/backtest_trend.py', BTC, 'BTCUSDT', '1h', ['--donchian', '20', '--atr-stop-mult', '2.5', '--trail-mult', '5.0']),
    'trend_2h_live': ('/tmp/backtest_trend.py', BTC, 'BTCUSDT', '2h', ['--donchian', '20', '--atr-stop-mult', '2.5', '--trail-mult', '5.0']),
    'pullback_2h': ('/tmp/backtest_pullback.py', BTC, 'BTCUSDT', '2h', ['--trend-lookback', '40', '--pullback-frac', '0.5', '--atr-stop-mult', '2.5', '--trail-mult', '5.0']),
    'SPX_1d':      ('/tmp/backtest_trend.py', SPX, 'SPX', '1d', ['--donchian', '30', '--atr-stop-mult', '2.5', '--trail-mult', '4.0']),
}

series = {}
for name, (script, data, sym, tf, extra) in RUNS.items():
    ep = OUT / f'{name}.jsonl'
    cmd = [PY, script, '--data', data, '--resample', tf, '--timeframe', tf, '--symbol', sym,
           '--fee-bps-roundtrip', '7.5', '--emit-trades', str(ep)] + extra
    subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=240)
    try:
        rows = [json.loads(ln) for ln in open(ep) if ln.strip()]
    except FileNotFoundError:
        rows = []
    if not rows:
        print(name, 'NO TRADES', flush=True)
        continue
    df = pd.DataFrame(rows)
    df['m'] = pd.to_datetime(df['entry_time'], utc=True, errors='coerce').dt.to_period('M').astype(str)
    series[name] = df.groupby('m')['net_r'].sum()
    print('%-14s trades=%d months=%d net_r=%.1f' % (name, len(rows), len(series[name]), df['net_r'].sum()), flush=True)

M = pd.DataFrame(series).fillna(0.0).sort_index()
print('\n=== monthly net-R correlation matrix (Pearson) ===', flush=True)
print(M.corr().round(2).to_string(), flush=True)
# BTC-only overlap (drop months where only SPX traded) for a fairer BTC cross-corr
btc_cols = [c for c in M.columns if c != 'SPX_1d']
Mb = M[(M[btc_cols] != 0).any(axis=1)]
print('\n=== overlapping months: %d (full), %d (BTC-active) ===' % (len(M), len(Mb)), flush=True)
print('CORR_DONE', flush=True)
