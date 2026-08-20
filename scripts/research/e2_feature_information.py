#!/usr/bin/env python3
"""M20 · **E2** — does any feature in the in-trade panel carry information about forward R?

Step **E2** of `docs/design/exit-mechanism-construction-PROCESS.md`. E2 sits between
E1 (widen the decision surface) and E3 (design levers), and its whole job is to stop
E3 being guesswork:

    *"a feature that does not beat a shuffled-label control carries no information,
    and no lever built on it can work."*

**This step had never been run before 2026-08-20.** Two things had to be true first,
and neither was:

1. **The thing that LOOKS like E2 already existing is the leaky version.**
   ``analyze_exit_head._univariate_fdr`` is called on the entire row set — no folds,
   no purge, no embargo, no grouping — so its analytic q-values assume the row
   independence that overlapping triple-barrier labels violate by construction. This
   module therefore **imports the splitter** (``_grouped_purged_folds``) and
   deliberately **does not reuse the univariate**.
2. **No shuffled-label control existed anywhere in the repo.** ``shuffled_label`` /
   ``label_shuffle`` / ``permutation_test`` matched zero files; the only ``shuffle``
   is permutation *importance* (``analyze_research_panel``), which permutes a
   **feature**, not the label — a different null answering a different question.
   E2's declared falsifier had no implementation. This is it.

---

## The statistic, and why it is shaped this way

Per feature, per fold: **Spearman rank association** between the feature and
``forward_r``, on that fold's **test** rows. The headline statistic is the
**absolute mean of the per-fold Spearman values**.

- **Rank-based**, so one outlier bar cannot manufacture a hit.
- **Mean of SIGNED per-fold values, then absolute** — not the mean of absolutes.
  A feature whose sign flips fold to fold cancels toward zero and correctly fails.
  Mean-of-absolutes would reward exactly that instability.

## The null, and the one detail that decides whether it means anything

⚠️ **The label shuffle is at the ``trade_id`` BLOCK level, never the row level.**

Rows within one trade share an overlapping label window and are strongly dependent.
Row-level shuffling destroys that dependence, producing an artificially tight null
that a trade-structured feature would "beat" — a harness that manufactures
positives. Block shuffling permutes whole trades' label sequences among trades,
leaving the within-trade dependence intact.

⚠️ **The inflation requires BOTH series to be trade-structured, not just the label.**
This module's first self-test asserted the block null is always the wider one and
**failed**, measured on an i.i.d.-per-row probe feature (block 95th 0.0917 vs row
95th 0.1045). That result is correct and the original rationale was overbroad: if
the feature is independent across rows, the label's block structure alone barely
moves the null. It matters here because the panel's features are **not** i.i.d. —
``running_mfe_r``, ``upnl_r``, ``bars_in_trade``, ``dist_to_stop_atr`` are all
functions of the trade's own path and are strongly autocorrelated within a trade,
which is exactly the regime where a row-level null is too tight. The self-test now
probes with a trade-structured feature, i.e. the case the panel actually presents.

Trades differ in length, so a donor block is **cycled** to fill a longer recipient.
That is a stated approximation, not a silent one: it is reported as
``shuffle_scheme: "trade_block_cyclic"`` in the artifact.

## Two decision rules, and the multiple-comparisons one is the binding one

Scoring N features against a per-feature null at α inflates the family-wise error
rate — with 16 features at α=0.05 roughly one spurious "informative" is *expected*.
So the artifact reports both, and the **family-wise rule is the pre-registered
decision**:

- ``informative_pointwise`` — beats the feature's OWN shuffled null. Reported for
  diagnosis; **not** the decision.
- ``informative_fwer`` — beats the **(1−α) quantile of the max-statistic null**
  (Westfall–Young): each shuffle records the LARGEST statistic across all real
  features, and the threshold is that distribution's upper quantile. Controls FWER
  without assuming the features are independent, which they emphatically are not.

The controls are scored **outside** the family: including the positive control in
the max-statistic null would inflate the threshold with a synthetic it was designed
to clear.

## The harness-validity gate — a negative is inadmissible unless this passes

Every run injects two synthetic features and reports both:

- **positive control** ``__ctrl_signal`` = a monotone function of the label plus
  noise. It **must** reach ``informative_fwer``.
- **negative control** ``__ctrl_noise`` = pure RNG, independent of everything. It
  **must not** reach ``informative_pointwise``.

If either misbehaves the harness is broken and **no negative from that run is
admissible** — reported as ``harness_valid: false``, and the verdict becomes
``harness_invalid`` rather than a result. A control that cannot fire is not a
control; this is the "every new measurement needs a positive control that can
actually fire" rule made mechanical.

## Underpowered is UNMEASURED, not negative

``_grouped_purged_folds`` needs ``n_folds + 1`` trades to yield anything at all, and
a handful of trades yields a null so wide nothing could ever clear it. A run below
the declared floors returns ``verdict: "unmeasured"`` with the binding floor named.
**An underpowered null is not a negative result** — reporting one as "no feature
carries information" would be the same sin as a green run that measured nothing.

Observe-only, read-only, Tier-1. Nothing here touches the order path.

Usage::

    python scripts/research/e2_feature_information.py --panel exit_head_panel.jsonl
    python scripts/research/e2_feature_information.py --panel p.jsonl \
        --n-folds 4 --embargo-bars 12 --n-shuffles 2000 --alpha 0.05
    python scripts/research/e2_feature_information.py --selftest
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

def _repo_root() -> Path:
    """Locate the repo so the splitter import works from OUTSIDE the tree too.

    This tool is transported to the trainer and run from a scratch dir, where
    ``parents[2]`` resolves to ``/`` and the import dies with a bare
    ``ModuleNotFoundError: No module named 'scripts'`` — which reads like a
    missing dependency rather than a wrong working directory. Each candidate is
    confirmed by the presence of the module actually being imported, so a root
    is never *assumed* to be right.
    """
    import os

    cands = []
    env = os.environ.get("METIS_REPO_ROOT")
    if env:
        cands.append(Path(env))
    cands.append(Path(__file__).resolve().parents[2])
    cands.append(Path.cwd())
    for c in cands:
        if (c / "scripts" / "research" / "analyze_exit_head.py").is_file():
            return c
    raise SystemExit(
        "E2: cannot locate the repo root — analyze_exit_head.py was not found "
        f"under any of {[str(c) for c in cands]}. Run from inside the repo, or "
        "set METIS_REPO_ROOT=/path/to/repo. (The splitter is imported, never "
        "copied, so this tool cannot run without the repo on the path.)"
    )


_REPO_ROOT = _repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The splitter is IMPORTED, never re-implemented: a second copy is free to drift
# from the one the exit head is validated under, and then the two answers disagree
# for reasons nobody can locate. `_univariate_fdr` from the same module is
# deliberately NOT imported — it is the pooled, un-purged version (see module doc).
from scripts.research.analyze_exit_head import (  # noqa: E402
    _dense_feats,
    _grouped_purged_folds,
    load_panel,
)

TARGET_COL = "forward_r"
CTRL_SIGNAL = "__ctrl_signal"
CTRL_NOISE = "__ctrl_noise"
SHUFFLE_SCHEME = "trade_block_cyclic"


# ---------------------------------------------------------------------------
# rank statistics (stdlib — this runs on the trainer, which has no guaranteed scipy)
# ---------------------------------------------------------------------------


def average_ranks(values: Sequence[float]) -> List[float]:
    """Ranks with ties averaged — the tie handling Spearman requires.

    Integer-valued and heavily-tied features are common in this panel
    (``bars_in_trade``, presence flags), and naive ordinal ranking would
    silently impose an arbitrary order on tied rows.
    """
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n < 3 or n != len(y):
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 1e-15 or syy <= 1e-15:
        # A constant vector has no correlation with anything. `None` says "not
        # defined"; returning 0.0 would assert a measured absence of association.
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) < 3 or len(x) != len(y):
        return None
    return pearson(average_ranks(x), average_ranks(y))


def quantile(sorted_vals: Sequence[float], q: float) -> Optional[float]:
    """Upper quantile of an ALREADY-SORTED sample (linear interpolation)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_vals[0])
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac)


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------


def inject_controls(rows: List[Dict[str, Any]], *, seed: int, signal_noise: float = 1.0) -> None:
    """Add the positive and negative control columns, in place.

    ``__ctrl_signal`` is a monotone function of the row's own label plus gaussian
    noise — it MUST be detectable, and a run where it is not is a broken run.
    ``__ctrl_noise`` is independent of everything and MUST NOT be detectable.
    """
    rng = random.Random(seed)
    for r in rows:
        y = r.get(TARGET_COL)
        r[CTRL_SIGNAL] = None if y is None else float(y) + rng.gauss(0.0, signal_noise)
        r[CTRL_NOISE] = rng.gauss(0.0, 1.0)


# ---------------------------------------------------------------------------
# the block shuffle
# ---------------------------------------------------------------------------


def trade_blocks(rows: Sequence[Dict[str, Any]], idx: Sequence[int]) -> List[List[int]]:
    """Row indices grouped by ``trade_id``, each block in original row order."""
    by_trade: Dict[Any, List[int]] = defaultdict(list)
    for i in idx:
        by_trade[rows[i].get("trade_id")].append(i)
    return [by_trade[t] for t in sorted(by_trade, key=lambda t: (str(t)))]


def block_shuffled_labels(
    blocks: Sequence[Sequence[int]],
    labels: Dict[int, float],
    rng: random.Random,
) -> Dict[int, float]:
    """Permute whole trades' label sequences among trades.

    Preserves the within-trade dependence a row-level shuffle would destroy. A
    donor block shorter than its recipient is **cycled**; this is the declared
    ``trade_block_cyclic`` approximation, reported in the artifact rather than
    left implicit.
    """
    n = len(blocks)
    donors = list(range(n))
    rng.shuffle(donors)
    out: Dict[int, float] = {}
    for recipient, donor in enumerate(donors):
        src = blocks[donor]
        dst = blocks[recipient]
        if not src:
            continue
        for pos, row_i in enumerate(dst):
            out[row_i] = labels[src[pos % len(src)]]
    return out


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def _fold_stat(
    rows: Sequence[Dict[str, Any]],
    test_idx: Sequence[int],
    feat: str,
    labels: Dict[int, float],
    min_fold_rows: int,
) -> Optional[float]:
    xs: List[float] = []
    ys: List[float] = []
    for i in test_idx:
        v = rows[i].get(feat)
        y = labels.get(i)
        if v is None or y is None:
            continue
        try:
            xs.append(float(v))
            ys.append(float(y))
        except (TypeError, ValueError):
            continue
    if len(xs) < max(3, min_fold_rows):
        return None
    return spearman(xs, ys)


def _aggregate(per_fold: Sequence[Optional[float]]) -> Tuple[Optional[float], int, Optional[float]]:
    """(|mean of signed fold values|, folds_used, sign_agreement_fraction)."""
    vals = [v for v in per_fold if v is not None]
    if not vals:
        return None, 0, None
    mean = sum(vals) / len(vals)
    pos = sum(1 for v in vals if v > 0)
    agree = max(pos, len(vals) - pos) / len(vals)
    return abs(mean), len(vals), agree


def _prepare_fold_feature(
    rows: Sequence[Dict[str, Any]],
    test_idx: Sequence[int],
    feat: str,
    min_fold_rows: int,
):
    """Precompute the parts of the Spearman that do NOT change under a label shuffle.

    A label shuffle changes only the LABEL, so a feature's own ranks are constant
    across every replicate. Recomputing them per shuffle made the null cost
    ``n_shuffles x n_folds x n_features`` full re-ranks — on a 5-year 15m panel
    that is tens of thousands of sorts of ~10k-element vectors, i.e. hours for a
    measurement that should take minutes.

    Returns ``(idx, centered_feature_ranks, norm)`` or ``None`` when the fold is
    too thin or the feature is constant on it (a constant vector has no
    correlation with anything — see ``pearson``).

    Note ``idx`` is stable across replicates: every row in ``usable`` carries a
    real label by construction, and a block shuffle only moves labels between
    rows, so the pairwise-complete mask is a property of the FEATURE alone.
    """
    idx: List[int] = []
    vals: List[float] = []
    for i in test_idx:
        v = rows[i].get(feat)
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
        idx.append(i)
    if len(idx) < max(3, min_fold_rows):
        return None
    fr = average_ranks(vals)
    m = sum(fr) / len(fr)
    cen = [a - m for a in fr]
    norm = math.sqrt(sum(a * a for a in cen))
    if norm <= 1e-15:
        return None
    return idx, cen, norm


def _fast_spearman(prep, labels: Dict[int, float]) -> Optional[float]:
    """Spearman using precomputed feature ranks; only the label side is ranked."""
    idx, cen_f, norm_f = prep
    lv = [labels[i] for i in idx]
    lr = average_ranks(lv)
    m = sum(lr) / len(lr)
    cen_l = [b - m for b in lr]
    norm_l = math.sqrt(sum(b * b for b in cen_l))
    if norm_l <= 1e-15:
        return None
    return sum(a * b for a, b in zip(cen_f, cen_l)) / (norm_f * norm_l)


def score_panel(
    rows: List[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    n_folds: int = 4,
    embargo_bars: int = 12,
    n_shuffles: int = 1000,
    alpha: float = 0.05,
    seed: int = 20260820,
    min_trades: int = 30,
    min_rows: int = 200,
    min_fold_rows: int = 20,
) -> Dict[str, Any]:
    feats = [f for f in _dense_feats(rows, manifest) if f not in (CTRL_SIGNAL, CTRL_NOISE)]
    usable = [r for r in rows if r.get(TARGET_COL) is not None]
    n_trades = len({r.get("trade_id") for r in usable})

    report: Dict[str, Any] = {
        "step": "E2",
        "target": TARGET_COL,
        "statistic": "abs_mean_of_per_fold_spearman",
        "shuffle_scheme": SHUFFLE_SCHEME,
        "decision_rule": "informative_fwer (max-statistic null, Westfall-Young)",
        "population": {
            "n_rows_total": len(rows),
            "n_rows_with_target": len(usable),
            "n_trades": n_trades,
            "n_features": len(feats),
            "symbol": (manifest or {}).get("symbol"),
            "timeframe": (manifest or {}).get("timeframe"),
            "cross_asset_state": ((manifest or {}).get("cross_asset") or {}).get("state"),
            "cross_asset_row_coverage": ((manifest or {}).get("cross_asset") or {}).get("row_coverage"),
        },
        "config": {
            "n_folds": n_folds,
            "embargo_bars": embargo_bars,
            "n_shuffles": n_shuffles,
            "alpha": alpha,
            "seed": seed,
            "min_trades": min_trades,
            "min_rows": min_rows,
            "min_fold_rows": min_fold_rows,
        },
        "features": [],
        "controls": {},
        "harness_valid": None,
        "verdict": None,
        "unmeasured_reason": None,
    }

    # ---- power floors: underpowered is UNMEASURED, never negative ----------
    if not feats:
        report["verdict"] = "unmeasured"
        report["unmeasured_reason"] = "no dense feature columns in the panel"
        return report
    if n_trades < min_trades:
        report["verdict"] = "unmeasured"
        report["unmeasured_reason"] = (
            f"{n_trades} trades < min_trades={min_trades}; the splitter needs "
            f"{n_folds + 1} to yield any fold and this floor is the power bar. "
            "An underpowered null is not a negative."
        )
        return report
    if len(usable) < min_rows:
        report["verdict"] = "unmeasured"
        report["unmeasured_reason"] = f"{len(usable)} labelled rows < min_rows={min_rows}"
        return report

    inject_controls(usable, seed=seed)

    folds = list(_grouped_purged_folds(usable, n_folds=n_folds, embargo_bars=embargo_bars))
    report["folds_formed"] = len(folds)
    if not folds:
        report["verdict"] = "unmeasured"
        report["unmeasured_reason"] = (
            f"_grouped_purged_folds yielded 0 folds from {n_trades} trades "
            f"at n_folds={n_folds} (needs > {n_folds} trades and a non-empty "
            "post-purge train side)"
        )
        return report

    true_labels = {i: float(usable[i][TARGET_COL]) for i in range(len(usable))}
    all_names = feats + [CTRL_SIGNAL, CTRL_NOISE]

    observed: Dict[str, Tuple[Optional[float], int, Optional[float]]] = {}
    for f in all_names:
        per_fold = [_fold_stat(usable, te, f, true_labels, min_fold_rows) for _, te in folds]
        observed[f] = _aggregate(per_fold)

    # ---- the shuffled-label null ------------------------------------------
    # Feature ranks are precomputed ONCE per (fold, feature); each replicate then
    # ranks only the label side. See _prepare_fold_feature for why that is exact
    # rather than an approximation.
    rng = random.Random(seed + 1)
    fold_blocks = [trade_blocks(usable, te) for _, te in folds]
    preps: List[Dict[str, Any]] = []
    for _, te in folds:
        preps.append({f: _prepare_fold_feature(usable, te, f, min_fold_rows) for f in all_names})
    report["prepared_cells"] = sum(1 for d in preps for v in d.values() if v is not None)

    null_by_feat: Dict[str, List[float]] = {f: [] for f in all_names}
    null_max_family: List[float] = []

    for _ in range(n_shuffles):
        shuffled: Dict[int, float] = {}
        for blocks in fold_blocks:
            shuffled.update(block_shuffled_labels(blocks, true_labels, rng))
        best_family = None
        for f in all_names:
            per_fold = [
                (_fast_spearman(preps[k][f], shuffled) if preps[k][f] is not None else None)
                for k in range(len(folds))
            ]
            stat, _, _ = _aggregate(per_fold)
            if stat is None:
                continue
            null_by_feat[f].append(stat)
            # The family is the REAL features only — folding the positive control
            # into the max would inflate the threshold with a planted synthetic.
            if f in feats and (best_family is None or stat > best_family):
                best_family = stat
        if best_family is not None:
            null_max_family.append(best_family)

    null_max_family.sort()
    fwer_threshold = quantile(null_max_family, 1.0 - alpha)
    report["fwer_threshold"] = fwer_threshold
    report["null_family_n"] = len(null_max_family)

    def _verdict_for(name: str) -> Dict[str, Any]:
        stat, folds_used, agree = observed[name]
        own = sorted(null_by_feat[name])
        pt_thr = quantile(own, 1.0 - alpha)
        # p is the fraction of null draws at least as extreme, +1/+1 so a p of
        # exactly 0 is never reported from a finite null.
        p = None
        if own:
            p = (sum(1 for v in own if stat is not None and v >= stat) + 1) / (len(own) + 1)
        return {
            "feature": name,
            "statistic": stat,
            "folds_used": folds_used,
            "sign_agreement": agree,
            "pointwise_threshold": pt_thr,
            "p_empirical": p,
            "informative_pointwise": bool(stat is not None and pt_thr is not None and stat > pt_thr),
            "informative_fwer": bool(
                stat is not None and fwer_threshold is not None and stat > fwer_threshold
            ),
        }

    report["features"] = sorted(
        (_verdict_for(f) for f in feats),
        key=lambda d: (d["statistic"] is None, -(d["statistic"] or 0.0)),
    )
    report["controls"] = {
        "positive": _verdict_for(CTRL_SIGNAL),
        "negative": _verdict_for(CTRL_NOISE),
    }

    pos_ok = report["controls"]["positive"]["informative_fwer"]
    neg_ok = not report["controls"]["negative"]["informative_pointwise"]
    report["harness_valid"] = bool(pos_ok and neg_ok)
    report["control_failure"] = None if report["harness_valid"] else {
        "positive_control_fired": pos_ok,
        "negative_control_stayed_silent": neg_ok,
    }

    if not report["harness_valid"]:
        # The whole point of the gate: a negative from a run whose controls
        # misbehaved is not admissible evidence about the fleet.
        report["verdict"] = "harness_invalid"
        return report

    hits = [d for d in report["features"] if d["informative_fwer"]]
    report["n_informative_fwer"] = len(hits)
    report["n_informative_pointwise"] = sum(1 for d in report["features"] if d["informative_pointwise"])
    report["verdict"] = "informative_features_found" if hits else "no_feature_beats_control"
    return report


# ---------------------------------------------------------------------------
# self-tests
# ---------------------------------------------------------------------------


def _synth_panel(n_trades: int, bars: int, *, seed: int = 7, signal: bool = True):
    """A synthetic panel with a KNOWN informative feature and a known noise one."""
    rng = random.Random(seed)
    rows = []
    for t in range(n_trades):
        base = rng.gauss(0.0, 1.0)
        for b in range(bars):
            y = base + rng.gauss(0.0, 0.3)
            rows.append({
                "trade_id": t,
                "decision_time": f"2026-01-{1 + t // 24:02d}T{t % 24:02d}:{b:02d}:00Z",
                "label_t0": t * bars + b + 1,
                "label_t1": t * bars + b + 2,
                TARGET_COL: y,
                "feat_real": (y * 2.0 + rng.gauss(0.0, 0.5)) if signal else rng.gauss(0.0, 1.0),
                "feat_noise": rng.gauss(0.0, 1.0),
            })
    manifest = {"dense_feature_cols": ["feat_real", "feat_noise"], "symbol": "SYNTH", "timeframe": "5m"}
    return rows, manifest


def _selftest() -> int:
    failures: List[str] = []
    ran = [0]

    def check(name: str, cond: bool, detail: str = "") -> None:
        ran[0] += 1
        if not cond:
            failures.append(f"{name}: {detail}")

    # --- rank statistics -------------------------------------------------
    check("ranks_ties", average_ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0],
          str(average_ranks([10, 20, 20, 30])))
    check("spearman_monotone", abs((spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) or 0) - 1.0) < 1e-9)
    check("spearman_inverse", abs((spearman([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) or 0) + 1.0) < 1e-9)
    check("spearman_nonlinear_monotone",
          abs((spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]) or 0) - 1.0) < 1e-9,
          "rank association must see a monotone nonlinearity Pearson would discount")
    check("constant_is_none", spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None,
          "a constant vector must be undefined, never 0.0")
    check("quantile_interp", abs((quantile([0.0, 1.0], 0.5) or 0) - 0.5) < 1e-9)

    # --- block shuffle preserves the multiset and the block structure ------
    rows, _ = _synth_panel(6, 5, seed=3)
    idx = list(range(len(rows)))
    blocks = trade_blocks(rows, idx)
    check("blocks_partition", sorted(i for b in blocks for i in b) == idx,
          "blocks must partition the row set exactly")
    labels = {i: float(rows[i][TARGET_COL]) for i in idx}
    sh = block_shuffled_labels(blocks, labels, random.Random(1))
    check("shuffle_covers_all_rows", set(sh) == set(idx))
    check("shuffle_moves_something", any(abs(sh[i] - labels[i]) > 1e-12 for i in idx),
          "a shuffle that changes nothing is not a shuffle")

    # --- POSITIVE CONTROL: a real signal must be found ---------------------
    rows, man = _synth_panel(60, 8, seed=11, signal=True)
    rep = score_panel(rows, man, n_folds=3, n_shuffles=200, seed=5, min_trades=30, min_rows=200)
    check("signal_harness_valid", rep["harness_valid"] is True,
          f"controls misbehaved: {rep.get('control_failure')}")
    check("signal_verdict", rep["verdict"] == "informative_features_found", str(rep["verdict"]))
    real = next((d for d in rep["features"] if d["feature"] == "feat_real"), None)
    noise = next((d for d in rep["features"] if d["feature"] == "feat_noise"), None)
    check("signal_real_found", bool(real and real["informative_fwer"]), str(real))
    check("signal_noise_not_found", bool(noise and not noise["informative_fwer"]), str(noise))

    # --- NEGATIVE CONTROL: pure noise must yield a clean negative ----------
    rows, man = _synth_panel(60, 8, seed=13, signal=False)
    rep_n = score_panel(rows, man, n_folds=3, n_shuffles=200, seed=5, min_trades=30, min_rows=200)
    check("null_harness_valid", rep_n["harness_valid"] is True,
          f"controls misbehaved on the null panel: {rep_n.get('control_failure')}")
    check("null_verdict", rep_n["verdict"] == "no_feature_beats_control", str(rep_n["verdict"]))

    # --- PLANTED FAILURE: the validity gate must actually be able to fire --
    # Break the positive control (make it pure noise) and confirm the run is
    # refused rather than reported. A gate that cannot fail is not a gate.
    rows, man = _synth_panel(60, 8, seed=17, signal=True)
    _orig = inject_controls

    def _broken(rs, *, seed, signal_noise=1.0):
        r = random.Random(seed)
        for row in rs:
            row[CTRL_SIGNAL] = r.gauss(0.0, 1.0)   # no longer a function of the label
            row[CTRL_NOISE] = r.gauss(0.0, 1.0)

    globals()["inject_controls"] = _broken
    try:
        rep_b = score_panel(rows, man, n_folds=3, n_shuffles=200, seed=5, min_trades=30, min_rows=200)
    finally:
        globals()["inject_controls"] = _orig
    check("planted_failure_detected", rep_b["verdict"] == "harness_invalid",
          f"a dead positive control must invalidate the run, got {rep_b['verdict']}")
    check("planted_failure_reported", rep_b.get("control_failure") is not None)

    # --- PLANTED FAILURE 2: a row-level shuffle must give a TIGHTER null ---
    # This is the claim the block-shuffle design rests on. If it does not
    # reproduce, the design rationale is wrong and should be rewritten.
    rows, man = _synth_panel(40, 10, seed=23, signal=False)
    # Probe with a TRADE-STRUCTURED feature (constant within a trade + a ramp),
    # which is what the real panel's path features look like. An i.i.d.-per-row
    # probe does NOT show the effect — see the module docstring.
    _lvl = {}
    for r in rows:
        _lvl.setdefault(r["trade_id"], random.Random(1000 + r["trade_id"]).gauss(0, 1))
    _seen = {}
    for r in rows:
        k = r["trade_id"]
        _seen[k] = _seen.get(k, -1) + 1
        r["feat_pathy"] = _lvl[k] + 0.05 * _seen[k]
    usable = [r for r in rows if r.get(TARGET_COL) is not None]
    lab = {i: float(usable[i][TARGET_COL]) for i in range(len(usable))}
    idx = list(range(len(usable)))
    blk = trade_blocks(usable, idx)
    rb = random.Random(2)
    block_stats, row_stats = [], []
    for _ in range(60):
        sh_b = block_shuffled_labels(blk, lab, rb)
        vals = [lab[i] for i in idx]
        rb.shuffle(vals)
        sh_r = dict(zip(idx, vals))
        for store, sh in ((block_stats, sh_b), (row_stats, sh_r)):
            s = _fold_stat(usable, idx, "feat_pathy", sh, 20)
            if s is not None:
                store.append(abs(s))
    if block_stats and row_stats:
        b_hi = quantile(sorted(block_stats), 0.95) or 0.0
        r_hi = quantile(sorted(row_stats), 0.95) or 0.0
        check("block_null_wider_for_pathy_feature", b_hi > r_hi,
              f"block 95th={b_hi:.4f} row 95th={r_hi:.4f} — for a TRADE-STRUCTURED "
              "feature the block null must be the wider one; if this stops "
              "reproducing, the shuffle-scheme rationale in the docstring is wrong "
              "and must be rewritten, not silenced")

    # --- the fast null path must EQUAL the slow one ------------------------
    # The precomputation is an optimization, and an optimization that changes the
    # number is a defect. Asserted rather than argued.
    rows, man = _synth_panel(30, 9, seed=41)
    _u = [r for r in rows if r.get(TARGET_COL) is not None]
    _idx = list(range(len(_u)))
    _lab = {i: float(_u[i][TARGET_COL]) for i in _idx}
    _worst = 0.0
    for _f in ("feat_real", "feat_noise"):
        _prep = _prepare_fold_feature(_u, _idx, _f, 20)
        _a = _fast_spearman(_prep, _lab)
        _b = _fold_stat(_u, _idx, _f, _lab, 20)
        if _a is not None and _b is not None:
            _worst = max(_worst, abs(_a - _b))
    check("fast_path_equals_slow_path", _worst < 1e-12,
          f"max |fast - slow| = {_worst:.3e} — the precomputed feature ranks must "
          "reproduce the direct Spearman exactly")

    # --- a constant feature must be REFUSED, not scored as zero ------------
    _c = [{"trade_id": 0, TARGET_COL: float(i), "feat_flat": 1.0} for i in range(40)]
    check("constant_feature_prepares_to_none",
          _prepare_fold_feature(_c, list(range(40)), "feat_flat", 20) is None,
          "a zero-variance feature has undefined association; it must drop out "
          "rather than contribute a fabricated 0.0 to the aggregate")

    # --- underpowered must be UNMEASURED, not negative ---------------------
    rows, man = _synth_panel(5, 4, seed=19)
    rep_u = score_panel(rows, man, n_folds=3, n_shuffles=20, min_trades=30, min_rows=200)
    check("underpowered_unmeasured", rep_u["verdict"] == "unmeasured", str(rep_u["verdict"]))
    check("underpowered_names_floor", bool(rep_u.get("unmeasured_reason")))
    check("underpowered_not_negative", rep_u["verdict"] != "no_feature_beats_control",
          "an underpowered run must never render as a negative result")

    # --- the leaky univariate is NOT imported ------------------------------
    src = Path(__file__).read_text(encoding="utf-8")
    # The needles are ASSEMBLED at runtime so the literals never appear in this
    # file. Spelled out, each check matched its own source line and failed on
    # itself — a guard whose own text is its only evidence proves nothing. Same
    # discipline as the repo's `# provenance:` override, which is excluded from
    # the evidence it annotates.
    _call_needle = "_univariate" + "_fdr("
    _def_needle = "def " + "_grouped_purged_folds"
    check("no_leaky_univariate_called", _call_needle not in src,
          "the pooled un-purged univariate must never be CALLED from here")
    _imp = src.split("from scripts.research" + ".analyze_exit_head import", 1)
    check("no_leaky_univariate_imported",
          len(_imp) > 1 and "_univariate" + "_fdr" not in _imp[1].split(")", 1)[0],
          "the pooled un-purged univariate must not appear in the import list")
    check("splitter_is_imported_not_copied",
          _def_needle not in src and "_grouped_purged_folds" in src,
          "the splitter must be imported, never re-implemented here")

    for f in failures:
        print(f"  FAIL {f}")
    print(f"e2_feature_information selftest: {ran[0] - len(failures)}/{ran[0]} checks passed")
    return 1 if failures else 0


# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="E2 — per-feature information vs forward R.")
    p.add_argument("--panel", help="Path to the in-trade exit panel jsonl.")
    p.add_argument("--n-folds", type=int, default=4)
    p.add_argument("--embargo-bars", type=int, default=12)
    p.add_argument("--n-shuffles", type=int, default=1000)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--seed", type=int, default=20260820)
    p.add_argument("--min-trades", type=int, default=30)
    p.add_argument("--min-rows", type=int, default=200)
    p.add_argument("--min-fold-rows", type=int, default=20)
    p.add_argument("--out", default=None, help="Write the full report JSON here.")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.panel:
        p.error("--panel is required (or use --selftest)")

    rows, manifest = load_panel(Path(args.panel))
    rep = score_panel(
        rows, manifest,
        n_folds=args.n_folds, embargo_bars=args.embargo_bars,
        n_shuffles=args.n_shuffles, alpha=args.alpha, seed=args.seed,
        min_trades=args.min_trades, min_rows=args.min_rows,
        min_fold_rows=args.min_fold_rows,
    )
    rep["panel_path"] = str(args.panel)

    pop = rep["population"]
    print(f"E2 [{args.panel}]")
    print(f"  population: {pop['n_rows_with_target']}/{pop['n_rows_total']} labelled rows · "
          f"{pop['n_trades']} trades · {pop['n_features']} features · "
          f"{pop['symbol']}/{pop['timeframe']} · xa={pop['cross_asset_state']} "
          f"(coverage={pop['cross_asset_row_coverage']})")
    print(f"  verdict: {rep['verdict']}")
    if rep["verdict"] == "unmeasured":
        print(f"  UNMEASURED — {rep['unmeasured_reason']}")
    else:
        c = rep["controls"]
        print(f"  harness_valid={rep['harness_valid']} "
              f"(+ctrl fwer={c['positive']['informative_fwer']}, "
              f"-ctrl pointwise={c['negative']['informative_pointwise']})")
        print(f"  fwer_threshold={rep.get('fwer_threshold')} over {rep.get('null_family_n')} draws")
        for d in rep["features"][:12]:
            flag = "FWER" if d["informative_fwer"] else ("pt" if d["informative_pointwise"] else "  ")
            print(f"    {flag:>4}  {d['feature']:<34} stat={d['statistic']} "
                  f"p={d['p_empirical']} folds={d['folds_used']} sign_agree={d['sign_agreement']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        print(f"  → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
