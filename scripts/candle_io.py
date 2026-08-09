#!/usr/bin/env python3
"""Shared OHLCV load + resample helpers for the research harnesses.

WHY THIS EXISTS
---------------
``scripts/research/backtest_trend.py`` was retired
(``BL-20260808-RESEARCH-TREND-ENGINE-RETIREMENT-BLOCKED-BY-TEST-COUPLING``), but
three research scripts imported its ``_load`` / ``_resample`` as **pure IO
helpers** — nothing to do with its engine. Those two functions were the only
reason the retirement was blocked on ``regime_adx_cutpoint_sweep.py`` and
``regime_tag_emitted.py`` at all, so they are lifted here rather than left to
die with the engine.

**Lifted VERBATIM.** The bodies below are byte-for-byte the retired engine's
``_load`` / ``_norm_rule`` / ``_resample``, so every consumer's behaviour is
unchanged by the move. That is deliberate: a retirement PR must not also be a
silent behaviour change to the callers it rescues.

THE ENGINE USES THIS TOO
------------------------
``scripts/backtest_trend.py::_load_candles`` delegates here. It used to be a
private CSV/Parquet-only reader, and retiring the research engine removed the
only trend harness that could read **JSONL** — which broke
``build_continuous_contract.py``'s documented workflow (it writes the canonical
``market_raw`` shape as JSONL and tells you to backtest that file). So there is
now ONE reader, not two that disagree about input format.

The delegation was verified before it was made, not assumed: 18 full-summary
backtest comparisons (2 committed corpora x 5min/15min/1h x 3 configs, incl. the
config-exact ``trend_donchian`` block) came out **identical**, and the loaded
frames compared equal with ``DataFrame.equals``. This reader is a strict
superset of what the engine previously accepted — it additionally handles JSONL
and the IBKR pull's ``ts`` column, and coerces OHLC to numeric so a row with an
unparseable price is dropped rather than poisoning the arithmetic downstream.
``BL-20260809-TWO-CANDLE-READERS-DIVERGE-ON-JSONL``.

Research only (Tier-1); no live-path touch.
"""
from __future__ import annotations

import json

import pandas as pd

__all__ = ["load_candles", "resample_ohlcv", "norm_rule"]


def load_candles(path: str) -> pd.DataFrame:
    """Read OHLCV from CSV / Parquet / JSONL into a normalised frame.

    Accepts the IBKR pull's ``ts`` timestamp column, coerces OHLC to numeric,
    and drops any row missing a timestamp or a price. Returned frame is sorted
    ascending with a fresh RangeIndex.
    """
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


def norm_rule(rule: str) -> str:
    """pandas 3.0 dropped the lowercase 'm' minutes alias — normalise to 'min'."""
    r = rule.strip().lower()
    return r[:-1] + 'min' if (r.endswith('m') and not r.endswith('min')) else r


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Right-labelled, right-closed OHLC resample (the harness convention)."""
    return (df.set_index('timestamp')
            .resample(norm_rule(rule), label='right', closed='right')
            .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
            .dropna().reset_index())
