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

## ⚠️ READ THIS BEFORE INTERPRETING A `forward_r` SCORE

`forward_r` is the triple-barrier outcome of holding, and it is measured **FROM
ENTRY** — `src/research/triple_barrier.py` says so explicitly, precisely so it is
comparable to the exit-now alternative `upnl_r`. **It therefore shares its baseline
with `feat_upnl_r`, and with every path feature that tracks accrued R.**

There is no lookahead — features read bars up to `t`, the label reads bars after `t`,
and the windows are disjoint by construction. But a high association between
`feat_upnl_r` and `forward_r` is substantially **the shared entry baseline plus path
persistence**, not evidence of forecasting power over the forward *increment*. A trade
up 1.5R now tends to still be up around 1.5R at the time stop; that is arithmetic about
where the trade already is, not a signal about where it is going.

The quantity a hold-vs-exit lever actually needs is the **increment**:

    advantage_r = forward_r - upnl_r - cost_r

which differences the baseline out, and `label_hold = 1[advantage_r > 0]` is its sign.
Both are already columns in the panel. **Score all three and compare** — a feature that
dominates on `forward_r` and vanishes on `advantage_r` was measuring the trade's
current position, not its future, and no lever can be built on it. `--target` selects
which one; the report stamps it.

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

Every run injects a positive control and a **bank** of negative controls:

- **positive control** ``__ctrl_signal`` = a monotone function of the label plus
  noise. It **must** reach ``informative_fwer``.
- **negative-control bank** = ``n_negative_controls`` independent pure-RNG columns,
  each scored exactly like a feature. Under a valid null each clears the pointwise
  bar with probability ``alpha``, so the bank's clear COUNT is ``Binomial(K, alpha)``
  and the run is refused only when that count's upper tail falls below
  ``gate_level``.

⚠️ **WHY A BANK AND NOT ONE COLUMN — READ THIS BEFORE "SIMPLIFYING" IT BACK.** The
gate used to be ``not negative.informative_pointwise`` on a SINGLE noise column.
That is a Bernoulli(alpha) draw: it discarded a **sound** run about 5% of the time
**by construction**, and one draw can never separate that from a genuinely broken
null. It fired 4 times in the 24-cell 2026-08-20 horizon sweep, all on one leg, and
the holes landed at the decisive rung — which then had to be argued about instead of
measured (``BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER``).

Measured over 40 seeded SOUND null panels in ``_selftest``: the old single-draw rule
discards **2/40 = 5.0%** — alpha, exactly as predicted — while this gate discards
**0/40**, with the bank's own clear-rate at **0.052** against an expected 0.05. The
rate is now a *measured property of the tool*, which is what the backlog row bound
the replacement to deliver.

``harness_state`` is reported as one of four states, never collapsed:
``valid`` · ``invalid_positive_control_dead`` · ``invalid_null_miscalibrated`` ·
``unchecked`` (``K = 0`` — the bank never ran, so **we did not look**; this is
emphatically *not* ``valid``). ``harness_valid`` remains as the boolean, and
``legacy_pointwise_gate_would_invalidate`` records what the old rule would have said
so a re-run is comparable to the sweep it replaces without re-running the old code.

If the gate refuses, **no negative from that run is admissible** and the verdict
becomes ``harness_invalid`` rather than a result. A control that cannot fire is not a
control — and neither is a gate that cannot fail, which is why ``_selftest`` plants a
null that fails to preserve the controls' own dependence and requires the gate to
catch it.

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

TARGET_COL = "forward_r"          # the pre-registered PRIMARY target
TARGETS = ("forward_r", "advantage_r", "label_hold")
CTRL_SIGNAL = "__ctrl_signal"
CTRL_NOISE = "__ctrl_noise"          # bank column 0; kept as the reported diagnostic
N_NEGATIVE_CONTROLS = 64             # see the module doc: the gate reads a RATE
GATE_LEVEL = 0.01                    # binomial tail below which the null is called broken


def noise_control_names(k: int) -> List[str]:
    """Bank column names. Column 0 keeps the legacy name so the single-control
    diagnostic the backlog asks to retain stays addressable by its old key.

    ``k <= 0`` yields NO columns — the bank is genuinely not run, which the gate
    reports as ``unchecked``. It deliberately does not fall back to one column:
    "we did not look" and "we looked with a bank of one" are different states, and
    the second is the very thing this bank replaced.
    """
    if k <= 0:
        return []
    return [CTRL_NOISE] + [f"{CTRL_NOISE}_{i:03d}" for i in range(1, k)]
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


def inject_controls(rows: List[Dict[str, Any]], *, seed: int, signal_noise: float = 1.0,
                    target: str = TARGET_COL,
                    n_negative: int = N_NEGATIVE_CONTROLS) -> None:
    """Add the positive control and the negative-control BANK, in place.

    ``__ctrl_signal`` is a monotone function of the row's own label plus gaussian
    noise — it MUST be detectable, and a run where it is not is a broken run.

    The negative side is a **bank of ``n_negative`` independent noise columns**,
    not one column. One column yields one Bernoulli draw at the gate's own alpha,
    which cannot distinguish "5% bad luck" from "the null on this panel is
    broken" — the ambiguity that produced
    ``BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER``. A bank yields a
    RATE, and a rate is a measurement.
    """
    rng = random.Random(seed)
    names = noise_control_names(n_negative)
    for r in rows:
        y = r.get(target)
        r[CTRL_SIGNAL] = None if y is None else float(y) + rng.gauss(0.0, signal_noise)
        for nm in names:
            r[nm] = rng.gauss(0.0, 1.0)


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


def _centered_label_ranks(idx: Sequence[int], labels: Dict[int, float]):
    """Centered label ranks over ``idx`` — the only part a shuffle changes."""
    lr = average_ranks([labels[i] for i in idx])
    m = sum(lr) / len(lr)
    cen = [b - m for b in lr]
    norm = math.sqrt(sum(b * b for b in cen))
    if norm <= 1e-15:
        return None
    return cen, norm


def _corr_from_centered(cen_f, norm_f, cen_l, norm_l) -> float:
    return sum(a * b for a, b in zip(cen_f, cen_l)) / (norm_f * norm_l)


def _fast_spearman(prep, labels: Dict[int, float]) -> Optional[float]:
    """Spearman using precomputed feature ranks; only the label side is ranked."""
    idx, cen_f, norm_f = prep
    lab = _centered_label_ranks(idx, labels)
    if lab is None:
        return None
    return _corr_from_centered(cen_f, norm_f, lab[0], lab[1])


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
    target: str = TARGET_COL,
    n_negative_controls: int = N_NEGATIVE_CONTROLS,
    gate_level: float = GATE_LEVEL,
) -> Dict[str, Any]:
    _noise_names = noise_control_names(n_negative_controls)
    _ctrl_all = set(_noise_names) | {CTRL_SIGNAL}
    feats = [f for f in _dense_feats(rows, manifest) if f not in _ctrl_all]
    usable = [r for r in rows if r.get(target) is not None]
    n_trades = len({r.get("trade_id") for r in usable})

    report: Dict[str, Any] = {
        "step": "E2",
        "target": target,
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
            "n_negative_controls": n_negative_controls,
            "gate_level": gate_level,
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

    inject_controls(usable, seed=seed, target=target, n_negative=n_negative_controls)

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

    true_labels = {i: float(usable[i][target]) for i in range(len(usable))}
    all_names = feats + [CTRL_SIGNAL] + _noise_names

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
    # Replicate-aligned matrix over the REAL-feature family, for the scale-free
    # min-p check below. Rows are replicates, keys are features.
    null_matrix: List[Dict[str, float]] = []

    # Features sharing a row-mask share the label ranking. On a dense panel that
    # is ONE ranking per fold per replicate instead of one per feature per fold
    # per replicate — the difference between a null that takes ~20 minutes and
    # one that takes ~20 seconds, which is what makes n_shuffles affordable
    # enough not to be tempted to lower it.
    mask_groups: List[Dict[Tuple[int, ...], List[str]]] = []
    for k in range(len(folds)):
        g: Dict[Tuple[int, ...], List[str]] = defaultdict(list)
        for f in all_names:
            if preps[k][f] is not None:
                g[tuple(preps[k][f][0])].append(f)
        mask_groups.append(dict(g))
    report["mask_groups_per_fold"] = [len(g) for g in mask_groups]

    for _ in range(n_shuffles):
        shuffled: Dict[int, float] = {}
        for blocks in fold_blocks:
            shuffled.update(block_shuffled_labels(blocks, true_labels, rng))
        per_fold_by_feat: Dict[str, List[Optional[float]]] = {f: [] for f in all_names}
        for k in range(len(folds)):
            for mask, names in mask_groups[k].items():
                lab = _centered_label_ranks(mask, shuffled)
                for f in names:
                    idx_f, cen_f, norm_f = preps[k][f]
                    per_fold_by_feat[f].append(
                        None if lab is None else _corr_from_centered(cen_f, norm_f, lab[0], lab[1])
                    )
        best_family = None
        rep_row: Dict[str, float] = {}
        for f in all_names:
            stat, _, _ = _aggregate(per_fold_by_feat[f])
            if stat is None:
                continue
            null_by_feat[f].append(stat)
            if f in feats:
                rep_row[f] = stat
            # The family is the REAL features only — folding the positive control
            # into the max would inflate the threshold with a planted synthetic.
            if f in feats and (best_family is None or stat > best_family):
                best_family = stat
        if best_family is not None:
            null_max_family.append(best_family)
        if rep_row:
            null_matrix.append(rep_row)

    null_max_family.sort()
    fwer_threshold = quantile(null_max_family, 1.0 - alpha)
    report["fwer_threshold"] = fwer_threshold
    report["null_family_n"] = len(null_max_family)

    # ---- scale-free companion: Westfall-Young MIN-P -------------------------
    # The raw max-statistic threshold is SCALE-DEPENDENT. Null widths are not
    # comparable across these features by design: a path feature is strongly
    # autocorrelated within a trade and gets a WIDE null, while a near-white
    # peer return gets a TIGHT one (this module's own self-test demonstrates the
    # mechanism). A single max-|stat| threshold is therefore set mostly by the
    # wide-null features and is conservative for the narrow-null ones — which
    # would quietly stack the test against exactly the exogenous block E1 was
    # built to evaluate.
    #
    # min-p removes the scale: each statistic is first converted to a p-value
    # against its OWN null, and the family-wise threshold is the alpha-quantile
    # of the per-replicate MINIMUM p. This is reported ALONGSIDE the
    # pre-registered rule, never in place of it — swapping the decision rule
    # after seeing scores is the move the pre-registration exists to prevent.
    minp_threshold = None
    minp_p: Dict[str, float] = {}
    if null_matrix and feats:
        cols = {f: sorted(null_by_feat[f]) for f in feats if null_by_feat[f]}

        def _p_against(f: str, v: float) -> float:
            col = cols.get(f) or []
            if not col:
                return 1.0
            return (sum(1 for x in col if x >= v) + 1) / (len(col) + 1)

        q_per_replicate: List[float] = []
        for row in null_matrix:
            ps = [_p_against(f, v) for f, v in row.items() if f in cols]
            if ps:
                q_per_replicate.append(min(ps))
        q_per_replicate.sort()
        minp_threshold = quantile(q_per_replicate, alpha)
        for f in feats:
            st = observed[f][0]
            minp_p[f] = 1.0 if st is None else _p_against(f, st)
    report["minp_threshold"] = minp_threshold
    report["minp_null_n"] = len(null_matrix)

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
            "minp_p": minp_p.get(name),
            "informative_minp": bool(
                name in minp_p and minp_threshold is not None
                and minp_p[name] <= minp_threshold
            ),
        }

    report["features"] = sorted(
        (_verdict_for(f) for f in feats),
        key=lambda d: (d["statistic"] is None, -(d["statistic"] or 0.0)),
    )
    bank = [_verdict_for(n) for n in _noise_names]
    report["controls"] = {
        "positive": _verdict_for(CTRL_SIGNAL),
        # Retained as the single-column DIAGNOSTIC the backlog row asks to keep.
        # It is no longer what the gate reads — see `negative_bank` below.
        "negative": bank[0] if bank else None,
    }

    # ---- the admissibility gate -------------------------------------------
    # WHAT CHANGED AND WHY (BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER).
    # The gate used to read `not negative.informative_pointwise` — ONE noise column
    # against the alpha-quantile of its own null. That is a Bernoulli(alpha) draw,
    # so it discarded a SOUND run about 5% of the time by construction, and a single
    # draw can never separate that from a genuinely broken null. It fired 4 times in
    # the 24-cell horizon sweep and the holes landed at the decisive rung.
    #
    # It is replaced by TWO tests over a BANK of noise columns, asking two different
    # questions that neither subsumes nor substitutes for the other:
    #
    # IS THE NULL ON THIS PANEL CALIBRATED? Under a valid null each column clears
    # the pointwise bar with probability alpha, so the bank's clear COUNT is
    # Binomial(K, alpha). Invalidate only when its upper tail falls below
    # `gate_level`. The false-invalidation rate is then bounded by `gate_level`
    # (0.01) instead of alpha (0.05) — a stated property, and `_selftest` MEASURES
    # it over seeded null panels rather than asserting it.
    #
    # ⚠️ A SECOND GATE WAS DESIGNED HERE AND REJECTED BY ITS OWN MEASUREMENT. The
    # obvious companion — "invalidate if any bank column clears the FWER bar, the
    # bar the DECISION uses" — reads well and is WRONG, because the rate at which an
    # out-of-family noise column clears the family-max threshold is not a known
    # constant: it falls as the family grows and as the family's null widths widen.
    # On a narrow family it is far from negligible, and P(at least one of K clears)
    # then rises with K, so the rule would get MORE trigger-happy the more carefully
    # you measured. Measured on this module's own 2-feature synthetic null panel:
    # 2 of 64 columns cleared FWER while the pointwise rate was a clean 3/64
    # (binomial p = 0.63). That is the gate inventing a failure, which is the exact
    # sin the row this replaces was filed about. It is kept as the REPORTED
    # diagnostic `n_cleared_fwer` and given no vote — and note it is redundant
    # anyway: the max-statistic construction already guarantees
    # P(any family member clears | global null) = alpha WHENEVER the null is valid,
    # which is precisely what the rate test checks. Do not re-promote it to a gate
    # without a measured null rate to calibrate it against.
    #
    # The states are kept apart rather than collapsed into one boolean: "the positive
    # control died" and "the null is miscalibrated" are different defects with
    # different fixes, and `unchecked` (K = 0 — we did not look) is emphatically NOT
    # `valid`.
    pos_ok = bool(report["controls"]["positive"]["informative_fwer"])
    n_bank = len(bank)
    n_pt = sum(1 for d in bank if d["informative_pointwise"])
    n_fw = sum(1 for d in bank if d["informative_fwer"])
    rate_tail = None
    if n_bank:
        rate_tail = sum(
            math.comb(n_bank, i) * (alpha ** i) * ((1.0 - alpha) ** (n_bank - i))
            for i in range(n_pt, n_bank + 1)
        )
    ps = [d["p_empirical"] for d in bank if d["p_empirical"] is not None]
    report["controls"]["negative_bank"] = {
        "n_controls": n_bank,
        "n_cleared_pointwise": n_pt,
        "rate_cleared_pointwise": (n_pt / n_bank) if n_bank else None,
        "expected_rate_if_null_valid": alpha,
        "binom_p_rate_above_alpha": rate_tail,
        # DIAGNOSTIC, NOT A GATE. See the rejection note in the gate block: the
        # null rate of an out-of-family column clearing the family-max bar is not a
        # known constant, so this cannot carry a vote without being calibrated.
        "n_cleared_fwer": n_fw,
        # Reported BESIDE the decision, never in place of it: a valid permutation
        # p is Uniform(0,1), so a bank mean far from 0.5 says the null is skewed
        # even when the tail count happens to look ordinary. Sharper than the rate,
        # but discrete labels (label_hold is binary) make it noisy enough that
        # letting it move the verdict would trade one misfire for another.
        "p_empirical_mean": (sum(ps) / len(ps)) if ps else None,
    }

    if n_bank == 0:
        harness_state = "unchecked"
    elif not pos_ok:
        harness_state = "invalid_positive_control_dead"
    elif rate_tail is not None and rate_tail < gate_level:
        harness_state = "invalid_null_miscalibrated"
    else:
        harness_state = "valid"
    report["harness_state"] = harness_state
    report["harness_valid"] = bool(harness_state == "valid")
    report["control_failure"] = None if report["harness_valid"] else {
        "harness_state": harness_state,
        "positive_control_fired": pos_ok,
        "n_bank_cleared_fwer": n_fw,   # diagnostic only — see the note above
        "n_bank_cleared_pointwise": n_pt,
        "n_controls": n_bank,
        "binom_p_rate_above_alpha": rate_tail,
        # The verdict the OLD single-draw gate would have returned, carried so the
        # re-run can be compared against the sweep it replaces without re-running
        # the old code.
        "legacy_pointwise_gate_would_invalidate": bool(
            bank and bank[0]["informative_pointwise"]
        ),
    }
    report["legacy_pointwise_gate_would_invalidate"] = bool(
        bank and bank[0]["informative_pointwise"]
    )

    if not report["harness_valid"]:
        # The whole point of the gate: a negative from a run whose controls
        # misbehaved is not admissible evidence about the fleet.
        report["verdict"] = "harness_invalid"
        return report

    hits = [d for d in report["features"] if d["informative_fwer"]]
    report["n_informative_fwer"] = len(hits)
    report["n_informative_pointwise"] = sum(1 for d in report["features"] if d["informative_pointwise"])
    report["n_informative_minp"] = sum(1 for d in report["features"] if d["informative_minp"])
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

    # This stub stands in for inject_controls, so it must accept its keywords;
    # IGNORING them is the PLANT. A positive control that is not a function of
    # the label is exactly the broken harness the test asserts gets refused, so
    # reading either would partially restore the control and defeat the test.
    def _broken(
        rs,
        *,
        seed,
        signal_noise=1.0,  # inert: signal_noise — signature parity with inject_controls; reading it would un-break the plant
        target=TARGET_COL,  # inert: target — signature parity with inject_controls; the plant must ignore the label
        n_negative=N_NEGATIVE_CONTROLS,
    ):
        r = random.Random(seed)
        names = noise_control_names(n_negative)
        for row in rs:
            row[CTRL_SIGNAL] = r.gauss(0.0, 1.0)   # no longer a function of the label
            for nm in names:
                row[nm] = r.gauss(0.0, 1.0)

    globals()["inject_controls"] = _broken
    try:
        rep_b = score_panel(rows, man, n_folds=3, n_shuffles=200, seed=5, min_trades=30, min_rows=200)
    finally:
        globals()["inject_controls"] = _orig
    check("planted_failure_detected", rep_b["verdict"] == "harness_invalid",
          f"a dead positive control must invalidate the run, got {rep_b['verdict']}")
    check("planted_failure_reported", rep_b.get("control_failure") is not None)

    # --- THE NEW GATE, MEASURED RATHER THAN ASSERTED ----------------------
    # BL-20260820-E2-NEGATIVE-CONTROL-GATED-POINTWISE-NOT-FWER binds the
    # replacement rule to carry "a self-test asserting the chosen gate's
    # false-invalidation rate over many seeded null panels, so the rate is a
    # measured property of the tool rather than an emergent surprise." This is it.
    # The OLD gate's rate was alpha = 5% BY CONSTRUCTION and nobody had measured it;
    # the whole point is that this number is now observed, not reasoned about.
    _M = 40
    _states = []
    for _s in range(_M):
        _r, _m = _synth_panel(60, 8, seed=400 + _s, signal=False)
        _rep = score_panel(_r, _m, n_folds=3, n_shuffles=200, seed=900 + _s,
                           min_trades=30, min_rows=200)
        _states.append(_rep.get("harness_state"))
    _false_inv = sum(1 for st in _states if st == "invalid_null_miscalibrated")
    _dead_pos = sum(1 for st in _states if st == "invalid_positive_control_dead")
    # gate_level is 0.01, so over 40 sound panels the expected count is 0.4 and
    # seeing 3+ would mean the bound is not holding. This is a LOOSE bound on
    # purpose: it must fail when the gate is broken, not flake when it is fine.
    check("gate_false_invalidation_rate_measured", _false_inv <= 2,
          f"{_false_inv}/{_M} sound null panels invalidated as miscalibrated "
          f"(gate_level=0.01 ⇒ expect ~0.4); states={_states[:8]}...")
    check("gate_positive_control_alive_on_null_panels", _dead_pos == 0,
          f"{_dead_pos}/{_M} panels reported a dead positive control")
    # And the comparison that says the change did something: the OLD single-draw
    # rule would have discarded ~alpha of these same sound panels.
    _legacy = 0
    for _s in range(_M):
        _r, _m = _synth_panel(60, 8, seed=400 + _s, signal=False)
        _rep = score_panel(_r, _m, n_folds=3, n_shuffles=200, seed=900 + _s,
                           min_trades=30, min_rows=200)
        _legacy += 1 if _rep.get("legacy_pointwise_gate_would_invalidate") else 0
    check("legacy_gate_discarded_more_than_the_new_one", _legacy >= _false_inv,
          f"legacy would discard {_legacy}/{_M}; new gate discards {_false_inv}/{_M}")

    # --- PLANTED FAILURE 3: the new gate MUST still be able to fire --------
    # "A gate that cannot fail is not a gate." Break the null in the one way that
    # actually breaks a NOISE bank's calibration: give the control columns trade
    # structure while the null shuffles at ROW level, so the null no longer
    # preserves the dependence the columns carry. Observed then sits systematically
    # high in its own null and the pointwise clear-rate blows past alpha.
    _rowsM, _manM = _synth_panel(60, 8, seed=71, signal=False)
    _orig_inj, _orig_shuf = inject_controls, block_shuffled_labels

    def _structured_controls(rs, *, seed, signal_noise=1.0, target=TARGET_COL,
                             n_negative=N_NEGATIVE_CONTROLS):
        r = random.Random(seed)
        names = noise_control_names(n_negative)
        per_trade = {}
        for row in rs:
            key = row.get("trade_id")
            if key not in per_trade:
                per_trade[key] = [r.gauss(0.0, 1.0) for _ in names]
            for nm, v in zip(names, per_trade[key]):
                row[nm] = v                      # CONSTANT within a trade
            y = row.get(target)
            row[CTRL_SIGNAL] = None if y is None else float(y) + r.gauss(0.0, signal_noise)

    def _row_level(blocks, labels, rng):
        idx = [i for b in blocks for i in b]
        vals = [labels[i] for i in idx]
        rng.shuffle(vals)                        # destroys the within-trade structure
        return dict(zip(idx, vals))

    globals()["inject_controls"] = _structured_controls
    globals()["block_shuffled_labels"] = _row_level
    try:
        _repM = score_panel(_rowsM, _manM, n_folds=3, n_shuffles=200, seed=5,
                            min_trades=30, min_rows=200)
    finally:
        globals()["inject_controls"] = _orig_inj
        globals()["block_shuffled_labels"] = _orig_shuf
    check("planted_miscalibration_detected",
          _repM.get("harness_state") == "invalid_null_miscalibrated",
          f"a null that does not preserve the controls' own dependence must be "
          f"refused, got {_repM.get('harness_state')} "
          f"(cleared {(_repM.get('controls', {}).get('negative_bank') or {}).get('n_cleared_pointwise')})")
    check("planted_miscalibration_is_not_a_verdict",
          _repM.get("verdict") == "harness_invalid", str(_repM.get("verdict")))

    # --- 'unchecked' is NOT a pass ----------------------------------------
    # K = 0 means the bank never ran: we did not look. The repo's collapsed-state
    # doctrine says that is its own state and must never read as `valid`.
    _r0, _m0 = _synth_panel(60, 8, seed=17, signal=True)
    _rep0 = score_panel(_r0, _m0, n_folds=3, n_shuffles=100, seed=5,
                        min_trades=30, min_rows=200, n_negative_controls=0)
    check("zero_bank_is_unchecked", _rep0.get("harness_state") == "unchecked",
          str(_rep0.get("harness_state")))
    check("unchecked_is_not_valid", _rep0.get("harness_valid") is False,
          "an unrun bank must never report a valid harness")
    check("unchecked_is_not_a_result", _rep0.get("verdict") == "harness_invalid",
          str(_rep0.get("verdict")))

    # --- the bank must not leak into the FAMILY ---------------------------
    # Bank columns are dense numeric columns sitting on the same rows as the real
    # features. If `_dense_feats` picked them up they would inflate the
    # max-statistic threshold with planted synthetics and be reported as findings.
    _rf, _mf = _synth_panel(60, 8, seed=17, signal=True)
    _repf = score_panel(_rf, _mf, n_folds=3, n_shuffles=100, seed=5,
                        min_trades=30, min_rows=200, n_negative_controls=8)
    _fnames = {d["feature"] for d in _repf["features"]}
    check("bank_excluded_from_family",
          not any(n.startswith(CTRL_NOISE) or n == CTRL_SIGNAL for n in _fnames),
          f"control columns leaked into the scored family: {sorted(_fnames)}")
    check("bank_size_echoed_in_config",
          _repf["config"]["n_negative_controls"] == 8,
          str(_repf["config"].get("n_negative_controls")))
    check("bank_reported_at_requested_size",
          (_repf["controls"]["negative_bank"] or {}).get("n_controls") == 8,
          str(_repf["controls"]["negative_bank"]))

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

    # --- min-p must actually DO something, not decorate the report ---------
    # A companion decision rule nobody can show changes a verdict is decoration.
    # Construct the regime it exists for: one WIDE-null feature (trade-constant,
    # uninformative) and one NARROW-null feature (near-white, genuinely but
    # weakly informative). The raw max-statistic threshold is set by the wide
    # one and misses the narrow one; min-p, being scale-free, does not.
    _rng = random.Random(99)
    _rows = []
    for _t in range(80):
        _base = _rng.gauss(0, 1)
        _lvl = _rng.gauss(0, 1)
        for _b in range(10):
            _y = _base + _rng.gauss(0, 0.3)
            _rows.append({
                "trade_id": _t,
                "decision_time": f"2026-01-{1 + _t // 24:02d}T{_t % 24:02d}:{_b:02d}:00Z",
                "label_t0": _t * 10 + _b + 1, "label_t1": _t * 10 + _b + 2,
                TARGET_COL: _y,
                "feat_tradelevel": _lvl + 0.02 * _b,
                "feat_white_weak": _y * 0.25 + _rng.gauss(0, 1.0),
            })
    _man = {"dense_feature_cols": ["feat_tradelevel", "feat_white_weak"],
            "symbol": "SYNTH", "timeframe": "5m"}
    _r = score_panel(_rows, _man, n_folds=3, n_shuffles=600, seed=3,
                     min_trades=30, min_rows=200)
    _w = next(d for d in _r["features"] if d["feature"] == "feat_white_weak")
    _tl = next(d for d in _r["features"] if d["feature"] == "feat_tradelevel")
    check("null_scales_are_heterogeneous",
          _tl["pointwise_threshold"] > 2.0 * _w["pointwise_threshold"],
          f"wide={_tl['pointwise_threshold']:.4f} narrow={_w['pointwise_threshold']:.4f} — "
          "if these converge, the scale problem min-p addresses is not present in "
          "this fixture and the test below proves nothing")
    check("minp_rescues_a_narrow_null_signal",
          _w["informative_minp"] and not _w["informative_fwer"],
          f"white_weak: minp={_w['informative_minp']} fwer={_w['informative_fwer']} — "
          "min-p must promote a real signal that the scale-dependent max-statistic "
          "rule misses, or it is not doing the job it was added for")
    check("minp_does_not_promote_the_uninformative_one",
          not _tl["informative_minp"],
          "the trade-constant uninformative feature must stay negative under min-p too")

    # --- --target must change the POPULATION, not just a label string ------
    rows, man = _synth_panel(60, 8, seed=11, signal=True)
    for r in rows:
        r["advantage_r"] = r[TARGET_COL] * -1.0
        r["label_hold"] = 1 if r[TARGET_COL] > 0 else 0
    _reps = {t: score_panel(rows, man, n_folds=3, n_shuffles=60, seed=5, target=t)
             for t in ("forward_r", "advantage_r", "label_hold")}
    check("target_is_stamped", all(_reps[t]["target"] == t for t in _reps),
          "the report must stamp which target produced it")
    check("target_changes_the_statistic",
          _reps["label_hold"]["features"][0]["statistic"]
          != _reps["forward_r"]["features"][0]["statistic"],
          "binarising the target must change the score; identical numbers would mean "
          "the flag is cosmetic and the target was never actually switched")

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
    p.add_argument("--n-negative-controls", type=int, default=N_NEGATIVE_CONTROLS,
                   help=("Size of the negative-control BANK. The gate reads the RATE at "
                         "which pure-noise columns clear the pointwise bar, because one "
                         "column is a Bernoulli(alpha) draw that cannot tell bad luck "
                         "from a broken null. 0 means the bank is not run at all, which "
                         "reports harness_state='unchecked' and is NOT a pass."))
    p.add_argument("--gate-level", type=float, default=GATE_LEVEL,
                   help=("Binomial upper-tail level below which the bank's clear-rate is "
                         "called miscalibration. This IS the gate's false-invalidation "
                         "rate; _selftest measures it rather than assuming it."))
    p.add_argument("--target", default=TARGET_COL, choices=list(TARGETS),
                   help=("Outcome column. 'forward_r' is the pre-registered primary and is "
                         "measured FROM ENTRY, so it shares a baseline with feat_upnl_r; "
                         "'advantage_r' = forward_r - upnl_r - cost_r differences that "
                         "baseline out and is what a hold-vs-exit lever actually needs; "
                         "'label_hold' is its sign."))
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
        min_fold_rows=args.min_fold_rows, target=args.target,
        n_negative_controls=args.n_negative_controls, gate_level=args.gate_level,
    )
    rep["panel_path"] = str(args.panel)

    pop = rep["population"]
    print(f"E2 [{args.panel}] target={args.target}")
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
        print(f"  minp_threshold={rep.get('minp_threshold')} (scale-free companion) "
              f"n_minp={rep.get('n_informative_minp')}")
        for d in rep["features"][:12]:
            flag = "FWER" if d["informative_fwer"] else (
                "minp" if d["informative_minp"] else ("pt" if d["informative_pointwise"] else "  "))
            print(f"    {flag:>4}  {d['feature']:<34} stat={d['statistic']} "
                  f"p={d['p_empirical']} folds={d['folds_used']} sign_agree={d['sign_agreement']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
        print(f"  → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
