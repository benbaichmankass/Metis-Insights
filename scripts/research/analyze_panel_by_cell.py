#!/usr/bin/env python3
"""M30 · C2b — per-regime-CELL conditional discovery over a research panel.

**Why this exists.** The C2 analyzer (``analyze_research_panel.py``) runs its
edge-tables / multivariate-OOS / FDR over the WHOLE panel — it answers "does a
feature discriminate the outcome *overall*?". But the load-bearing M18/M30 prior
is that **entries are a coin-flip overall while the edge lives in the regime**:
a strategy that is ~50/50 across the full history can still be materially
directional *within* a ``chop`` bar or a ``volatile`` bar. This driver tests
exactly that — it partitions the panel by a decision-time **cell** column
(``cat_regime`` / ``cat_vol_regime``, or any ``cat_*`` the panel carries) and
runs the SAME C2 ``analyze`` per cell, so a per-cell OOS-AUC materially above the
overall (still-CV'd, still-FDR'd) is the honest signal that the coin-flip is a
*mixture* of separable regimes, not a genuine no-edge.

**What it reports.** For the overall panel and for each cell with enough rows:
``n``, base win-rate, the C2 FDR survivors, and the multivariate **OOS metric**
(logistic OOS-AUC for ``win``; ridge OOS-R² for a continuous outcome) under the
canonical purged/embargoed walk-forward CV. The verdict per cell is read exactly
as the base C2: an OOS-AUC materially > 0.5 (or an FDR survivor) *within* a cell
is the discovery; a per-cell wall of ~0.5 confirms the coin-flip is regime-blind
too. **Every guard the base C2 applies is inherited unchanged** — leakage split
asserted from the manifest, cohort discipline, the coin-flip prior stamped, the
numpy/splitter honest ``not_computed`` envelope — because this driver only
*slices the rows* and calls ``analyze`` on each slice.

**Cross-cell honesty.** Slicing multiplies the number of tests (one C2 pass per
cell), so a per-cell "win" is only credible when the cell is (a) pre-specified —
a real regime axis the strategy already stamps, not a fished partition — and (b)
survives its OWN in-cell purged-CV + FDR, NOT merely a higher point-AUC than the
pooled fit. The report stamps the cell count as the implicit multiple-comparison
denominator so a reader discounts a lone marginal cell.

**Observe-only, Tier-1.** Reads the JSONL panel a C1 builder wrote; never opens a
DB / broker socket, never writes the money DB, never touches the order path. It
imports the C2 analyzer as a library (no re-implementation of the stats).

Usage::

    # is the ict_scalp entry a coin-flip WITHIN each trend-regime cell?
    python scripts/research/analyze_panel_by_cell.py \\
        --panel runtime_logs/research/bt_ict_scalp_panel.jsonl \\
        --cell cat_regime --outcome win
    # condition on the vol-regime axis instead, continuous exit outcome:
    python scripts/research/analyze_panel_by_cell.py \\
        --panel runtime_logs/research/bt_exit.jsonl \\
        --cell cat_vol_regime --outcome giveback_r --min-cell 40
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_c2():
    """Load ``analyze_research_panel`` as a module (scripts/research is not a
    package, so import by file path — the same pattern the tests use)."""
    spec = importlib.util.spec_from_file_location(
        "analyze_research_panel", str(_HERE / "analyze_research_panel.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_C2 = _load_c2()


def _cell_value(row: Dict[str, Any], cell_col: str) -> str:
    v = row.get(cell_col)
    return "∅missing" if v is None else str(v)


def _one_pass(
    rows: Sequence[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    outcome: str,
    n_buckets: int,
    min_bucket: int,
    fdr_alpha: float,
    cv_folds: int,
    cohort: str,
) -> Dict[str, Any]:
    """Run the full C2 ``analyze`` on ``rows`` and distil the headline verdict.

    Returns the compact per-cell summary PLUS the full report under ``full`` so a
    consumer can drill in without a second run.
    """
    report = _C2.analyze(
        rows,
        manifest,
        outcome=outcome,
        n_buckets=n_buckets,
        min_bucket=min_bucket,
        fdr_alpha=fdr_alpha,
        cv_folds=cv_folds,
        min_train_fraction=0.5,
        label_horizon=1,
        embargo_fraction=0.02,
        perm_repeats=5,
        seed=1729,
        cohort=cohort,
    )
    reg = report.get("regression", {})
    fdr = report.get("fdr", {})
    is_logistic = reg.get("model") == "logistic"
    metric_name = "oos_auc" if is_logistic else "oos_r2"
    cv = reg.get("cv", {}) if reg.get("computed") else {}
    summary = {
        "n": report.get("row_count"),
        "error": report.get("error"),
        "fdr_survivors": fdr.get("survivors", []),
        "fdr_m": fdr.get("m"),
        "regression_computed": bool(reg.get("computed")),
        "oos_metric_name": metric_name if reg.get("computed") else None,
        "oos_metric": cv.get(metric_name) if reg.get("computed") else None,
        "folds_usable": cv.get("folds_usable") if reg.get("computed") else None,
        "regression_note": None if reg.get("computed") else reg.get("note"),
        "top_importance": reg.get("permutation_importance_ranked", [])[:3],
        "full": report,
    }
    return summary


def analyze_by_cell(
    rows: Sequence[Dict[str, Any]],
    manifest: Optional[Dict[str, Any]],
    *,
    cell_col: str,
    outcome: str,
    n_buckets: int,
    min_bucket: int,
    min_cell: int,
    fdr_alpha: float,
    cv_folds: int,
    cohort: str,
) -> Dict[str, Any]:
    """Partition ``rows`` by ``cell_col`` and run C2 per cell + on the pooled set.

    A cell with fewer than ``min_cell`` rows is reported as ``skipped`` (its
    in-cell CV would be degenerate) rather than dropped silently.
    """
    counts: Dict[str, int] = {}
    for r in rows:
        counts[_cell_value(r, cell_col)] = counts.get(_cell_value(r, cell_col), 0) + 1

    overall = _one_pass(
        rows, manifest, outcome=outcome, n_buckets=n_buckets, min_bucket=min_bucket,
        fdr_alpha=fdr_alpha, cv_folds=cv_folds, cohort=cohort,
    )

    cells: Dict[str, Any] = {}
    tested = 0
    for cell_val in sorted(counts):
        cell_rows = [r for r in rows if _cell_value(r, cell_col) == cell_val]
        if len(cell_rows) < min_cell:
            cells[cell_val] = {"n": len(cell_rows), "skipped": "too few rows (< min_cell)"}
            continue
        tested += 1
        cells[cell_val] = _one_pass(
            cell_rows, manifest, outcome=outcome, n_buckets=n_buckets,
            min_bucket=min_bucket, fdr_alpha=fdr_alpha, cv_folds=cv_folds, cohort=cohort,
        )

    return {
        "cell_col": cell_col,
        "outcome": outcome,
        "cell_counts": counts,
        "cells_tested": tested,
        "multiple_comparison_note": (
            f"{tested} cells tested — treat any single cell's OOS lift as ONE of "
            f"{tested} implicit comparisons; a per-cell win is credible only if the "
            "cell is a pre-specified regime axis AND clears its own in-cell CV+FDR, "
            "not merely a higher point-metric than the pooled fit."
        ),
        "coin_flip_prior": _C2._COIN_FLIP_PRIOR,
        "overall": overall,
        "by_cell": cells,
    }


def format_markdown(result: Dict[str, Any]) -> str:
    L: List[str] = [
        f"# M30 · Per-cell conditional discovery — {result['cell_col']} "
        f"(outcome={result['outcome']})",
        "",
        f"> {result['coin_flip_prior']}",
        "",
        f"- cells tested: **{result['cells_tested']}** · "
        f"cell mix: {result['cell_counts']}",
        f"- {result['multiple_comparison_note']}",
        "",
        "## Overall (pooled baseline)",
    ]

    def _line(tag: str, s: Dict[str, Any]) -> str:
        if s.get("skipped"):
            return f"- **{tag}** — n={s.get('n')} · _skipped: {s['skipped']}_"
        if s.get("error"):
            return f"- **{tag}** — n={s.get('n')} · ❌ {s['error']}"
        metric = (
            f"{s['oos_metric_name'].upper()}={s['oos_metric']}"
            if s.get("regression_computed") else f"regression: {s.get('regression_note')}"
        )
        surv = s.get("fdr_survivors") or "none"
        return f"- **{tag}** — n={s.get('n')} · FDR survivors: {surv} · {metric}"

    L.append(_line("overall", result["overall"]))
    L.append("")
    L.append("## By cell")
    for cell_val, s in result["by_cell"].items():
        L.append(_line(cell_val, s))
    L.append("")
    return "\n".join(L) + "\n"


def _strip_full(result: Dict[str, Any]) -> Dict[str, Any]:
    """A copy without the heavy nested ``full`` C2 reports — for the compact
    stdout / a lean summary artifact."""
    out = {k: v for k, v in result.items() if k not in ("overall", "by_cell")}
    out["overall"] = {k: v for k, v in result["overall"].items() if k != "full"}
    out["by_cell"] = {
        cv: {k: v for k, v in s.items() if k != "full"}
        for cv, s in result["by_cell"].items()
    }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Per-regime-CELL conditional discovery: partition a C1/C2 research "
            "panel by a decision-time cat_* cell (cat_regime / cat_vol_regime) "
            "and run the C2 analyzer per cell, to test whether an overall "
            "coin-flip entry is directional WITHIN a regime. Read-only, Tier-1."
        )
    )
    parser.add_argument("--panel", default="runtime_logs/research/panel.jsonl",
                        help="Panel JSONL (+ sibling .manifest.json) from a C1 builder.")
    parser.add_argument("--cell", default="cat_regime",
                        help="Decision-time cell column to partition on "
                             "(cat_regime / cat_vol_regime / any cat_* in the panel).")
    parser.add_argument("--outcome", default="win",
                        help="Outcome column (win / r / a continuous excursion "
                             "outcome like giveback_r) — same semantics as the C2 analyzer.")
    parser.add_argument("--min-cell", type=int, default=40,
                        help="Minimum rows for a cell to be tested (else skipped — "
                             "an in-cell purged CV needs enough rows to be non-degenerate).")
    parser.add_argument("--n-buckets", type=int, default=4)
    parser.add_argument("--min-bucket", type=int, default=10)
    parser.add_argument("--fdr-alpha", type=float, default=0.1)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--cohort", default="auto",
                        help="Cohort discipline forwarded to the C2 analyzer.")
    parser.add_argument("--out", default="runtime_logs/research/panel_by_cell.json",
                        help="Output JSON (compact, no nested full reports); a "
                             "sibling .md summary + a .full.json (with the per-cell "
                             "C2 reports) are written too.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    panel_path = Path(args.panel)
    rows, manifest = _C2.load_panel(panel_path)
    result = analyze_by_cell(
        rows, manifest, cell_col=args.cell, outcome=args.outcome,
        n_buckets=args.n_buckets, min_bucket=args.min_bucket, min_cell=args.min_cell,
        fdr_alpha=args.fdr_alpha, cv_folds=args.cv_folds, cohort=args.cohort,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    compact = _strip_full(result)
    out_path.write_text(
        json.dumps(_C2._json_safe(compact), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    full_path = out_path.with_suffix(".full.json")
    full_path.write_text(
        json.dumps(_C2._json_safe(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path = out_path.with_suffix(".md")
    md_path.write_text(format_markdown(result), encoding="utf-8")

    if not args.quiet:
        print(
            f"per-cell [{args.cell}, outcome={args.outcome}]: "
            f"{len(rows)} rows, {result['cells_tested']} cells tested → {out_path} "
            f"(+ {md_path.name}, {full_path.name})"
        )
        ov = result["overall"]
        print(
            f"  overall: n={ov.get('n')} OOS={ov.get('oos_metric')} "
            f"survivors={ov.get('fdr_survivors') or 'none'}"
        )
        for cv, s in result["by_cell"].items():
            if s.get("skipped"):
                print(f"  {cv}: n={s['n']} (skipped)")
            else:
                print(
                    f"  {cv}: n={s.get('n')} OOS={s.get('oos_metric')} "
                    f"survivors={s.get('fdr_survivors') or 'none'}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
