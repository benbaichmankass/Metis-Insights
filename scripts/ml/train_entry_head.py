#!/usr/bin/env python3
"""M21 E-3 — P_win entry head: training + offline τ-skip policy replay.

Consumes one E0 family dataset (``build_exit_head_dataset.py`` rows.jsonl,
rebuilt with the post-E-3 builder so rows carry ``first_touch_1r`` /
``reaches_2r`` / ``entry_confidence``) and runs the E-3 protocol from
docs/research/M21-entry-refinement-DESIGN.md:

* **Model** — LightGBM classifier on the per-trade ``first_touch_1r`` label
  (did the trade touch +1R, bar-high basis, before −1R, bar-low basis —
  both-in-one-bar counts as loss-first), trained on the ``age_bars == 0``
  slice with ENTRY-TIME features only (everything known at the decision
  bar; nothing from the hold).
* **Splits** — purged walk-forward by TIME: per-year test folds; each fold
  trains on trades whose LAST bar closed strictly before the fold start
  minus a 7-day embargo (an overlapping hold can't leak its label).
* **Model metric** — per-fold OOS AUC + a 10-bin reliability curve.
* **Decision metric** — the τ-SKIP replay: take only the trades whose
  P(win) >= τ; the survivor sequence (entry-time order, actual final_r —
  pure truncation, no re-simulation) is compared vs taking every trade on
  net_total_r AND running-peak maxDD.
* **Live validation** — a model trained on ALL harness trades is applied
  to the live-source trades (never trained on): AUC + τ-skip replay.

Gate (design § E-3): OOS AUC materially > 0.55 AND a τ-skip arm beats
actual on net_R AND maxDD across the walk-forward AND the live set agrees
in sign. Primary consumer: the M18 allocator ranking (P_win input), then
optional per-leg entry gating (Tier-3).

Output: ``<family_dir>/entry_head_report.json`` + a printed summary.
Advisory only — never touches config or the registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# Entry-time features only — all observable at the decision bar (age 0).
FEATURES = [
    "mom_8", "donchian_mid_dist_atr", "hour_of_day", "dayofweek",
    "is_long", "entry_confidence",
]
# Optional extras (post-P4 datasets; entry-observable — trailing-window
# stats over bars <= the entry bar, never the hold).
FEATURES_EXT = ["mom_decay", "atr_impulse_phase", "band_ext_pctile"]
TARGET = "first_touch_1r"
EMBARGO_S = 7 * 86400
TAUS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]


def load_entries(path: Path) -> List[dict]:
    """One record per trade: the age_bars==0 row + the trade's last bar_t
    (the purge boundary) and its source/final_r."""
    groups: Dict[str, dict] = {}
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        g = groups.setdefault(str(r["trade_key"]), {"entry": None, "last_t": 0})
        g["last_t"] = max(g["last_t"], int(r["bar_t"]))
        if r.get("age_bars") == 0:
            r["is_long"] = 1 if r.get("direction") == "long" else 0
            g["entry"] = r
    out = []
    for g in groups.values():
        e = g["entry"]
        if e is None or e.get(TARGET) is None or e.get("final_r") is None:
            continue
        e["_last_t"] = g["last_t"]
        out.append(e)
    out.sort(key=lambda e: e["bar_t"])  # entry-time order (replay sequence)
    return out


def matrix(entries: List[dict]):
    X = np.array([[float(e.get(f) if e.get(f) is not None else np.nan)
                   for f in FEATURES] for e in entries], dtype=float)
    y = np.array([int(e[TARGET]) for e in entries], dtype=int)
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


def train_model(entries: List[dict]):
    import lightgbm as lgb
    X, y = matrix(entries)
    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31,
        min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
        reg_lambda=1.0, random_state=7, verbose=-1)
    clf.fit(X, y)
    return clf


def replay(entries: List[dict], keep: Optional[np.ndarray]) -> dict:
    """Survivor-sequence stats in entry-time order (entries pre-sorted).
    keep=None takes every trade (the actual arm)."""
    rs = [float(e["final_r"]) for i, e in enumerate(entries)
          if keep is None or keep[i]]
    if not rs:
        return {"trades": 0, "net_r": 0.0, "max_dd_r": 0.0}
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    return {"trades": len(rs), "net_r": round(float(sum(rs)), 2),
            "max_dd_r": round(dd, 2),
            "kept_frac": round(len(rs) / len(entries), 3),
            "expectancy_r": round(float(sum(rs)) / len(rs), 4)}


def eval_split(model, entries: List[dict]) -> dict:
    X, y = matrix(entries)
    p = model.predict_proba(X)[:, 1]
    out = {
        "n_trades": len(entries),
        "auc": auc_score(y, p),
        "base_rate": round(float(y.mean()), 4) if len(y) else None,
        "reliability": reliability(y, p),
        "actual": replay(entries, None),
        "skip": {},
    }
    for tau in TAUS:
        out["skip"][f"tau_{tau}"] = replay(entries, p >= tau)
    return out


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family-dir", required=True,
                    help="E0 family dir containing rows.jsonl")
    ap.add_argument("--min-fold-trades", type=int, default=50)
    ap.add_argument("--target", choices=["first_touch_1r", "reaches_2r"],
                    default="first_touch_1r")
    ap.add_argument("--features", choices=["base", "extended"],
                    default="base")
    a = ap.parse_args(argv[1:])
    global TARGET, FEATURES
    TARGET = a.target
    if a.features == "extended":
        FEATURES = FEATURES + FEATURES_EXT

    fam_dir = Path(a.family_dir)
    entries = load_entries(fam_dir / "rows.jsonl")
    if not entries:
        print("no usable entry rows — rebuild the dataset with the post-E-3 "
              "builder (first_touch_1r/entry_confidence labels required)")
        return 2
    harness = [e for e in entries if e["source"] == "harness"]
    live = [e for e in entries if e["source"] == "live"]
    print(f"{fam_dir.name}: {len(harness)} harness entries, "
          f"{len(live)} live entries; target={TARGET}; "
          f"base_rate={round(float(np.mean([e[TARGET] for e in harness])), 3) if harness else None}")

    # ---- purged walk-forward by year over harness entries
    years = sorted({e["year"] for e in harness})
    folds = []
    for ytest in years[1:]:
        y0 = datetime(ytest, 1, 1, tzinfo=timezone.utc).timestamp()
        test = [e for e in harness if e["year"] == ytest]
        train = [e for e in harness if e["_last_t"] < y0 - EMBARGO_S]
        if len(test) < a.min_fold_trades or len(train) < 200:
            print(f"  fold {ytest}: skipped (test={len(test)}, train={len(train)})")
            continue
        model = train_model(train)
        res = eval_split(model, test)
        res["year"] = ytest
        res["train_trades"] = len(train)
        folds.append(res)
        best = max(res["skip"].items(),
                   key=lambda kv: kv[1].get("net_r") or -1e9)
        print(f"  fold {ytest}: AUC={res['auc'] and round(res['auc'], 3)} "
              f"actual net_R={res['actual']['net_r']} "
              f"dd={res['actual']['max_dd_r']} best_skip={best[0]} "
              f"(net_R={best[1].get('net_r')}, dd={best[1].get('max_dd_r')}, "
              f"kept={best[1].get('kept_frac')})")

    # ---- per-τ walk-forward roll-up: beats actual on net_R AND maxDD
    wf = {}
    for tau in TAUS:
        wins = usable = 0
        for f in folds:
            s = f["skip"].get(f"tau_{tau}")
            act = f["actual"]
            if not s or s["trades"] == 0:
                continue
            usable += 1
            if (s["net_r"] >= act["net_r"]
                    and s["max_dd_r"] <= act["max_dd_r"]):
                wins += 1
        wf[f"tau_{tau}"] = f"{wins}/{usable}"

    # ---- live validation: train on ALL harness entries, apply to live
    live_eval = None
    if live and harness:
        model_all = train_model(harness)
        live_eval = eval_split(model_all, live)
        print(f"  live: AUC={live_eval['auc'] and round(live_eval['auc'], 3)} "
              f"n={live_eval['n_trades']}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "family": fam_dir.name, "features": FEATURES, "target": TARGET,
        "taus": TAUS, "embargo_days": EMBARGO_S // 86400,
        "harness_entries": len(harness), "live_entries": len(live),
        "folds": folds, "walkforward_beats_actual": wf,
        "live_validation": live_eval,
        "gate_note": ("E-3 gate: OOS AUC materially > 0.55 AND a tau-skip "
                      "arm beats actual on net_R AND maxDD across the "
                      "walk-forward AND the live set agrees in sign. "
                      "Primary consumer: M18 allocator P_win ranking."),
    }
    out = fam_dir / "entry_head_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
