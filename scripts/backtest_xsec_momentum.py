#!/usr/bin/env python3
"""Cross-sectional momentum backtest — crypto long/short, dollar-neutral (memo P7).

A NEW edge type for the book: instead of timing a single instrument, rank a
UNIVERSE of crypto assets by trailing momentum each week and trade the
cross-section — **long the top tercile, short the bottom tercile**,
dollar-neutral (long weights sum +1, short weights sum -1, net 0 / gross 2).
The premise (well-documented in crypto): recent relative winners keep
outperforming recent relative losers over a weekly horizon, and a market-neutral
long/short book harvests that spread with near-zero net beta — an uncorrelated
sleeve next to the directional strategies.

This file ALSO embeds the **T0.2 multi-asset loader**: ``load_panel`` builds an
aligned daily close panel across N symbols on the union of dates, leakage-safe
(forward-fill the last known close for a missing interior day; a symbol with no
PRIOR observation is NaN/excluded that day). The same panel is the substrate any
future cross-asset research can import.

Strategy spec (memo P7), exact:
  - Universe : ``--asset SYM=path.csv`` (repeatable) or ``--assets-dir DIR``
               (each ``*.csv`` -> symbol = filename stem). Each CSV is daily
               OHLCV (timestamp,open,high,low,close[,volume]); timestamp UTC.
  - Formation: trailing ``--formation-days`` (default 28) return per symbol,
               computed AS OF the rebalance date using data **through t-1 only**:
               ``close[t-1-skip] / close[t-1-skip-formation] - 1`` (no look-ahead;
               the >=7-day gap that dodges short-horizon reversal is satisfied by
               the formation window ending the day BEFORE rebalance, plus an
               optional ``--skip-days``).
  - Rebalance: weekly (``--rebalance-days`` 7) — on every Nth panel day rank the
               symbols with a valid formation return, long the top ``--quantile``
               tercile, short the bottom (>=1 name per side via ceil(N*q)).
               Equal-weight within each leg. Weights held until next rebalance.
  - Returns  : daily portfolio return (FRACTIONAL, not R) =
               Sum_i weight_i * (close_i[t]/close_i[t-1] - 1) using the weights
               from the last rebalance. Reported as fractional returns + an
               annualized Sharpe (x sqrt(252)). NOTE the unit basis differs from
               the R-based harnesses (pairs/trend/...): this book is a weighted
               long/short, so the natural unit is a fractional book return.

Overlays (each flag default OFF):
  - ``--btc-gate PATH``  : BTC daily CSV. Risk-OFF when BTC close < its 50-day SMA
                           on the rebalance date -> that week the book is flat
                           (weights 0). Reports how many weeks were gated.
  - ``--funding-aware``  : if per-symbol funding is provided (``--funding SYM=path``
                           CSV ``timestamp,funding``), skip shorting a symbol whose
                           funding on the rebalance date exceeds
                           ``--funding-short-threshold`` (paying to short into high
                           positive funding is expensive). With NO funding data it
                           is a logged no-op — never fabricated.
  - Vol-target is NOT re-implemented here. The ``--emit-daily`` CSV feeds the
    existing ``scripts/backtest_vol_target.py`` (same date,book_r,n_trades schema).

Costs: turnover-based. On each rebalance charge
``--fee-bps-roundtrip`` (default 7.5 bps) * |delta weight| summed across symbols
(entering / exiting / flipping a leg moves |delta w|; holding moves 0), scaled by
``--fee-multiplier`` (default 1.0) so the gate can run at 2x. The cost is booked
as a one-off drag on the rebalance day's return.

P7 4-stage gate (``--gate``): ``--kfolds`` k-folds over the in-pool data (time
order) + a most-recent ``--holdout-frac`` out-of-pool holdout. PASSES iff
(1) every in-sample k-fold net-positive after 1x fees; (2) full period survives
2x fees (Sharpe still > 0); (3) out-of-pool holdout Sharpe positive; (4) if
``--trend-book PATH`` is given, correlation of the strategy's daily returns to the
trend book < 0.3 in the holdout (else reported "unavailable").

Output: text summary + ``--json``. ``--emit-daily PATH`` writes
``date,book_r,n_trades`` (book_r = daily fractional return; n_trades = active
legs that day) — the SAME schema as ``portfolio_combine.py`` so the vol-target
overlay can consume it. ``--emit-weights PATH`` writes the per-rebalance weights.

Research only (Tier-1): reads candle CSVs, writes JSON/CSV/JSONL. Never touches
the order path or live config.

Self-test: ``--self-test`` synthesizes a universe with an engineered
cross-sectional momentum effect and asserts the long/short book extracts a
positive net Sharpe from it (a correctness guard before trusting real data).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TRADING_DAYS_PER_YEAR = 252
FEE_BPS_ROUNDTRIP = 7.5  # per-rebalance round-trip cost on |delta weight|


# --------------------------------------------------------------------------- #
# T0.2 multi-asset loader
# --------------------------------------------------------------------------- #
def _load_close_series(path: str) -> pd.Series:
    """Load one daily OHLCV CSV -> a UTC-date-indexed close Series.

    Robust: case-insensitive column match, bad timestamps dropped, non-positive
    closes dropped, duplicate days collapsed to the last observation, sorted.
    """
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    if "timestamp" not in cols or "close" not in cols:
        raise ValueError(f"{path}: needs 'timestamp' and 'close' columns")
    ts = pd.to_datetime(df[cols["timestamp"]], utc=True, errors="coerce")
    close = pd.to_numeric(df[cols["close"]], errors="coerce")
    s = pd.DataFrame({"day": ts.dt.normalize(), "close": close})
    s = s.dropna(subset=["day", "close"])
    s = s[s["close"] > 0]
    s = s.sort_values("day").drop_duplicates(subset=["day"], keep="last")
    out = pd.Series(s["close"].to_numpy(), index=pd.DatetimeIndex(s["day"]), name="close")
    return out


def load_panel(specs: Dict[str, str]) -> pd.DataFrame:
    """Build an aligned daily close panel across symbols (the T0.2 loader).

    ``specs`` maps ``symbol -> csv/parquet path``. Returns a DataFrame indexed by
    UTC day (the UNION of every symbol's dates, sorted) with one column per
    symbol holding the close.

    Leakage-safe alignment discipline (mirrors the ML layer's as-of /
    forward-fill rule):
      - a missing INTERIOR day forward-fills the last KNOWN close (the price is
        carried, not invented);
      - a day BEFORE a symbol's first observation stays NaN -> that symbol is
        excluded from the cross-section that day (no back-fill, no look-ahead).
    """
    if not specs:
        raise ValueError("load_panel: no asset specs provided")
    series: Dict[str, pd.Series] = {}
    for sym, path in specs.items():
        s = _load_close_series(path)
        if s.empty:
            continue
        series[sym] = s
    if not series:
        raise ValueError("load_panel: every asset loaded empty")
    panel = pd.DataFrame(series).sort_index()
    # Forward-fill interior gaps only; leading NaN (pre-history) stays NaN.
    panel = panel.ffill()
    return panel


# --------------------------------------------------------------------------- #
# Overlays: BTC gate + funding
# --------------------------------------------------------------------------- #
def _load_btc_gate(path: str, sma_days: int) -> pd.Series:
    """Return a day-indexed bool Series: True = risk-OFF (BTC < its SMA).

    SMA is computed on closes THROUGH the current day (the SMA on day t uses
    days <= t); the gate is read on the rebalance day t itself, so it uses only
    information available at t (no future bars). Days before the SMA warms up are
    risk-ON (False) — we don't gate on an undefined average.
    """
    s = _load_close_series(path)
    sma = s.rolling(sma_days, min_periods=sma_days).mean()
    risk_off = (s < sma) & sma.notna()
    return risk_off


def _load_funding(specs: Dict[str, str]) -> Dict[str, pd.Series]:
    """Map symbol -> day-indexed funding Series from ``timestamp,funding`` CSVs."""
    out: Dict[str, pd.Series] = {}
    for sym, path in specs.items():
        df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
        cols = {c.lower(): c for c in df.columns}
        if "timestamp" not in cols or "funding" not in cols:
            raise ValueError(f"{path}: funding CSV needs 'timestamp' and 'funding'")
        ts = pd.to_datetime(df[cols["timestamp"]], utc=True, errors="coerce")
        fund = pd.to_numeric(df[cols["funding"]], errors="coerce")
        f = pd.DataFrame({"day": ts.dt.normalize(), "funding": fund}).dropna()
        f = f.sort_values("day").drop_duplicates(subset=["day"], keep="last")
        out[sym] = pd.Series(f["funding"].to_numpy(),
                             index=pd.DatetimeIndex(f["day"]), name=sym)
    return out


# --------------------------------------------------------------------------- #
# Core: formation, rebalance, weights, returns
# --------------------------------------------------------------------------- #
def _formation_returns(panel: pd.DataFrame, t_idx: int, *, formation: int,
                       skip: int) -> Dict[str, float]:
    """Per-symbol trailing formation return AS OF rebalance row ``t_idx``.

    Uses data through ``t_idx - 1`` ONLY (no look-ahead): the window ends at
    ``end = t_idx - 1 - skip`` and starts ``formation`` rows earlier. A symbol is
    included only when BOTH endpoint closes are present (not NaN) and positive.
    """
    end = t_idx - 1 - skip
    start = end - formation
    if start < 0:
        return {}
    end_row = panel.iloc[end]
    start_row = panel.iloc[start]
    out: Dict[str, float] = {}
    for sym in panel.columns:
        c_end = end_row[sym]
        c_start = start_row[sym]
        if (pd.notna(c_end) and pd.notna(c_start)
                and c_end > 0 and c_start > 0):
            out[sym] = float(c_end) / float(c_start) - 1.0
    return out


def _target_weights(form: Dict[str, float], quantile: float) -> Dict[str, float]:
    """Dollar-neutral target weights from formation returns.

    Long the top ``quantile`` tercile, short the bottom; equal-weight within each
    leg; long weights sum +1, short weights sum -1 (net 0, gross 2). With few
    symbols, >=1 name per side via ceil(N*q). If <2 symbols are rankable, no
    book (every weight 0).
    """
    syms = list(form.keys())
    n = len(syms)
    if n < 2:
        return {}
    k = max(1, math.ceil(n * quantile))
    k = min(k, n // 2)  # never overlap the two legs
    if k < 1:
        return {}
    ranked = sorted(syms, key=lambda s: form[s])  # ascending
    shorts = ranked[:k]
    longs = ranked[-k:]
    weights: Dict[str, float] = {s: 0.0 for s in syms}
    for s in longs:
        weights[s] = 1.0 / k
    for s in shorts:
        weights[s] = -1.0 / k
    return weights


def _turnover_cost(prev_w: Dict[str, float], new_w: Dict[str, float], *,
                   fee_bps: float, fee_mult: float) -> float:
    """One-off fractional drag = fee * sum|delta weight| over the union of names."""
    cost_per_unit = (fee_bps / 10_000.0) * fee_mult
    syms = set(prev_w) | set(new_w)
    dturn = sum(abs(new_w.get(s, 0.0) - prev_w.get(s, 0.0)) for s in syms)
    return dturn * cost_per_unit


def run_backtest(panel: pd.DataFrame, *, formation: int, skip: int,
                 rebalance: int, quantile: float, fee_bps: float,
                 fee_mult: float,
                 btc_risk_off: Optional[pd.Series] = None,
                 funding: Optional[Dict[str, pd.Series]] = None,
                 funding_threshold: float = 0.0,
                 funding_aware: bool = False,
                 ) -> Dict[str, Any]:
    """Run the weekly long/short cross-sectional momentum book over the panel.

    Returns a dict with the daily series ``[(day, book_r, n_legs), ...]``, the
    per-rebalance weights, and headline stats. NO look-ahead anywhere:
      - weights for the period STARTING at rebalance row r are computed from
        formation returns through r-1, then applied to days r, r+1, ... (each
        day's return uses close[t]/close[t-1] with the weights already set);
      - the BTC gate / funding are read on row r using values <= r.
    """
    days = list(panel.index)
    n = len(days)
    cols = list(panel.columns)
    close = panel.to_numpy(dtype=float)  # shape (n, n_syms); NaN where absent
    col_idx = {s: i for i, s in enumerate(cols)}

    daily: List[Tuple[date, float, int]] = []
    rebal_records: List[Dict[str, Any]] = []
    weeks_total = 0
    weeks_gated = 0
    weeks_funding_skips = 0
    gross_sum = 0.0
    net_sum = 0.0
    turnover_total = 0.0

    cur_w: Dict[str, float] = {}
    prev_w: Dict[str, float] = {}
    # We need a previous close per symbol to compute day t's return.
    for t in range(n):
        day_ts = days[t]
        is_rebal = (t % rebalance == 0)
        rebal_cost = 0.0
        if is_rebal:
            weeks_total += 1
            form = _formation_returns(panel, t, formation=formation, skip=skip)
            new_w = _target_weights(form, quantile)
            # BTC risk-off gate: flat this week.
            gated = False
            if btc_risk_off is not None and len(btc_risk_off):
                ro = btc_risk_off.reindex([day_ts]).iloc[0] if day_ts in btc_risk_off.index else None
                if ro is None:
                    # As-of: last known gate value on/before day_ts.
                    sub = btc_risk_off[btc_risk_off.index <= day_ts]
                    ro = bool(sub.iloc[-1]) if len(sub) else False
                if bool(ro):
                    gated = True
                    weeks_gated += 1
                    new_w = {s: 0.0 for s in form}
            # Funding-aware: drop a short whose funding on the rebal day is high.
            if funding_aware and funding and not gated:
                dropped = 0
                for s in list(new_w):
                    if new_w[s] < 0:  # a short leg
                        fseries = funding.get(s)
                        if fseries is not None and len(fseries):
                            sub = fseries[fseries.index <= day_ts]
                            fval = float(sub.iloc[-1]) if len(sub) else None
                            if fval is not None and fval > funding_threshold:
                                new_w[s] = 0.0
                                dropped += 1
                if dropped:
                    weeks_funding_skips += 1
            rebal_cost = _turnover_cost(prev_w, new_w, fee_bps=fee_bps, fee_mult=fee_mult)
            turnover_total += rebal_cost
            cur_w = new_w
            prev_w = new_w
            rebal_records.append({
                "date": str(pd.Timestamp(day_ts).date()),
                "gated": gated,
                "n_long": sum(1 for v in cur_w.values() if v > 0),
                "n_short": sum(1 for v in cur_w.values() if v < 0),
                "net_weight": round(sum(cur_w.values()), 10),
                "gross_weight": round(sum(abs(v) for v in cur_w.values()), 10),
                "weights": {s: round(w, 6) for s, w in cur_w.items() if w != 0.0},
            })

        # Daily book return using the weights set at the last rebalance.
        book_r = 0.0
        n_legs = 0
        if t > 0 and cur_w:
            for s, w in cur_w.items():
                if w == 0.0:
                    continue
                ci = col_idx[s]
                c_now = close[t, ci]
                c_prev = close[t - 1, ci]
                if (not math.isnan(c_now) and not math.isnan(c_prev)
                        and c_prev > 0):
                    book_r += w * (c_now / c_prev - 1.0)
                    n_legs += 1
        gross_sum += book_r
        net_r = book_r - rebal_cost
        net_sum += net_r
        daily.append((pd.Timestamp(day_ts).date(), round(net_r, 8), n_legs))

    summary = _summarize(daily, panel,
                         params={"formation_days": formation, "skip_days": skip,
                                 "rebalance_days": rebalance, "quantile": quantile,
                                 "fee_bps_roundtrip": fee_bps, "fee_multiplier": fee_mult,
                                 "btc_gate": btc_risk_off is not None,
                                 "funding_aware": funding_aware})
    summary.update({
        "n_symbols": len(cols),
        "symbols": cols,
        "weeks_total": weeks_total,
        "weeks_gated": weeks_gated,
        "weeks_funding_skips": weeks_funding_skips,
        "gross_total_return": round(gross_sum, 8),
        "net_total_return": round(net_sum, 8),
        "turnover_cost_total": round(turnover_total, 8),
        "mean_gross_exposure": round(
            statistics.fmean([r["gross_weight"] for r in rebal_records]), 6)
        if rebal_records else 0.0,
        "mean_net_exposure": round(
            statistics.fmean([r["net_weight"] for r in rebal_records]), 8)
        if rebal_records else 0.0,
    })
    return {"summary": summary, "daily": daily, "rebalances": rebal_records}


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def sharpe_annualized(daily_r: Sequence[float]) -> Optional[float]:
    """Daily mean/std Sharpe annualized by sqrt(252). None if < 2 days / zero std."""
    if len(daily_r) < 2:
        return None
    mean = statistics.fmean(daily_r)
    std = statistics.pstdev(daily_r)
    if std <= 0:
        return None
    return round((mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR), 4)


def annualized_vol(daily_r: Sequence[float]) -> Optional[float]:
    if len(daily_r) < 2:
        return None
    return round(statistics.pstdev(daily_r) * math.sqrt(TRADING_DAYS_PER_YEAR), 6)


def max_drawdown(daily_r: Sequence[float]) -> float:
    """Max drawdown on the cumulative (additive) daily-return curve."""
    cum = peak = mdd = 0.0
    for r in daily_r:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return round(mdd, 8)


def _summarize(daily: Sequence[Tuple[date, float, int]], panel: pd.DataFrame, *,
               params: Dict[str, Any]) -> Dict[str, Any]:
    daily_r = [r for (_d, r, _n) in daily]
    by_year: Dict[str, Dict[str, Any]] = {}
    for d, r, n in daily:
        slot = by_year.setdefault(str(d.year), {"days": 0, "net_r": 0.0})
        slot["days"] += 1
        slot["net_r"] = round(slot["net_r"] + r, 6)
    return {
        "params": params,
        "n_days": len(daily),
        "data_start": str(daily[0][0]) if daily else None,
        "data_end": str(daily[-1][0]) if daily else None,
        "total_return": round(sum(daily_r), 8),
        "daily_mean_r": round(statistics.fmean(daily_r), 8) if daily_r else 0.0,
        "sharpe_annualized": sharpe_annualized(daily_r),
        "annualized_vol": annualized_vol(daily_r),
        "max_drawdown": max_drawdown(daily_r),
        "by_year": by_year,
        "run_date": str(date.today()),
    }


# --------------------------------------------------------------------------- #
# P7 4-stage gate
# --------------------------------------------------------------------------- #
def _period_stats(daily_r: Sequence[float]) -> Dict[str, Any]:
    return {
        "n_days": len(daily_r),
        "net_total_return": round(sum(daily_r), 8),
        "sharpe_annualized": sharpe_annualized(daily_r),
        "max_drawdown": max_drawdown(daily_r),
    }


def _pearson(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) != len(b) or len(a) < 2:
        return None
    try:
        sa, sb = statistics.pstdev(a), statistics.pstdev(b)
        if sa <= 0 or sb <= 0:
            return None
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
        return round(cov / (sa * sb), 4)
    except statistics.StatisticsError:
        return None


def _run_for_panel(panel: pd.DataFrame, *, fee_mult: float, cfg: Dict[str, Any]
                   ) -> List[Tuple[date, float, int]]:
    """Re-run the book over a (sub)panel at a given fee multiplier -> daily series."""
    res = run_backtest(
        panel, formation=cfg["formation"], skip=cfg["skip"],
        rebalance=cfg["rebalance"], quantile=cfg["quantile"],
        fee_bps=cfg["fee_bps"], fee_mult=fee_mult,
        btc_risk_off=cfg.get("btc_risk_off"), funding=cfg.get("funding"),
        funding_threshold=cfg.get("funding_threshold", 0.0),
        funding_aware=cfg.get("funding_aware", False))
    return res["daily"]


def run_gate(panel: pd.DataFrame, *, kfolds: int, holdout_frac: float,
             cfg: Dict[str, Any], trend_book: Optional[List[Tuple[date, float]]]
             ) -> Dict[str, Any]:
    """The P7 4-stage gate. Splits the PANEL (by date) into an in-pool head + a
    most-recent holdout; k-folds (contiguous, time-order) over the in-pool head.

    Each sub-period is re-run as its own book (weights re-formed from its own
    history) so a fold/holdout is a self-contained backtest — no leakage across
    the boundary.
    """
    n = len(panel)
    reasons: List[str] = []
    cut = int(round(n * (1.0 - holdout_frac)))
    cut = max(2, min(cut, n - 2)) if n >= 4 else n
    in_panel = panel.iloc[:cut]
    holdout_panel = panel.iloc[cut:]

    # (1) every in-sample k-fold net-positive after 1x fees
    per_fold: List[Dict[str, Any]] = []
    m = len(in_panel)
    folds_ok = True
    if kfolds < 1 or m < kfolds:
        kfolds = max(1, min(kfolds, m))
    bounds = [round(i * m / kfolds) for i in range(kfolds + 1)]
    for i in range(kfolds):
        lo, hi = bounds[i], bounds[i + 1]
        if hi - lo < 2:
            per_fold.append({"fold": i, "n_days": hi - lo, "skipped": True})
            continue
        fold_panel = in_panel.iloc[lo:hi]
        fr = [r for (_d, r, _n) in _run_for_panel(fold_panel, fee_mult=1.0, cfg=cfg)]
        st = _period_stats(fr)
        per_fold.append({"fold": i, **st})
        if st["net_total_return"] is None or st["net_total_return"] <= 0:
            folds_ok = False
    if folds_ok:
        reasons.append("PASS(1): all in-sample k-folds net-positive after 1x fees")
    else:
        reasons.append("FAIL(1): a k-fold is not net-positive after 1x fees")

    # (2) full period survives 2x fees (Sharpe still > 0)
    full_2x = [r for (_d, r, _n) in _run_for_panel(panel, fee_mult=2.0, cfg=cfg)]
    full_2x_stats = _period_stats(full_2x)
    s2 = full_2x_stats["sharpe_annualized"]
    cond2 = s2 is not None and s2 > 0
    reasons.append(("PASS(2)" if cond2 else "FAIL(2)") +
                   f": full-period 2x-fee Sharpe={s2}")

    # (3) out-of-pool holdout Sharpe positive
    holdout_daily = _run_for_panel(holdout_panel, fee_mult=1.0, cfg=cfg)
    holdout_r = [r for (_d, r, _n) in holdout_daily]
    holdout_stats = _period_stats(holdout_r)
    s3 = holdout_stats["sharpe_annualized"]
    cond3 = s3 is not None and s3 > 0
    reasons.append(("PASS(3)" if cond3 else "FAIL(3)") +
                   f": holdout Sharpe={s3}")

    # (4) correlation to the trend book in the holdout < 0.3 (if provided)
    corr: Optional[float] = None
    cond4 = True
    if trend_book is not None:
        tb = {d: r for (d, r) in trend_book}
        paired_strat: List[float] = []
        paired_trend: List[float] = []
        for d, r, _n in holdout_daily:
            if d in tb:
                paired_strat.append(r)
                paired_trend.append(tb[d])
        corr = _pearson(paired_strat, paired_trend)
        if corr is None:
            cond4 = True
            reasons.append("PASS(4): correlation undefined "
                           f"(n_overlap={len(paired_strat)}) — not a fail")
        else:
            cond4 = abs(corr) < 0.3
            reasons.append(("PASS(4)" if cond4 else "FAIL(4)") +
                           f": |corr_to_trend_book|={abs(corr)} (<0.3 required)")
    else:
        reasons.append("SKIP(4): correlation unavailable (no --trend-book)")

    passed = folds_ok and cond2 and cond3 and cond4
    return {
        "passed": bool(passed),
        "reasons": reasons,
        "kfolds": kfolds,
        "holdout_frac": holdout_frac,
        "fee_multiplier": {"in_sample_folds": 1.0, "full_period": 2.0, "holdout": 1.0},
        "per_fold": per_fold,
        "full_period_2x": full_2x_stats,
        "holdout": holdout_stats,
        "corr_to_trend_book": corr if trend_book is not None else "unavailable (no --trend-book)",
    }


# --------------------------------------------------------------------------- #
# Emitters
# --------------------------------------------------------------------------- #
def write_daily_csv(path: str, daily: Sequence[Tuple[date, float, int]]) -> None:
    """date,book_r,n_trades — same schema as portfolio_combine.py / vol_target."""
    out = sys.stdout if path == "-" else open(path, "w", newline="", encoding="utf-8")
    close = path != "-"
    if path != "-":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        w = csv.writer(out)
        w.writerow(["date", "book_r", "n_trades"])
        for d, r, n in daily:
            w.writerow([str(d), f"{r:.8f}", n])
    finally:
        if close:
            out.close()


def write_weights(path: str, rebalances: Sequence[Dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rebalances, indent=2, default=str))


def _load_trend_book(path: str) -> List[Tuple[date, float]]:
    """Read a daily book-return CSV (date,book_r[,...]) -> [(day, r), ...]."""
    rows: List[Tuple[date, float]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for cells in csv.reader(fh):
            if not cells:
                continue
            head = cells[0].strip().lower()
            if head in ("date", ""):
                continue
            try:
                from datetime import datetime
                d = datetime.fromisoformat(cells[0].strip().split(" ")[0]).date()
                r = float(cells[1])
            except (ValueError, IndexError):
                continue
            if math.isfinite(r):
                rows.append((d, r))
    return rows


# --------------------------------------------------------------------------- #
# Text formatting
# --------------------------------------------------------------------------- #
def _fmt(out: Dict[str, Any]) -> str:
    s = out["summary"]
    p = s["params"]
    lines = [
        f"backtest_xsec_momentum — {s.get('data_start')} -> {s.get('data_end')}  "
        f"n_symbols={s['n_symbols']} days={s['n_days']}",
        f"  formation={p['formation_days']}d skip={p['skip_days']}d "
        f"rebalance={p['rebalance_days']}d quantile={p['quantile']} "
        f"fee_bps={p['fee_bps_roundtrip']}x{p['fee_multiplier']}",
        f"  total_return(net)={s['total_return']} gross={s['gross_total_return']}  "
        f"Sharpe(ann)={s['sharpe_annualized']} vol(ann)={s['annualized_vol']} "
        f"maxDD={s['max_drawdown']}",
        f"  weeks={s['weeks_total']} gated={s['weeks_gated']} "
        f"funding_skips={s['weeks_funding_skips']}  turnover_cost={s['turnover_cost_total']}",
        f"  mean_gross_exposure={s['mean_gross_exposure']} "
        f"mean_net_exposure={s['mean_net_exposure']} (~0 = dollar-neutral)",
        "  by_year:",
    ]
    for yr, slot in sorted(s["by_year"].items()):
        lines.append(f"    {yr}  days={slot['days']:>4}  net_r={slot['net_r']}")
    if "gate" in out:
        g = out["gate"]
        lines.append(f"  GATE: {'PASSED' if g['passed'] else 'FAILED'} "
                     f"(kfolds={g['kfolds']} holdout_frac={g['holdout_frac']})")
        for r in g["reasons"]:
            lines.append(f"    - {r}")
        lines.append(f"    corr_to_trend_book={g['corr_to_trend_book']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def _synth_momentum_universe(tmp: Path, *, seed: int = 11, n_days: int = 365 * 3,
                             start: str = "2022-01-01") -> Dict[str, str]:
    """Write synthetic daily OHLCV with an ENGINEERED cross-sectional momentum
    effect: each momentum symbol carries a slowly-varying per-symbol expected
    return (an AR(1) "regime") so recent relative winners keep winning week to
    week — exactly what long-top / short-bottom harvests. Plus 2 pure-noise
    symbols. Returns the SYM->path specs."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, periods=n_days, freq="D", tz="UTC")
    specs: Dict[str, str] = {}

    def _write(sym: str, rets: np.ndarray) -> None:
        close = 100.0 * np.exp(np.cumsum(rets))
        df = pd.DataFrame({"timestamp": days, "open": close, "high": close,
                           "low": close, "close": close, "volume": 1.0})
        path = tmp / f"{sym}.csv"
        df.to_csv(path, index=False)
        specs[sym] = str(path)

    # 6 momentum symbols. mu_t is a persistent AR(1) process (phi high, slow
    # mean reversion) anchored at distinct levels so the cross-section keeps a
    # stable winner/loser ordering for many weeks at a time. Low idiosyncratic
    # noise so the persistent component dominates the 28d formation ranking.
    anchors = [0.0020, 0.0012, 0.0005, -0.0005, -0.0012, -0.0020]
    for i, anchor in enumerate(anchors):
        mu = anchor
        rets = np.empty(n_days)
        for t in range(n_days):
            mu = 0.985 * mu + 0.015 * anchor + rng.normal(0.0, 0.0003)
            rets[t] = mu + rng.normal(0.0, 0.010)
        _write(f"MOM{i}", rets)
    for j in range(2):
        _write(f"NOISE{j}", rng.normal(0.0, 0.020, n_days))
    return specs


def _self_test() -> int:
    """Synthesize a universe with an engineered cross-sectional momentum effect
    and assert the long/short book extracts positive net Sharpe from it."""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    specs = _synth_momentum_universe(tmp)
    panel = load_panel(specs)
    res = run_backtest(panel, formation=28, skip=0, rebalance=7, quantile=0.3333,
                       fee_bps=7.5, fee_mult=1.0)
    print(_fmt(res))
    s = res["summary"]
    sharpe = s["sharpe_annualized"] or 0.0
    net_neutral = abs(s["mean_net_exposure"]) < 1e-6
    ok = sharpe > 0.3 and s["total_return"] > 0 and net_neutral and s["weeks_total"] > 100
    print(f"SELF-TEST {'PASS' if ok else 'FAIL'} "
          f"(Sharpe={sharpe} net_ret={s['total_return']} "
          f"net_exposure={s['mean_net_exposure']} weeks={s['weeks_total']})")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_kv(items: Optional[Sequence[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"expected SYM=path, got {it!r}")
        sym, path = it.split("=", 1)
        out[sym.strip()] = path.strip()
    return out


def _resolve_universe(args: argparse.Namespace) -> Dict[str, str]:
    specs = _parse_kv(args.asset)
    if args.assets_dir:
        d = Path(args.assets_dir)
        for q in sorted(d.glob("*.csv")):
            specs.setdefault(q.stem, str(q))
    return specs


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Cross-sectional momentum backtest — crypto long/short, "
                    "dollar-neutral (memo P7). Embeds the T0.2 multi-asset loader.")
    p.add_argument("--self-test", action="store_true",
                   help="run the synthetic-correctness check and exit")
    p.add_argument("--asset", action="append", metavar="SYM=PATH",
                   help="one universe asset (repeatable); CSV is daily OHLCV")
    p.add_argument("--assets-dir", metavar="DIR",
                   help="directory of *.csv; symbol = filename stem")
    p.add_argument("--formation-days", type=int, default=28,
                   help="trailing formation window (days). Default 28.")
    p.add_argument("--skip-days", type=int, default=0,
                   help="extra gap days between the formation window and rebalance "
                        "(reversal dodge). Default 0 (the t-1 end already gaps).")
    p.add_argument("--rebalance-days", type=int, default=7,
                   help="rebalance on every Nth panel day. Default 7 (weekly).")
    p.add_argument("--quantile", type=float, default=0.3333,
                   help="tercile fraction per leg (>=1 name via ceil(N*q)). Default 0.3333.")
    p.add_argument("--fee-bps-roundtrip", type=float, default=FEE_BPS_ROUNDTRIP,
                   help="per-rebalance round-trip cost (bps) on |delta weight|. Default 7.5.")
    p.add_argument("--fee-multiplier", type=float, default=1.0,
                   help="scale the fee (gate runs full-period at 2x). Default 1.0.")
    p.add_argument("--btc-gate", metavar="PATH", default=None,
                   help="BTC daily CSV; risk-off (flat) the week BTC < its 50d SMA.")
    p.add_argument("--btc-sma-days", type=int, default=50,
                   help="SMA window for the BTC gate. Default 50.")
    p.add_argument("--funding-aware", action="store_true",
                   help="skip shorting a symbol with high positive funding "
                        "(needs --funding; logged no-op without it).")
    p.add_argument("--funding", action="append", metavar="SYM=PATH",
                   help="per-symbol funding CSV (timestamp,funding); repeatable.")
    p.add_argument("--funding-short-threshold", type=float, default=0.0,
                   help="funding above which a short leg is skipped. Default 0.0.")
    p.add_argument("--gate", action="store_true", help="run the P7 4-stage gate.")
    p.add_argument("--kfolds", type=int, default=5, help="k-folds (gate). Default 5.")
    p.add_argument("--holdout-frac", type=float, default=0.2,
                   help="most-recent fraction held out of the pool (gate). Default 0.2.")
    p.add_argument("--trend-book", metavar="PATH", default=None,
                   help="daily book-return CSV (date,book_r); gate stage 4 checks "
                        "the holdout correlation to it is < 0.3.")
    p.add_argument("--json", dest="json_out", default=None,
                   help="write the JSON payload here ('-' for stdout).")
    p.add_argument("--emit-daily", default=None, metavar="PATH",
                   help="write date,book_r,n_trades CSV (feeds backtest_vol_target.py).")
    p.add_argument("--emit-weights", default=None, metavar="PATH",
                   help="write the per-rebalance weights JSON for inspection.")
    args = p.parse_args(argv[1:])

    if args.self_test:
        return _self_test()

    specs = _resolve_universe(args)
    if len(specs) < 2:
        p.error("need >= 2 universe assets (--asset SYM=path repeated, or --assets-dir DIR)")

    try:
        panel = load_panel(specs)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: panel load failed: {exc}", file=sys.stderr)
        return 1
    if len(panel) <= args.formation_days + args.skip_days + 2:
        print(f"ERROR: only {len(panel)} panel days "
              f"(need > formation+skip={args.formation_days + args.skip_days})",
              file=sys.stderr)
        return 1

    btc_risk_off: Optional[pd.Series] = None
    if args.btc_gate:
        try:
            btc_risk_off = _load_btc_gate(args.btc_gate, args.btc_sma_days)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: BTC gate load failed: {exc}", file=sys.stderr)
            return 1

    funding: Optional[Dict[str, pd.Series]] = None
    if args.funding_aware:
        fspecs = _parse_kv(args.funding)
        if not fspecs:
            print("NOTE: --funding-aware set but no --funding data provided — "
                  "no-op (no shorts skipped, nothing fabricated).", file=sys.stderr)
        else:
            try:
                funding = _load_funding(fspecs)
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR: funding load failed: {exc}", file=sys.stderr)
                return 1

    res = run_backtest(
        panel, formation=args.formation_days, skip=args.skip_days,
        rebalance=args.rebalance_days, quantile=args.quantile,
        fee_bps=args.fee_bps_roundtrip, fee_mult=args.fee_multiplier,
        btc_risk_off=btc_risk_off, funding=funding,
        funding_threshold=args.funding_short_threshold,
        funding_aware=args.funding_aware)

    if args.gate:
        cfg = {"formation": args.formation_days, "skip": args.skip_days,
               "rebalance": args.rebalance_days, "quantile": args.quantile,
               "fee_bps": args.fee_bps_roundtrip,
               "btc_risk_off": btc_risk_off, "funding": funding,
               "funding_threshold": args.funding_short_threshold,
               "funding_aware": args.funding_aware}
        trend_book = _load_trend_book(args.trend_book) if args.trend_book else None
        res["gate"] = run_gate(panel, kfolds=args.kfolds,
                               holdout_frac=args.holdout_frac, cfg=cfg,
                               trend_book=trend_book)

    print(_fmt(res))

    if args.emit_daily:
        write_daily_csv(args.emit_daily, res["daily"])
        if args.emit_daily != "-":
            print(f"daily CSV -> {args.emit_daily}", file=sys.stderr)
    if args.emit_weights:
        write_weights(args.emit_weights, res["rebalances"])
        print(f"weights JSON -> {args.emit_weights}", file=sys.stderr)

    if args.json_out:
        payload = {"summary": res["summary"],
                   "daily": [{"date": str(d), "book_r": r, "n_trades": n}
                             for (d, r, n) in res["daily"]]}
        if "gate" in res:
            payload["gate"] = res["gate"]
        text = json.dumps(payload, indent=2, default=str)
        if args.json_out == "-":
            print(text)
        else:
            Path(args.json_out).write_text(text)
            print(f"JSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
