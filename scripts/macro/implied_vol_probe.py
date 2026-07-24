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
# S3 — held-out OOS split + cost-aware long/short conviction spread
# ---------------------------------------------------------------------------
#
# S2 confirms an in-sample IC exists; S3 is the honest kill-test the program has
# used to catch every prior "looks positive" result (entry 11: an S2-significant
# signal whose OOS IC flipped negative). Two independent OOS checks, both on the
# SECOND (held-out) time-fraction, with orientation fit ONLY on the first fraction
# (no lookahead):
#   1. OOS IC  — does the honest non-overlapping IC hold sign + significance OOS?
#   2. net conviction spread — a dollar-neutral long/short-by-quantile book
#      (long the bin the IS-fit orientation predicts up, short the other) pays a
#      round-trip fee; does the OOS spread stay positive net of cost?
# `pays_oos` requires BOTH (OOS IC significant, same sign as IS) AND net spread > 0.

def _anchors(feature_series: list, target_vals: list, horizon: int) -> list:
    """Non-overlapping ``(feature, fwd_return)`` anchors in time order."""
    out = []
    for i in range(0, len(feature_series) - horizon, max(1, horizon)):
        f = feature_series[i]
        fwd = log_return(target_vals[i], target_vals[i + horizon])
        if f is not None and fwd is not None:
            out.append((f, fwd))
    return out


def conviction_s3_row(feature_series: list, target_vals: list, horizon: int, *,
                      split_frac: float = 0.6, fee_frac: float = 0.001,
                      q: float = 0.34) -> dict:
    """One horizon's OOS split + cost-aware long/short spread. All time-ordered;
    orientation is fit on the IS fraction only (no lookahead)."""
    anchors = _anchors(feature_series, target_vals, horizon)
    n = len(anchors)
    row = {"horizon": horizon, "n_is": 0, "n_oos": 0, "is_ic": None, "oos_ic": None,
           "oos_ic_t": None, "orient": None, "gross_spread": None, "net_spread": None,
           "pays_oos": False}
    if n < 16:
        return row
    cut = int(n * split_frac)
    is_a, oos_a = anchors[:cut], anchors[cut:]
    if len(is_a) < 8 or len(oos_a) < 8:
        return row
    is_ic = spearman_ic(is_a)
    oos_ic = spearman_ic(oos_a)
    row["n_is"], row["n_oos"] = len(is_a), len(oos_a)
    if is_ic is None or oos_ic is None:
        return row
    row["is_ic"], row["oos_ic"], row["oos_ic_t"] = is_ic["ic"], oos_ic["ic"], oos_ic["ic_t"]
    # orientation from IS sign: negative IC ⇒ LOW feature predicts UP (long low bin)
    orient = -1 if is_ic["ic"] < 0 else 1
    row["orient"] = orient
    # OOS quantile bins on the feature
    oos_sorted = sorted(oos_a, key=lambda p: p[0])
    k = max(1, int(len(oos_sorted) * q))
    low_bin, high_bin = oos_sorted[:k], oos_sorted[-k:]
    low_mean = sum(p[1] for p in low_bin) / len(low_bin)
    high_mean = sum(p[1] for p in high_bin) / len(high_bin)
    long_mean, short_mean = (low_mean, high_mean) if orient < 0 else (high_mean, low_mean)
    gross = long_mean - short_mean
    net = gross - 2.0 * fee_frac        # one long-short round-trip on the spread
    row["gross_spread"], row["net_spread"] = gross, net
    same_sign = (oos_ic["ic"] < 0) == (is_ic["ic"] < 0)
    row["pays_oos"] = bool(net > 0 and same_sign and abs(oos_ic["ic_t"]) >= 2.0)
    return row


def scan_s3(feature_series: list, target_vals: list, horizons, *,
            split_frac: float = 0.6, fee_frac: float = 0.001, q: float = 0.34) -> dict:
    """S3 across horizons → verdict. ``pays_oos_net`` if any horizon passes both the
    OOS-IC and net-spread gates; else ``s2_only_no_s3`` / ``no_data``."""
    rows = [conviction_s3_row(feature_series, target_vals, h, split_frac=split_frac,
                              fee_frac=fee_frac, q=q) for h in horizons]
    computable = [r for r in rows if r["oos_ic"] is not None]
    if not computable:
        verdict = "no_data"
    elif any(r["pays_oos"] for r in rows):
        verdict = "pays_oos_net"
    else:
        verdict = "s2_only_no_s3"
    return {"verdict": verdict, "rows": rows,
            "pays_oos": any(r["pays_oos"] for r in rows)}


# ---------------------------------------------------------------------------
# S4-prep — multi-fold walk-forward robustness
# ---------------------------------------------------------------------------
#
# S3 leans on ONE 60/40 split. A signal can pass that split by luck of where the
# cut lands — the vix_term S3 "pass" rested on a single small-N cell (H=42d, ~23
# OOS anchors). The honest next question is robustness: does the OOS IC hold its
# sign across SEVERAL held-out windows, or only one? This runs an EXPANDING-window
# walk-forward: fold j fits the orientation on all anchors BEFORE test-block j
# (never on the future — no lookahead) and measures the OOS IC sign on block j.
# The reference "expected sign" is the whole-sample IS orientation; a fold `holds`
# when its OOS IC carries that sign. `sign_consistency` = fraction of computable
# folds that hold. Verdict tiers:
#   robust           — sign_consistency >= robust_frac AND the pooled OOS IC is
#                       significant (|t|>=2) in the expected sign (a real edge).
#   regime_dependent — sign_consistency >= 0.5 but not robust (holds in SOME
#                       windows only — "one good regime", not a durable edge).
#   not_robust       — sign_consistency < 0.5 (the OOS IC flips across folds).
#   insufficient_sample — < 2 computable folds (the honest N-too-small verdict,
#                       expected at the long horizons where non-overlapping N is
#                       tiny). NEVER silently upgraded to a pass.

def walkforward_row(feature_series: list, target_vals: list, horizon: int, *,
                    k_folds: int = 4, min_train: int = 10, min_test: int = 8,
                    robust_frac: float = 0.75) -> dict:
    """One horizon's expanding-window walk-forward. Orientation is fit only on
    anchors strictly before each test block (no lookahead)."""
    anchors = _anchors(feature_series, target_vals, horizon)
    n = len(anchors)
    row = {"horizon": horizon, "n_anchors": n, "k_used": 0, "folds": [],
           "sign_consistency": None, "expected_sign": None,
           "pooled_oos_ic": None, "pooled_oos_ic_t": None, "verdict": "insufficient_sample"}
    # need a seed train + at least 2 test blocks of min_test each
    if n < min_train + 2 * min_test:
        return row
    whole = spearman_ic(anchors)
    if whole is None:
        return row
    expected_sign = -1 if whole["ic"] < 0 else 1
    row["expected_sign"] = expected_sign
    test_total = n - min_train
    k = min(k_folds, test_total // min_test)
    if k < 2:
        return row
    block = test_total // k
    folds, holds, computable, pooled_test = [], 0, 0, []
    for j in range(k):
        train_end = min_train + j * block          # expanding train
        test_end = n if j == k - 1 else train_end + block
        train, test = anchors[:train_end], anchors[train_end:test_end]
        tic, oic = spearman_ic(train), spearman_ic(test)
        fold = {"fold": j, "n_train": len(train), "n_test": len(test),
                "train_ic": None, "oos_ic": None, "oos_ic_t": None, "hold": None}
        if tic is not None and oic is not None:
            computable += 1
            oos_sign = -1 if oic["ic"] < 0 else 1
            hold = oos_sign == expected_sign
            holds += 1 if hold else 0
            pooled_test.extend(test)
            fold.update({"train_ic": tic["ic"], "oos_ic": oic["ic"],
                         "oos_ic_t": oic["ic_t"], "hold": hold})
        folds.append(fold)
    row["folds"], row["k_used"] = folds, k
    if computable < 2:
        return row                                  # insufficient_sample
    sign_consistency = holds / computable
    row["sign_consistency"] = sign_consistency
    pooled = spearman_ic(pooled_test)
    if pooled is not None:
        row["pooled_oos_ic"], row["pooled_oos_ic_t"] = pooled["ic"], pooled["ic_t"]
    pooled_sig_same_sign = (
        pooled is not None
        and (-1 if pooled["ic"] < 0 else 1) == expected_sign
        and abs(pooled["ic_t"]) >= 2.0
    )
    if sign_consistency >= robust_frac and pooled_sig_same_sign:
        row["verdict"] = "robust"
    elif sign_consistency >= 0.5:
        row["verdict"] = "regime_dependent"
    else:
        row["verdict"] = "not_robust"
    return row


_WF_RANK = {"robust": 3, "regime_dependent": 2, "not_robust": 1,
            "insufficient_sample": 0}


def scan_walkforward(feature_series: list, target_vals: list, horizons, *,
                     k_folds: int = 4, min_train: int = 10, min_test: int = 8,
                     robust_frac: float = 0.75) -> dict:
    """Walk-forward across horizons → probe verdict = the strongest horizon's
    (robust > regime_dependent > not_robust > insufficient_sample)."""
    rows = [walkforward_row(feature_series, target_vals, h, k_folds=k_folds,
                            min_train=min_train, min_test=min_test,
                            robust_frac=robust_frac) for h in horizons]
    best = max(rows, key=lambda r: _WF_RANK.get(r["verdict"], 0)) if rows else None
    verdict = best["verdict"] if best else "insufficient_sample"
    return {"verdict": verdict, "rows": rows, "best_horizon": best["horizon"] if best else None,
            "is_robust": verdict == "robust"}


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


def run_probes_s3(probes=DEFAULT_PROBES, horizons=DEFAULT_HORIZONS, *,
                  urlopen=None, split_frac: float = 0.6, fee_frac: float = 0.001,
                  q: float = 0.34) -> dict:
    """S3 pass: OOS split + cost-aware conviction spread for each probe."""
    sids = set()
    for _, vol, tgt, _, extra in probes:
        sids.update([vol, tgt] + ([extra] if extra else []))
    dated = fetch_fred_series_history_dated(sorted(sids), urlopen=urlopen)
    results = []
    for name, vol_sid, tgt_sid, feature, extra_sid in probes:
        vol_d, tgt_d = dated.get(vol_sid, []), dated.get(tgt_sid, [])
        if not vol_d or not tgt_d:
            results.append({"name": name, "feature": feature, "verdict": "no_data"})
            continue
        dates, vol_vals, tgt_vals = align_dated(vol_d, tgt_d)
        extra_vals = None
        if extra_sid:
            emap = {d: v for d, v in dated.get(extra_sid, [])}
            extra_vals = [_to_float(emap.get(d)) for d in dates]
        feat = build_feature_series(feature, vol_vals, tgt_vals, extra_vals=extra_vals)
        s3 = scan_s3(feat, tgt_vals, horizons, split_frac=split_frac, fee_frac=fee_frac, q=q)
        results.append({"name": name, "feature": feature, "vol_sid": vol_sid,
                        "target_sid": tgt_sid, "verdict": s3["verdict"], "rows": s3["rows"]})
    return {"split_frac": split_frac, "fee_frac": fee_frac, "q": q,
            "horizons": list(horizons), "probes": results,
            "any_pays_oos": any(r.get("verdict") == "pays_oos_net" for r in results)}


def run_probes_walkforward(probes=DEFAULT_PROBES, horizons=DEFAULT_HORIZONS, *,
                           urlopen=None, k_folds: int = 4, min_train: int = 10,
                           min_test: int = 8, robust_frac: float = 0.75) -> dict:
    """S4-prep pass: expanding-window walk-forward robustness for each probe."""
    sids = set()
    for _, vol, tgt, _, extra in probes:
        sids.update([vol, tgt] + ([extra] if extra else []))
    dated = fetch_fred_series_history_dated(sorted(sids), urlopen=urlopen)
    results = []
    for name, vol_sid, tgt_sid, feature, extra_sid in probes:
        vol_d, tgt_d = dated.get(vol_sid, []), dated.get(tgt_sid, [])
        if not vol_d or not tgt_d:
            results.append({"name": name, "feature": feature, "verdict": "no_data"})
            continue
        dates, vol_vals, tgt_vals = align_dated(vol_d, tgt_d)
        extra_vals = None
        if extra_sid:
            emap = {d: v for d, v in dated.get(extra_sid, [])}
            extra_vals = [_to_float(emap.get(d)) for d in dates]
        feat = build_feature_series(feature, vol_vals, tgt_vals, extra_vals=extra_vals)
        wf = scan_walkforward(feat, tgt_vals, horizons, k_folds=k_folds,
                              min_train=min_train, min_test=min_test, robust_frac=robust_frac)
        results.append({"name": name, "feature": feature, "vol_sid": vol_sid,
                        "target_sid": tgt_sid, "verdict": wf["verdict"],
                        "best_horizon": wf["best_horizon"], "rows": wf["rows"]})
    return {"k_folds": k_folds, "min_train": min_train, "min_test": min_test,
            "robust_frac": robust_frac, "horizons": list(horizons), "probes": results,
            "any_robust": any(r.get("verdict") == "robust" for r in results)}


def _fmt(v) -> str:
    return "—" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def _print_s2(out) -> None:
    print("M31 Track A — implied-volatility HONEST non-overlapping directional IC (S2)")
    print("=" * 74)
    print(f"t_flag={out['t_flag']}  horizons={out['horizons']} trading-days")
    for r in out["probes"]:
        print(f"\n  {r['name']}  ({r.get('vol_sid','?')}→{r.get('target_sid','?')}, "
              f"{r['feature']}): verdict={r['verdict']}")
        if r.get("reason"):
            print(f"      {r['reason']}")
        for row in r.get("rows", []):
            print(f"      H={row['horizon']:>3}d  n={_fmt(row['n_nonoverlap'])}  "
                  f"ic={_fmt(row['ic'])} (t={_fmt(row['ic_t'])})")
    print(f"\nany_directional_edge={out['any_edge']}")


def _print_s3(out) -> None:
    print("\nM31 Track A — cost-aware OOS conviction test (S3)")
    print("=" * 74)
    print(f"split_frac={out['split_frac']} (orientation fit IS-only)  "
          f"fee={out['fee_frac']} round-trip  q={out['q']} tails  horizons={out['horizons']}")
    for r in out["probes"]:
        print(f"\n  {r['name']}  ({r.get('vol_sid','?')}→{r.get('target_sid','?')}, "
              f"{r['feature']}): verdict={r['verdict']}")
        for row in r.get("rows", []):
            print(f"      H={row['horizon']:>3}d  is_ic={_fmt(row['is_ic'])} "
                  f"oos_ic={_fmt(row['oos_ic'])} (t={_fmt(row['oos_ic_t'])})  "
                  f"gross={_fmt(row['gross_spread'])} net={_fmt(row['net_spread'])}  "
                  f"pays_oos={row['pays_oos']}")
    print(f"\nany_pays_oos_net={out['any_pays_oos']}")
    print("pays_oos = OOS IC significant (|t|>=2) AND same sign as IS AND net spread > 0.")


def _print_wf(out) -> None:
    print("\nM31 Track A — multi-fold walk-forward robustness (S4-prep)")
    print("=" * 74)
    print(f"k_folds={out['k_folds']} (expanding, orientation fit on the past only)  "
          f"min_train={out['min_train']}  min_test={out['min_test']}  "
          f"robust_frac={out['robust_frac']}  horizons={out['horizons']}")
    for r in out["probes"]:
        print(f"\n  {r['name']}  ({r.get('vol_sid','?')}→{r.get('target_sid','?')}, "
              f"{r['feature']}): verdict={r['verdict']}  best_horizon={r.get('best_horizon')}")
        for row in r.get("rows", []):
            print(f"      H={row['horizon']:>3}d  n_anchors={_fmt(row['n_anchors'])} "
                  f"k={row['k_used']}  sign_consistency={_fmt(row['sign_consistency'])}  "
                  f"pooled_oos_ic={_fmt(row['pooled_oos_ic'])} "
                  f"(t={_fmt(row['pooled_oos_ic_t'])})  → {row['verdict']}")
            for f in row.get("folds", []):
                print(f"          fold {f['fold']}: n_train={f['n_train']} "
                      f"n_test={f['n_test']}  oos_ic={_fmt(f['oos_ic'])} "
                      f"(t={_fmt(f['oos_ic_t'])})  hold={f['hold']}")
    print(f"\nany_robust={out['any_robust']}")
    print("robust = sign_consistency>=robust_frac AND pooled OOS IC significant same-sign; "
          "regime_dependent = holds in SOME folds only; insufficient_sample = N too small.")


def main() -> int:
    ap = argparse.ArgumentParser(description="M31 Track A — implied-vol IC + OOS + walk-forward probe")
    ap.add_argument("--mode", choices=["s2", "s3", "wf", "both", "all"], default="all")
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS))
    ap.add_argument("--t-flag", type=float, default=2.0)
    ap.add_argument("--fee-frac", type=float, default=0.001)
    ap.add_argument("--split-frac", type=float, default=0.6)
    ap.add_argument("--k-folds", type=int, default=4)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    bundle = {}
    if args.mode in ("s2", "both", "all"):
        bundle["s2"] = run_probes(horizons=horizons, t_flag=args.t_flag)
    if args.mode in ("s3", "both", "all"):
        bundle["s3"] = run_probes_s3(horizons=horizons, fee_frac=args.fee_frac,
                                     split_frac=args.split_frac)
    if args.mode in ("wf", "all"):
        bundle["wf"] = run_probes_walkforward(horizons=horizons, k_folds=args.k_folds)
    if args.json:
        print(json.dumps(bundle, indent=2))
        return 0
    if "s2" in bundle:
        _print_s2(bundle["s2"])
    if "s3" in bundle:
        _print_s3(bundle["s3"])
    if "wf" in bundle:
        _print_wf(bundle["wf"])
    print("\nic = drift-neutral fwd-return Spearman IC on NON-OVERLAPPING anchors "
          "(honest t, N≈n/H).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
