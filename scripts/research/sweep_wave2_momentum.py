#!/usr/bin/env python3
"""Wave-2 research sweep: time-series momentum / MA-cross (new-idea harness).

Sweeps /tmp/research_momentum.py across markets, timeframes, lookbacks,
long-only vs both-sides, and walk-forward windows. Appends to the SAME
/tmp/research/results.jsonl so the final ranker sees waves 1 and 2 together.
"""
import json
import subprocess
import itertools
import time
from pathlib import Path

REPO = '/home/ubuntu/ict-trading-bot'
PY = f'{REPO}/.venv/bin/python'
MOM = '/tmp/research_momentum.py'
OUT = Path('/tmp/research')
OUT.mkdir(exist_ok=True)
RESULTS = OUT / 'results.jsonl'
DATA_BTC = '/tmp/btc5m.csv'
DATA_SPX = f'{REPO}/data/SPX500_1m.parquet'

WINDOWS = {'full': [], 'IS': ['--end', '2023-12-31'], 'OOS': ['--start', '2024-01-01']}
runs = []


def add(market, data, symbol, tf, strat, lb, slow, tm, lo, label):
    extra = ['--strategy', strat, '--lookback', str(lb), '--slow', str(slow),
             '--atr-stop-mult', '2.5', '--trail-mult', str(tm)]
    if lo:
        extra.append('--long-only')
    base = [MOM, '--data', data, '--resample', tf, '--timeframe', tf, '--symbol', symbol] + extra
    runs.append((f'mom_{strat}', market, tf, base, label))


for strat, tf, lb, tm, lo in itertools.product(['tsmom', 'macross'], ['4h', '1d'], [20, 40], [4.0, 5.0], [False, True]):
    add('BTC', DATA_BTC, 'BTCUSDT', tf, strat, lb, lb * 3, tm, lo, f'lb{lb}_tm{tm}_lo{int(lo)}')

for strat, tf, lb, tm in itertools.product(['tsmom', 'macross'], ['1d'], [20, 50], [4.0, 5.0]):
    add('SPX', DATA_SPX, 'SPX', tf, strat, lb, lb * 3, tm, True, f'lb{lb}_tm{tm}_lo1')

print(f'wave2: {len(runs)} configs x 3 windows = {len(runs)*3} runs', flush=True)
done = 0
t0 = time.time()
with open(RESULTS, 'a') as rf:
    for family, market, tf, base, label in runs:
        for win, wargs in WINDOWS.items():
            jpath = OUT / f'{family}_{market}_{tf}_{label}_{win}.json'
            cmd = [PY] + base + wargs + ['--json', str(jpath)]
            rec = {'family': family, 'market': market, 'tf': tf, 'params': label, 'window': win}
            try:
                subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=180)
                d = json.loads(jpath.read_text())
                rec.update({'net_r': round(d.get('net_total_r', 0), 2),
                            'net_long': round(d.get('net_total_r_long', 0), 2),
                            'net_short': round(d.get('net_total_r_short', 0), 2),
                            'trades': d.get('total_trades', 0), 'win': d.get('win_rate_pct', 0),
                            'maxdd': round(d.get('max_drawdown_r', 0), 2), 'ok': True})
            except Exception as e:  # noqa: BLE001
                rec.update({'ok': False, 'err': str(e)[:120]})
            rf.write(json.dumps(rec) + '\n')
            rf.flush()
            done += 1
print(f'WAVE2_DONE {done} runs in {time.time()-t0:.0f}s', flush=True)
