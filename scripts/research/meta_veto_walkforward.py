#!/usr/bin/env python3
"""M19 S1-v0 — does a meta-model carry WITHIN-CELL veto information on the costed harness corpus?

WHY THIS EXISTS (post-mortem finding, docs/research/M19-ai-trader-postmortem-and-staged-path-2026-09-28.md)
------------------------------------------------------------------------------------------------------------
Every retired M19 manifest predicted a price-derived proxy label (volatility
regime, direction) and was graded on macro_f1. None was ever graded on the
net-of-cost R of a trade decision, and the one trade-outcome head had n_eval=20.
The two later attempts at a real trade-selection head (M18/T1.3 ranker, M23
meta-label) both found that the pooled signal was BETWEEN-cell base rate ("this
leg bleeds"), which the roster already answers, and never established a
WITHIN-cell veto. S1-v0 asks that question directly, on the one corpus this
repo now has that is (a) net of the FULL cost stack, (b) config-exact, and
(c) committed: the per-trade rows behind `comms/strategy_evidence/*.json`
(`comms/strategy_evidence/runs/<date>/<leg>__trades.jsonl`).

WHAT IT COMPUTES
----------------
Population: for every evidence record, the trades file beside its own
`source_run` (so each leg is counted once, from the run the record was built
from). Each row is a harness trade with `entry_time`, `exit_time`, `net_r`
(fee + slippage + funding), `confidence`, `entry`, `sl`, `direction`.

Features (decision-time only, all available at `entry_time`):
  confidence, stop_dist_pct = |entry - sl| / entry, direction, hour (sin/cos),
  day-of-week, and the cell's TRAILING expectancy over trades that had already
  EXITED before this entry (`cell_trail_mean_r`, `cell_trail_winrate`,
  `cell_trail_n`) — strictly as-of, never the trade's own outcome.

Walk-forward: trades sorted by entry_time; the first `--train-frac` is the
warm-up train set; the remainder is cut into `--folds` contiguous OOS blocks.
For fold k the model is fit on every trade whose EXIT is at least
`--embargo-days` before the block's first entry (purge + embargo), so no
overlapping label leaks in. Model: standardized logistic regression on
`won = 1[net_r > 0]`, full-batch gradient descent, stdlib only.

THE VETO STATISTIC (registered in research/queue/RQ-20260928-001.yaml)
---------------------------------------------------------------------
Every OOS trade's net_r is DEMEANED WITHIN ITS CELL over the OOS population
(evaluation-time normalisation, applied identically to every comparator), which
removes the between-cell base rate the earlier attempts were fooled by. Then:

    veto_delta_r = mean(demeaned net_r of KEPT trades) - mean(demeaned net_r of ALL trades)

where KEPT = the trades NOT in the lowest `--veto-frac` of model scores within
each OOS fold. The same statistic is computed for a `confidence`-only veto (the
strategies' own signal), and a permutation null (`--perms` random vetoes of the
same size per fold) gives `perm_p` = P(random veto >= observed). `folds_positive`
counts OOS folds where the model veto's delta is > 0.

`--census` reports the population (legs, rows, folds) and computes NO statistic:
that is how a unit measures its n BEFORE registering a rule against it.

Tier-1 / offline / read-only: reads committed files, prints JSON, writes nothing
unless `--json` is given. No config, no registry, no order path, no VM.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EVIDENCE_DIR = os.path.join(ROOT, "comms", "strategy_evidence")

FEATURES = ["confidence", "stop_dist_pct", "is_long", "hour_sin", "hour_cos", "dow",
            "cell_trail_mean_r", "cell_trail_winrate", "cell_trail_n"]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def trades_files_from_evidence(evidence_dir: str) -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    """leg -> trades file, following each record's OWN source_run. Second item: legs skipped, with why."""
    out: Dict[str, str] = {}
    skipped: List[Tuple[str, str]] = []
    for f in sorted(glob.glob(os.path.join(evidence_dir, "*.json"))):
        leg = os.path.basename(f)[:-5]
        try:
            rec = json.load(open(f))
        except (OSError, ValueError) as e:  # unreadable is reported, never dropped silently
            skipped.append((leg, f"unreadable record: {e}"))
            continue
        src = rec.get("source_run") or ""
        if isinstance(src, dict):
            src = src.get("path") or ""
        if not src:
            skipped.append((leg, f"no source_run (coverage_state={rec.get('coverage_state')!r})"))
            continue
        tp = os.path.join(ROOT, src.replace("__bt.json", "__trades.jsonl"))
        if not os.path.exists(tp):
            skipped.append((leg, f"trades file missing beside source_run: {src}"))
            continue
        out[leg] = tp
    return out, skipped


def load_rows(files: Dict[str, str]) -> Tuple[List[dict], int]:
    rows: List[dict] = []
    unreadable = 0
    for leg, path in files.items():
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            et, xt = _parse_ts(str(d.get("entry_time", ""))), _parse_ts(str(d.get("exit_time", "")))
            nr = d.get("net_r")
            if et is None or xt is None or not isinstance(nr, (int, float)):
                unreadable += 1
                continue
            entry, sl = d.get("entry"), d.get("sl")
            sd = (abs(float(entry) - float(sl)) / float(entry)
                  if isinstance(entry, (int, float)) and isinstance(sl, (int, float)) and entry else None)
            conf = d.get("confidence")
            rows.append({
                "cell": leg, "entry_time": et, "exit_time": xt, "net_r": float(nr),
                "confidence": float(conf) if isinstance(conf, (int, float)) else None,
                "stop_dist_pct": sd,
                "is_long": 1.0 if str(d.get("direction", "")).lower() == "long" else 0.0,
                "hour_sin": math.sin(2 * math.pi * et.hour / 24.0),
                "hour_cos": math.cos(2 * math.pi * et.hour / 24.0),
                "dow": float(et.weekday()),
            })
    rows.sort(key=lambda r: r["entry_time"])
    return rows, unreadable


def add_trailing_cell_stats(rows: List[dict]) -> None:
    """As-of cell expectancy: only trades of the same cell that EXITED before this entry."""
    by_cell: Dict[str, List[dict]] = {}
    for r in rows:
        by_cell.setdefault(r["cell"], []).append(r)
    for cell, rs in by_cell.items():
        exits = sorted(rs, key=lambda r: r["exit_time"])
        for r in rs:
            prior = [p["net_r"] for p in exits if p["exit_time"] < r["entry_time"]]
            n = len(prior)
            r["cell_trail_n"] = float(n)
            r["cell_trail_mean_r"] = (sum(prior) / n) if n else 0.0
            r["cell_trail_winrate"] = (sum(1 for p in prior if p > 0) / n) if n else 0.0


# ---------------------------------------------------------------------------
# model — standardized logistic regression, full-batch GD (stdlib)
# ---------------------------------------------------------------------------

def _matrix(rows: Sequence[dict]) -> List[List[float]]:
    return [[(r.get(f) if r.get(f) is not None else 0.0) for f in FEATURES] for r in rows]


class Logit:
    def __init__(self, l2: float = 1e-3, steps: int = 400, lr: float = 0.1):
        self.l2, self.steps, self.lr = l2, steps, lr
        self.mu: List[float] = []
        self.sd: List[float] = []
        self.w: List[float] = []
        self.b = 0.0

    def fit(self, X: List[List[float]], y: List[int]) -> "Logit":
        n, k = len(X), len(X[0])
        self.mu = [sum(row[j] for row in X) / n for j in range(k)]
        self.sd = [math.sqrt(sum((row[j] - self.mu[j]) ** 2 for row in X) / n) or 1.0 for j in range(k)]
        Z = [[(row[j] - self.mu[j]) / self.sd[j] for j in range(k)] for row in X]
        self.w, self.b = [0.0] * k, 0.0
        for _ in range(self.steps):
            gw, gb = [0.0] * k, 0.0
            for z, t in zip(Z, y):
                p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, sum(wj * zj for wj, zj in zip(self.w, z)) + self.b))))
                e = p - t
                gb += e
                for j in range(k):
                    gw[j] += e * z[j]
            self.w = [wj - self.lr * (gw[j] / n + self.l2 * wj) for j, wj in enumerate(self.w)]
            self.b -= self.lr * gb / n
        return self

    def score(self, X: List[List[float]]) -> List[float]:
        out = []
        for row in X:
            z = sum(self.w[j] * (row[j] - self.mu[j]) / self.sd[j] for j in range(len(row))) + self.b
            out.append(1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z)))))
        return out


def auc(scores: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    pos = [s for s, lab in zip(scores, labels) if lab == 1]
    neg = [s for s, lab in zip(scores, labels) if lab == 0]
    if not pos or not neg:
        return None
    # rank-based, ties count half
    allv = sorted(set(scores))
    rank = {v: i for i, v in enumerate(allv)}
    wins = 0.0
    neg_sorted = sorted(rank[s] for s in neg)
    import bisect
    for s in pos:
        r = rank[s]
        lo = bisect.bisect_left(neg_sorted, r)
        hi = bisect.bisect_right(neg_sorted, r)
        wins += lo + 0.5 * (hi - lo)
    return wins / (len(pos) * len(neg))


# ---------------------------------------------------------------------------
# walk-forward + veto statistic
# ---------------------------------------------------------------------------

def make_folds(rows: List[dict], folds: int, train_frac: float) -> List[List[int]]:
    n = len(rows)
    start = int(n * train_frac)
    idx = list(range(start, n))
    size = max(1, len(idx) // folds)
    blocks = [idx[i * size:(i + 1) * size] for i in range(folds)]
    if len(idx) > folds * size:
        blocks[-1].extend(idx[folds * size:])
    return [b for b in blocks if b]


def veto_delta(demeaned: Sequence[float], keep: Sequence[bool]) -> Optional[float]:
    kept = [d for d, k in zip(demeaned, keep) if k]
    if not kept or len(kept) == len(demeaned):
        return None
    return sum(kept) / len(kept) - sum(demeaned) / len(demeaned)


def veto_mask(scores: Sequence[float], frac: float) -> List[bool]:
    """Keep everything except the lowest `frac` of scores (ties broken by order)."""
    n = len(scores)
    k = int(round(n * frac))
    order = sorted(range(n), key=lambda i: (scores[i], i))
    drop = set(order[:k])
    return [i not in drop for i in range(n)]


def run(rows: List[dict], folds: int, train_frac: float, embargo_days: int, veto_frac: float,
        perms: int, seed: int, min_train: int = 50) -> dict:
    add_trailing_cell_stats(rows)
    blocks = make_folds(rows, folds, train_frac)
    rng = random.Random(seed)
    oos_idx: List[int] = []
    model_scores: List[float] = []
    conf_scores: List[float] = []
    fold_of: List[int] = []
    fold_notes = []
    for fi, block in enumerate(blocks):
        first_entry = rows[block[0]]["entry_time"]
        cutoff = first_entry - timedelta(days=embargo_days)
        train = [i for i in range(block[0]) if rows[i]["exit_time"] <= cutoff]
        if len(train) < min_train:
            fold_notes.append({"fold": fi, "state": "skipped_insufficient_train", "n_train": len(train)})
            continue
        X = _matrix([rows[i] for i in train])
        y = [1 if rows[i]["net_r"] > 0 else 0 for i in train]
        if len(set(y)) < 2:
            fold_notes.append({"fold": fi, "state": "skipped_single_class_train", "n_train": len(train)})
            continue
        m = Logit().fit(X, y)
        s = m.score(_matrix([rows[i] for i in block]))
        oos_idx.extend(block)
        model_scores.extend(s)
        conf_scores.extend([(rows[i]["confidence"] if rows[i]["confidence"] is not None else 0.0) for i in block])
        fold_of.extend([fi] * len(block))
        fold_notes.append({"fold": fi, "state": "scored", "n_train": len(train), "n_test": len(block),
                           "test_from": rows[block[0]]["entry_time"].isoformat(),
                           "test_to": rows[block[-1]]["entry_time"].isoformat()})
    if not oos_idx:
        return {"read_state": "no_data", "n_oos": 0, "folds": fold_notes}
    net = [rows[i]["net_r"] for i in oos_idx]
    cells = [rows[i]["cell"] for i in oos_idx]
    cell_mean: Dict[str, float] = {}
    for c in set(cells):
        v = [n for n, cc in zip(net, cells) if cc == c]
        cell_mean[c] = sum(v) / len(v)
    dem = [n - cell_mean[c] for n, c in zip(net, cells)]
    won = [1 if n > 0 else 0 for n in net]
    dem_pos = [1 if d > 0 else 0 for d in dem]

    def per_fold(scores: Sequence[float]) -> Tuple[Optional[float], List[Optional[float]], List[bool]]:
        keep_all: List[bool] = [True] * len(scores)
        deltas: List[Optional[float]] = []
        for fi in sorted(set(fold_of)):
            ids = [k for k, f in enumerate(fold_of) if f == fi]
            mask = veto_mask([scores[k] for k in ids], veto_frac)
            for k, keep in zip(ids, mask):
                keep_all[k] = keep
            deltas.append(veto_delta([dem[k] for k in ids], mask))
        return veto_delta(dem, keep_all), deltas, keep_all

    model_delta, model_fold_deltas, keep_model = per_fold(model_scores)
    conf_delta, conf_fold_deltas, _ = per_fold(conf_scores)
    # permutation null: random vetoes of the same size per fold
    null: List[float] = []
    for _ in range(perms):
        rs = [rng.random() for _ in oos_idx]
        d, _, _ = per_fold(rs)
        if d is not None:
            null.append(d)
    perm_p = (sum(1 for d in null if d >= (model_delta or 0.0)) + 1) / (len(null) + 1) if null else None
    conf_perm_p = (sum(1 for d in null if d >= (conf_delta or 0.0)) + 1) / (len(null) + 1) if null else None
    return {
        "read_state": "measured",
        "n_oos": len(oos_idx),
        "n_vetoed": sum(1 for k in keep_model if not k),
        "n_cells_oos": len(cell_mean),
        "veto_frac": veto_frac,
        "veto_delta_r": model_delta,
        "fold_deltas": model_fold_deltas,
        "folds_scored": len([d for d in model_fold_deltas if d is not None]),
        "folds_positive": sum(1 for d in model_fold_deltas if d is not None and d > 0),
        "perm_p": perm_p,
        "perms": len(null),
        "auc_oos_won": auc(model_scores, won),
        "auc_oos_demeaned_pos": auc(model_scores, dem_pos),
        "baseline_confidence": {"veto_delta_r": conf_delta, "fold_deltas": conf_fold_deltas,
                                "perm_p": conf_perm_p, "auc_oos_won": auc(conf_scores, won)},
        "take_all_mean_net_r_oos": sum(net) / len(net),
        "kept_mean_net_r_oos": (sum(n for n, k in zip(net, keep_model) if k) / max(1, sum(keep_model))),
        "features": FEATURES,
        "folds": fold_notes,
    }


# ---------------------------------------------------------------------------
# self-test — planted positive and a null, both synthetic
# ---------------------------------------------------------------------------

def _synthetic(n_cells: int, per_cell: int, signal: float, seed: int) -> List[dict]:
    rng = random.Random(seed)
    rows: List[dict] = []
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for c in range(n_cells):
        base = rng.uniform(-0.3, 0.3)  # between-cell base rate the statistic must IGNORE
        for k in range(per_cell):
            et = t0 + timedelta(hours=6 * k + c)
            conf = rng.random()
            # within-cell: high confidence -> better net_r when signal > 0
            nr = base + signal * (conf - 0.5) + rng.gauss(0, 0.6)
            rows.append({"cell": f"c{c}", "entry_time": et, "exit_time": et + timedelta(hours=4),
                         "net_r": nr, "confidence": conf, "stop_dist_pct": rng.uniform(0.005, 0.03),
                         "is_long": float(rng.random() < 0.5),
                         "hour_sin": math.sin(2 * math.pi * et.hour / 24), "hour_cos": math.cos(2 * math.pi * et.hour / 24),
                         "dow": float(et.weekday())})
    rows.sort(key=lambda r: r["entry_time"])
    return rows


def _self_test() -> int:
    fails = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            fails.append(label)

    pos = run(_synthetic(6, 250, signal=1.2, seed=1), folds=4, train_frac=0.4, embargo_days=1,
              veto_frac=0.25, perms=60, seed=1)
    check("planted positive: measured", pos["read_state"] == "measured")
    check("planted positive: veto_delta_r > 0", (pos["veto_delta_r"] or 0) > 0)
    check("planted positive: perm_p < 0.05", (pos["perm_p"] or 1) < 0.05)
    check("planted positive: every scored fold positive", pos["folds_positive"] == pos["folds_scored"] > 0)
    null = run(_synthetic(6, 250, signal=0.0, seed=2), folds=4, train_frac=0.4, embargo_days=1,
               veto_frac=0.25, perms=60, seed=2)
    check("null: measured", null["read_state"] == "measured")
    check("null: perm_p not significant", (null["perm_p"] or 0) > 0.05)
    check("null: |veto_delta_r| small", abs(null["veto_delta_r"] or 0) < 0.08)
    # between-cell base rate alone must NOT register as within-cell veto information
    base_only = _synthetic(6, 250, signal=0.0, seed=3)
    for r in base_only:
        r["confidence"] = 0.5  # no within-cell signal at all; only cell base rates differ
    bo = run(base_only, folds=4, train_frac=0.4, embargo_days=1, veto_frac=0.25, perms=60, seed=3)
    check("base-rate-only: perm_p not significant", (bo["perm_p"] or 0) > 0.05)
    # embargo/purge: no training trade may exit inside the embargo before the test block
    blocks = make_folds(pos_rows := _synthetic(3, 100, 0.5, 4), 4, 0.4)
    add_trailing_cell_stats(pos_rows)
    first = pos_rows[blocks[0][0]]["entry_time"]
    train = [i for i in range(blocks[0][0]) if pos_rows[i]["exit_time"] <= first - timedelta(days=1)]
    check("purge: all training exits precede test start minus embargo",
          all(pos_rows[i]["exit_time"] <= first - timedelta(days=1) for i in train))
    # as-of trailing stats never see the trade's own outcome
    check("trailing stats are as-of (first trade of a cell has n=0)",
          all(r["cell_trail_n"] == 0.0 for r in pos_rows if r["entry_time"] == min(
              x["entry_time"] for x in pos_rows if x["cell"] == r["cell"])))
    print(f"meta_veto_walkforward --self-test: {'OK' if not fails else 'FAILED ' + str(fails)}")
    return 0 if not fails else 1


# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    ap.add_argument("--trades", action="append", default=[],
                    help="explicit trades.jsonl path(s); overrides --evidence-dir. Cell = file stem.")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--train-frac", type=float, default=0.4)
    ap.add_argument("--embargo-days", type=int, default=7)
    ap.add_argument("--veto-frac", type=float, default=0.25)
    ap.add_argument("--perms", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--census", action="store_true", help="population only; compute NO statistic")
    ap.add_argument("--json", default=None, help="write the result JSON here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.trades:
        files = {os.path.basename(p).replace("__trades.jsonl", "").replace(".jsonl", ""): p for p in a.trades}
        skipped: List[Tuple[str, str]] = []
    else:
        files, skipped = trades_files_from_evidence(a.evidence_dir)
    rows, unreadable = load_rows(files)
    census = {
        "population": "one costed harness trade per row; one trades file per evidence record, "
                      "read from the record's own source_run",
        "legs": len(files), "rows": len(rows), "unreadable_rows": unreadable,
        "legs_skipped": [{"leg": leg, "why": why} for leg, why in skipped],
        "entry_time_min": rows[0]["entry_time"].isoformat() if rows else None,
        "entry_time_max": rows[-1]["entry_time"].isoformat() if rows else None,
        "folds": a.folds, "train_frac": a.train_frac, "embargo_days": a.embargo_days,
    }
    if a.census:
        blocks = make_folds(rows, a.folds, a.train_frac)
        census["oos_rows"] = sum(len(b) for b in blocks)
        census["expected_vetoed_at_veto_frac"] = int(round(census["oos_rows"] * a.veto_frac))
        out = {"census": census, "statistic_computed": False}
    else:
        res = run(rows, a.folds, a.train_frac, a.embargo_days, a.veto_frac, a.perms, a.seed)
        out = {"census": census, "statistic_computed": True, "result": res}
    text = json.dumps(out, indent=2, default=str)
    print(text)
    if a.json:
        with open(a.json, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
