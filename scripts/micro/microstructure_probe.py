#!/usr/bin/env python3
# wiring: manual-only - an M30 S0 FEASIBILITY probe. It answers 'is there
#     signal in this input class at all', which is a question asked once per
#     research program, not on a cadence. Scheduling it would re-measure a
#     settled feasibility verdict forever; its OUTPUT belongs in the research
#     ledger, not in a timer.
"""M30 S0 — microstructure feasibility probe (higher-frequency OHLCV off Bybit klines).

The M28 signal-research program exhausted the free/cheap **daily-bar macro** input
class (COT, crypto funding/OI, value cross-section — 15 graded constructions, zero
survivors; see ``docs/research/M28-signal-research-ledger.md``). The honest next
step is a **different input class**: higher-frequency price/volume structure, which
carries short-horizon predictive content a one-obs/day macro series cannot.

**This is S0 — FEASIBILITY, not a graded verdict.** It answers two questions before
any S1/S2/S3 build is justified:

  1. **Data:** can we accrue a clean *intraday* OHLCV panel off the already-wired
     **keyless** Bybit v5 kline API (bytick-first, so a US runner reaches it)? How
     dense, how much history, any gaps?
  2. **Signal plausibility:** do candidate microstructure features have a
     **non-degenerate** distribution AND a *first-look* information coefficient
     (IC) vs the next-bar return that is interesting enough to justify the full
     honest funnel? The first-look IC is an in-sample **preview**, explicitly NOT
     the honest non-overlapping S2 verdict (that's the next build if S0 passes).

**Honest scoping of "microstructure" on a free feed.** True tape/book microstructure
(order-flow imbalance from the trade tape, order-book depth imbalance) has **no free
historical feed** — Bybit's ``recent-trade`` is a short rolling window and the
order-book endpoint is a snapshot with no history. So the tractable free intraday
signal is **intrabar OHLCV shape** at 5m/15m/1h: realized-vol term structure, short-
horizon return autocorrelation, intrabar close-in-range position (a buying-pressure
proxy), and volume z-score. That is a *modest* information step-up over daily bars
(finer resolution + intrabar shape), NOT full order-flow microstructure — an S0
finding worth stating plainly rather than overclaiming.

Pure feature functions (no network, fully testable) + an off-VM-guarded Bybit fetch
that reuses ``crypto_signals_data``'s geo-block-aware base fallback. Tier-1 research,
observe-only: no order path, no ``src``/``config`` write, no DB write.
"""
from __future__ import annotations

import argparse
import json as _json
import math
import os
import statistics
import sys
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # repo root
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "macro"))  # crypto_signals_data

import crypto_signals_data as csd  # noqa: E402  (reuse the Bybit base/urlopen layer)

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_INTERVAL = "60"           # 1h bars — enough history in one 1000-bar page (~41d)
DEFAULT_HORIZONS = (1, 2, 4, 8)   # forward bars to preview IC against
DEFAULT_VOL_SHORT = 6
DEFAULT_VOL_LONG = 24
DEFAULT_MIN_BARS = 200            # feasibility floor for a first-look IC
_MIN_DENOM = 1e-12

# feature names emitted per bar (all PIT — computed from data up to & including bar i)
FEATURES = ("realized_vol", "rv_term_structure", "ret_autocorr_lag1",
            "range_position", "volume_zscore")


# ---------------------------------------------------------------------------
# pure OHLCV parse (Bybit v5 kline row: [start, open, high, low, close, vol, turnover])
# ---------------------------------------------------------------------------


def parse_kline_ohlcv(payload) -> list:
    """``[{t, o, h, l, c, v}, ...]`` ascending from a v5 kline body. Skips a row
    with any non-finite OHLC. ``t`` is epoch-ms (int)."""
    out = []
    for r in csd._result_list(payload):
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        t = csd._to_float(r[0])
        o, h, lo, c = (csd._to_float(r[1]), csd._to_float(r[2]),
                       csd._to_float(r[3]), csd._to_float(r[4]))
        v = csd._to_float(r[5])
        if None in (t, o, h, lo, c) or v is None:
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": lo, "c": c, "v": v})
    out.sort(key=lambda x: x["t"])
    return out


# ---------------------------------------------------------------------------
# pure feature functions
# ---------------------------------------------------------------------------


def log_returns(closes) -> list:
    """Bar-to-bar log returns ``[r_1, ..., r_{n-1}]`` (len n-1). A non-positive
    close yields ``0.0`` for that step (kept aligned, never dropped silently)."""
    out = []
    for a, b in zip(closes[:-1], closes[1:]):
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
        else:
            out.append(0.0)
    return out


def realized_vol(rets, window) -> Optional[float]:
    """Population stdev of the trailing ``window`` returns (``None`` if < 2 avail)."""
    w = list(rets)[-window:] if window else list(rets)
    if len(w) < 2:
        return None
    return statistics.pstdev(w)


def rv_term_structure(rets, short_w, long_w) -> Optional[float]:
    """Short-window RV ÷ long-window RV — a vol-expansion ratio (>1 = expanding).
    ``None`` when either leg is unavailable or the long leg is ~0."""
    sv, lv = realized_vol(rets, short_w), realized_vol(rets, long_w)
    if sv is None or lv is None or lv < _MIN_DENOM:
        return None
    return sv / lv


def ret_autocorr_lag1(rets, window) -> Optional[float]:
    """Lag-1 autocorrelation of the trailing ``window`` returns. ``None`` when
    < 3 points or the series is constant (zero variance)."""
    w = list(rets)[-window:] if window else list(rets)
    if len(w) < 3:
        return None
    x0, x1 = w[:-1], w[1:]
    m = statistics.fmean(w)
    num = sum((a - m) * (b - m) for a, b in zip(x0, x1))
    den = sum((a - m) ** 2 for a in w)
    if den < _MIN_DENOM:
        return None
    return num / den


def range_position(bar) -> Optional[float]:
    """Where the close sits in the bar's high–low range, ``(c - l)/(h - l)`` ∈ [0,1]
    — a cheap intrabar buying-pressure proxy (1 = closed on the high). ``None`` on a
    zero-range bar. PIT: uses only bar ``i`` itself."""
    h, lo, c = bar.get("h"), bar.get("l"), bar.get("c")
    if h is None or lo is None or c is None or (h - lo) < _MIN_DENOM:
        return None
    return (c - lo) / (h - lo)


def volume_zscore(vols, window) -> Optional[float]:
    """Z-score of the latest volume vs the trailing ``window`` (inclusive). ``None``
    when < 2 points or zero dispersion."""
    w = list(vols)[-window:] if window else list(vols)
    if len(w) < 2:
        return None
    sd = statistics.pstdev(w)
    if sd < _MIN_DENOM:
        return None
    return (w[-1] - statistics.fmean(w)) / sd


def forward_return(closes, i, horizon) -> Optional[float]:
    """Log return from bar ``i`` to bar ``i+horizon`` (the target). ``None`` if the
    forward bar is out of range or a close is non-positive."""
    j = i + horizon
    if j >= len(closes) or i < 0:
        return None
    a, b = closes[i], closes[j]
    if not (a and b and a > 0 and b > 0):
        return None
    return math.log(b / a)


def pearson_ic(pairs) -> Optional[dict]:
    """First-look IC of ``[(feature, fwd_return), ...]`` — Pearson r + a two-sided
    t-stat ``r*sqrt((n-2)/(1-r^2))``. ``None`` when < 5 pairs or zero variance.
    IN-SAMPLE, OVERLAPPING — a *preview*, NOT the honest non-overlapping S2 read."""
    xs = [p[0] for p in pairs if p[0] is not None and p[1] is not None]
    ys = [p[1] for p in pairs if p[0] is not None and p[1] is not None]
    n = len(xs)
    if n < 5:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx < _MIN_DENOM or syy < _MIN_DENOM:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    r = sxy / math.sqrt(sxx * syy)
    r = max(-1.0, min(1.0, r))
    t = r * math.sqrt((n - 2) / (1 - r ** 2)) if abs(r) < 1 - _MIN_DENOM else float("inf")
    return {"n": n, "ic": r, "ic_t": t}


# ---------------------------------------------------------------------------
# S2 — honest NON-OVERLAPPING IC (the real verdict, not the S0 preview)
# ---------------------------------------------------------------------------


def rank(xs) -> list:
    """Average ranks (ties → mean rank), 0-based — the basis for a Spearman IC."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_ic(pairs) -> Optional[dict]:
    """Rank IC — Pearson on the ranks of ``[(x, y), ...]`` — more robust to the
    fat tails of intraday returns than raw Pearson. Same ``{n, ic, ic_t}`` shape;
    ``None`` when < 5 usable pairs or degenerate."""
    xs = [p[0] for p in pairs if p[0] is not None and p[1] is not None]
    ys = [p[1] for p in pairs if p[0] is not None and p[1] is not None]
    if len(xs) < 5:
        return None
    return pearson_ic(list(zip(rank(xs), rank(ys))))


def s2_nonoverlap_row(bars, feature, horizon, *, vol_short=DEFAULT_VOL_SHORT,
                      vol_long=DEFAULT_VOL_LONG, method="spearman") -> dict:
    """Honest **non-overlapping** IC for one feature at one horizon.

    Subsamples bars at **stride = horizon** so the forward-H-bar windows are DISJOINT
    (no overlap → the t-stat's N is the honest effective sample, not the inflated
    overlapping count). Then splits two ICs to expose a magnitude-vs-direction artifact:

      - **ic_signed_demeaned** — IC vs the forward return with its sample mean removed
        (drift-neutral, the *directional* edge — this is what a directional signal needs).
      - **ic_magnitude** — IC vs |forward return| (does the feature predict SIZE of the
        move regardless of sign? realized_vol trivially might — that's vol-forecasting,
        NOT tradeable directional alpha).

    A magnitude feature (like realized_vol) that shows a big ``ic_magnitude`` but a
    ~0 ``ic_signed_demeaned`` is the **directional-regime artifact** the S0 caveat warned
    of — real for vol-targeting, useless as a directional entry signal."""
    panel = build_feature_panel(bars, vol_short=vol_short, vol_long=vol_long)
    closes = [b["c"] for b in bars]
    stride = max(1, int(horizon))
    feat, fwd = [], []
    for i in range(0, len(bars), stride):
        f = panel[i][feature]
        r = forward_return(closes, i, horizon)
        if f is not None and r is not None:
            feat.append(f)
            fwd.append(r)
    n = len(feat)
    row = {"horizon": horizon, "n_nonoverlap": n, "feature": feature,
           "ic_signed_demeaned": None, "ic_signed_t": None,
           "ic_magnitude": None, "ic_magnitude_t": None}
    if n < 5:
        return row
    mfwd = statistics.fmean(fwd)
    fwd_dm = [r - mfwd for r in fwd]          # drift-neutral directional target
    fwd_abs = [abs(r) for r in fwd]           # magnitude target
    ic_fn = spearman_ic if method == "spearman" else pearson_ic
    signed = ic_fn(list(zip(feat, fwd_dm)))
    magnitude = ic_fn(list(zip(feat, fwd_abs)))
    if signed:
        row["ic_signed_demeaned"] = round(signed["ic"], 4)
        row["ic_signed_t"] = round(signed["ic_t"], 3)
    if magnitude:
        row["ic_magnitude"] = round(magnitude["ic"], 4)
        row["ic_magnitude_t"] = round(magnitude["ic_t"], 3)
    return row


def s2_scan(bars, feature, horizons, *, vol_short=DEFAULT_VOL_SHORT,
            vol_long=DEFAULT_VOL_LONG, method="spearman", t_flag=2.0) -> dict:
    """Non-overlapping S2 verdict for one feature across horizons. Directional edge is
    real only when some horizon clears ``|ic_signed_t| >= t_flag`` on the drift-neutral
    target. Also reports whether the feature is magnitude-predictive-only (the artifact)."""
    rows = [s2_nonoverlap_row(bars, feature, h, vol_short=vol_short, vol_long=vol_long,
                              method=method) for h in horizons]
    directional = [r for r in rows if r["ic_signed_t"] is not None and abs(r["ic_signed_t"]) >= t_flag]
    magnitude_only = [
        r for r in rows
        if (r["ic_magnitude_t"] is not None and abs(r["ic_magnitude_t"]) >= t_flag)
        and (r["ic_signed_t"] is None or abs(r["ic_signed_t"]) < t_flag)
    ]
    best_dir = max(directional, key=lambda r: abs(r["ic_signed_t"]), default=None)
    return {
        "feature": feature, "method": method, "t_flag": t_flag, "rows": rows,
        "has_directional_edge": bool(directional),
        "magnitude_only_horizons": [r["horizon"] for r in magnitude_only],
        "best_directional": best_dir,
        "verdict": (
            "directional_edge" if directional
            else "magnitude_only_no_direction" if magnitude_only
            else "no_edge" if any(r["n_nonoverlap"] >= 5 for r in rows)
            else "no_data"
        ),
    }


# ---------------------------------------------------------------------------
# panel + S0 report
# ---------------------------------------------------------------------------


def build_feature_panel(bars, *, vol_short=DEFAULT_VOL_SHORT, vol_long=DEFAULT_VOL_LONG) -> list:
    """One row per bar with the PIT feature block. Each feature at bar ``i`` uses only
    bars ``0..i`` (leakage-safe); the forward-return target is added later by the report."""
    closes = [b["c"] for b in bars]
    vols = [b["v"] for b in bars]
    rets = log_returns(closes)            # rets[k] is the return INTO bar k+1
    rows = []
    for i in range(len(bars)):
        rets_upto = rets[:i]              # returns strictly up to (not including) bar i's move
        rows.append({
            "t": bars[i]["t"],
            "realized_vol": realized_vol(rets_upto, vol_long),
            "rv_term_structure": rv_term_structure(rets_upto, vol_short, vol_long),
            "ret_autocorr_lag1": ret_autocorr_lag1(rets_upto, vol_long),
            "range_position": range_position(bars[i]),
            "volume_zscore": volume_zscore(vols[:i + 1], vol_long),
        })
    return rows


def s0_report(symbol, bars, *, horizons=DEFAULT_HORIZONS, vol_short=DEFAULT_VOL_SHORT,
              vol_long=DEFAULT_VOL_LONG, min_bars=DEFAULT_MIN_BARS) -> dict:
    """S0 feasibility scorecard for one symbol: data density + per-feature coverage,
    dispersion, degeneracy, and a first-look IC vs each forward horizon."""
    n = len(bars)
    span_days = round((bars[-1]["t"] - bars[0]["t"]) / 86_400_000.0, 2) if n >= 2 else 0.0
    panel = build_feature_panel(bars, vol_short=vol_short, vol_long=vol_long)
    closes = [b["c"] for b in bars]

    feats = {}
    for f in FEATURES:
        vals = [row[f] for row in panel if row[f] is not None]
        coverage = round(len(vals) / n, 4) if n else 0.0
        std = statistics.pstdev(vals) if len(vals) >= 2 else 0.0
        degenerate = len(vals) < 5 or std < _MIN_DENOM
        by_h = {}
        for h in horizons:
            pairs = [(row[f], forward_return(closes, i, h)) for i, row in enumerate(panel)]
            ic = pearson_ic(pairs)
            by_h[str(h)] = ic  # may be None
        feats[f] = {
            "coverage": coverage,
            "std": std,
            "degenerate": degenerate,
            "first_look_ic": by_h,
        }

    data_ok = n >= min_bars and span_days > 0
    non_degenerate = [f for f, d in feats.items() if not d["degenerate"]]
    # a first-look "interesting" flag: any non-degenerate feature with |ic_t| >= 2 at any horizon
    interesting = []
    for f in non_degenerate:
        for h, ic in feats[f]["first_look_ic"].items():
            if ic and abs(ic["ic_t"]) >= 2.0:
                interesting.append({"feature": f, "horizon": int(h),
                                    "ic": round(ic["ic"], 4), "ic_t": round(ic["ic_t"], 3)})
    return {
        "symbol": symbol,
        "n_bars": n,
        "span_days": span_days,
        "data_ok": data_ok,
        "non_degenerate_features": non_degenerate,
        "interesting_first_look": interesting,
        "features": feats,
        "note": ("S0 FEASIBILITY only. first_look_ic is IN-SAMPLE + OVERLAPPING "
                 "(a preview, NOT the honest non-overlapping S2 verdict). Free Bybit "
                 "klines give intrabar OHLCV shape, NOT tape/book order-flow."),
    }


# ---------------------------------------------------------------------------
# off-VM Bybit fetch (reuses the geo-block-aware base fallback)
# ---------------------------------------------------------------------------


def fetch_ohlcv(symbol, *, category="linear", interval=DEFAULT_INTERVAL, limit=1000,
                base=None, urlopen=None, timeout=30.0) -> list:
    """Intraday OHLCV bars for one symbol. Reuses ``crypto_signals_data``'s bytick-first
    base list + off-VM guard. Best-effort → ``[]``."""
    import urllib.parse
    urlopen = csd._resolve_urlopen(urlopen)
    q = urllib.parse.urlencode({"category": category, "symbol": symbol,
                                "interval": interval, "limit": int(limit)})
    for b in csd._bases(base):
        out = parse_kline_ohlcv(csd._get_json(f"{b}/v5/market/kline?{q}", urlopen, timeout))
        if out:
            return out
    return []


def _f(v) -> str:
    """Format a possibly-None number for the console (None → em-dash)."""
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def _run_s0(args, symbols, horizons) -> int:
    reports = []
    for sym in symbols:
        bars = fetch_ohlcv(sym, interval=args.interval, limit=args.limit)
        if not bars:
            print(f"{sym}: no bars (fetch failed / empty)")
            reports.append({"symbol": sym, "n_bars": 0, "data_ok": False,
                            "error": "no_bars", "features": {}})
            continue
        rep = s0_report(sym, bars, horizons=horizons, vol_short=args.vol_short,
                        vol_long=args.vol_long, min_bars=args.min_bars)
        reports.append(rep)

    summary = {
        "probe": "m30_microstructure_s0", "interval": args.interval,
        "horizons": list(horizons), "reports": reports,
        "any_data_ok": any(r.get("data_ok") for r in reports),
        "any_interesting": any(r.get("interesting_first_look") for r in reports),
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print("M30 S0 microstructure feasibility probe")
    print("=" * 40)
    for r in reports:
        if not r.get("data_ok"):
            print(f"  {r['symbol']:>8}: n={r.get('n_bars', 0)}  DATA NOT OK ({r.get('error', 'thin')})")
            continue
        nd = ",".join(r["non_degenerate_features"]) or "(none)"
        it = r["interesting_first_look"]
        print(f"  {r['symbol']:>8}: n={r['n_bars']} span={r['span_days']}d  "
              f"non-degenerate=[{nd}]  interesting={len(it)}")
        for hit in it:
            print(f"           · {hit['feature']}@{hit['horizon']}bar  ic={hit['ic']}  t={hit['ic_t']}")
    print(f"\nany_data_ok={summary['any_data_ok']}  any_interesting={summary['any_interesting']}")
    print("S0 = feasibility only; first-look IC is an in-sample preview, not the S2 verdict.")
    return 0 if summary["any_data_ok"] else 1


def _run_s2(args, symbols, horizons) -> int:
    features = [f.strip() for f in (args.feature or ",".join(FEATURES)).split(",") if f.strip()]
    results = []
    for sym in symbols:
        bars = fetch_ohlcv(sym, interval=args.interval, limit=args.limit)
        if not bars:
            print(f"{sym}: no bars (fetch failed / empty)")
            results.append({"symbol": sym, "n_bars": 0, "error": "no_bars", "scans": []})
            continue
        scans = [s2_scan(bars, f, horizons, vol_short=args.vol_short, vol_long=args.vol_long,
                         method=args.method, t_flag=args.t_flag) for f in features]
        results.append({"symbol": sym, "n_bars": len(bars), "scans": scans})

    summary = {
        "probe": "m30_microstructure_s2_nonoverlapping", "interval": args.interval,
        "horizons": list(horizons), "method": args.method, "t_flag": args.t_flag,
        "features": features, "results": results,
        "any_directional_edge": any(sc.get("has_directional_edge")
                                     for r in results for sc in r.get("scans", [])),
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print("M30 S2 microstructure — HONEST non-overlapping IC (the real verdict)")
    print("=" * 68)
    print(f"method={args.method}  t_flag={args.t_flag}  horizons={list(horizons)}")
    for r in results:
        if r.get("error"):
            print(f"  {r['symbol']:>8}: DATA NOT OK ({r['error']})")
            continue
        print(f"  {r['symbol']:>8}: n_bars={r['n_bars']}")
        for sc in r["scans"]:
            print(f"      {sc['feature']:>18}: verdict={sc['verdict']}")
            for row in sc["rows"]:
                print(f"          H={row['horizon']:>3}bar n={row['n_nonoverlap']:>4}  "
                      f"ic_dir={_f(row['ic_signed_demeaned'])} (t={_f(row['ic_signed_t'])})  "
                      f"ic_mag={_f(row['ic_magnitude'])} (t={_f(row['ic_magnitude_t'])})")
    print(f"\nany_directional_edge={summary['any_directional_edge']}")
    print("ic_dir = drift-neutral DIRECTIONAL IC (non-overlapping → honest t); "
          "ic_mag = magnitude IC. Big mag + ~0 dir = the directional-regime artifact.")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="M30 microstructure probe (Bybit klines) — S0 feasibility / S2 honest IC")
    ap.add_argument("--mode", choices=["s0", "s2"], default="s0",
                    help="s0 = feasibility (in-sample preview); s2 = honest non-overlapping IC verdict")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--interval", default=DEFAULT_INTERVAL, help="Bybit kline interval (e.g. 5,15,60)")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--vol-short", type=int, default=DEFAULT_VOL_SHORT)
    ap.add_argument("--vol-long", type=int, default=DEFAULT_VOL_LONG)
    ap.add_argument("--min-bars", type=int, default=DEFAULT_MIN_BARS)
    ap.add_argument("--feature", default=None, help="s2 mode: CSV of features to scan (default: all)")
    ap.add_argument("--method", choices=["spearman", "pearson"], default="spearman")
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--out", default=None, help="write the full JSON report here")
    args = ap.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    return _run_s2(args, symbols, horizons) if args.mode == "s2" else _run_s0(args, symbols, horizons)


if __name__ == "__main__":
    raise SystemExit(main())
