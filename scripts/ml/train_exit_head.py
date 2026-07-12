#!/usr/bin/env python3
"""M20 E1 — exit-head training + offline policy evaluation.

Consumes one E0 family dataset (``build_exit_head_dataset.py`` rows.jsonl)
and runs the full E1 protocol from docs/research/M20-exit-head-PROGRAM.md:

* **Model** — LightGBM classifier on ``holding_pays`` (per-family; the
  pooled-model comparison is a follow-up once >1 family gates in).
* **Splits** — purged walk-forward by TIME: per-year test folds over the
  harness rows; each fold trains on strictly-earlier harness trades with a
  7-day embargo before the fold start (an overlapping hold can't leak).
* **Model metric** — per-fold OOS AUC + a 10-bin reliability curve.
* **Decision metric** — the τ-policy replay: exit at the FIRST bar where
  P(holding pays) < τ; exit value = that bar's observed close mark
  (``open_r``) — pure truncation, identical honesty to the M20
  counterfactuals (no barrier re-simulation). Compared per fold vs
  (a) actual exits and (b) the best hard levers replayed on the SAME rows
  (stale-stop 8 bars/<0R; giveback 1.0R @ MFE>=1R).
* **Capital efficiency** — net_R per position-day for every arm.
* **Live validation** — a model trained on ALL harness rows is applied to
  the live-source trades (never trained on): AUC + τ-policy replay. The
  E1→E2 gate requires the live set to agree in SIGN with the walk-forward.

Output: ``<family_dir>/e1_report.json`` + a printed summary. Advisory
only — this script never touches config or the registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

FEATURES = [
    "age_bars", "open_r", "mfe_r", "mae_r", "giveback_r",
    "chop_frac_so_far", "stagnation_run", "dist_to_stop_r",
    "vol_ratio_vs_entry", "atr_ratio_vs_entry", "donchian_mid_dist_atr",
    "hour_of_day", "dayofweek", "is_long",
]
EMBARGO_S = 7 * 86400
TAUS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]
TF_S = {"5m": 300, "15m": 900, "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


def load_rows(path: Path) -> List[dict]:
    rows = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        r["is_long"] = 1 if r.get("direction") == "long" else 0
        rows.append(r)
    return rows


def group_trades(rows: List[dict]) -> Dict[str, List[dict]]:
    """trade_key -> bars sorted by age."""
    out: Dict[str, List[dict]] = {}
    for r in rows:
        out.setdefault(str(r["trade_key"]), []).append(r)
    for bars in out.values():
        bars.sort(key=lambda r: r["age_bars"])
    return out


def matrix(rows: List[dict]):
    X = np.array([[float(r.get(f) if r.get(f) is not None else np.nan)
                   for f in FEATURES] for r in rows], dtype=float)
    y = np.array([int(r["holding_pays"]) for r in rows], dtype=int)
    return X, y


def auc_score(y, p) -> Optional[float]:
    if len(set(y.tolist())) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def reliability(y, p, bins: int = 10) -> List[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        m = (p >= lo) & (p < hi if b < bins - 1 else p <= hi)
        if m.sum() == 0:
            continue
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()),
                    "mean_p": round(float(p[m].mean()), 4),
                    "frac_pos": round(float(y[m].mean()), 4)})
    return out


def train_model(rows: List[dict]):
    import lightgbm as lgb
    X, y = matrix(rows)
    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31,
        min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
        reg_lambda=1.0, random_state=7, verbose=-1)
    clf.fit(X, y)
    return clf


# ------------------------------------------------------------- policy replay
def replay_trade(bars: List[dict], exit_idx: Optional[int]) -> dict:
    """Exit at bar exit_idx (mark-to-close truncation) or ride to actual."""
    if exit_idx is None or exit_idx >= len(bars) - 1:
        r = float(bars[0]["final_r"])
        held = len(bars)
    else:
        r = float(bars[exit_idx]["open_r"])
        held = exit_idx + 1
    return {"r": r, "bars": held}


def policy_model(bars: List[dict], probs: np.ndarray, tau: float) -> dict:
    idx = None
    for i in range(len(bars)):
        if probs[i] < tau:
            idx = i
            break
    return replay_trade(bars, idx)


# E1.5 conditional shapes (memo § 8 queued item 1): arm the head ONLY in the
# states where the chop-hold loss lives, so a running trend is never
# truncated by a low score alone. Motivated by live trade 3344 (BTC donchian
# held 2d+ around flat, P(pays) ~0.12-0.24 the whole tail, but the trade sat
# marginally ABOVE the stale-stop's <0R reference cell).
_SHAPES = {
    # only cut while the trade has not proven itself (< +0.5R at the bar close)
    "below_half_r": lambda b, i: float(b[i]["open_r"]) < 0.5,
    # only cut before the trade ever reached +1R MFE (past that, the
    # chandelier trail / giveback owns the exit)
    "pre_mfe1": lambda b, i: float(b[i]["mfe_r"]) < 1.0,
    # only cut mature trades (>= 8 bars — the stale-stop's age gate)
    "age8": lambda b, i: b[i]["age_bars"] >= 8,
    # combined: mature AND unproven
    "age8_below_half_r": lambda b, i: (b[i]["age_bars"] >= 8
                                       and float(b[i]["open_r"]) < 0.5),
}


def policy_model_cond(bars: List[dict], probs: np.ndarray, tau: float,
                      cond) -> dict:
    idx = None
    for i in range(len(bars)):
        if probs[i] < tau and cond(bars, i):
            idx = i
            break
    return replay_trade(bars, idx)


def policy_stale(bars: List[dict], n: int = 8, below_r: float = 0.0) -> dict:
    idx = None
    for i, b in enumerate(bars):
        if b["age_bars"] >= n and float(b["open_r"]) < below_r:
            idx = i
            break
    return replay_trade(bars, idx)


def policy_giveback(bars: List[dict], min_mfe: float = 1.0,
                    gb: float = 1.0) -> dict:
    idx = None
    for i, b in enumerate(bars):
        if float(b["mfe_r"]) >= min_mfe and float(b["giveback_r"]) >= gb:
            idx = i
            break
    return replay_trade(bars, idx)


def agg(results: List[dict], tf_s: int) -> dict:
    if not results:
        return {"trades": 0}
    rs = [x["r"] for x in results]
    days = sum(x["bars"] for x in results) * tf_s / 86400.0
    net = float(sum(rs))
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return {"trades": len(rs), "net_r": round(net, 2),
            "max_dd_r": round(dd, 2),
            "mean_hold_bars": round(sum(x["bars"] for x in results) / len(rs), 1),
            "net_r_per_pos_day": round(net / days, 4) if days > 0 else None}


def eval_split(model, trades: Dict[str, List[dict]], tf_s: int) -> dict:
    """AUC + reliability + per-τ / hard-lever / actual replay on a trade set."""
    all_rows = [b for bars in trades.values() for b in bars]
    X, y = matrix(all_rows)
    p = model.predict_proba(X)[:, 1]
    # slice probs back per trade
    probs: Dict[str, np.ndarray] = {}
    i = 0
    for tk, bars in trades.items():
        probs[tk] = p[i:i + len(bars)]
        i += len(bars)
    out = {
        "n_trades": len(trades), "n_rows": len(all_rows),
        "auc": auc_score(y, p),
        "reliability": reliability(y, p),
        "actual": agg([replay_trade(b, None) for b in trades.values()], tf_s),
        "stale_8_0": agg([policy_stale(b) for b in trades.values()], tf_s),
        "giveback_1_1": agg([policy_giveback(b) for b in trades.values()], tf_s),
        "model": {},
    }
    for tau in TAUS:
        out["model"][f"tau_{tau}"] = agg(
            [policy_model(b, probs[tk], tau) for tk, b in trades.items()], tf_s)
    # E1.5 conditional shapes on a focused tau grid
    out["model_cond"] = {}
    for shape, cond in _SHAPES.items():
        for tau in (0.10, 0.15, 0.20):
            out["model_cond"][f"{shape}_tau_{tau}"] = agg(
                [policy_model_cond(b, probs[tk], tau, cond)
                 for tk, b in trades.items()], tf_s)
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True,
                    help="E0 family dir containing rows.jsonl")
    ap.add_argument("--tf", required=True, choices=sorted(TF_S))
    ap.add_argument("--min-fold-trades", type=int, default=50)
    a = ap.parse_args(argv[1:])

    fam_dir = Path(a.family_dir)
    rows = load_rows(fam_dir / "rows.jsonl")
    tf_s = TF_S[a.tf]
    harness = [r for r in rows if r["source"] == "harness"]
    live = [r for r in rows if r["source"] == "live"]
    h_trades = group_trades(harness)
    l_trades = group_trades(live)
    print(f"{fam_dir.name}: {len(h_trades)} harness trades "
          f"({len(harness)} rows), {len(l_trades)} live trades "
          f"({len(live)} rows)")

    # ---- purged walk-forward by year over harness trades
    def t_entry(bars):  # first bar time as trade entry proxy
        return bars[0]["bar_t"]
    years = sorted({r["year"] for r in harness})
    folds = []
    for ytest in years[1:]:
        y0 = datetime(ytest, 1, 1, tzinfo=timezone.utc).timestamp()
        test = {tk: b for tk, b in h_trades.items()
                if datetime.fromtimestamp(t_entry(b), tz=timezone.utc).year == ytest}
        # purge on the trade's LAST bar: a hold spanning into the test year
        # (or the embargo) would leak its final_r label into training.
        train_rows = [r for tk, b in h_trades.items() for r in b
                      if b[-1]["bar_t"] < y0 - EMBARGO_S]
        if len(test) < a.min_fold_trades or len(train_rows) < 500:
            print(f"  fold {ytest}: skipped (test={len(test)} trades, "
                  f"train={len(train_rows)} rows)")
            continue
        model = train_model(train_rows)
        res = eval_split(model, test, tf_s)
        res["year"] = ytest
        res["train_rows"] = len(train_rows)
        folds.append(res)
        print(f"  fold {ytest}: AUC={res['auc'] and round(res['auc'],3)} "
              f"actual net_R={res['actual']['net_r']} "
              f"best_tau={max(res['model'].items(), key=lambda kv: kv[1].get('net_r') or -1e9)[0]}")

    # ---- live validation: train on ALL harness rows, apply to live trades
    live_eval = None
    if l_trades:
        model_all = train_model(harness)
        live_eval = eval_split(model_all, l_trades, tf_s)
        print(f"  live: AUC={live_eval['auc'] and round(live_eval['auc'],3)} "
              f"n={live_eval['n_trades']} actual net_R={live_eval['actual']['net_r']}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "family": fam_dir.name, "tf": a.tf, "features": FEATURES,
        "taus": TAUS, "embargo_days": EMBARGO_S // 86400,
        "harness_trades": len(h_trades), "live_trades": len(l_trades),
        "folds": folds, "live_validation": live_eval,
        "gate_note": ("E1->E2 gate: OOS AUC materially > 0.55 AND a tau-policy "
                      "beats the best hard rule on net_R AND maxDD in the "
                      "walk-forward AND the live set agrees in sign."),
    }
    out = fam_dir / "e1_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
