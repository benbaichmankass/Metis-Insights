#!/usr/bin/env python3
"""M19 D2 spike A — do pooled real+paper labels lift the REAL-money slice?

The trade-outcome heads are an order of magnitude under any defensible
sample-size standard on real-money labels alone (~350 lifetime; the conviction
A/B evaluated on n=20). The journal holds ~10× more closed PAPER trades from
the same strategies/signal builders. The M14 record says the one partially
successful meta-label direction was POOLING with a domain flag
(S-MLOPT-S8: cross-symbol pooling first to clear the majority baseline), while
naive synthetic→real transfer failed (S-MLOPT-S6). Spike A retries pooling
with the better-matched pool: **real + paper executions, an explicit
`account_class` domain flag, and the real-money slice held out as the decisive
evaluation** (`MB-20260705-META-LABEL-WALL`).

Three arms, ONE shared chronologically-held real-money evaluation slice
(never trained on by any arm):

- ``real_only``  — trained on real-money rows only (the label-wall status quo).
- ``pooled_flag``— trained on real+paper with the ``account_class`` domain flag.
- ``pooled_bare``— trained on real+paper WITHOUT the flag (does the flag
  itself matter, or just the extra rows?).

Metrics per arm on the held real slice: accuracy, precision/recall/F1 on the
win class, Brier, AUC — next to the majority-class baseline (the S6/S8 bar).
A 3-fold chronological stability loop repeats the comparison at earlier
cut-points. EPV accounting (positives-in-train / feature count) is printed so
an under-powered read can't pass silently.

Reads the built ``trade_outcomes`` dataset (jsonl) + ``config/accounts.yaml``
(public field ``account_class`` only, to map ``account_id`` → domain; falls
back to the id heuristic ``*_paper``/``bybit_1``→paper). Writes an optional
JSON report. Tier-1 research — offline, no registry write, never touches the
order path.

Run on the trainer:
    python3 scripts/ml/spike_a_pooled_labels.py \
      --dataset datasets-out/trade_outcomes/all/all/v002/data.jsonl \
      --json /tmp/spike_a.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

FEATURES_CAT = ["strategy_name", "symbol", "direction", "setup_type",
                "killzone", "bias"]
FEATURES_NUM = ["equity_at_signal", "daily_pnl_realized_at_signal",
                "daily_equity_high_at_signal", "daily_drawdown_pct_at_signal",
                "open_trades_count_at_signal"]
DOMAIN_COL = "account_class"


def _load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _account_class_map(accounts_yaml: Path) -> dict[str, str]:
    """account_id → 'real_money'/'paper' from config/accounts.yaml (public
    field only). Missing file / field → empty map (heuristic fallback)."""
    try:
        import yaml

        cfg = yaml.safe_load(accounts_yaml.read_text()) or {}
    except Exception:
        return {}
    out = {}
    accounts = cfg.get("accounts") or cfg
    if isinstance(accounts, dict):
        for aid, a in accounts.items():
            if isinstance(a, dict) and a.get("account_class"):
                out[str(aid)] = str(a["account_class"])
    elif isinstance(accounts, list):
        for a in accounts:
            if isinstance(a, dict) and a.get("id") and a.get("account_class"):
                out[str(a["id"])] = str(a["account_class"])
    return out


def _domain_of(account_id: str, amap: dict[str, str]) -> str:
    if account_id in amap:
        return amap[account_id]
    aid = (account_id or "").lower()
    if aid == "bybit_2":
        return "real_money"
    if "paper" in aid or aid in ("bybit_1", "demo"):
        return "paper"
    return "unknown"


def _sort_key(r: dict) -> float:
    v = r.get("created_at") or r.get("timestamp") or 0
    try:
        f = float(v)
        return f / 1000.0 if f > 1e11 else f
    except (TypeError, ValueError):
        pass
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _encode(rows: list[dict], cat_maps: dict[str, dict], with_flag: bool):
    """Rows → (X, y) numeric matrices; categoricals via shared code maps."""
    X, y = [], []
    for r in rows:
        feats = []
        for c in FEATURES_CAT:
            feats.append(float(cat_maps[c].get(str(r.get(c) or ""), -1)))
        for c in FEATURES_NUM:
            v = r.get(c)
            try:
                fv = float(v)
                feats.append(fv if math.isfinite(fv) else float("nan"))
            except (TypeError, ValueError):
                feats.append(float("nan"))
        if with_flag:
            feats.append(1.0 if r["_domain"] == "real_money" else 0.0)
        X.append(feats)
        y.append(1 if (r.get("pnl") or 0) > 0 else 0)
    return X, y


def _train_eval(train_rows, eval_rows, cat_maps, with_flag, seed=42) -> Optional[dict]:
    # Native lgb.train API — the same surface the repo's LightGBM trainer uses
    # (no scikit-learn dependency on the trainer venv).
    import lightgbm as lgb
    import numpy as np

    Xtr, ytr = _encode(train_rows, cat_maps, with_flag)
    Xev, yev = _encode(eval_rows, cat_maps, with_flag)
    if not Xtr or not Xev or len(set(ytr)) < 2:
        return None
    cat_idx = list(range(len(FEATURES_CAT)))
    params = {
        "objective": "binary", "learning_rate": 0.05, "num_leaves": 31,
        "min_data_in_leaf": 20, "bagging_fraction": 0.9, "bagging_freq": 5,
        "feature_fraction": 0.9, "seed": seed, "verbose": -1,
    }
    booster = lgb.train(
        params,
        lgb.Dataset(np.array(Xtr), label=np.array(ytr),
                    categorical_feature=cat_idx, free_raw_data=False),
        num_boost_round=200,
    )
    p = np.asarray(booster.predict(np.array(Xev)))
    yhat = (p >= 0.5).astype(int)
    yev_a = np.array(yev)
    tp = int(((yhat == 1) & (yev_a == 1)).sum())
    fp = int(((yhat == 1) & (yev_a == 0)).sum())
    fn = int(((yhat == 0) & (yev_a == 1)).sum())
    acc = float((yhat == yev_a).mean())
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if prec and rec else 0.0
    brier = float(((p - yev_a) ** 2).mean())
    # rank AUC (ties=0.5)
    pos = p[yev_a == 1]
    neg = p[yev_a == 0]
    auc = None
    if len(pos) and len(neg):
        wins = sum((pos_i > neg).sum() + 0.5 * (pos_i == neg).sum() for pos_i in pos)
        auc = float(wins / (len(pos) * len(neg)))
    return {
        "n_train": len(Xtr), "n_train_pos": int(sum(ytr)),
        "n_eval": len(Xev), "n_eval_pos": int(sum(yev)),
        "epv_train": round(sum(ytr) / (len(FEATURES_CAT) + len(FEATURES_NUM)
                                       + (1 if with_flag else 0)), 2),
        "accuracy": round(acc, 4),
        "precision_win": round(prec, 4) if prec is not None else None,
        "recall_win": round(rec, 4) if rec is not None else None,
        "f1_win": round(f1, 4),
        "brier": round(brier, 4),
        "auc": round(auc, 4) if auc is not None else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True,
                    help="trade_outcomes data.jsonl (built with include_snapshots=true)")
    ap.add_argument("--accounts-yaml", default="config/accounts.yaml")
    ap.add_argument("--holdout-fraction", type=float, default=0.2,
                    help="fraction of REAL rows (chronological tail) held for eval")
    ap.add_argument("--folds", type=int, default=3,
                    help="extra chronological cut-points for stability")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    rows = _load_rows(Path(args.dataset))
    amap = _account_class_map(Path(args.accounts_yaml))
    for r in rows:
        r["_domain"] = _domain_of(str(r.get("account_id") or ""), amap)
    rows = [r for r in rows if r.get("pnl") is not None]
    rows.sort(key=_sort_key)
    real = [r for r in rows if r["_domain"] == "real_money"]
    paper = [r for r in rows if r["_domain"] == "paper"]
    unknown = [r for r in rows if r["_domain"] == "unknown"]

    # shared categorical code maps over ALL rows (fit once, both arms see the
    # same encoding; unseen at eval time can't occur)
    cat_maps: dict[str, dict] = {}
    for c in FEATURES_CAT:
        vals = sorted({str(r.get(c) or "") for r in rows})
        cat_maps[c] = {v: i for i, v in enumerate(vals)}

    def run_at(holdout_frac: float) -> dict:
        k = max(1, int(len(real) * holdout_frac))
        held = real[-k:]
        real_train = real[:-k]
        cutoff = _sort_key(held[0])
        # paper rows strictly BEFORE the eval window start — no temporal leak
        paper_train = [r for r in paper if _sort_key(r) < cutoff]
        return {
            "held_real_n": len(held),
            "held_real_pos": sum(1 for r in held if (r.get("pnl") or 0) > 0),
            "majority_baseline_acc": round(
                max(sum(1 for r in held if (r.get("pnl") or 0) > 0),
                    sum(1 for r in held if (r.get("pnl") or 0) <= 0)) / len(held), 4),
            "real_only": _train_eval(real_train, held, cat_maps, with_flag=False),
            "pooled_flag": _train_eval(real_train + paper_train, held, cat_maps,
                                       with_flag=True),
            "pooled_bare": _train_eval(real_train + paper_train, held, cat_maps,
                                       with_flag=False),
        }

    primary = run_at(args.holdout_fraction)
    stability = []
    for i in range(1, args.folds + 1):
        frac = args.holdout_fraction + i * 0.1
        if int(len(real) * frac) >= len(real) - 30:
            break
        stability.append({"holdout_fraction": round(frac, 2), **run_at(frac)})

    report = {
        "dataset": args.dataset,
        "rows_total": len(rows), "real_n": len(real), "paper_n": len(paper),
        "unknown_domain_n": len(unknown),
        "real_win_rate": round(sum(1 for r in real if (r.get("pnl") or 0) > 0)
                               / len(real), 4) if real else None,
        "paper_win_rate": round(sum(1 for r in paper if (r.get("pnl") or 0) > 0)
                                / len(paper), 4) if paper else None,
        "features": FEATURES_CAT + FEATURES_NUM + [DOMAIN_COL + " (pooled_flag only)"],
        "primary": primary,
        "stability": stability,
        "read_me": "Decision metric: pooled_* vs real_only ON THE HELD REAL SLICE "
                   "vs majority_baseline_acc. A pooled win that fails to also beat "
                   "the majority baseline is not a win (S-MLOPT-S6 bar).",
    }
    print(json.dumps(report, indent=1, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
