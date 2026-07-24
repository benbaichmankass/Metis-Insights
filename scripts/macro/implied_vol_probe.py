#!/usr/bin/env python3
"""M31 Track A — implied-volatility signal probe (free CBOE/FRED vol family).

Tests the **implied-volatility / options-derived** input class through the honest
non-overlapping IC gate, on FREE keyless FRED series (the CBOE vol family) — no
Schwab, no soak, no credentials. This class is *forward-looking* by construction
(option-implied), unlike the exhausted backward-looking macro/OHLCV series, and it
is **instrument-aligned**: VIX → the S&P 500 (the bot's MES leg), OVX → oil.

**Three features per (vol_series → target):**
  - ``level_pct``  — trailing percentile of the vol level (contrarian hypothesis:
    high implied vol = fear priced in → forward bounce in the underlying).
  - ``term_ratio`` — VIX3M / VIX (contango > 1 / backwardation < 1); needs a 3-month
    sibling series. Backwardation historically marks stress → forward direction.
  - ``vrp``        — vol level − realized vol of the target (21d, annualised): the
    variance-risk-premium, a documented forward predictor.

**Honest metric.** For each feature we take NON-OVERLAPPING anchors (stride = the
forward horizon H) so the t-stat's N is the honest effective sample (≈ n/H, not the
overlap-inflated n — the entry-11 / M30 trap), and report the **directional**
Spearman IC of the feature vs the target's forward log return, plus its ``ic_t``.
The gate is ``|ic_t| >= t_flag`` at some tradeable horizon (5/10/21/42 trading days).

Off-VM-guarded (reuses ``fred_adapter``'s guard) + injectable ``urlopen`` for tests.
Import-pure: no order path, no DB write, no ``src.*`` runtime import beyond the
keyless FRED adapter.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from src.units.strategies.macro_thesis.fred_adapter import (  # noqa: E402
    fetch_fred_series_history_dated,
)

# The free CBOE/FRED vol family + instrument-aligned price targets (all keyless
# fredgraph.csv series). A series that comes back empty honest-nulls its probe —
# never a crash — so a discontinued id degrades gracefully.
DEFAULT_PROBES = (
    # name,          vol_sid,   target_sid,      feature,       extra_sid (3m sibling)
    ("vix_level",    "VIXCLS",  "SP500",         "level_pct",   None),
    ("vix_vrp",      "VIXCLS",  "SP500",         "vrp",         None),
    ("vix_term",     "VIXCLS",  "SP500",         "term_ratio",  "VXVCLS"),
    ("ovx_level",    "OVXCLS",  "DCOILWTICO",    "level_pct",   None),
    ("ovx_vrp",      "OVXCLS",  "DCOILWTICO",    "vrp",         None),
)

DEFAULT_HORIZONS = (5, 10, 21, 42)   # trading days
PCT_WINDOW = 252                     # 1y trailing percentile window
RV_WINDOW = 21                       # realized-vol trailing window
_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def _to_float(x) -> Optional[float]:
    try:
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def align_dated(a: list, b: list) -> tuple:
    """Inner-join two ``[(date, val), ...]`` series on the date string.

    Returns ``(dates, a_vals, b_vals)`` in ascending date order over the common
    dates only. Non-finite values on either side drop that date."""
    bmap = {d: v for d, v in b}
    dates, av, bv = [], [], []
    for d, v in a:
        if d in bmap:
            va, vb = _to_float(v), _to_float(bmap[d])
            if va is not None and vb is not None:
                dates.append(d)
                av.append(va)
                bv.append(vb)
    return dates, av, bv


def pct_rank_last(window_vals: list) -> Optional[float]:
    """Percentile (0..1) of the LAST value within ``window_vals`` (its own
    trailing window). ``None`` for < 20 points or a degenerate window."""
    if len(window_vals) < 20:
        return None
    last = window_vals[-1]
    lo = sum(1 for v in window_vals if v < last)
    eq = sum(1 for v in window_vals if v == last)
    return (lo + 0.5 * eq) / len(window_vals)


def realized_vol(returns: list) -> Optional[float]:
    """Annualised realized vol (stdev of log returns × √252). ``None`` if < 2."""
    if len(returns) < 2:
        return None
    try:
        return statistics.pstdev(returns) * math.sqrt(_TRADING_DAYS)
    except statistics.StatisticsError:
        return None


def log_return(a: float, b: float) -> Optional[float]:
    """log(b / a); ``None`` on a non-positive operand."""
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    return math.log(b / a)


def build_feature_series(feature: str, vol_vals: list, target_vals: list,
                         *, extra_vals: Optional[list] = None,
                         pct_window: int = PCT_WINDOW, rv_window: int = RV_WINDOW) -> list:
    """Point-in-time feature value at each aligned index (``None`` until warm).

    All inputs are already date-aligned lists (see :func:`align_dated`). Every
    feature at index ``i`` uses ONLY data through ``i`` (no lookahead)."""
    n = len(vol_vals)
    out: list = [None] * n
    if feature == "level_pct":
        for i in range(n):
            lo = max(0, i - pct_window + 1)
            out[i] = pct_rank_last(vol_vals[lo:i + 1])
    elif feature == "term_ratio":
        ev = extra_vals or []
        for i in range(n):
            if i < len(ev) and ev[i] and vol_vals[i]:
                out[i] = ev[i] / vol_vals[i]
    elif feature == "vrp":
        # realized vol of the TARGET over the trailing rv_window, vs the implied level
        rets: list = [None] * n
        for i in range(1, n):
            rets[i] = log_return(target_vals[i - 1], target_vals[i])
        for i in range(n):
            lo = max(1, i - rv_window + 1)
            window = [r for r in rets[lo:i + 1] if r is not None]
            rv = realized_vol(window)
            if rv is not None:
                out[i] = vol_vals[i] - rv * 100.0   # VIX is in vol-points (×100)
    else:
        raise ValueError(f"unknown feature {feature!r}")
    return out


def rank(xs: list) -> list:
    """Average ranks (ties → mean rank)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_ic(pairs: list) -> Optional[dict]:
    """Spearman IC (Pearson on ranks) + t-stat over ``[(x, y), ...]``.

    ``None`` for < 8 pairs or zero variance on either side."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 8:
        return None
    xs = rank([p[0] for p in pairs])
    ys = rank([p[1] for p in pairs])
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx <= 0 or vy <= 0:
        return None
    ic = cov / math.sqrt(vx * vy)
    ic = max(-0.999999, min(0.999999, ic))
    t = ic * math.sqrt((n - 2) / (1 - ic * ic)) if n > 2 else 0.0
    return {"n": n, "ic": ic, "ic_t": t}


def nonoverlap_ic_row(feature_series: list, target_vals: list, horizon: int) -> Optional[dict]:
    """Honest directional IC at ``horizon`` on NON-OVERLAPPING anchors (stride=H)."""
    n = len(feature_series)
    pairs = []
    for i in range(0, n - horizon, max(1, horizon)):
        f = feature_series[i]
        fwd = log_return(target_vals[i], target_vals[i + horizon])
        if f is not None and fwd is not None:
            pairs.append((f, fwd))
    ic = spearman_ic(pairs)
    if ic is None:
        return {"horizon": horizon, "n_nonoverlap": len(pairs), "ic": None, "ic_t": None}
    return {"horizon": horizon, "n_nonoverlap": ic["n"], "ic": ic["ic"], "ic_t": ic["ic_t"]}


def scan_probe(feature_series: list, target_vals: list, horizons, *, t_flag: float = 2.0) -> dict:
    """Run the IC scan across horizons → verdict.

    ``directional_edge`` if any horizon has ``|ic_t| >= t_flag``; else ``no_edge``;
    ``no_data`` if no horizon produced a computable IC."""
    rows = [nonoverlap_ic_row(feature_series, target_vals, h) for h in horizons]
    sig = [r for r in rows if r["ic_t"] is not None and abs(r["ic_t"]) >= t_flag]
    computable = [r for r in rows if r["ic_t"] is not None]
    if not computable:
        verdict = "no_data"
    elif sig:
        verdict = "directional_edge"
    else:
        verdict = "no_edge"
    best = max(computable, key=lambda r: abs(r["ic_t"])) if computable else None
    return {"verdict": verdict, "rows": rows, "best": best,
            "has_edge": bool(sig)}


# ---------------------------------------------------------------------------
# network wrapper (off-VM-guarded via fred_adapter) + CLI
# ---------------------------------------------------------------------------

def run_probes(probes=DEFAULT_PROBES, horizons=DEFAULT_HORIZONS, *,
               urlopen=None, t_flag: float = 2.0) -> dict:
    """Fetch the FRED series each probe needs and grade it. Best-effort per probe."""
    sids = set()
    for _, vol, tgt, _, extra in probes:
        sids.update([vol, tgt] + ([extra] if extra else []))
    dated = fetch_fred_series_history_dated(sorted(sids), urlopen=urlopen)
    results = []
    for name, vol_sid, tgt_sid, feature, extra_sid in probes:
        vol_d, tgt_d = dated.get(vol_sid, []), dated.get(tgt_sid, [])
        if not vol_d or not tgt_d:
            results.append({"name": name, "feature": feature, "verdict": "no_data",
                            "reason": f"empty series (vol={len(vol_d)}, target={len(tgt_d)})"})
            continue
        dates, vol_vals, tgt_vals = align_dated(vol_d, tgt_d)
        extra_vals = None
        if extra_sid:
            # map the 3m sibling onto the vol/target common dates (PIT-aligned)
            emap = {d: v for d, v in dated.get(extra_sid, [])}
            extra_vals = [_to_float(emap.get(d)) for d in dates]
        feat = build_feature_series(feature, vol_vals, tgt_vals, extra_vals=extra_vals)
        scan = scan_probe(feat, tgt_vals, horizons, t_flag=t_flag)
        results.append({"name": name, "feature": feature, "vol_sid": vol_sid,
                        "target_sid": tgt_sid, "n_aligned": len(dates),
                        "verdict": scan["verdict"], "best": scan["best"],
                        "rows": scan["rows"]})
    return {"t_flag": t_flag, "horizons": list(horizons), "probes": results,
            "any_edge": any(r.get("verdict") == "directional_edge" for r in results)}


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def main() -> int:
    ap = argparse.ArgumentParser(description="M31 Track A — implied-vol IC probe")
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    out = run_probes(horizons=horizons, t_flag=args.t_flag)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    print("M31 Track A — implied-volatility HONEST non-overlapping directional IC")
    print("=" * 70)
    print(f"t_flag={args.t_flag}  horizons={list(horizons)} trading-days")
    for r in out["probes"]:
        print(f"\n  {r['name']}  ({r.get('vol_sid','?')}→{r.get('target_sid','?')}, "
              f"{r['feature']}): verdict={r['verdict']}")
        if r.get("reason"):
            print(f"      {r['reason']}")
        for row in r.get("rows", []):
            print(f"      H={row['horizon']:>3}d  n={_fmt(row['n_nonoverlap'])}  "
                  f"ic={_fmt(row['ic'])} (t={_fmt(row['ic_t'])})")
    print(f"\nany_directional_edge={out['any_edge']}")
    print("ic = drift-neutral forward-return Spearman IC on NON-OVERLAPPING anchors "
          "(honest t, N≈n/H).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
