#!/usr/bin/env python3
"""M35 — crypto OI/basis MOMENTUM probe (the untested directional sign).

The signal-research program declared "crypto exhausted," but that verdict was
earned specifically on **funding** (entries 9/10/11: impulse, level, level×OI,
dense-horizon IC). **Open-interest and perp-basis were only ever graded once —
in entry 3, under the weakest construction (trailing-percentile, CONTRARIAN**
`higher_is_cheaper` — high OI/basis = "rich" = fade). Two things were never
tested on OI/basis: (a) a D1 *momentum* transform (rate-of-change / trailing
z-score of the level), and (b) the *directional* (trend-continuation) sign —
rising OI/basis → LONG, the opposite of the refuted contrarian fade. The
`crypto_signals_data.py` note even flags OI as the "weakest directional claim,"
which is a hypothesis to TEST, not assume.

This probe builds OI and basis MOMENTUM features (ROC + trailing z-score) and
grades them through the honest funnel the whole program uses — non-overlapping
directional IC (stride = horizon, no overlap inflation) → OOS split + cost-aware
conviction spread. The IC's SIGN is read empirically (momentum vs contrarian is
whatever the data says), and significance + OOS + net-of-cost is the guard
against a false positive. If every cell is insignificant, that closes the
crypto record honestly (OI/basis momentum tested, not just assumed exhausted).

**Bybit is geo-blocked** from the sandbox + GitHub runners, so the live grade
runs on the **trainer VM** (the reliable Bybit path, as the crypto sleeve
backfill did). Off-VM guarded (`ICT_OFFVM_BUILD_HOST=1`); network injected via
fetcher callables in tests. Import-pure; observe-only Tier-1 research — writes
nothing, touches no `src`/`config`/order path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Callable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import implied_vol_probe as iv  # noqa: E402  (spearman_ic / log_return / conviction_s3_row)

_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_HORIZONS = (1, 3, 7, 14)     # crypto OI/basis mean-revert fast → short/medium
DEFAULT_WINDOWS = (7, 14)            # trailing window for ROC + z-score


# ---------------------------------------------------------------------------
# feature construction (point-in-time: index i uses only data through i)
# ---------------------------------------------------------------------------

def _roc(values: list, window: int) -> list:
    """Trailing rate-of-change over ``window`` bars. ``None`` until warm / on a
    non-positive base."""
    n = len(values)
    out: list = [None] * n
    for i in range(window, n):
        base = values[i - window]
        if base is not None and values[i] is not None and base != 0:
            out[i] = (values[i] - base) / abs(base)
    return out


def _zscore(values: list, window: int) -> list:
    """Trailing z-score of the level over ``window`` bars (point-in-time)."""
    n = len(values)
    out: list = [None] * n
    for i in range(window, n):
        w = [v for v in values[i - window + 1:i + 1] if v is not None]
        if len(w) < max(3, window // 2):
            continue
        mean = sum(w) / len(w)
        var = sum((v - mean) ** 2 for v in w) / (len(w) - 1) if len(w) > 1 else 0.0
        if var <= 1e-24 or values[i] is None:
            continue
        out[i] = (values[i] - mean) / math.sqrt(var)
    return out


def build_features(oi_vals: list, basis_vals: list, *, window: int) -> dict:
    """The four momentum features for one symbol, aligned to the shared grid."""
    return {
        f"oi_roc{window}": _roc(oi_vals, window),
        f"oi_z{window}": _zscore(oi_vals, window),
        f"basis_roc{window}": _roc(basis_vals, window),
        f"basis_z{window}": _zscore(basis_vals, window),
    }


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------

def align_days(*dated_series) -> tuple:
    """Align N ``[(day, val), ...]`` series onto their common days. Returns
    ``(days, [values_per_series...])`` with every series ordered by day."""
    maps = [{d: iv._to_float(v) for d, v in (s or [])} for s in dated_series]
    common = None
    for m in maps:
        keys = {d for d, v in m.items() if v is not None}
        common = keys if common is None else (common & keys)
    days = sorted(common or [])
    return days, [[m[d] for d in days] for m in maps]


# ---------------------------------------------------------------------------
# grading — honest non-overlapping directional IC (pooled across symbols) + S3
# ---------------------------------------------------------------------------

def _pooled_anchors(per_symbol_feat: list, per_symbol_ret: list, horizon: int) -> list:
    """Non-overlapping ``(feature, fwd_return)`` anchors pooled across symbols
    (each symbol strided independently at stride=horizon)."""
    out = []
    for feat, ret in zip(per_symbol_feat, per_symbol_ret):
        n = min(len(feat), len(ret))
        for i in range(0, n - horizon, max(1, horizon)):
            f = feat[i]
            fwd = iv.log_return(ret[i], ret[i + horizon])
            if f is not None and fwd is not None:
                out.append((f, fwd))
    return out


def grade_feature(per_symbol_feat: list, per_symbol_ret: list, horizons, *,
                  split_frac: float = 0.6, fee_frac: float = 0.001,
                  t_flag: float = 2.0) -> dict:
    """One feature across horizons: pooled non-overlapping IC (S2) + OOS
    conviction spread (S3). ``directional_edge`` iff some horizon has a
    significant honest IC AND pays OOS net-of-cost with a sign-consistent OOS IC."""
    rows = []
    for h in horizons:
        anchors = _pooled_anchors(per_symbol_feat, per_symbol_ret, h)
        ic = iv.spearman_ic(anchors)
        row = {"horizon": h, "n": len(anchors), "ic": None, "ic_t": None,
               "oos_ic": None, "oos_ic_t": None, "net_spread": None, "pays_oos": False}
        if ic is not None:
            row["ic"], row["ic_t"] = ic["ic"], ic["ic_t"]
            # S3 on the pooled anchors (already time-order-ish by symbol blocks;
            # conviction_s3_row splits IS/OOS on the pooled sequence).
            s3 = _s3_on_anchors(anchors, split_frac=split_frac, fee_frac=fee_frac, t_flag=t_flag)
            row.update(s3)
        rows.append(row)
    passes = any(r["pays_oos"] and r["ic_t"] is not None and abs(r["ic_t"]) >= t_flag
                 for r in rows)
    return {"verdict": "directional_edge" if passes else "no_directional_edge", "rows": rows}


def _s3_on_anchors(anchors: list, *, split_frac: float, fee_frac: float,
                   t_flag: float, q: float = 0.34) -> dict:
    """OOS split + cost-aware long/short conviction spread on pre-built anchors
    (mirrors implied_vol_probe.conviction_s3_row, which takes series not anchors)."""
    out = {"oos_ic": None, "oos_ic_t": None, "net_spread": None, "pays_oos": False}
    n = len(anchors)
    if n < 16:
        return out
    cut = int(n * split_frac)
    is_a, oos_a = anchors[:cut], anchors[cut:]
    if len(is_a) < 8 or len(oos_a) < 8:
        return out
    is_ic, oos_ic = iv.spearman_ic(is_a), iv.spearman_ic(oos_a)
    if is_ic is None or oos_ic is None:
        return out
    out["oos_ic"], out["oos_ic_t"] = oos_ic["ic"], oos_ic["ic_t"]
    orient = -1 if is_ic["ic"] < 0 else 1
    oos_sorted = sorted(oos_a, key=lambda p: p[0])
    k = max(1, int(len(oos_sorted) * q))
    low = sum(p[1] for p in oos_sorted[:k]) / k
    high = sum(p[1] for p in oos_sorted[-k:]) / k
    long_m, short_m = (low, high) if orient < 0 else (high, low)
    net = (long_m - short_m) - 2.0 * fee_frac
    out["net_spread"] = net
    same_sign = (oos_ic["ic"] < 0) == (is_ic["ic"] < 0)
    out["pays_oos"] = bool(net > 0 and same_sign and abs(oos_ic["ic_t"]) >= t_flag)
    return out


# ---------------------------------------------------------------------------
# data pull (Bybit, off-VM/trainer only) → per-symbol aligned OI/basis/return
# ---------------------------------------------------------------------------

def build_symbol_series(symbol: str, *, oi_fetch: Callable, kline_fetch: Callable,
                        oi_interval: str = "1d") -> dict:
    """Pull + align one symbol's daily OI, basis, and perp-return series.
    ``oi_fetch(symbol, interval_time=)`` → ``[(ms, oi)]``;
    ``kline_fetch(symbol, spot=bool)`` → ``[(ms, close)]``. Returns
    ``{days, oi, basis, ret}`` on the common day grid (ret = perp close)."""
    import crypto_signals_data as cs
    oi_daily = cs.resample_daily_last(oi_fetch(symbol, interval_time=oi_interval) or [])
    perp_daily = cs.resample_daily_last(kline_fetch(symbol, spot=False) or [])
    spot_daily = cs.resample_daily_last(kline_fetch(symbol, spot=True) or [])
    basis_daily = cs.compute_basis(perp_daily, spot_daily)
    days, (oi, basis, ret) = align_days(oi_daily, basis_daily, perp_daily)
    return {"days": days, "oi": oi, "basis": basis, "ret": ret, "n_days": len(days)}


def run_probe(symbols=DEFAULT_SYMBOLS, *, oi_fetch: Optional[Callable] = None,
              kline_fetch: Optional[Callable] = None, horizons=DEFAULT_HORIZONS,
              windows=DEFAULT_WINDOWS, split_frac: float = 0.6, cost_bps: float = 10.0,
              t_flag: float = 2.0) -> dict:
    """Grade OI/basis momentum features across symbols. Fetchers default to the
    guarded live Bybit callables (off-VM only); inject in tests."""
    if oi_fetch is None or kline_fetch is None:
        import crypto_signals_data as cs
        if oi_fetch is None:
            oi_fetch = cs.fetch_open_interest
        if kline_fetch is None:
            def kline_fetch(sym, spot=False):  # noqa: E306
                # fetch_kline_close selects the book via category=, not spot=
                return cs.fetch_kline_close(sym, category="spot" if spot else "linear")
    series = {}
    for sym in symbols:
        try:
            series[sym] = build_symbol_series(sym, oi_fetch=oi_fetch, kline_fetch=kline_fetch)
        except Exception as exc:  # best-effort per symbol
            series[sym] = {"days": [], "oi": [], "basis": [], "ret": [], "n_days": 0,
                           "error": str(exc)[:200]}
    fee_frac = cost_bps / 10000.0
    results = []
    for window in windows:
        feats_by_name: dict = {}
        rets: list = []
        for sym in symbols:
            s = series[sym]
            if s["n_days"] < window + max(horizons) + 20:
                continue
            f = build_features(s["oi"], s["basis"], window=window)
            for name, vals in f.items():
                feats_by_name.setdefault(name, []).append(vals)
            rets.append(s["ret"])
        for name, per_symbol_feat in feats_by_name.items():
            graded = grade_feature(per_symbol_feat, rets, horizons,
                                   split_frac=split_frac, fee_frac=fee_frac, t_flag=t_flag)
            results.append({"feature": name, "window": window, **graded})
    any_edge = any(r["verdict"] == "directional_edge" for r in results)
    return {"cost_bps": cost_bps, "t_flag": t_flag, "horizons": list(horizons),
            "windows": list(windows), "symbols": list(symbols),
            "coverage": {s: series[s]["n_days"] for s in symbols},
            "verdict": "directional_edge" if any_edge else "no_directional_edge",
            "features": results}


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _f(v, nd=4) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _print(out) -> None:
    print("M35 — crypto OI/basis MOMENTUM probe (directional sign; honest non-overlapping IC → OOS)")
    print("=" * 82)
    print(f"symbols={out['symbols']}  horizons={out['horizons']}  windows={out['windows']}  "
          f"cost={out['cost_bps']}bps  t_flag={out['t_flag']}")
    print(f"coverage (aligned days/symbol): {out['coverage']}\n")
    print("  Momentum = rising OI/basis → the feature; the IC SIGN is read empirically")
    print("  (positive = trend-continuation, negative = contrarian). An edge needs a")
    print("  significant honest IC AND an OOS conviction spread that pays net of cost.\n")
    for r in out["features"]:
        print(f"  {r['feature']} (w={r['window']}): {r['verdict']}")
        for row in r["rows"]:
            print(f"       H={row['horizon']:>3}d  n={row['n']:>4}  "
                  f"IC={_f(row['ic'])} t={_f(row['ic_t'],2)}  "
                  f"oosIC={_f(row['oos_ic'])} t={_f(row['oos_ic_t'],2)}  "
                  f"net={_f(row['net_spread'])}  pays_oos={row['pays_oos']}")
        print()
    print(f"OVERALL: {out['verdict']}")
    print("Observe-only: OI/basis momentum was never graded directionally (entry 3 tested only "
          "contrarian level). This closes that cell with a graded result, not an assumption.")


def main() -> int:
    ap = argparse.ArgumentParser(description="M35 crypto OI/basis momentum probe")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--split-frac", type=float, default=0.6)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = run_probe(symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
                    split_frac=args.split_frac, cost_bps=args.cost_bps, t_flag=args.t_flag)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        _print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
