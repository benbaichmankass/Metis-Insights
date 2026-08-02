#!/usr/bin/env python3
"""M31 Track A-S5 — single-asset TIMING backtest of the robust ``vix_term`` signal.

The S2/S3/walk-forward funnel (``implied_vol_probe``) established that the VIX3M/VIX
term ratio is a **robust, cost-surviving** directional lead on US equity indices
(SP500 + NASDAQ-100 clear every gate; DJIA is a lead that fails the cost gate). Those
gates answer *is there a real, sign-stable, cost-surviving signal* — they do NOT answer
the question a deployment decision actually turns on for a **known/crowded** macro
signal: **how big is the tradeable edge** (annualized return, Sharpe, drawdown) once it
is expressed as an actual long/short/flat position on the index future, net of the
*real* futures round-trip cost (~1 bp of notional — far below the 10 bp fractional proxy
the S3 conviction spread used)?

This is that missing piece: a **single-asset timing** backtest (``vix_term`` is a
timing overlay on ONE index at a time, not a cross-sectional rank — so the cross-
sectional ``pnl_harness`` does not fit). It is deliberately **honest and un-fitted**:

- **A-priori direction, never fitted.** The economic thesis is fixed before seeing the
  data: an ELEVATED term ratio (calm / steep contango) precedes LOWER forward equity
  returns (vol mean-reverts), so the rule is **short the index when the term-ratio
  trailing-percentile is high, long when low, flat in the middle** — the same negative
  orientation the IC showed, but declared as the prior, not read from the sample.
- **Non-overlapping** rebalances (stride = horizon), so each period is one independent
  bet — identical to the S2/S3 anchoring; no overlap-inflated Sharpe.
- **Point-in-time** feature (trailing percentile through ``i`` only; no lookahead), the
  same ``build_feature_series`` the grade uses.
- **Out-of-sample split** reported alongside the full sample so no number is
  in-sample-only (``RESEARCH-RIGOR-STANDARD.md``).

Reuses the pure ``implied_vol_probe`` helpers (fetch, align, feature, percentile) so the
inputs are byte-identical to the signal grade. Off-VM-guarded (keyless fredgraph) +
injectable ``urlopen`` for tests. Import-pure: no order path, no ``src.*`` beyond the
FRED adapter. **Observe-only research — nothing here places or sizes a live trade;** the
S4 productionization it informs stays Tier-3, operator-gated.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
)
from implied_vol_probe import (  # noqa: E402
    _fmt,
    align_dated,
    build_feature_series,
    pct_rank_last,
)

# name,          target_sid,  (VIX3M/VIX term ratio is the feature for all)
DEFAULT_TARGETS = (
    ("SP500",     "SP500"),      # ES / MES leg — the validated control
    ("NASDAQ100", "NASDAQ100"),  # NQ / MNQ leg — the 2nd cost-surviving leg (Track A-XI)
    ("DJIA",      "DJIA"),       # YM leg — a lead that failed the S3 cost gate
)
VOL_SID, VOL3M_SID = "VIXCLS", "VXVCLS"
DEFAULT_HORIZONS = (5, 10, 21, 42)   # trading days — the robust horizons
_TRADING_DAYS = 252
PCT_TRAIL_WINDOW = 252               # 1y trailing percentile window for the position rule


def _positions(feature_series: list, *, lo_q: float, hi_q: float,
               trail: int = PCT_TRAIL_WINDOW) -> list:
    """A-priori long/short/flat position at each index from the feature's OWN trailing
    percentile (point-in-time). **Fixed negative orientation** (the declared thesis):
    percentile > ``hi_q`` → SHORT (−1); < ``lo_q`` → LONG (+1); else FLAT (0). ``None``
    until the trailing window is warm."""
    n = len(feature_series)
    out: list = [None] * n
    for i in range(n):
        lo = max(0, i - trail + 1)
        window = [v for v in feature_series[lo:i + 1] if v is not None]
        if len(window) < 20 or feature_series[i] is None:
            continue
        p = pct_rank_last(window)
        if p is None:
            continue
        out[i] = -1.0 if p > hi_q else (1.0 if p < lo_q else 0.0)
    return out


def _simple_return(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return b / a - 1.0


def _period_returns(positions: list, target_vals: list, horizon: int, *,
                    cost_frac: float) -> list:
    """Net per-period returns on NON-OVERLAPPING anchors (stride = horizon). Each period:
    ``pos · fwd_return − |pos| · cost_frac`` (the round-trip cost is a drag on any taken
    position; a flat period pays nothing). Skips periods with a missing position/return."""
    n = len(positions)
    out: list = []
    for i in range(0, n - horizon, max(1, horizon)):
        pos = positions[i]
        fwd = _simple_return(target_vals[i], target_vals[i + horizon])
        if pos is None or fwd is None:
            continue
        out.append(pos * fwd - abs(pos) * cost_frac)
    return out


def _equity_metrics(period_returns: list, *, ann_periods: float) -> dict:
    """Compound period returns → equity curve + risk metrics. ``ann_periods`` = periods
    per year (``252/horizon``) to annualize. Honest-null on too few periods."""
    taken = [r for r in period_returns if r is not None]
    if len(taken) < 4:
        return {"n_periods": len(taken), "total_return": None, "cagr": None,
                "mean_period": None, "sharpe": None, "max_drawdown": None,
                "hit_rate": None, "exposure": None}
    equity, e, peak, max_dd = [], 1.0, 1.0, 0.0
    for r in taken:
        e *= (1.0 + r)
        equity.append(e)
        peak = max(peak, e)
        max_dd = min(max_dd, e / peak - 1.0)
    mean = statistics.fmean(taken)
    sd = statistics.pstdev(taken)
    sharpe = (mean / sd) * math.sqrt(ann_periods) if sd > 0 else None
    total = equity[-1] - 1.0
    years = len(taken) / ann_periods
    cagr = (equity[-1] ** (1.0 / years) - 1.0) if years > 0 and equity[-1] > 0 else None
    nonflat = [r for r in taken if r != 0.0]
    return {
        "n_periods": len(taken),
        "total_return": round(total, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "mean_period": round(mean, 6),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 6),
        "hit_rate": round(sum(1 for r in nonflat if r > 0) / len(nonflat), 4) if nonflat else None,
        "exposure": round(len(nonflat) / len(taken), 4),
    }


def _grade_one(name, tgt_sid, dated, horizons, *, cost_frac, lo_q, hi_q, oos_frac):
    vol_d, vol3m_d, tgt_d = dated.get(VOL_SID, []), dated.get(VOL3M_SID, []), dated.get(tgt_sid, [])
    if not vol_d or not vol3m_d or not tgt_d:
        return {"name": name, "target_sid": tgt_sid, "verdict": "no_data",
                "reason": f"empty (vol={len(vol_d)}, vol3m={len(vol3m_d)}, target={len(tgt_d)})"}
    # Build the term-ratio feature on the vol<->target alignment, exactly as the grade
    # does (align VIX with the target, map VIX3M onto those dates) — same point-in-time
    # inputs as the S2/S3 scan, so the backtest can't diverge from the graded signal.
    dates, vol_vals, tgt_vals = align_dated(vol_d, tgt_d)
    emap = {d: v for d, v in vol3m_d}
    extra_vals = [emap.get(d) for d in dates]
    feat_vals = build_feature_series("term_ratio", vol_vals, tgt_vals, extra_vals=extra_vals)
    positions = _positions(feat_vals, lo_q=lo_q, hi_q=hi_q)
    # Disclose the graded window. FRED's SP500/DJIA daily series are ROLLING ~10-year
    # windows (they drop the oldest data as time advances), while NASDAQ100/VIX go back
    # decades — so re-running this on different days grades SP500/DJIA over a SLID window
    # at ~identical n, and a Sharpe read that is not pinned to a span is not reproducible
    # (2026-08-02: SP500/DJIA 21d full-Sharpe swung 0.18/0.05 → 0.64/0.62 vs #7577 at
    # identical n=118 purely from the roll; NDX byte-identical). Emitting the span makes
    # that visible instead of absorbed. BL-20260802-VIXTERM-ROLLING-WINDOW-SPAN.
    data_span = {"start": dates[0], "end": dates[-1], "n_aligned": len(dates)} if dates else None
    rows = []
    for h in horizons:
        pr = _period_returns(positions, tgt_vals, h, cost_frac=cost_frac)
        split = int(len(pr) * (1.0 - oos_frac))
        full = _equity_metrics(pr, ann_periods=_TRADING_DAYS / h)
        oos = _equity_metrics(pr[split:], ann_periods=_TRADING_DAYS / h)
        rows.append({"horizon": h, "full": full, "oos": oos})
    # Verdict: deployable if ANY horizon shows a positive OOS Sharpe with positive OOS
    # total return AND the full-sample Sharpe is also positive (sign-consistent).
    deployable = any(
        (r["oos"]["sharpe"] or 0) > 0 and (r["oos"]["total_return"] or 0) > 0
        and (r["full"]["sharpe"] or 0) > 0
        for r in rows
    )
    return {"name": name, "target_sid": tgt_sid, "data_span": data_span,
            "verdict": "positive_oos_edge" if deployable else "no_deployable_edge",
            "rows": rows}


def run_backtest(targets=DEFAULT_TARGETS, horizons=DEFAULT_HORIZONS, *, urlopen=None,
                 cost_bps: float = 1.5, lo_q: float = 0.33, hi_q: float = 0.67,
                 oos_frac: float = 0.4) -> dict:
    """Fetch the term-ratio inputs + each index target and run the a-priori timing
    backtest. ``cost_bps`` = round-trip futures cost in bps of notional (default 1.5)."""
    sids = {VOL_SID, VOL3M_SID}
    for _, tgt in targets:
        sids.add(tgt)
    dated = fetch_fred_series_history_dated(sorted(sids), urlopen=urlopen)
    cost_frac = cost_bps / 10_000.0
    results = [_grade_one(n, tgt, dated, horizons,
                          cost_frac=cost_frac, lo_q=lo_q, hi_q=hi_q, oos_frac=oos_frac)
               for n, tgt in targets]
    return {"cost_bps": cost_bps, "lo_q": lo_q, "hi_q": hi_q, "oos_frac": oos_frac,
            "horizons": list(horizons), "targets": results}


def _print(out) -> None:
    print("M31 Track A-S5 — vix_term single-asset TIMING backtest "
          "(a-priori short-high/long-low, non-overlapping)")
    print("=" * 84)
    print(f"cost={out['cost_bps']}bps round-trip  tails=({out['lo_q']},{out['hi_q']})  "
          f"oos_frac={out['oos_frac']}  horizons={out['horizons']}")
    for r in out["targets"]:
        print(f"\n  {r['name']} ({r['target_sid']}): verdict={r['verdict']}")
        sp = r.get("data_span")
        if sp:
            print(f"      window {sp['start']} → {sp['end']} (n_aligned={sp['n_aligned']}) "
                  f"— pin this when comparing runs (FRED SP500/DJIA are rolling ~10y)")
        if r.get("reason"):
            print(f"      {r['reason']}")
        for row in r.get("rows", []):
            h, f, o = row["horizon"], row["full"], row["oos"]
            print(f"      H={h:>3}d  FULL sharpe={_fmt(f['sharpe'])} cagr={_fmt(f['cagr'])} "
                  f"maxDD={_fmt(f['max_drawdown'])} hit={_fmt(f['hit_rate'])} "
                  f"exp={_fmt(f['exposure'])} (n={f['n_periods']})")
            print(f"            OOS  sharpe={_fmt(o['sharpe'])} ret={_fmt(o['total_return'])} "
                  f"maxDD={_fmt(o['max_drawdown'])} hit={_fmt(o['hit_rate'])} (n={o['n_periods']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="M31 Track A-S5 — vix_term timing backtest (free FRED)")
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--cost-bps", type=float, default=1.5,
                    help="round-trip futures cost in bps of notional (ES/NQ ~1 bp)")
    ap.add_argument("--lo-q", type=float, default=0.33)
    ap.add_argument("--hi-q", type=float, default=0.67)
    ap.add_argument("--oos-frac", type=float, default=0.4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    out = run_backtest(horizons=horizons, cost_bps=args.cost_bps,
                       lo_q=args.lo_q, hi_q=args.hi_q, oos_frac=args.oos_frac)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    _print(out)
    print("\nNet-of-cost, non-overlapping, a-priori direction (short high term-ratio / long low). "
          "Observe-only research; S4 productionization is Tier-3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
