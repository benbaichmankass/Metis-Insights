#!/usr/bin/env python3
"""M30 · C2 — the research-panel analysis TOOLKIT (standing discovery).

The technical-quant-research platform's second brick (scoping:
``docs/research/technical-quant-research-platform-scoping-2026-07-27.md`` § C2).
It reads the C1 panel (``scripts/research/build_research_panel.py`` →
``panel.jsonl`` + ``.manifest.json``) and runs the data-first feature→outcome
discovery the operator asked for:

  1. **Conditional edge / expectancy-by-bucket tables** — per feature (graded →
     quantile buckets, categorical → per level, gate → true/false): n, win-rate,
     expectancy (mean pnl), mean R, plus a univariate discrimination p-value.
  2. **Multivariate regression** — logistic (win) + linear (R-multiple) of the
     outcome on the standardized graded-feature panel. Hand-rolled IRLS / ridge
     OLS (no sklearn/statsmodels) so the tool stays dependency-light; read the
     coefficients as *leads*, the OOS metric is the honest discrimination.
  3. **Permutation feature-importance** — OOS AUC drop per graded feature under
     the same CV (shuffle one test column, measure the discrimination it loses).
  4. **Collinearity / redundancy map** — correlation matrix + VIF, so a
     "discovery" isn't three views of the same variable; + top pairwise
     interaction terms.
  5. **All under purged / embargoed walk-forward CV** (the canonical
     ``ml.experiments.splitters.iter_folds`` with ``purged_walk_forward``) and a
     **Benjamini-Hochberg FDR** control over the univariate p-values — mandatory,
     not optional: discovery on the small real book (the M23 ~376-row wall)
     manufactures spurious correlations otherwise (the M18 coin-flip prior). The
     report stamps that prior and the FDR survivors loudly.

**Leakage discipline (inherited from the panel, asserted here).** The panel
manifest splits ``feature_cols`` (decision-time) from ``outcome_cols`` (strictly
future). This toolkit regresses **outcome on feature only** and refuses to run a
model if the split is missing or violated — never an outcome on a same-time
column by accident.

**Observe-only, Tier-1.** Reads a JSONL file the C1 builder wrote; never opens a
DB / broker socket, never writes the money DB, never touches the order path.
Numpy-gated: the edge tables + univariate p-values + FDR are pure-python and
always compute; the regression / importance / VIF sections emit an honest
``not_computed`` envelope when numpy is unavailable — never a faked number.

Usage::

    python scripts/research/analyze_research_panel.py                         # reads runtime_logs/research/panel.jsonl
    python scripts/research/analyze_research_panel.py --panel /tmp/panel.jsonl --outcome win
    python scripts/research/analyze_research_panel.py --n-buckets 4 --fdr-alpha 0.1 --cv-folds 5
    # Restrict the MULTIVARIATE fit to a dense common-core subset so a pooled,
    # block-sparse panel can run regression/importance/VIF (edge tables + FDR
    # stay over the full univariate set):
    python scripts/research/analyze_research_panel.py \\
        --features feat_confidence,feat_model_score_mean,feat_model_score_max,feat_adx_14
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The canonical purged/embargoed walk-forward CV — single source of truth.
try:
    from ml.experiments.splitters import iter_folds  # noqa: E402

    _SPLITTERS_OK = True
except Exception:  # noqa: BLE001 — keep the tool importable on a minimal tree
    _SPLITTERS_OK = False

    def iter_folds(rows, config):  # type: ignore[misc]  # inert: rows, config — import-fallback stub that only raises; the signature must match ml.experiments.splitters.iter_folds
        raise RuntimeError("ml.experiments.splitters unavailable")


# numpy is OPTIONAL — only the regression / importance / VIF sections use it.
# The edge tables, univariate p-values, and BH-FDR are pure-python (numpy-free),
# matching the numpy-gated precedent in component_edge_report.py.
try:
    import numpy as _np  # noqa: E402

    _NUMPY_OK = True
except Exception:  # noqa: BLE001
    _np = None  # type: ignore[assignment]
    _NUMPY_OK = False


# The M18 coin-flip prior — stamped on every report so a reader never treats a
# discovery on the small real book as established without the CV + FDR passing.
_COIN_FLIP_PRIOR = (
    "M18 coin-flip prior: on a small real book (the M23 ~376-row wall) "
    "unguarded discovery manufactures spurious correlations. A feature is a "
    "LEAD, not a finding, unless it (a) survives Benjamini-Hochberg FDR at the "
    "chosen alpha AND (b) shows positive out-of-sample discrimination under the "
    "purged/embargoed walk-forward CV. In-sample coefficients are never a gate."
)


# ---------------------------------------------------------------------------
# Panel load + leakage assertion
# ---------------------------------------------------------------------------


def load_panel(panel_path: Path) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Read the panel JSONL + its sibling ``.manifest.json`` (if present).

    Tolerant: a missing / unreadable panel returns ``([], None)`` so the caller
    emits an honest empty report rather than crashing.
    """
    rows: List[Dict[str, Any]] = []
    if panel_path.exists():
        try:
            with panel_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(obj, dict):
                        rows.append(obj)
        except OSError:
            rows = []
    manifest: Optional[Dict[str, Any]] = None
    manifest_path = panel_path.with_suffix(panel_path.suffix + ".manifest.json")
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, TypeError, ValueError):
            manifest = None
    return rows, manifest


def resolve_columns(
    rows: Sequence[Dict[str, Any]], manifest: Optional[Dict[str, Any]]
) -> Tuple[List[str], List[str], Dict[str, bool]]:
    """Return ``(feature_cols, outcome_cols, leakage)``.

    Prefer the manifest's stamped split; fall back to a prefix scan of the rows
    (``feat_``/``cat_``/``gate_`` = feature; ``pnl``/``win``/``r`` = outcome) so
    the tool still works against a hand-built panel, but flag that the leakage
    contract was NOT asserted from a manifest in that case.
    """
    outcome_cols = ["pnl", "win", "r"]
    if manifest and isinstance(manifest.get("feature_cols"), list):
        feature_cols = [str(c) for c in manifest["feature_cols"]]
        outcome_cols = [str(c) for c in manifest.get("outcome_cols", outcome_cols)]
        asserted = True
    else:
        seen = {k for row in rows for k in row}
        feature_cols = sorted(
            c
            for c in seen
            if c.startswith(("feat_", "cat_", "gate_")) and c != "model_score_count"
        )
        asserted = False
    overlap = sorted(set(feature_cols) & set(outcome_cols))
    leakage = {
        "manifest_asserted": asserted,
        "clean": not overlap,
        "overlap": overlap,
    }
    return feature_cols, outcome_cols, leakage


def _col_kind(col: str) -> str:
    if col.startswith("feat_"):
        return "graded"
    if col.startswith("cat_"):
        return "categorical"
    if col.startswith("gate_"):
        return "gate"
    return "graded"


# ---------------------------------------------------------------------------
# Pure-python statistics (numpy-free) — always available
# ---------------------------------------------------------------------------


def _norm_sf(z: float) -> float:
    """Upper-tail standard-normal survival function via ``math.erfc`` (stdlib).

    ``P(Z > z)``; two-sided p for a statistic is ``2 * _norm_sf(|z|)``.
    """
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _two_sided_p(z: float) -> float:
    return max(0.0, min(1.0, 2.0 * _norm_sf(abs(z))))


def auc_win(values: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    """Mann-Whitney rank AUC of a numeric feature against a binary label.

    Tie-aware (average ranks). Returns ``None`` when a class is missing or
    fewer than 2 usable points. Pure-python (no numpy).
    """
    pairs = [(float(v), int(lab)) for v, lab in zip(values, labels) if v is not None]
    n = len(pairs)
    n_pos = sum(1 for _, lab in pairs if lab == 1)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    pairs.sort(key=lambda t: t[0])
    # Average ranks (1-based) with tie handling.
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average of 1-based ranks i+1..j+1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sum_pos = sum(ranks[k] for k in range(n) if pairs[k][1] == 1)
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return u / (n_pos * n_neg)


def _auc_pvalue(auc: float, n_pos: int, n_neg: int) -> float:
    """Two-sided p that AUC differs from 0.5 (normal approx to Mann-Whitney U)."""
    if n_pos <= 0 or n_neg <= 0:
        return 1.0
    se_auc = math.sqrt((n_pos + n_neg + 1) / (12.0 * n_pos * n_neg))
    if se_auc <= 0:
        return 1.0
    return _two_sided_p((auc - 0.5) / se_auc)


def _two_proportion_p(w1: int, n1: int, w2: int, n2: int) -> float:
    """Two-sided p for a difference in two win-rate proportions (pooled z)."""
    if n1 <= 0 or n2 <= 0:
        return 1.0
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1.0 / n1 + 1.0 / n2))
    if se <= 0:
        return 1.0
    return _two_sided_p((p1 - p2) / se)


def _rank(xs: Sequence[float]) -> List[float]:
    """Average (tie-corrected) 1-based ranks of ``xs``. Pure-python."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman_p(values: Sequence[float], ys: Sequence[float]) -> Tuple[Optional[float], float]:
    """(rho, two-sided p) for the Spearman rank correlation of a graded feature
    vs a **continuous** outcome (normal approx z = rho·√(n−1)). The continuous
    analogue of the win-AUC univariate test, so continuous outcomes (e.g. the
    excursion columns giveback_r / capture_ratio) get their own BH-FDR p. Pure
    python; ``(None, 1.0)`` when under-powered or degenerate."""
    pairs = [
        (float(v), float(y))
        for v, y in zip(values, ys)
        if v is not None and y is not None
    ]
    n = len(pairs)
    if n < 3:
        return None, 1.0
    xr = _rank([p[0] for p in pairs])
    yr = _rank([p[1] for p in pairs])
    mx, my = sum(xr) / n, sum(yr) / n
    cov = sum((xr[i] - mx) * (yr[i] - my) for i in range(n))
    vx = sum((xr[i] - mx) ** 2 for i in range(n))
    vy = sum((yr[i] - my) ** 2 for i in range(n))
    if vx <= 0 or vy <= 0:
        return 0.0, 1.0
    rho = cov / math.sqrt(vx * vy)
    return round(rho, 4), _two_sided_p(rho * math.sqrt(n - 1))


def _group_outcome_p(rows, outcome, in_group):
    """Two-sided p that a **continuous** ``outcome`` differs between a group
    (``in_group(r)`` truthy) and the rest — Mann-Whitney via the rank-AUC,
    reusing ``auc_win`` (the outcome as the value, group membership as the
    binary label). The continuous analogue of the two-proportion gate /
    categorical test. ``1.0`` when a class is empty / degenerate."""
    vals: List[float] = []
    labs: List[int] = []
    for r in rows:
        o = r.get(outcome)
        if o is None:
            continue
        vals.append(float(o))
        labs.append(1 if in_group(r) else 0)
    auc = auc_win(vals, labs)
    if auc is None:
        return 1.0
    n_pos = sum(labs)
    return _auc_pvalue(auc, n_pos, len(labs) - n_pos)


def _quantile_edges(values: Sequence[float], n_buckets: int) -> List[float]:
    s = sorted(values)
    if not s:
        return []
    edges = []
    for q in range(1, n_buckets):
        pos = q / n_buckets * (len(s) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(s) - 1)
        frac = pos - lo
        edges.append(s[lo] * (1 - frac) + s[hi] * frac)
    return edges


def _bucket_of(v: float, edges: Sequence[float]) -> int:
    for i, e in enumerate(edges):
        if v <= e:
            return i
    return len(edges)


def _bucket_stats(
    rows_slice: Sequence[Dict[str, Any]], outcome: str = "win"
) -> Dict[str, Any]:
    n = len(rows_slice)
    wins = sum(int(r.get("win", 0)) for r in rows_slice)
    pnls = [float(r["pnl"]) for r in rows_slice if r.get("pnl") is not None]
    rs = [float(r["r"]) for r in rows_slice if r.get("r") is not None]
    stats = {
        "n": n,
        "win_rate": round(wins / n, 4) if n else None,
        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "mean_r": round(sum(rs) / len(rs), 4) if rs else None,
        "r_n": len(rs),
    }
    # For a non-standard (continuous) outcome, report the mean of THAT column
    # per bucket so the edge table is meaningful (win_rate/mean_r stay for
    # reference, computed only where those columns exist).
    if outcome not in ("win", "pnl", "r"):
        ovals = [float(r[outcome]) for r in rows_slice if r.get(outcome) is not None]
        stats["mean_outcome"] = round(sum(ovals) / len(ovals), 4) if ovals else None
        stats["outcome_n"] = len(ovals)
    return stats


def conditional_edge_tables(
    rows: Sequence[Dict[str, Any]],
    feature_cols: Sequence[str],
    *,
    n_buckets: int,
    min_bucket: int,
    outcome: str = "win",
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, float]]]:
    """Per-feature edge-by-bucket table + one univariate p-value per feature.

    Returns ``(tables, pvalues)`` where ``pvalues`` is ``[(feature, p), ...]``
    for the BH-FDR pass. Pure-python — always computes.

    ``outcome`` selects the univariate test: ``win`` / ``r`` keep the original
    **win-AUC** univariate (unchanged, backward-compatible); any OTHER (continuous)
    outcome — e.g. the P5 excursion columns ``giveback_r`` / ``capture_ratio`` —
    uses a **rank** test against that column (Spearman for graded features,
    Mann-Whitney of the outcome between groups for gate/categorical), so the
    FDR p reflects the actual outcome being studied.
    """
    # Continuous-outcome mode: a genuinely new numeric outcome column (not the
    # legacy win/r whose univariate stays win-AUC for backward compatibility).
    continuous = outcome not in ("win", "r")
    base_n = len(rows)
    base_wins = sum(int(r.get("win", 0)) for r in rows)
    tables: List[Dict[str, Any]] = []
    pvalues: List[Tuple[str, float]] = []

    for col in feature_cols:
        kind = _col_kind(col)
        measured = [r for r in rows if r.get(col) is not None]
        if len(measured) < min_bucket:
            tables.append(
                {"feature": col, "kind": kind, "n": len(measured),
                 "note": "too few measured rows", "buckets": []}
            )
            continue

        table: Dict[str, Any] = {"feature": col, "kind": kind, "n": len(measured)}
        pval = 1.0

        if kind == "graded":
            values = [float(r[col]) for r in measured]
            labels = [int(r.get("win", 0)) for r in measured]
            edges = _quantile_edges(values, n_buckets)
            buckets: List[Dict[str, Any]] = []
            for b in range(len(edges) + 1):
                members = [measured[i] for i, v in enumerate(values) if _bucket_of(v, edges) == b]
                if not members:
                    continue
                bstat = _bucket_stats(members, outcome)
                bstat["bucket"] = b
                buckets.append(bstat)
            table["buckets"] = buckets
            if continuous:
                rho, pval = _spearman_p(values, [r.get(outcome) for r in measured])
                if rho is not None:
                    table["spearman_rho"] = rho
                else:
                    table["note"] = "too few outcome-measured rows for Spearman"
            else:
                auc = auc_win(values, labels)
                n_pos = sum(labels)
                n_neg = len(labels) - n_pos
                if auc is not None:
                    table["auc"] = round(auc, 4)
                    pval = _auc_pvalue(auc, n_pos, n_neg)
                else:
                    table["note"] = "single outcome class — AUC undefined"

        elif kind == "gate":
            true_rows = [r for r in measured if bool(r[col])]
            false_rows = [r for r in measured if not bool(r[col])]
            buckets = []
            for label, grp in (("true", true_rows), ("false", false_rows)):
                if grp:
                    bstat = _bucket_stats(grp, outcome)
                    bstat["level"] = label
                    buckets.append(bstat)
            table["buckets"] = buckets
            if true_rows and false_rows:
                if continuous:
                    pval = _group_outcome_p(measured, outcome, lambda r: bool(r[col]))
                else:
                    pval = _two_proportion_p(
                        sum(int(r.get("win", 0)) for r in true_rows), len(true_rows),
                        sum(int(r.get("win", 0)) for r in false_rows), len(false_rows),
                    )

        else:  # categorical
            levels: Dict[str, List[Dict[str, Any]]] = {}
            for r in measured:
                levels.setdefault(str(r[col]), []).append(r)
            buckets = []
            best_p = 1.0
            for lvl, grp in sorted(levels.items()):
                bstat = _bucket_stats(grp, outcome)
                bstat["level"] = lvl
                buckets.append(bstat)
                # best level-vs-rest test (implicit multi-compare — conservatively
                # folded into the FDR denominator via one p).
                if len(grp) >= min_bucket:
                    rest = [r for r in measured if str(r.get(col)) != lvl]
                    if rest:
                        if continuous:
                            p = _group_outcome_p(
                                measured, outcome, lambda r, _lvl=lvl: str(r.get(col)) == _lvl
                            )
                        else:
                            p = _two_proportion_p(
                                sum(int(r.get("win", 0)) for r in grp), len(grp),
                                sum(int(r.get("win", 0)) for r in rest), len(rest),
                            )
                        best_p = min(best_p, p)
            table["buckets"] = buckets
            table["note"] = (
                "categorical p = best level-vs-rest (implicit multi-compare; "
                "read conservatively)"
            )
            pval = best_p

        table["p_value"] = round(pval, 6)
        tables.append(table)
        pvalues.append((col, pval))

    tables_meta: Dict[str, Any] = {
        "base_n": base_n,
        "base_win_rate": round(base_wins / base_n, 4) if base_n else None,
    }
    if continuous:
        ov = [float(r[outcome]) for r in rows if r.get(outcome) is not None]
        tables_meta["outcome"] = outcome
        tables_meta["base_mean_outcome"] = round(sum(ov) / len(ov), 4) if ov else None
    for t in tables:
        t["_base"] = tables_meta
    return tables, pvalues


def benjamini_hochberg(
    pvalues: Sequence[Tuple[str, float]], alpha: float
) -> Dict[str, Any]:
    """Benjamini-Hochberg FDR over ``[(name, p), ...]``.

    Returns q-values (monotone-enforced) + the set surviving at ``alpha``.
    """
    m = len(pvalues)
    if m == 0:
        return {"alpha": alpha, "m": 0, "q_values": {}, "survivors": []}
    ordered = sorted(pvalues, key=lambda t: t[1])
    q_raw = [(name, p * m / (rank + 1)) for rank, (name, p) in enumerate(ordered)]
    # Enforce monotonicity from the largest rank downward.
    q_mono: List[Tuple[str, float]] = [(q_raw[-1][0], min(1.0, q_raw[-1][1]))]
    for i in range(len(q_raw) - 2, -1, -1):
        name, q = q_raw[i]
        q_mono.append((name, min(q_mono[-1][1], min(1.0, q))))
    q_mono.reverse()
    q_values = {name: round(q, 6) for name, q in q_mono}
    survivors = [name for name, q in q_mono if q <= alpha]
    return {"alpha": alpha, "m": m, "q_values": q_values, "survivors": survivors}


# ---------------------------------------------------------------------------
# numpy-gated: model matrix, regression, importance, VIF (under purged CV)
# ---------------------------------------------------------------------------


def _graded_cols(feature_cols: Sequence[str]) -> List[str]:
    return [c for c in feature_cols if _col_kind(c) == "graded"]


def _complete_matrix(
    rows: Sequence[Dict[str, Any]], graded: Sequence[str], outcome: str
):
    """(X, y, kept_rows) over rows with EVERY graded col + a non-null outcome.

    ``kept_rows`` preserves the original dict rows (for the CV time_column).
    """
    kept, xs, ys = [], [], []
    for r in rows:
        if r.get(outcome) is None:
            continue
        if any(r.get(c) is None for c in graded):
            continue
        try:
            xs.append([float(r[c]) for c in graded])
            ys.append(float(r[outcome]))
        except (TypeError, ValueError):
            continue
        kept.append(r)
    if not kept:
        return None, None, []
    return _np.array(xs, dtype=float), _np.array(ys, dtype=float), kept


def _standardize(x_train, x_apply):
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0)
    sd_safe = _np.where(sd > 1e-12, sd, 1.0)
    return (x_apply - mu) / sd_safe, mu, sd_safe


def _fit_logistic(x, y, *, iters: int = 50, ridge: float = 1e-4):
    """Tiny IRLS logistic regression (numpy) — the same primitive the Layer-1a
    edge report uses. Returns the coefficient vector or ``None`` on divergence."""
    n_feat = x.shape[1]
    beta = _np.zeros(n_feat)
    eye = _np.eye(n_feat) * ridge
    for _ in range(iters):
        eta = _np.clip(x @ beta, -30, 30)
        p = 1.0 / (1.0 + _np.exp(-eta))
        w = _np.clip(p * (1 - p), 1e-6, None)
        grad = x.T @ (y - p)
        hess = x.T @ (x * w[:, None]) + eye
        try:
            step = _np.linalg.solve(hess, grad)
        except Exception:  # noqa: BLE001
            return None
        beta = beta + step
        if not _np.all(_np.isfinite(beta)):
            return None
        if _np.max(_np.abs(step)) < 1e-6:
            break
    return beta if _np.all(_np.isfinite(beta)) else None


def _fit_ridge_ols(x, y, *, ridge: float = 1e-4):
    """Ridge OLS closed form; returns the coefficient vector or ``None``."""
    n_feat = x.shape[1]
    xtx = x.T @ x + _np.eye(n_feat) * ridge
    try:
        return _np.linalg.solve(xtx, x.T @ y)
    except Exception:  # noqa: BLE001
        return None


def _auc_np(scores, labels) -> Optional[float]:
    order = _np.argsort(scores, kind="mergesort")
    labels = labels[order]
    n_pos = float(labels.sum())
    n_neg = float(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _np.arange(1, len(labels) + 1, dtype=float)
    rank_sum_pos = ranks[labels == 1].sum()
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def _cv_config(cv_folds: int, min_train_fraction: float, label_horizon: int, embargo_fraction: float):
    return {
        "split_strategy": "purged_walk_forward",
        "time_column": "closed_at",
        "n_folds": cv_folds,
        "min_train_fraction": min_train_fraction,
        "label_horizon": label_horizon,
        "embargo_fraction": embargo_fraction,
    }


def _not_computed(note: str) -> Dict[str, Any]:
    return {"computed": False, "note": note}


def regression_and_importance(
    rows: Sequence[Dict[str, Any]],
    feature_cols: Sequence[str],
    *,
    outcome: str,
    cv_folds: int,
    min_train_fraction: float,
    label_horizon: int,
    embargo_fraction: float,
    perm_repeats: int,
    seed: int,
) -> Dict[str, Any]:
    """Multivariate regression + permutation importance under purged WF-CV.

    ``outcome`` ∈ {"win" (logistic, OOS AUC), "r" (linear, OOS R²)}. Numpy-gated;
    honest ``not_computed`` when numpy / the splitter is unavailable or the
    panel is too small for a stable fit.
    """
    if not _NUMPY_OK:
        return _not_computed("numpy unavailable — no multivariate fit attempted")
    if not _SPLITTERS_OK:
        return _not_computed("ml.experiments.splitters unavailable — no CV")

    graded = _graded_cols(feature_cols)
    if not graded:
        return _not_computed("no graded feature columns in the panel")

    x_all, y_all, kept = _complete_matrix(rows, graded, outcome)
    n_feat = len(graded)
    if x_all is None or len(kept) < max(20, 5 * n_feat):
        got = 0 if x_all is None else len(kept)
        return _not_computed(
            f"only {got} complete-vector rows for {n_feat} features — too few "
            f"for a stable fit (need >= max(20, 5*n_feat))"
        )
    if outcome == "win" and set(int(v) for v in y_all) != {0, 1}:
        return _not_computed("only one outcome class present — logistic undefined")

    is_logistic = outcome == "win"
    config = _cv_config(cv_folds, min_train_fraction, label_horizon, embargo_fraction)

    # The purged/embargoed WF-CV orders rows by ``closed_at``; a row with no
    # close time cannot be placed on that axis (and the canonical splitter's
    # sort key chokes on a None), so drop null-time rows from the CV set with an
    # honest count. Full-sample coefficients below still use every kept row.
    time_col = config["time_column"]

    def _has_time(r: Dict[str, Any]) -> bool:
        v = r.get(time_col)
        return v is not None and str(v).strip() != ""

    cv_rows = [r for r in kept if _has_time(r)]
    dropped_null_time = len(kept) - len(cv_rows)
    if len(cv_rows) < max(20, 5 * n_feat):
        return _not_computed(
            f"only {len(cv_rows)} rows carry a usable '{time_col}' for the "
            f"purged CV ({dropped_null_time} dropped as null-time) — too few "
            f"for a stable out-of-sample fit"
        )

    # ---- OOS metric + permutation importance across purged folds -----------
    rng = _np.random.default_rng(seed)
    try:
        folds = iter_folds(cv_rows, config)
    except Exception as exc:  # noqa: BLE001
        return _not_computed(f"CV produced no usable folds: {exc}")

    fold_metrics: List[float] = []
    # importance accumulates AUC/R² DROP when a feature's test column is shuffled
    imp_drops: Dict[str, List[float]] = {c: [] for c in graded}

    def _metric(model_beta, x_std_test, y_test) -> Optional[float]:
        xb = _np.column_stack([_np.ones(len(x_std_test)), x_std_test])
        if is_logistic:
            eta = _np.clip(xb @ model_beta, -30, 30)
            scores = 1.0 / (1.0 + _np.exp(-eta))
            return _auc_np(scores, y_test.astype(int))
        pred = xb @ model_beta
        ss_res = float(((y_test - pred) ** 2).sum())
        ss_tot = float(((y_test - y_test.mean()) ** 2).sum())
        return None if ss_tot <= 0 else 1.0 - ss_res / ss_tot

    def _row_matrix(fold_rows):
        xs, ys = [], []
        for r in fold_rows:
            xs.append([float(r[c]) for c in graded])
            ys.append(float(r[outcome]))
        return _np.array(xs, dtype=float), _np.array(ys, dtype=float)

    usable_folds = 0
    for train_rows, test_rows in folds:
        if len(train_rows) < max(10, n_feat + 2) or len(test_rows) < 5:
            continue
        x_tr, y_tr = _row_matrix(train_rows)
        x_te, y_te = _row_matrix(test_rows)
        if is_logistic and set(int(v) for v in y_tr) != {0, 1}:
            continue
        x_tr_std, mu, sd = _standardize(x_tr, x_tr)
        x_te_std = (x_te - mu) / sd
        xb_tr = _np.column_stack([_np.ones(len(x_tr_std)), x_tr_std])
        beta = _fit_logistic(xb_tr, y_tr) if is_logistic else _fit_ridge_ols(xb_tr, y_tr)
        if beta is None:
            continue
        base = _metric(beta, x_te_std, y_te)
        if base is None:
            continue
        fold_metrics.append(base)
        usable_folds += 1
        for j, col in enumerate(graded):
            drops = []
            for _ in range(perm_repeats):
                x_perm = x_te_std.copy()
                x_perm[:, j] = rng.permutation(x_perm[:, j])
                m = _metric(beta, x_perm, y_te)
                if m is not None:
                    drops.append(base - m)
            if drops:
                imp_drops[col].append(sum(drops) / len(drops))

    if usable_folds == 0:
        return _not_computed(
            "no fold had both a stable fit and a defined OOS metric "
            "(panel too small / degenerate for purged CV)"
        )

    metric_name = "oos_auc" if is_logistic else "oos_r2"
    oos_mean = round(sum(fold_metrics) / len(fold_metrics), 4)
    importance = {
        c: round(sum(v) / len(v), 5) if v else None for c, v in imp_drops.items()
    }
    importance_ranked = sorted(
        ((c, i) for c, i in importance.items() if i is not None),
        key=lambda t: t[1],
        reverse=True,
    )

    # ---- Full-sample coefficients (a LEAD, never a gate) -------------------
    x_std_full, _, _ = _standardize(x_all, x_all)
    xb_full = _np.column_stack([_np.ones(len(x_std_full)), x_std_full])
    beta_full = _fit_logistic(xb_full, y_all) if is_logistic else _fit_ridge_ols(xb_full, y_all)
    coeffs = None
    if beta_full is not None:
        coeffs = {c: round(float(beta_full[i + 1]), 4) for i, c in enumerate(graded)}

    return {
        "computed": True,
        "outcome": outcome,
        "model": "logistic" if is_logistic else "ridge_ols",
        "n_rows": len(kept),
        "n_features": n_feat,
        "cv": {
            "strategy": "purged_walk_forward",
            "folds_requested": cv_folds,
            "folds_usable": usable_folds,
            "cv_rows": len(cv_rows),
            "dropped_null_time": dropped_null_time,
            "min_train_fraction": min_train_fraction,
            "label_horizon": label_horizon,
            "embargo_fraction": embargo_fraction,
            metric_name: oos_mean,
            f"{metric_name}_by_fold": [round(m, 4) for m in fold_metrics],
        },
        "standardized_coefficients": coeffs,
        "permutation_importance": importance,
        "permutation_importance_ranked": importance_ranked,
        "note": (
            "OOS metric is the honest discrimination; coefficients are a lead. "
            f"AUC 0.5 / R² 0 = coin-flip. perm_repeats={perm_repeats}, seed={seed}."
        ),
    }


def collinearity_map(
    rows: Sequence[Dict[str, Any]],
    feature_cols: Sequence[str],
    *,
    top_interactions: int,
) -> Dict[str, Any]:
    """Correlation matrix + VIF over the graded panel, + top interaction leads."""
    if not _NUMPY_OK:
        return _not_computed("numpy unavailable — no correlation/VIF")
    graded = _graded_cols(feature_cols)
    if len(graded) < 2:
        return _not_computed("need >= 2 graded features for collinearity")
    x, _, kept = _complete_matrix(rows, graded, "win")
    if x is None or len(kept) < max(10, len(graded) + 2):
        got = 0 if x is None else len(kept)
        return _not_computed(f"only {got} complete-vector rows — too few for VIF")

    # Correlation matrix. A zero-variance (constant) graded column makes
    # corrcoef divide 0/0 → NaN; suppress the benign warning and emit NaN as
    # JSON-safe ``None`` (a literal ``NaN`` token is invalid JSON for a strict
    # non-Python consumer). VIF/interactions below are unaffected.
    def _finite3(v: Any) -> Optional[float]:
        fv = float(v)
        return round(fv, 3) if math.isfinite(fv) else None

    with _np.errstate(invalid="ignore", divide="ignore"):
        corr = _np.corrcoef(x, rowvar=False)
    corr_out = {
        graded[i]: {graded[j]: _finite3(corr[i, j]) for j in range(len(graded))}
        for i in range(len(graded))
    }
    # VIF_j = 1/(1-R²_j) from regressing x_j on the other columns.
    vif: Dict[str, Optional[float]] = {}
    x_std, _, _ = _standardize(x, x)
    for j, col in enumerate(graded):
        others = _np.delete(x_std, j, axis=1)
        target = x_std[:, j]
        xb = _np.column_stack([_np.ones(len(others)), others])
        beta = _fit_ridge_ols(xb, target)
        if beta is None:
            vif[col] = None
            continue
        pred = xb @ beta
        ss_res = float(((target - pred) ** 2).sum())
        ss_tot = float(((target - target.mean()) ** 2).sum())
        r2 = 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot
        r2 = min(max(r2, 0.0), 0.999999)
        vif[col] = round(1.0 / (1.0 - r2), 3)

    # Top pairwise interaction leads by |univariate AUC of the product|.
    interactions: List[Dict[str, Any]] = []
    y = _np.array([int(r.get("win", 0)) for r in kept])
    pairs: List[Tuple[str, str, float]] = []
    for i in range(len(graded)):
        for j in range(i + 1, len(graded)):
            prod = x[:, i] * x[:, j]
            a = _auc_np(prod, y)
            if a is not None:
                pairs.append((graded[i], graded[j], abs(a - 0.5)))
    pairs.sort(key=lambda t: t[2], reverse=True)
    for gi, gj, edge in pairs[:top_interactions]:
        interactions.append(
            {"pair": [gi, gj], "abs_auc_minus_half": round(edge, 4)}
        )

    high_vif = [c for c, v in vif.items() if v is not None and v >= 5.0]
    return {
        "computed": True,
        "n_rows": len(kept),
        "correlation": corr_out,
        "vif": vif,
        "high_vif_features": high_vif,
        "top_interactions": interactions,
        "note": (
            "VIF >= 5 flags a feature largely explained by the others (a "
            "'discovery' there may be a re-view of another variable). "
            "Interactions are leads to test, not findings."
        ),
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def analyze(
    rows: Sequence[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    outcome: str,
    n_buckets: int,
    min_bucket: int,
    fdr_alpha: float,
    cv_folds: int,
    min_train_fraction: float,
    label_horizon: int,
    embargo_fraction: float,
    perm_repeats: int,
    seed: int,
    cohort: Optional[str] = None,
    feature_subset: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    feature_cols, outcome_cols, leakage = resolve_columns(rows, manifest)

    # --- Cohort discipline: real and paper are NEVER silently blended. ---
    # C1 stamps a per-row ``cohort`` (real/paper). A panel built ``--cohort both``
    # carries >1 cohort; pooling those in one fit / edge-table would blend
    # real-money and paper outcomes — the "real and paper are never blended"
    # contract forbids it. So: always report the mix, and require an EXPLICIT
    # choice on a mixed panel (``--cohort <name>`` to isolate, ``--cohort all``
    # to opt into pooling). A single-cohort panel (the C1 default) is unchanged.
    cohort_counts: Dict[str, int] = {}
    for _r in rows:
        _c = str(_r.get("cohort", "unknown"))
        cohort_counts[_c] = cohort_counts.get(_c, 0) + 1
    distinct_cohorts = sorted(cohort_counts)
    cohort_sel = (cohort or "auto").strip().lower()
    if cohort_sel not in ("auto", "all"):
        rows = [r for r in rows if str(r.get("cohort", "unknown")) == cohort_sel]

    # --- P1: --features common-core selector for the MULTIVARIATE fit. ---
    # On a pooled panel the graded features are block-sparse by strategy, so
    # listwise-complete cases across ALL graded cols collapse to zero and the
    # multivariate regression / permutation-importance / VIF path returns
    # not_computed (Study 1). Restricting the fit to a chosen dense,
    # strategy-agnostic subset (e.g. feat_confidence, feat_model_score_*,
    # feat_adx_14) recovers complete-case rows so the OOS pass can run. The
    # multivariate model is graded-only by construction (categorical/gate cols
    # are not encoded into the design matrix), so a cat_/gate_ name passed here
    # is honored in the univariate edge tables (which already cover every
    # feature) but does NOT enter the fit — recorded honestly below. The edge
    # tables + BH-FDR always run over the FULL feature set: the univariate pass
    # is sparsity-tolerant and the FDR denominator must reflect every feature
    # actually tested, not just the fitted subset.
    mv_feature_cols: List[str] = list(feature_cols)
    mv_selection: Optional[Dict[str, Any]] = None
    if feature_subset:
        requested = [str(c).strip() for c in feature_subset if str(c).strip()]
        in_panel = [c for c in requested if c in feature_cols]
        missing = [c for c in requested if c not in feature_cols]
        graded_used = [c for c in in_panel if _col_kind(c) == "graded"]
        non_graded = [c for c in in_panel if _col_kind(c) != "graded"]
        mv_feature_cols = graded_used
        mv_selection = {
            "applied": True,
            "requested": requested,
            "used": graded_used,
            "missing_from_panel": missing,
            "ignored_non_graded": non_graded,
        }

    report: Dict[str, Any] = {
        "row_count": len(rows),
        "feature_count": len(feature_cols),
        "outcome_cols": outcome_cols,
        "leakage": leakage,
        "cohort_mix": cohort_counts,
        "cohort_selection": cohort_sel,
        "multivariate_feature_selection": mv_selection,
        "coin_flip_prior": _COIN_FLIP_PRIOR,
        "numpy_available": _NUMPY_OK,
        "splitters_available": _SPLITTERS_OK,
        "params": {
            "outcome": outcome,
            "n_buckets": n_buckets,
            "min_bucket": min_bucket,
            "fdr_alpha": fdr_alpha,
            "cv_folds": cv_folds,
            "min_train_fraction": min_train_fraction,
            "label_horizon": label_horizon,
            "embargo_fraction": embargo_fraction,
            "perm_repeats": perm_repeats,
            "seed": seed,
        },
    }

    if not rows:
        report["note"] = "empty panel — nothing to analyze"
        return report
    if not leakage["clean"]:
        report["error"] = (
            f"leakage: feature/outcome columns overlap {leakage['overlap']} — "
            "refusing to regress an outcome on itself"
        )
        return report
    if cohort_sel == "auto" and len(distinct_cohorts) > 1:
        report["error"] = (
            f"panel blends {len(distinct_cohorts)} cohorts {distinct_cohorts} — "
            "refusing to silently pool real + paper. Pass --cohort <name> to "
            "isolate one, or --cohort all to pool explicitly."
        )
        return report
    if cohort_sel == "all" and len(distinct_cohorts) > 1:
        report["cohort_pooled"] = True

    tables, pvalues = conditional_edge_tables(
        rows, feature_cols, n_buckets=n_buckets, min_bucket=min_bucket, outcome=outcome
    )
    report["conditional_edge_tables"] = tables
    report["fdr"] = benjamini_hochberg(pvalues, fdr_alpha)
    report["regression"] = regression_and_importance(
        rows, mv_feature_cols, outcome=outcome, cv_folds=cv_folds,
        min_train_fraction=min_train_fraction, label_horizon=label_horizon,
        embargo_fraction=embargo_fraction, perm_repeats=perm_repeats, seed=seed,
    )
    report["collinearity"] = collinearity_map(
        rows, mv_feature_cols, top_interactions=3
    )
    return report


def format_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = ["# M30 · Research-panel analysis (C2 toolkit)", ""]
    lines.append(f"- rows: **{report['row_count']}**, features: **{report['feature_count']}**")
    lk = report.get("leakage", {})
    lines.append(
        f"- leakage contract: {'✅ clean' if lk.get('clean') else '❌ VIOLATED'}"
        f" (manifest-asserted: {lk.get('manifest_asserted')})"
    )
    lines.append(f"- numpy: {report['numpy_available']} · splitters: {report['splitters_available']}")
    lines.append("")
    lines.append(f"> {report['coin_flip_prior']}")
    lines.append("")

    if report.get("error"):
        lines.append(f"**ERROR:** {report['error']}")
        return "\n".join(lines) + "\n"
    if report.get("note"):
        lines.append(f"_{report['note']}_")
        return "\n".join(lines) + "\n"

    fdr = report.get("fdr", {})
    survivors = fdr.get("survivors", [])
    lines.append(f"## FDR survivors (BH, alpha={fdr.get('alpha')}, m={fdr.get('m')})")
    if survivors:
        for name in survivors:
            q = fdr.get("q_values", {}).get(name)
            lines.append(f"- **{name}** — q={q}")
    else:
        lines.append("- _none survive_ — no univariate feature clears FDR (expected on a small book)")
    lines.append("")

    mv_sel = report.get("multivariate_feature_selection")
    if mv_sel and mv_sel.get("applied"):
        lines.append(
            f"## Multivariate feature selection (--features)\n"
            f"- fit restricted to: **{mv_sel.get('used') or 'none (no graded match)'}**"
        )
        if mv_sel.get("missing_from_panel"):
            lines.append(f"- requested but not in panel: {mv_sel['missing_from_panel']}")
        if mv_sel.get("ignored_non_graded"):
            lines.append(
                f"- requested but non-graded (edge-tables only, not in the fit): "
                f"{mv_sel['ignored_non_graded']}"
            )
        lines.append("")

    reg = report.get("regression", {})
    lines.append(f"## Multivariate {reg.get('model', 'regression')} (outcome={reg.get('outcome', report['params']['outcome'])})")
    if reg.get("computed"):
        cv = reg.get("cv", {})
        metric_name = "oos_auc" if reg.get("model") == "logistic" else "oos_r2"
        lines.append(
            f"- OOS {metric_name.upper()}: **{cv.get(metric_name)}** "
            f"across {cv.get('folds_usable')} purged folds "
            f"(by fold: {cv.get(metric_name + '_by_fold')})"
        )
        ranked = reg.get("permutation_importance_ranked", [])
        if ranked:
            lines.append("- permutation importance (OOS metric drop, ranked):")
            for name, imp in ranked[:8]:
                lines.append(f"  - {name}: {imp}")
    else:
        lines.append(f"- not computed — {reg.get('note')}")
    lines.append("")

    col = report.get("collinearity", {})
    lines.append("## Collinearity / VIF")
    if col.get("computed"):
        hv = col.get("high_vif_features", [])
        lines.append(f"- high-VIF (>=5) features: {hv or 'none'}")
        inter = col.get("top_interactions", [])
        if inter:
            lines.append("- top interaction leads: " + ", ".join(
                f"{'×'.join(it['pair'])} ({it['abs_auc_minus_half']})" for it in inter
            ))
    else:
        lines.append(f"- not computed — {col.get('note')}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN/±Inf) with ``None`` so the
    emitted JSON is valid for a strict (non-Python) consumer — a literal
    ``NaN``/``Infinity`` token is not spec-legal JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze the M30 research panel (C2 toolkit): conditional-edge "
            "tables + multivariate regression + permutation importance + "
            "collinearity/VIF, all under purged WF-CV + BH-FDR. Read-only, "
            "observe-only, Tier-1."
        )
    )
    parser.add_argument("--panel", default="runtime_logs/research/panel.jsonl",
                        help="Panel JSONL from build_research_panel.py.")
    parser.add_argument("--outcome", default="win",
                        help="Outcome column: 'win' (logistic/OOS-AUC) or any "
                             "numeric column — 'r' or a P5 excursion outcome like "
                             "giveback_r / capture_ratio / mfe_r (linear/OOS-R², "
                             "Spearman univariate). Must be an outcome column in "
                             "the panel manifest, never a feature.")
    parser.add_argument("--cohort", default="auto",
                        help="Cohort discipline: 'auto' (refuse a mixed real+paper "
                             "panel), 'all' (explicitly pool cohorts), or a specific "
                             "cohort (e.g. real / paper) to isolate. A single-cohort "
                             "panel needs no flag.")
    parser.add_argument("--features", default=None,
                        help="Comma-separated graded feature columns to restrict the "
                             "MULTIVARIATE fit (regression + permutation importance + "
                             "VIF) to — the common-core selector that lets a pooled, "
                             "block-sparse panel produce complete-case rows. Edge "
                             "tables + BH-FDR still run over the FULL feature set. "
                             "Non-graded (cat_/gate_) names are honored in the edge "
                             "tables but excluded from the fit. Omit for all graded cols.")
    parser.add_argument("--n-buckets", type=int, default=4,
                        help="Quantile buckets for graded-feature edge tables.")
    parser.add_argument("--min-bucket", type=int, default=10,
                        help="Minimum measured rows to include a feature/level.")
    parser.add_argument("--fdr-alpha", type=float, default=0.1,
                        help="Benjamini-Hochberg FDR level.")
    parser.add_argument("--cv-folds", type=int, default=5,
                        help="Purged walk-forward folds.")
    parser.add_argument("--min-train-fraction", type=float, default=0.5,
                        help="First train block fraction for the WF-CV.")
    parser.add_argument("--label-horizon", type=int, default=1,
                        help="Purge width in rows (label window).")
    parser.add_argument("--embargo-fraction", type=float, default=0.02,
                        help="Embargo width as a fraction of rows.")
    parser.add_argument("--perm-repeats", type=int, default=5,
                        help="Permutation-importance shuffles per feature per fold.")
    parser.add_argument("--seed", type=int, default=1729,
                        help="RNG seed (deterministic permutation importance).")
    parser.add_argument("--out", default="runtime_logs/research/panel_analysis.json",
                        help="Output JSON path (a sibling .md summary is written too).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the human summary line (still writes files).")
    args = parser.parse_args(argv)

    panel_path = Path(args.panel)
    rows, manifest = load_panel(panel_path)
    feature_subset = (
        [c.strip() for c in args.features.split(",") if c.strip()]
        if args.features else None
    )
    report = analyze(
        rows, manifest,
        outcome=args.outcome, n_buckets=args.n_buckets, min_bucket=args.min_bucket,
        fdr_alpha=args.fdr_alpha, cv_folds=args.cv_folds,
        min_train_fraction=args.min_train_fraction, label_horizon=args.label_horizon,
        embargo_fraction=args.embargo_fraction, perm_repeats=args.perm_repeats,
        seed=args.seed, cohort=args.cohort, feature_subset=feature_subset,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_json_safe(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path = out_path.with_suffix(".md")
    md_path.write_text(format_markdown(report), encoding="utf-8")

    if not args.quiet:
        fdr = report.get("fdr", {})
        reg = report.get("regression", {})
        print(
            f"analysis: {report['row_count']} rows, {report['feature_count']} features "
            f"→ {out_path} (+ {md_path.name})"
        )
        if report.get("error"):
            print(f"  ERROR: {report['error']}")
        elif rows:
            print(
                f"  FDR survivors: {fdr.get('survivors') or 'none'} · "
                f"regression: {'computed' if reg.get('computed') else reg.get('note')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
