#!/usr/bin/env python3
"""Overnight strategy-research sweep orchestrator.

Runs the standalone net-of-fee backtest harnesses across param grids,
timeframes, and walk-forward windows (full / in-sample / out-of-sample),
recording every result to /tmp/research/results.jsonl and printing a ranked
leaderboard. Each harness owns its own resample + fee model + JSON schema
(net_total_r, net_total_r_long/short, total_trades, win_rate_pct,
max_drawdown_r, by_year).

Robust: every run is subprocess-isolated with a timeout; a failure is logged
and the sweep continues.
"""
import json
import subprocess
import itertools
import time
from pathlib import Path

REPO = '/home/ubuntu/ict-trading-bot'
PY = f'{REPO}/.venv/bin/python'
OUT = Path('/tmp/research')
OUT.mkdir(exist_ok=True)
RESULTS = OUT / 'results.jsonl'
DATA_BTC = '/tmp/btc5m.csv'          # 5m, 2021-05 -> 2026-05
DATA_SPX = f'{REPO}/data/SPX500_1m.parquet'  # 1m, 2020 -> 2026
DATA_MES = '/tmp/mes5m.csv'          # 5m, 2025 -> 2026 (short)

# scripts (staged into /tmp by the launcher)
S_TREND = '/tmp/backtest_trend.py'
S_PULL = '/tmp/backtest_pullback.py'
S_FADE = '/tmp/backtest_fade.py'
S_SQZ = '/tmp/backtest_squeeze.py'

WINDOWS = {
    'full': [],
    'IS':   ['--end', '2023-12-31'],
    'OOS':  ['--start', '2024-01-01'],
}

runs = []  # (family, market, tf, script, base_args, params_label)


def add(family, market, data, symbol, tf, script, extra, label):
    base = [script, '--data', data, '--resample', tf, '--timeframe', tf, '--symbol', symbol] + extra
    runs.append((family, market, tf, base, label))


# ---- BTC trend (Donchian breakout) ----
for tf, dc, tm in itertools.product(['1h', '2h', '4h'], [20, 30, 55], [3.0, 4.0, 5.0]):
    add('trend', 'BTC', DATA_BTC, 'BTCUSDT', tf, S_TREND,
        ['--donchian', str(dc), '--atr-stop-mult', '2.5', '--trail-mult', str(tm)],
        f'dc{dc}_tm{tm}')

# ---- BTC pullback (refine + extend today's walk-forward winner) ----
for tf, tl, pf, tm in itertools.product(['2h', '4h'], [40, 50, 60], [0.4, 0.5], [4.0, 5.0]):
    add('pullback', 'BTC', DATA_BTC, 'BTCUSDT', tf, S_PULL,
        ['--trend-lookback', str(tl), '--pullback-frac', str(pf), '--atr-stop-mult', '2.5', '--trail-mult', str(tm)],
        f'tl{tl}_pf{pf}_tm{tm}')

# ---- BTC fade (mean-reversion) ----
for tf, dc, tm in itertools.product(['1h', '2h'], [20, 30], [3.0, 5.0]):
    add('fade', 'BTC', DATA_BTC, 'BTCUSDT', tf, S_FADE,
        ['--donchian', str(dc), '--trail-mult', str(tm)], f'dc{dc}_tm{tm}')

# ---- BTC squeeze (vol breakout) ----
for tf, std, tm in itertools.product(['2h', '4h'], [2.0, 2.5], [3.0, 5.0]):
    add('squeeze', 'BTC', DATA_BTC, 'BTCUSDT', tf, S_SQZ,
        ['--bb-std', str(std), '--kc-mult', '1.5', '--atr-stop-mult', '2.5', '--trail-mult', str(tm)],
        f'std{std}_tm{tm}')

# ---- SPX trend (deepen the long-only diversification lead) ----
for tf, dc, tm in itertools.product(['2h', '4h', '1d'], [20, 30], [4.0, 5.0]):
    add('trend', 'SPX', DATA_SPX, 'SPX', tf, S_TREND,
        ['--donchian', str(dc), '--atr-stop-mult', '2.5', '--trail-mult', str(tm)],
        f'dc{dc}_tm{tm}')

print(f'planned {len(runs)} configs x 3 windows = {len(runs)*3} runs', flush=True)

done = 0
t0 = time.time()
with open(RESULTS, 'w') as rf:
    for family, market, tf, base, label in runs:
        for win, wargs in WINDOWS.items():
            jpath = OUT / f'{family}_{market}_{tf}_{label}_{win}.json'
            cmd = [PY] + base + wargs + ['--json', str(jpath)]
            rec = {'family': family, 'market': market, 'tf': tf, 'params': label, 'window': win}
            try:
                subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=180)
                d = json.loads(jpath.read_text())
                rec.update({
                    'net_r': round(d.get('net_total_r', 0), 2),
                    'net_long': round(d.get('net_total_r_long', 0), 2),
                    'net_short': round(d.get('net_total_r_short', 0), 2),
                    'trades': d.get('total_trades', 0),
                    'win': d.get('win_rate_pct', 0),
                    'maxdd': round(d.get('max_drawdown_r', 0), 2),
                    'ok': True,
                })
            except Exception as e:  # noqa: BLE001
                rec.update({'ok': False, 'err': str(e)[:120]})
            rf.write(json.dumps(rec) + '\n')
            rf.flush()
            done += 1
        if done % 30 == 0:
            print(f'  {done}/{len(runs)*3} runs, {time.time()-t0:.0f}s', flush=True)

print(f'SWEEP_DONE {done} runs in {time.time()-t0:.0f}s', flush=True)
