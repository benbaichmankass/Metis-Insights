#!/usr/bin/env python3
"""Sweep the v1 conviction-blend weights against realized outcomes (design § 4.2,
§ 6 decision #4).

Replaces the hand-set ``DEFAULT_CONVICTION_WEIGHTS`` (c_strat 0.45 / c_setup 0.20
/ c_wr 0.20 / c_reg 0.15, ``src/runtime/conviction.py``) with **evidence** — or
confirms the hand-set defaults stand when the data is too thin to choose.

Corpus
------
One row per closed, filled, non-backtest order package — the calibrated
conviction-lens inputs (``c_strat`` always present; ``c_setup`` / ``c_wr`` /
``c_reg`` only when a head scored the decision) paired with ``won`` (pnl>0). This
is exactly the ``conviction_meta`` dataset family, so we **reuse its builder**
(no train/serve skew). Optionally augment with a backtest ``--emit-trades`` corpus
(``(confidence, won)`` rows → ``c_strat``-only, ``source=backtest``) to thicken
``c_strat`` evidence; live rows are weighted up (``--live-weight``).

Method
------
A candidate weight vector IS the model: per row, blend the present inputs with the
candidate weights (the live ``compute_conviction``), then score the blended
conviction against ``won`` with **rank-AUC** (discrimination, reuses
``ml.promotion.attribution.rank_auc``) + **Brier** (calibration). Selection is
**out-of-sample** under a **purged walk-forward** split (chronological folds, a gap
between train and test), never in-sample. We only recommend a *change* when a
candidate robustly beats the hand-set default's OOS rank-AUC by a margin AND the
**multi-input** sample is large enough to identify the relative weights — a 4-way
weighting is unidentifiable from ``c_strat``-only rows no matter how many there
are. Otherwise the finding is "keep the hand-set defaults" (a valid § 6 #4
outcome).

Usage
-----
    python -m scripts.ml.sweep_conviction_weights --db /data/bot-data/trade_journal.db \
        --backtest-corpus 'runtime_logs/calibration/*_trades.jsonl' \
        --live-weight 3 --out runtime_logs/conviction_weight_sweep.json

Tier-1, offline, read-only — never writes config. The chosen weights (if any) ship
via a separate operator-approved PR editing ``DEFAULT_CONVICTION_WEIGHTS``.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# Repo-root on path when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.promotion.attribution import rank_auc  # noqa: E402
from src.runtime.conviction import (  # noqa: E402
    DEFAULT_CONVICTION_WEIGHTS,
    compute_conviction,
)

_SLOTS: tuple[str, ...] = ("c_strat", "c_setup", "c_wr", "c_reg")
# A weighting over c_setup/c_wr/c_reg vs c_strat is only identifiable from rows
# carrying >=2 inputs. Below this many multi-input rows we will not re-weight.
_MIN_MULTI_INPUT_ROWS = 150
# Minimum OOS rank-AUC margin a candidate must beat the hand-set default by.
_MIN_AUC_MARGIN = 0.02


@dataclass
class Row:
    inputs: dict[str, float]
    won: bool
    weight: float = 1.0
    source: str = "live"
    order: int = 0  # chronological rank (for the walk-forward split)


# --------------------------------------------------------------------------- #
# corpus assembly
# --------------------------------------------------------------------------- #


def load_live_rows(db_path: str | Path) -> list[Row]:
    """Build the live conviction_meta corpus via the dataset builder."""
    from ml.datasets.families.conviction_meta import ConvictionMetaBuilder

    builder = ConvictionMetaBuilder()
    rows: list[Row] = []
    for i, rec in enumerate(builder.iter_rows(db_path=db_path)):
        inputs = {s: float(rec[s]) for s in _SLOTS if rec.get(s) is not None}
        if not inputs:
            continue
        rows.append(Row(inputs=inputs, won=bool(rec.get("won")), source="live",
                        order=i))
    return rows


def load_backtest_rows(globs: Sequence[str]) -> list[Row]:
    """Load ``(confidence, won|net_r)`` rows from --emit-trades JSONL corpora.

    These carry only ``c_strat`` (the strategy confidence) — no head inputs — so
    they thicken c_strat evidence but cannot inform the head weights.
    """
    rows: list[Row] = []
    order = 0
    for pattern in globs:
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        conf = rec.get("confidence")
                        if conf is None:
                            continue
                        won = rec.get("won")
                        if won is None:
                            nr = rec.get("net_r")
                            if nr is None:
                                continue
                            won = float(nr) > 0
                        rows.append(Row(inputs={"c_strat": float(conf)},
                                        won=bool(won), source="backtest",
                                        order=order))
                        order += 1
            except OSError:
                continue
    return rows


def input_presence(rows: Iterable[Row]) -> dict[str, int]:
    counts = {s: 0 for s in _SLOTS}
    counts["__multi__"] = 0
    counts["__total__"] = 0
    for r in rows:
        counts["__total__"] += 1
        present = [s for s in _SLOTS if s in r.inputs]
        for s in present:
            counts[s] += 1
        if len(present) >= 2:
            counts["__multi__"] += 1
    return counts


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


def _blend(row: Row, weights: dict[str, float]) -> float:
    r = compute_conviction(row.inputs, weights=weights)
    return r.conviction if r.conviction is not None else 0.0


def score_weights(rows: Sequence[Row], weights: dict[str, float]) -> dict[str, Any]:
    """Rank-AUC + (weighted) Brier of the blended conviction vs ``won``."""
    wins: list[float] = []
    losses: list[float] = []
    se = 0.0
    wsum = 0.0
    for r in rows:
        s = _blend(r, weights)
        (wins if r.won else losses).append(s)
        se += r.weight * (s - (1.0 if r.won else 0.0)) ** 2
        wsum += r.weight
    auc = rank_auc(wins, losses)
    brier = se / wsum if wsum else None
    return {"auc": auc, "brier": brier, "n": len(rows),
            "n_win": len(wins), "n_loss": len(losses)}


def _normalize(w: dict[str, float]) -> dict[str, float]:
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()} if tot > 0 else dict(w)


def candidate_grid(step: float = 0.15) -> list[dict[str, float]]:
    """A coarse simplex grid over the 4 slot weights + reference anchors."""
    levels = [round(i * step, 4) for i in range(int(1.0 / step) + 1)]
    grid: list[dict[str, float]] = []
    seen: set[tuple] = set()
    for a in levels:
        for b in levels:
            for c in levels:
                for d in levels:
                    if a + b + c + d == 0:
                        continue
                    w = _normalize({"c_strat": a, "c_setup": b, "c_wr": c, "c_reg": d})
                    key = tuple(round(w[s], 3) for s in _SLOTS)
                    if key in seen:
                        continue
                    seen.add(key)
                    grid.append(w)
    # anchors always evaluated
    grid.append(_normalize(dict(DEFAULT_CONVICTION_WEIGHTS)))
    grid.append({s: 0.25 for s in _SLOTS})                       # uniform
    grid.append({"c_strat": 1.0, "c_setup": 0.0, "c_wr": 0.0, "c_reg": 0.0})
    return grid


def walk_forward_folds(
    rows: Sequence[Row], n_folds: int = 4, purge: int = 0,
) -> list[tuple[list[Row], list[Row]]]:
    """Chronological expanding-window folds with a purge gap between train/test."""
    ordered = sorted(rows, key=lambda r: r.order)
    n = len(ordered)
    if n < n_folds * 2:
        return []
    fold_size = n // (n_folds + 1)
    out: list[tuple[list[Row], list[Row]]] = []
    for k in range(1, n_folds + 1):
        split = fold_size * k
        train = ordered[: max(0, split - purge)]
        test = ordered[split: split + fold_size]
        if train and test:
            out.append((train, test))
    return out


def evaluate_oos(
    rows: Sequence[Row], weights: dict[str, float], *, n_folds: int = 4, purge: int = 0,
) -> dict[str, Any]:
    """Mean OOS rank-AUC / Brier of a fixed weight vector over walk-forward folds."""
    folds = walk_forward_folds(rows, n_folds=n_folds, purge=purge)
    if not folds:
        # too few rows for a split → single in-sample pass (flagged)
        s = score_weights(rows, weights)
        return {"auc": s["auc"], "brier": s["brier"], "folds": 0,
                "split": "in_sample"}
    aucs = [score_weights(test, weights)["auc"] for _, test in folds]
    briers = [score_weights(test, weights)["brier"] for _, test in folds]
    aucs = [a for a in aucs if a is not None]
    briers = [b for b in briers if b is not None]
    return {
        "auc": (sum(aucs) / len(aucs)) if aucs else None,
        "brier": (sum(briers) / len(briers)) if briers else None,
        "folds": len(folds),
        "split": "walk_forward",
    }


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def run_sweep(
    rows: list[Row], *, n_folds: int = 4, purge: int = 0,
) -> dict[str, Any]:
    presence = input_presence(rows)
    default_w = _normalize(dict(DEFAULT_CONVICTION_WEIGHTS))
    default_oos = evaluate_oos(rows, default_w, n_folds=n_folds, purge=purge)

    ranked: list[dict[str, Any]] = []
    for w in candidate_grid():
        oos = evaluate_oos(rows, w, n_folds=n_folds, purge=purge)
        if oos["auc"] is None:
            continue
        ranked.append({"weights": {s: round(w[s], 4) for s in _SLOTS}, **oos})
    ranked.sort(key=lambda d: (-(d["auc"] or 0.0), d["brier"] if d["brier"] is not None else 1.0))

    best = ranked[0] if ranked else None
    multi = presence.get("__multi__", 0)
    identifiable = multi >= _MIN_MULTI_INPUT_ROWS
    beats_default = (
        best is not None
        and default_oos["auc"] is not None
        and best["auc"] is not None
        and (best["auc"] - default_oos["auc"]) >= _MIN_AUC_MARGIN
    )
    recommend_change = bool(identifiable and beats_default)

    if recommend_change:
        recommendation = "adopt_swept_weights"
        rationale = (
            f"{multi} multi-input rows (>= {_MIN_MULTI_INPUT_ROWS}) and the best "
            f"candidate OOS rank-AUC {best['auc']:.3f} beats the hand-set default "
            f"{default_oos['auc']:.3f} by >= {_MIN_AUC_MARGIN}."
        )
    elif not identifiable:
        recommendation = "keep_hand_set_defaults"
        rationale = (
            f"Only {multi} multi-input rows (< {_MIN_MULTI_INPUT_ROWS} required to "
            "identify the head weights vs c_strat). A 4-way weighting is not "
            "identifiable from c_strat-only rows; keep the documented hand-set "
            "defaults until the soak accrues multi-input decisions."
        )
    else:
        recommendation = "keep_hand_set_defaults"
        rationale = (
            "No candidate beats the hand-set default's OOS rank-AUC by the "
            f"{_MIN_AUC_MARGIN} margin — the default is robust; do not overfit."
        )

    return {
        "n_rows": len(rows),
        "n_live": sum(1 for r in rows if r.source == "live"),
        "n_backtest": sum(1 for r in rows if r.source == "backtest"),
        "input_presence": presence,
        "default_weights": {s: round(default_w[s], 4) for s in _SLOTS},
        "default_oos": default_oos,
        "best_candidate": best,
        "top5": ranked[:5],
        "min_multi_input_rows": _MIN_MULTI_INPUT_ROWS,
        "min_auc_margin": _MIN_AUC_MARGIN,
        "identifiable": identifiable,
        "beats_default": beats_default,
        "recommendation": recommendation,
        "rationale": rationale,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", help="trade_journal.db path (live conviction_meta corpus)")
    ap.add_argument("--backtest-corpus", action="append", default=[],
                    help="glob(s) for --emit-trades JSONL (c_strat-only augment)")
    ap.add_argument("--live-weight", type=float, default=1.0,
                    help="row weight multiplier for live rows (default 1.0)")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--purge", type=int, default=0)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)

    rows: list[Row] = []
    if args.db:
        try:
            live = load_live_rows(args.db)
            for r in live:
                r.weight = args.live_weight
            rows.extend(live)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: live corpus load failed: {exc}", file=sys.stderr)
    if args.backtest_corpus:
        rows.extend(load_backtest_rows(args.backtest_corpus))

    if not rows:
        print("ERROR: empty corpus (need --db and/or --backtest-corpus)", file=sys.stderr)
        return 2

    report = run_sweep(rows, n_folds=args.folds, purge=args.purge)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)
    print(f"\nRECOMMENDATION: {report['recommendation']} — {report['rationale']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
