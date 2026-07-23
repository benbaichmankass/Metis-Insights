"""M28 Phase A2 — the S3 PnL harness: conviction-weighted, net-of-cost portfolio backtest.

The signal grade (S2 — `horizon_ic_scan.py`) answers *is there signal*: does
conviction rank forward return, and is the conviction-sorted spread positive. It does
NOT answer *does it make money at a tradeable size after costs* — a positive IC can
sit on a spread smaller than fees (crypto's 1d: flagged IC, sub-fee spread). This
harness is S3: it turns the graded signals into an actual **conviction-weighted
portfolio**, carries it forward net of costs, and reports the numbers that decide a
build — **total return, Sharpe, max-drawdown, hit-rate, turnover** — with an
out-of-sample split so no claim is in-sample-only (`RESEARCH-RIGOR-STANDARD.md`).

Model: under the non-overlapping rebalance regime the S2 scan uses (spacing ≥
horizon), each rebalance date's cohort of theses is one **independent** portfolio
period, so a period's return is the weighted mean of that cohort's net-of-cost
per-thesis returns. Reuses `thesis_backtest.net_return` (the P4 core) so the per-leg
economics are identical to the signal grade. Pure, stdlib-only; consumes the same
`build_replay_entries` output the scan does. Three books per run:

- **conviction_weighted** — signed by direction, magnitude = conviction, gross-
  normalized (Σ|w| = 1): the directional book that respects the signal's long/short.
- **long_short_neutral** — dollar-neutral (longs → +0.5, shorts → −0.5): the
  market-neutral book, the conviction spread realized as a portfolio.
- **baseline_all_long** — equal-weight, all-long: the naive book the strategy must beat.
"""

from __future__ import annotations

import math
import os
import statistics
import sys
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.units.strategies.macro_thesis.thesis_backtest import net_return  # noqa: E402


# ---------------------------------------------------------------------------
# book construction (per rebalance cohort)
# ---------------------------------------------------------------------------


def _signed_conviction(direction: str, conviction) -> Optional[float]:
    """Signed weight before normalization: +conviction for long, −conviction for
    short. ``None`` for a missing conviction or an unknown direction (dropped)."""
    if conviction is None:
        return None
    d = (direction or "").lower()
    if d not in ("long", "short"):
        return None
    return float(conviction) if d == "long" else -float(conviction)


def _gross_normalize(signed: dict, gross: float = 1.0) -> dict:
    """Scale signed weights so Σ|w| = ``gross`` (a fully-invested book). Empty if the
    cohort has no gross exposure (all-zero convictions)."""
    tot = sum(abs(v) for v in signed.values())
    if tot <= 0:
        return {}
    return {s: (v / tot) * gross for s, v in signed.items()}


def _neutral_book(signed: dict, gross: float = 1.0) -> dict:
    """Dollar-neutral long-short: longs scaled to +gross/2, shorts to −gross/2 (each
    side conviction-weighted within itself). If only one side is present it degrades to
    that side gross-normalized (can't be neutral with one leg) — recorded, not faked."""
    longs = {s: v for s, v in signed.items() if v > 0}
    shorts = {s: v for s, v in signed.items() if v < 0}
    if not longs or not shorts:
        return _gross_normalize(signed, gross)
    lt = sum(longs.values())
    st = sum(-v for v in shorts.values())
    out = {s: (v / lt) * (gross / 2.0) for s, v in longs.items()}
    out.update({s: (v / st) * (gross / 2.0) for s, v in shorts.items()})   # v<0 → negative
    return out


def _all_long_book(symbols, gross: float = 1.0) -> dict:
    """Equal-weight, all-long baseline over the cohort's symbols."""
    n = len(symbols)
    return {s: gross / n for s in symbols} if n else {}


def _cohort_gross_cost(cohort, *, fee_frac: float, carry_frac_per_day: float):
    """Per-symbol ``(gross, cost)`` for one rebalance cohort:

    - ``gross[s]`` = the symbol's **direction-agnostic long move** ``(exit−entry)/entry``
      (via the P4 `net_return` with direction=long, zero cost). The book's SIGNED weight
      applies the direction — so a signed weight × gross correctly earns a short the
      inverse move — and the cost is never double-signed.
    - ``cost[s]`` = the always-positive drag ``|fee| + |carry_per_day|·hold_days``,
      subtracted on ``|weight|`` (gross exposure) regardless of side.

    Duplicate symbols on a date (rare) are averaged."""
    gacc, cacc = {}, {}
    for e in cohort:
        sym = e.get("symbol")
        if sym is None:
            continue
        g = net_return("long", e.get("entry_price"), e.get("exit_price"), fee_frac=0.0, carry_frac=0.0)
        if g is None:
            continue
        c = abs(fee_frac) + abs(carry_frac_per_day) * float(e.get("hold_days", 0.0))
        gacc.setdefault(sym, []).append(g)
        cacc.setdefault(sym, []).append(c)
    gross = {s: statistics.fmean(v) for s, v in gacc.items() if v}
    cost = {s: statistics.fmean(cacc[s]) for s in gross}
    return gross, cost


def _book_period_return(weights: dict, gross: dict, cost: dict) -> float:
    """Portfolio period return = Σ_s [ w_s·gross_s − |w_s|·cost_s ] — the signed weight
    carries direction; the cost is a drag on gross exposure regardless of side."""
    return sum(w * gross.get(s, 0.0) - abs(w) * cost.get(s, 0.0) for s, w in weights.items())


# ---------------------------------------------------------------------------
# metrics over a period-return series
# ---------------------------------------------------------------------------


def _r(v, nd=6):
    return None if v is None else round(float(v), nd)


def _turnover(weight_series: list) -> Optional[float]:
    """Mean one-sided turnover across consecutive rebalances: ½·Σ_s |w_t(s) − w_{t-1}(s)|
    over the union of symbols. ``None`` with < 2 periods."""
    if len(weight_series) < 2:
        return None
    tos = []
    for prev, cur in zip(weight_series, weight_series[1:]):
        syms = set(prev) | set(cur)
        tos.append(0.5 * sum(abs(cur.get(s, 0.0) - prev.get(s, 0.0)) for s in syms))
    return statistics.fmean(tos) if tos else None


def _equity_metrics(period_returns: list, *, ann_periods: float, weight_series=None) -> dict:
    """Compound the period returns → equity curve + risk-adjusted metrics."""
    n = len(period_returns)
    if n == 0:
        return {"n_periods": 0, "total_return": None, "mean_period_return": None,
                "stdev_period_return": None, "sharpe": None, "max_drawdown": None,
                "hit_rate": None, "turnover": None, "equity": []}
    equity, e = [], 1.0
    peak, maxdd = 1.0, 0.0
    for r in period_returns:
        e *= (1.0 + r)
        equity.append(_r(e))
        peak = max(peak, e)
        if peak > 0:
            maxdd = max(maxdd, (peak - e) / peak)
    mean = statistics.fmean(period_returns)
    sd = statistics.pstdev(period_returns) if n >= 2 else 0.0
    sharpe = (mean / sd) * math.sqrt(ann_periods) if sd > 0 else None
    wins = sum(1 for r in period_returns if r > 0)
    return {
        "n_periods": n,
        "total_return": _r(e - 1.0),
        "mean_period_return": _r(mean),
        "stdev_period_return": _r(sd),
        "sharpe": _r(sharpe, 3),
        "max_drawdown": _r(maxdd),
        "hit_rate": _r(wins / n, 4),
        "turnover": _r(_turnover(weight_series)) if weight_series else None,
        "equity": equity,
    }


def _ann_periods(dates: list) -> float:
    """Rebalance periods per year, from the median gap between sorted rebalance dates."""
    import datetime as _dt
    ds = sorted({d for d in dates})
    if len(ds) < 2:
        return 1.0
    gaps = []
    for a, b in zip(ds, ds[1:]):
        try:
            da = _dt.date.fromisoformat(str(a)[:10])
            db = _dt.date.fromisoformat(str(b)[:10])
            gaps.append((db - da).days)
        except ValueError:
            continue
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return 1.0
    return 365.25 / statistics.median(gaps)


# ---------------------------------------------------------------------------
# the harness
# ---------------------------------------------------------------------------


def run_pnl_backtest(entries, *, fee_frac: float = 0.0, carry_frac_per_day: float = 0.0,
                     oos_frac: float = 0.5) -> dict:
    """Conviction-weighted net-of-cost portfolio backtest over replay entries.

    Groups entries by rebalance date (``as_of``), builds three books per cohort
    (conviction_weighted / long_short_neutral / baseline_all_long), computes each
    book's period return, and reports full-sample + out-of-sample metrics. ``oos_frac``
    is the trailing fraction of rebalance dates held out (0.5 → last half is OOS).
    """
    # group by rebalance date
    by_date: dict = {}
    for e in entries or []:
        by_date.setdefault(str(e.get("as_of")), []).append(e)
    dates = sorted(by_date)

    series = {"conviction_weighted": [], "long_short_neutral": [], "baseline_all_long": []}
    weights = {"conviction_weighted": [], "long_short_neutral": [], "baseline_all_long": []}
    used_dates = []
    for d in dates:
        cohort = by_date[d]
        gross, cost = _cohort_gross_cost(cohort, fee_frac=fee_frac, carry_frac_per_day=carry_frac_per_day)
        if not gross:
            continue
        signed = {}
        for e in cohort:
            sw = _signed_conviction(e.get("direction"), e.get("conviction"))
            if sw is None or e.get("symbol") not in gross:
                continue
            # if a symbol repeats, keep the strongest-conviction signed weight
            s = e["symbol"]
            if s not in signed or abs(sw) > abs(signed[s]):
                signed[s] = sw
        if not signed:
            continue
        books = {
            "conviction_weighted": _gross_normalize(signed),
            "long_short_neutral": _neutral_book(signed),
            "baseline_all_long": _all_long_book(list(gross.keys())),
        }
        used_dates.append(d)
        for k, bk in books.items():
            series[k].append(_book_period_return(bk, gross, cost))
            weights[k].append(bk)

    ann = _ann_periods(used_dates)
    split = max(1, int(round(len(used_dates) * (1.0 - oos_frac)))) if used_dates else 0

    def _pack(k):
        full = _equity_metrics(series[k], ann_periods=ann, weight_series=weights[k])
        oos = _equity_metrics(series[k][split:], ann_periods=ann, weight_series=weights[k][split:])
        return {"full": full, "oos": oos}

    out = {k: _pack(k) for k in series}
    # headline edge: strategy full-sample total return minus the all-long baseline
    cw = out["conviction_weighted"]["full"]["total_return"]
    ls = out["long_short_neutral"]["full"]["total_return"]
    base = out["baseline_all_long"]["full"]["total_return"]
    out["summary"] = {
        "n_rebalances": len(used_dates),
        "ann_periods": _r(ann, 2),
        "edge_conviction_vs_baseline": _r(None if cw is None or base is None else cw - base),
        "edge_neutral_vs_zero": _r(ls),   # a market-neutral book's baseline is 0
        # the S3 gate: the conviction-weighted book beats all-long net of costs AND the
        # market-neutral book is positive, on the OUT-OF-SAMPLE half.
        "pays_oos": bool(
            out["conviction_weighted"]["oos"]["total_return"] is not None
            and out["baseline_all_long"]["oos"]["total_return"] is not None
            and out["conviction_weighted"]["oos"]["total_return"] > out["baseline_all_long"]["oos"]["total_return"]
            and (out["long_short_neutral"]["oos"]["total_return"] or 0) > 0
        ),
    }
    out["meta"] = {"fee_frac": fee_frac, "carry_frac_per_day": carry_frac_per_day,
                   "oos_frac": oos_frac, "oos_split_index": split}
    return out
