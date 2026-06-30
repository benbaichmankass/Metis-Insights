"""M18 scorer-quality — walk-forward ranker evaluation on the candidate dataset.

Consumes the per-candidate CSV from allocator_candidate_dataset.py and answers the
core question rigorously: **can a multi-feature model out-predict the current
`ev_r` scorer OUT-OF-SAMPLE at ranking winners?** If a walk-forward (train-on-past,
test-on-future) model's pooled OOS AUC isn't meaningfully above both 0.5 and the
single-feature `ev_r` baseline, no ranker will beat dumb priority and we stop.

Honest decomposition — two model variants are reported:
  * market-features-only (ret_1h/4h/12h, rr, stop/tp dist, vol, hour, dow) — the
    genuine cross-candidate RANKING signal, independent of which strategy fired.
  * +owner one-hot — adds strategy identity. Identity is the dominant separator
    (trend_donchian-BTC bleeds), so the gap between the two variants shows how much
    apparent "skill" is just "learn which strategy is good" (a Tier-3 strategy call,
    not a ranking insight).

Baselines reported alongside: AUC of `ev_r` alone and `confidence` alone (the
current/naive scorers), pooled over the same OOS folds.

Stdlib + numpy only. A tiny standardized logistic regression (full-batch gradient
descent) keeps it transparent + sklearn-free. Tier-1; reads a CSV, prints a report.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

_MARKET_FEATS = ["confidence", "ev_r", "rr", "stop_dist_pct", "tp_dist_pct",
                 "ret_1h", "ret_4h", "ret_12h", "vol_1h", "mom_align_1h",
                 "hour_sin", "hour_cos", "dow"]


def _load(path: str) -> List[Dict[str, str]]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _f(v: Optional[str]) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _row_features(r: Dict[str, str], feats: List[str], owners: List[str],
                  include_owner: bool) -> Optional[List[float]]:
    x: List[float] = []
    for f in feats:
        if f == "hour_sin":
            h = _f(r.get("hour_utc"))
            x.append(math.sin(2 * math.pi * h / 24.0) if h is not None else 0.0)
        elif f == "hour_cos":
            h = _f(r.get("hour_utc"))
            x.append(math.cos(2 * math.pi * h / 24.0) if h is not None else 0.0)
        else:
            v = _f(r.get(f))
            if v is None:
                return None  # drop rows with a missing market feature (warmup)
            x.append(v)
    if include_owner:
        for o in owners:
            x.append(1.0 if r.get("owner") == o else 0.0)
    return x


def _auc(scores: np.ndarray, labels: np.ndarray) -> Optional[float]:
    pos = scores[labels == 1]
    neg = scores[labels == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), float)
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    sum_pos = ranks[labels == 1].sum()
    u = sum_pos - len(pos) * (len(pos) + 1) / 2.0
    return float(u / (len(pos) * len(neg)))


def _fit_logreg(X: np.ndarray, y: np.ndarray, iters: int = 800, lr: float = 0.3,
                l2: float = 1.0) -> Tuple[np.ndarray, float, np.ndarray, float]:
    """Standardized full-batch logistic regression. Returns (w, b, mu, sigma-pack).

    Standardize on the TRAIN fold only (mu/sigma returned so the test fold uses the
    same transform — no leakage). L2 ridge for stability on small n."""
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma[sigma == 0] = 1.0
    Xs = (X - mu) / sigma
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iters):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        g = p - y
        gw = Xs.T @ g / n + l2 * w / n
        gb = g.mean()
        w -= lr * gw
        b -= lr * gb
    return w, b, mu, sigma


def _predict(X: np.ndarray, w: np.ndarray, b: float, mu: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    Xs = (X - mu) / sigma
    return 1.0 / (1.0 + np.exp(-(Xs @ w + b)))


def walk_forward(rows: List[Dict[str, str]], feats: List[str], include_owner: bool,
                 folds: int) -> Dict[str, object]:
    # chronological order
    rows = sorted(rows, key=lambda r: r.get("entry_ts", ""))
    owners = sorted({r.get("owner", "") for r in rows}) if include_owner else []
    X_all, y_all, idx = [], [], []
    for k, r in enumerate(rows):
        x = _row_features(r, feats, owners, include_owner)
        y = _f(r.get("win"))
        if x is None or y is None:
            continue
        X_all.append(x)
        y_all.append(int(y))
        idx.append(k)
    if len(X_all) < folds * 10:
        return {"error": f"too few usable rows ({len(X_all)}) for {folds} folds"}
    X_all = np.array(X_all, float)
    y_all = np.array(y_all, int)
    n = len(X_all)
    bounds = [int(round(n * t / folds)) for t in range(folds + 1)]
    oos_p, oos_y = [], []
    # expanding-window walk-forward: train on [0, bounds[k]); test on [bounds[k], bounds[k+1])
    for k in range(1, folds):
        tr_hi = bounds[k]
        te_lo, te_hi = bounds[k], bounds[k + 1]
        if te_hi - te_lo < 5 or tr_hi < 20:
            continue
        w, b, mu, sigma = _fit_logreg(X_all[:tr_hi], y_all[:tr_hi])
        p = _predict(X_all[te_lo:te_hi], w, b, mu, sigma)
        oos_p.extend(p.tolist())
        oos_y.extend(y_all[te_lo:te_hi].tolist())
    if not oos_p:
        return {"error": "no OOS folds produced predictions"}
    oos_p = np.array(oos_p)
    oos_y = np.array(oos_y)
    auc = _auc(oos_p, oos_y)
    # full-sample refit for reported weights (sign/importance only)
    w, b, mu, sigma = _fit_logreg(X_all, y_all)
    names = list(feats) + ([f"own:{o}" for o in owners] if include_owner else [])
    weights = sorted(zip(names, w.tolist()), key=lambda t: -abs(t[1]))
    return {"oos_auc": auc, "oos_n": int(len(oos_y)), "weights_top": weights[:12]}


def _single_feature_auc(rows: List[Dict[str, str]], col: str) -> Optional[float]:
    sv, yv = [], []
    for r in rows:
        s = _f(r.get(col))
        y = _f(r.get("win"))
        if s is None or y is None:
            continue
        sv.append(s)
        yv.append(int(y))
    if not sv:
        return None
    return _auc(np.array(sv), np.array(yv))


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="M18 walk-forward ranker eval on the candidate CSV.")
    p.add_argument("--csv", required=True)
    p.add_argument("--folds", type=int, default=5)
    args = p.parse_args(argv[1:])
    rows = _load(args.csv)
    n = len(rows)
    wins = sum(1 for r in rows if _f(r.get("win")) == 1)
    print(f"candidates={n}  win_rate={100*wins/max(1,n):.1f}%")
    print("\n— single-feature OOS-agnostic AUC baselines (whole sample) —")
    for col in ("ev_r", "confidence", "rr", "ret_1h", "ret_4h"):
        a = _single_feature_auc(rows, col)
        print(f"  {col:<12} AUC={a:.3f}" if a is not None else f"  {col:<12} AUC=  —")

    print("\n— walk-forward logistic ranker (pooled OOS AUC) —")
    market = walk_forward(rows, _MARKET_FEATS, include_owner=False, folds=args.folds)
    print(f"  market-only:  {market}")
    withown = walk_forward(rows, _MARKET_FEATS, include_owner=True, folds=args.folds)
    print(f"  +owner:       {withown}")
    print("\nDecision: a ranker is worth building ONLY if market-only OOS AUC is")
    print("meaningfully > 0.5 AND > the ev_r baseline above. The +owner gap shows")
    print("how much is strategy identity (a Tier-3 strategy call, not ranking).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
