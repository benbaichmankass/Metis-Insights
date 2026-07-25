#!/usr/bin/env python3
"""M33 — calendar-seasonality probe (a NEW free signal family beyond the macro inputs).

The M28/M31/M32 program swept every free *macro* input class (COT · crypto · value ·
implied-vol · credit/rates) and mapped its ceiling: two validated leads, zero deployable
standalone edges. This opens a different, orthogonal free family the program never
touched — **intrinsic calendar structure** of the traded instruments: does the forward
daily return differ by **day-of-week**, **day-of-month bucket**, or **turn-of-month**?

Honest, un-fitted funnel (mirrors the macro sleeves' rigor):
  - **Chronological in-sample / OOS split** (60/40). The best/worst calendar bucket is
    picked on the IN-SAMPLE half ONLY, then evaluated on the untouched OOS half — no
    lookahead in the bucket selection (the classic seasonality data-snoop trap).
  - **Significance** — the OOS mean daily return of the in-sample-selected bucket must
    clear a t-stat bar, not just be positive.
  - **Net of cost** — a calendar timing rule trades in/out around the bucket days, so a
    per-entry futures round-trip cost is subtracted; a gross edge that a ~1 bp cost
    erases is not tradeable.

Verdict per (target, dimension): ``seasonal_edge`` only if the in-sample-best bucket's
OOS mean return is t-significant AND positive net-of-cost; else ``no_seasonal_edge``.
An a-priori expectation of "crowded / mostly decayed" is fine — a clean null still maps
the boundary, and the tradeable-on-THIS-instrument question is genuinely untested.

Keyless FRED daily closes (SP500 / NASDAQ100 / DJIA / WTI oil), off-VM-guarded +
``urlopen``-injectable for tests. Import-pure: no order path, no ``src.*`` beyond the
FRED adapter. Observe-only research.
"""
from __future__ import annotations

import argparse
import datetime as _dt
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

DEFAULT_TARGETS = ("SP500", "NASDAQ100", "DJIA", "DCOILWTICO")
_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _daily_returns(dated: list) -> list:
    """``[(date, close), ...]`` ascending → ``[(date, simple_return), ...]`` (drops the
    first row + any non-positive/invalid close pair)."""
    out: list = []
    prev = None
    for d, v in dated:
        try:
            c = float(v)
        except (TypeError, ValueError):
            prev = None
            continue
        if prev is not None and prev > 0 and c > 0:
            out.append((d, c / prev - 1.0))
        prev = c
    return out


def _dow(date_str: str) -> Optional[int]:
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return _dt.date(y, m, d).weekday()  # 0=Mon .. 6=Sun (explicit args; no clock read)
    except (ValueError, TypeError):
        return None


def _dom(date_str: str) -> Optional[int]:
    try:
        return int(date_str.split("-")[2])
    except (ValueError, IndexError):
        return None


def _bucket(date_str: str, dimension: str) -> Optional[str]:
    """Map a date to its calendar bucket for the given dimension (None if unparseable)."""
    if dimension == "dow":
        w = _dow(date_str)
        return _DOW_NAMES[w] if w is not None and w <= 4 else None  # trading days only
    if dimension == "dom":
        dm = _dom(date_str)
        if dm is None:
            return None
        return "early" if dm <= 10 else ("mid" if dm <= 20 else "late")
    if dimension == "tom":
        dm = _dom(date_str)
        if dm is None:
            return None
        return "turn" if (dm >= 26 or dm <= 4) else "rest"  # month-turn window proxy
    return None


def _mean_t(xs: list) -> tuple:
    """(mean, t-stat, n). t = mean / (stdev/sqrt(n)); None t for n<8 or zero variance."""
    n = len(xs)
    if n < 8:
        return (statistics.fmean(xs) if xs else None, None, n)
    mean = statistics.fmean(xs)
    sd = statistics.pstdev(xs)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else None
    return (mean, t, n)


def grade_dimension(returns: list, dimension: str, *, split_frac: float,
                    cost_frac: float, t_flag: float) -> dict:
    """Pick the best in-sample bucket, evaluate it OOS net-of-cost. Honest: selection is
    in-sample only. ``returns`` = ``[(date, ret), ...]`` ascending."""
    if len(returns) < 40:
        return {"dimension": dimension, "verdict": "no_data",
                "reason": f"too few returns ({len(returns)})"}
    split = int(len(returns) * split_frac)
    is_rows, oos_rows = returns[:split], returns[split:]

    # In-sample mean per bucket → pick the best (highest-mean) bucket.
    is_by: dict = {}
    for d, r in is_rows:
        b = _bucket(d, dimension)
        if b is not None:
            is_by.setdefault(b, []).append(r)
    is_means = {b: statistics.fmean(v) for b, v in is_by.items() if v}
    if not is_means:
        return {"dimension": dimension, "verdict": "no_data", "reason": "no buckets in-sample"}
    best_bucket = max(is_means, key=is_means.get)

    # OOS: the selected bucket's daily returns, net of a per-holding-day round-trip cost.
    oos_ret = [r for d, r in oos_rows if _bucket(d, dimension) == best_bucket]
    gross_mean, t, n = _mean_t(oos_ret)
    net_mean = (gross_mean - cost_frac) if gross_mean is not None else None
    seasonal = bool(t is not None and abs(t) >= t_flag and gross_mean is not None
                    and t > 0 and net_mean is not None and net_mean > 0)
    return {
        "dimension": dimension,
        "best_bucket": best_bucket,
        "is_mean": round(is_means[best_bucket], 6),
        "oos_gross_mean": round(gross_mean, 6) if gross_mean is not None else None,
        "oos_net_mean": round(net_mean, 6) if net_mean is not None else None,
        "oos_t": round(t, 4) if t is not None else None,
        "oos_n": n,
        "verdict": "seasonal_edge" if seasonal else "no_seasonal_edge",
    }


def run_probe(targets=DEFAULT_TARGETS, *, urlopen=None, split_frac: float = 0.6,
              cost_bps: float = 1.0, t_flag: float = 2.0,
              dimensions=("dow", "dom", "tom")) -> dict:
    dated = fetch_fred_series_history_dated(sorted(set(targets)), urlopen=urlopen)
    cost_frac = cost_bps / 10_000.0
    results = []
    for tgt in targets:
        series = dated.get(tgt, [])
        if not series:
            results.append({"target": tgt, "verdict": "no_data", "rows": []})
            continue
        rets = _daily_returns(series)
        rows = [grade_dimension(rets, dim, split_frac=split_frac, cost_frac=cost_frac,
                                t_flag=t_flag) for dim in dimensions]
        any_edge = any(r.get("verdict") == "seasonal_edge" for r in rows)
        results.append({"target": tgt, "n_returns": len(rets),
                        "verdict": "seasonal_edge" if any_edge else "no_seasonal_edge",
                        "rows": rows})
    return {"cost_bps": cost_bps, "split_frac": split_frac, "t_flag": t_flag,
            "dimensions": list(dimensions), "targets": results}


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.6f}" if isinstance(v, float) else str(v))


def _print(out) -> None:
    print("M33 — calendar-seasonality probe (day-of-week / day-of-month / turn-of-month)")
    print("=" * 78)
    print(f"cost={out['cost_bps']}bps  split={out['split_frac']}  t_flag={out['t_flag']}  "
          f"dims={out['dimensions']}  (best bucket picked IN-SAMPLE, evaluated OOS)")
    for r in out["targets"]:
        print(f"\n  {r['target']}: verdict={r['verdict']}  (n_returns={r.get('n_returns','—')})")
        for row in r.get("rows", []):
            if row["verdict"] == "no_data":
                print(f"      {row['dimension']}: no_data ({row.get('reason','')})")
                continue
            print(f"      {row['dimension']:>4}  best={row['best_bucket']:>5}  "
                  f"OOS gross={_fmt(row['oos_gross_mean'])} net={_fmt(row['oos_net_mean'])} "
                  f"t={_fmt(row['oos_t'])} n={row['oos_n']}  → {row['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M33 — calendar-seasonality probe (free FRED)")
    ap.add_argument("--cost-bps", type=float, default=1.0)
    ap.add_argument("--split-frac", type=float, default=0.6)
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = run_probe(cost_bps=args.cost_bps, split_frac=args.split_frac, t_flag=args.t_flag)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    _print(out)
    print("\nHonest: bucket selected on the in-sample half only, evaluated on the untouched "
          "OOS half, net of cost. Observe-only research.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
