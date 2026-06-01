#!/usr/bin/env python3
"""Pre-shadow validation: fee/slippage sensitivity + uniform 3-fold robustness.

Runs the four candidate leads cleared by the overnight sweep through two extra
gates before any shadow wiring:
  1. fee sensitivity — does net R stay positive at 10 and 15 bps round-trip
     (live cost/slippage > the 7.5 bps backtest assumption)?
  2. 3-fold robustness — net-positive in each of three non-overlapping
     sub-periods (not an artifact of one IS/OOS split)?
Prints a verdict table. Reads the same staged harnesses + data on the VM.
"""
import json
import subprocess
from pathlib import Path

REPO = '/home/ubuntu/ict-trading-bot'
PY = f'{REPO}/.venv/bin/python'
OUT = Path('/tmp/research/validate')
OUT.mkdir(parents=True, exist_ok=True)
BTC = '/tmp/btc5m.csv'
SPX = f'{REPO}/data/SPX500_1m.parquet'

# name -> (script, data, symbol, base extra args, long_only_metric)
CAND = {
    'trend_dc20_1h_tm5':      ('/tmp/backtest_trend.py', BTC, 'BTCUSDT', '1h',
                               ['--donchian', '20', '--atr-stop-mult', '2.5', '--trail-mult', '5.0'], False),
    'pullback_tl40_pf0.5_2h': ('/tmp/backtest_pullback.py', BTC, 'BTCUSDT', '2h',
                               ['--trend-lookback', '40', '--pullback-frac', '0.5', '--atr-stop-mult', '2.5', '--trail-mult', '5.0'], False),
    'squeeze_std2.0_2h':      ('/tmp/backtest_squeeze.py', BTC, 'BTCUSDT', '2h',
                               ['--bb-std', '2.0', '--kc-mult', '1.5', '--atr-stop-mult', '2.5', '--trail-mult', '5.0'], False),
    'SPX_trend_dc30_1d':      ('/tmp/backtest_trend.py', SPX, 'SPX', '1d',
                               ['--donchian', '30', '--atr-stop-mult', '2.5', '--trail-mult', '4.0'], True),
}
FOLDS = {
    'BTCUSDT': [('21-22', '2021-01-01', '2022-12-31'), ('23-24', '2023-01-01', '2024-12-31'), ('25-26', '2025-01-01', '2026-12-31')],
    'SPX':     [('20-21', '2020-01-01', '2021-12-31'), ('22-23', '2022-01-01', '2023-12-31'), ('24-26', '2024-01-01', '2026-12-31')],
}


def run(script, data, symbol, tf, extra, fee=7.5, start=None, end=None, tag='x'):
    jp = OUT / f'{tag}.json'
    cmd = [PY, script, '--data', data, '--resample', tf, '--timeframe', tf, '--symbol', symbol,
           '--fee-bps-roundtrip', str(fee)] + extra + ['--json', str(jp)]
    if start:
        cmd += ['--start', start]
    if end:
        cmd += ['--end', end]
    try:
        subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=240)
        d = json.loads(jp.read_text())
        return d.get('net_total_r', 0.0), d.get('net_total_r_long', 0.0), d.get('total_trades', 0), d.get('max_drawdown_r', 0.0)
    except Exception as e:  # noqa: BLE001
        return None, None, 0, str(e)[:60]


print('=== FEE / SLIPPAGE SENSITIVITY (full period) ===', flush=True)
print('%-26s %10s %10s %10s' % ('candidate', 'net@7.5', 'net@10', 'net@15'), flush=True)
for name, (script, data, symbol, tf, extra, lo) in CAND.items():
    cells = []
    for fee in (7.5, 10.0, 15.0):
        net, nlong, tr, dd = run(script, data, symbol, tf, extra, fee=fee, tag=f'{name}_fee{fee}')
        val = nlong if lo else net
        cells.append('n/a' if val is None else f'{val:.1f}')
    metric = 'long' if lo else 'net'
    print('%-26s %10s %10s %10s  (%s R)' % (name, cells[0], cells[1], cells[2], metric), flush=True)

print('\n=== 3-FOLD ROBUSTNESS (fee 7.5) ===', flush=True)
print('%-26s %12s %12s %12s' % ('candidate', 'fold1', 'fold2', 'fold3'), flush=True)
for name, (script, data, symbol, tf, extra, lo) in CAND.items():
    cells = []
    for tag, s, e in FOLDS[symbol]:
        net, nlong, tr, dd = run(script, data, symbol, tf, extra, start=s, end=e, tag=f'{name}_{tag}')
        val = nlong if lo else net
        cells.append('n/a' if val is None else f'{tag}:{val:.0f}')
    print('%-26s %12s %12s %12s' % (name, cells[0], cells[1], cells[2]), flush=True)
print('VALIDATE_DONE', flush=True)
