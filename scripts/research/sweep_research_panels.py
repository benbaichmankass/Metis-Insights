#!/usr/bin/env python3
"""M30 · P2 — the per-strategy / asset-class research SWEEP driver.

The technical-quant-research platform's coverage brick (scoping:
``docs/research/technical-quant-research-platform-scoping-2026-07-27.md``; the
loose-end **P2**). Studies 1–3 established the C1→C2 loop but each was
hand-run against one book at a time. This driver runs that loop **across every
live strategy in one pass** — one panel per strategy above a power floor, thin
books pooled by asset class — and rolls the honest per-group verdict
(FDR survivors × OOS discrimination) into a single skimmable summary, so
coverage is self-serve instead of a per-strategy manual crank.

**What it does:**

1. Build the full pooled panel **once** (reusing C1
   ``build_research_panel.build_panel`` — one read-only DB read for the whole
   sweep).
2. Group its rows **by strategy**. A strategy with ``>= --power-floor`` closed
   trades gets its **own** C2 analysis (its features are dense enough to matter).
3. Strategies **below** the floor are **pooled by asset class**
   (``src.web.api._asset_class.asset_class_for_symbol``) so a handful of thin
   books together clear the floor as e.g. a "commodity" pool — coverage without
   manufacturing per-book noise. A pool still under the floor is **reported as
   underpowered**, never silently dropped (the honest-null discipline).
4. Each analyzable group runs the full C2 toolkit (reusing
   ``analyze_research_panel.analyze`` — conditional-edge + BH-FDR + multivariate
   regression + permutation importance + VIF, all under purged WF-CV) and gets a
   verdict against the platform's binding bar:

   - **candidate_finding** — has BH-FDR survivor(s) **AND** positive OOS
     discrimination (win AUC > 0.5 / r R² > 0). The only class that may route to
     the C3 bridge (per-feature confirmation still required).
   - **lead** — FDR survivor(s) but OOS not positive / not computable.
   - **null** — no FDR survivor.
   - **underpowered** — below the floor, or the group's complete-case set was
     too small for the multivariate OOS pass to run.

**Observe-only, Tier-1.** Read-only: it drives the two read-only research bricks
and writes only under ``--out-dir`` (default ``runtime_logs/research/sweep/``).
Never opens a DB write / broker socket / order path. A missing / empty DB yields
an honest empty sweep, never a crash. It invents **no** new statistics — every
number comes from the same C1/C2 primitives every prior study used, so a sweep
row is directly comparable to a hand-run study.

Usage::

    python scripts/research/sweep_research_panels.py               # real cohort, both outcomes
    python scripts/research/sweep_research_panels.py --db /path/trade_journal.db --power-floor 30
    python scripts/research/sweep_research_panels.py --outcome win --cohort paper
    # Restrict each group's MULTIVARIATE fit to a dense common-core (passthrough
    # to the C2 --features selector) — helps block-sparse asset-class pools:
    python scripts/research/sweep_research_panels.py \\
        --features feat_confidence,feat_adx_14
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """Load a sibling scripts/research module by path (the dir is not a package —
    same importlib pattern the research tests use)."""
    spec = importlib.util.spec_from_file_location(name, str(_HERE / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_c1 = _load_sibling("build_research_panel")
_c2 = _load_sibling("analyze_research_panel")

# Canonical DB resolver + asset-class helper (both read-only, reporting-only).
from src.utils.paths import trade_journal_db_path  # noqa: E402

try:
    from src.web.api._asset_class import asset_class_for_symbol  # noqa: E402
except Exception:  # noqa: BLE001 — keep the driver importable on a minimal tree
    def asset_class_for_symbol(symbol: Optional[str]) -> str:  # type: ignore[misc]  # inert: symbol — import-fallback stub; "unknown" is the declared degraded value
        return "unknown"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

_FEATURE_PREFIXES = ("feat_", "cat_", "gate_")


def _slice_manifest(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Synthesize the minimal C1-shaped manifest a slice needs so C2 asserts the
    leakage contract from a manifest (not a prefix-scan fallback). The
    feature/outcome split is the same deterministic prefix contract C1 stamps —
    ``feat_``/``cat_``/``gate_`` are decision-time, ``pnl``/``win``/``r`` are the
    strictly-future outcome — so deriving it from the slice is faithful, not a
    claim beyond what C1 guarantees."""
    feature_cols = sorted(
        {
            k
            for r in rows
            for k in r
            if k.startswith(_FEATURE_PREFIXES) and k != "model_score_count"
        }
    )
    return {
        "feature_cols": feature_cols,
        "outcome_cols": ["pnl", "win", "r"],
        "manifest_synthesized_by": "sweep_research_panels",
    }


def group_rows(
    rows: Sequence[Dict[str, Any]], *, power_floor: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition panel rows into analyzable groups + underpowered leftovers.

    Returns ``(groups, underpowered)``. A ``group`` dict is
    ``{key, kind, rows, symbols, strategies}`` where ``kind`` ∈
    {``strategy``, ``asset_pool``}. Strategies at/above the floor become their
    own ``strategy`` group; the rest are pooled by asset class into
    ``asset_pool`` groups that clear the floor. Everything still short of the
    floor is returned in ``underpowered`` (reported, never dropped).
    """
    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_strategy.setdefault(str(r.get("strategy", "(unknown)")), []).append(r)

    groups: List[Dict[str, Any]] = []
    thin: List[Dict[str, Any]] = []  # rows from below-floor strategies
    for strat, srows in sorted(by_strategy.items()):
        if len(srows) >= power_floor:
            groups.append(
                {
                    "key": strat,
                    "kind": "strategy",
                    "rows": srows,
                    "symbols": sorted({str(r.get("symbol", "")) for r in srows}),
                    "strategies": [strat],
                }
            )
        else:
            thin.extend(srows)

    # Pool the thin books by asset class.
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for r in thin:
        cls = asset_class_for_symbol(r.get("symbol"))
        by_class.setdefault(cls, []).append(r)

    underpowered: List[Dict[str, Any]] = []
    for cls, crows in sorted(by_class.items()):
        meta = {
            "key": f"asset:{cls}",
            "kind": "asset_pool",
            "rows": crows,
            "symbols": sorted({str(r.get("symbol", "")) for r in crows}),
            "strategies": sorted({str(r.get("strategy", "")) for r in crows}),
        }
        if len(crows) >= power_floor:
            groups.append(meta)
        else:
            underpowered.append(
                {
                    "key": meta["key"],
                    "kind": "asset_pool",
                    "n": len(crows),
                    "symbols": meta["symbols"],
                    "strategies": meta["strategies"],
                    "reason": f"pool n={len(crows)} < power_floor {power_floor}",
                }
            )
    return groups, underpowered


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def _oos_positive(reg: Dict[str, Any]) -> Optional[bool]:
    """Is the group's OOS discrimination positive? ``None`` if not computed.

    win → OOS AUC > 0.5; r → OOS R² > 0 (the platform's criterion (b)).
    """
    if not reg or not reg.get("computed"):
        return None
    cv = reg.get("cv", {})
    if reg.get("model") == "logistic":
        auc = cv.get("oos_auc")
        return None if auc is None else bool(auc > 0.5)
    r2 = cv.get("oos_r2")
    return None if r2 is None else bool(r2 > 0.0)


def verdict_for(report: Dict[str, Any]) -> Dict[str, Any]:
    """Roll one C2 report into the platform-bar verdict for a group/outcome."""
    fdr = report.get("fdr", {}) or {}
    survivors = list(fdr.get("survivors", []))
    reg = report.get("regression", {}) or {}
    oos_pos = _oos_positive(reg)
    cv = reg.get("cv", {}) if reg.get("computed") else {}
    metric_name = "oos_auc" if reg.get("model") == "logistic" else "oos_r2"

    if report.get("error"):
        verdict = "error"
    elif not survivors:
        verdict = "null"
    elif oos_pos is True:
        verdict = "candidate_finding"
    elif oos_pos is None:
        verdict = "lead"  # FDR survivor but OOS could not be computed
    else:
        verdict = "lead"  # FDR survivor but OOS not positive

    return {
        "verdict": verdict,
        "fdr_survivors": survivors,
        "regression_computed": bool(reg.get("computed")),
        "oos_metric": metric_name if reg.get("computed") else None,
        "oos_value": cv.get(metric_name) if reg.get("computed") else None,
        "oos_positive": oos_pos,
        "oos_by_fold": cv.get(f"{metric_name}_by_fold") if reg.get("computed") else None,
        "regression_note": None if reg.get("computed") else reg.get("note"),
    }


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def run_sweep(
    *,
    db_path: str,
    cohort: str = "real",
    power_floor: int = 30,
    outcomes: Sequence[str] = ("win", "r"),
    features: Optional[Sequence[str]] = None,
    n_buckets: int = 4,
    min_bucket: int = 10,
    fdr_alpha: float = 0.1,
    cv_folds: int = 5,
    seed: int = 1729,
) -> Dict[str, Any]:
    """Build the pooled panel, group it, and analyze each group for each outcome.

    Returns the consolidated sweep report (JSON-serialisable). Tolerant: an
    unreadable/empty DB yields an empty sweep with an honest note.
    """
    rows, panel_manifest = _c1.build_panel(db_path=db_path, cohort=cohort, strategy=None)
    groups, underpowered = group_rows(rows, power_floor=power_floor)

    group_reports: List[Dict[str, Any]] = []
    for g in groups:
        grows = g["rows"]
        gman = _slice_manifest(grows)
        per_outcome: Dict[str, Any] = {}
        for outcome in outcomes:
            report = _c2.analyze(
                grows,
                gman,
                outcome=outcome,
                n_buckets=n_buckets,
                min_bucket=min_bucket,
                fdr_alpha=fdr_alpha,
                cv_folds=cv_folds,
                min_train_fraction=0.5,
                label_horizon=1,
                embargo_fraction=0.02,
                perm_repeats=5,
                seed=seed,
                cohort=cohort,
                feature_subset=features,
            )
            per_outcome[outcome] = {
                "verdict": verdict_for(report),
                "report": report,
            }
        group_reports.append(
            {
                "key": g["key"],
                "kind": g["kind"],
                "n": len(grows),
                "symbols": g["symbols"],
                "strategies": g["strategies"],
                "outcomes": per_outcome,
            }
        )

    # Roll-up counts per verdict, worst-informative first for the reader.
    verdict_counts: Dict[str, int] = {}
    for gr in group_reports:
        for outcome, ob in gr["outcomes"].items():
            v = ob["verdict"]["verdict"]
            verdict_counts[f"{outcome}:{v}"] = verdict_counts.get(f"{outcome}:{v}", 0) + 1

    return {
        "source_db": db_path,
        "cohort": cohort,
        "power_floor": power_floor,
        "outcomes": list(outcomes),
        "features_common_core": list(features) if features else None,
        "panel_row_count": len(rows),
        "panel_feature_cols": panel_manifest.get("feature_cols", []),
        "group_count": len(group_reports),
        "groups": group_reports,
        "underpowered": underpowered,
        "verdict_counts": verdict_counts,
        "coin_flip_prior": _c2._COIN_FLIP_PRIOR,
        "note": (
            "Per-group C2 under purged WF-CV + BH-FDR. A candidate_finding "
            "(FDR survivor AND positive OOS) is the only class that may route to "
            "the C3 bridge, and only after per-feature confirmation."
        ),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_survivors(v: Dict[str, Any]) -> str:
    s = v.get("fdr_survivors") or []
    return ", ".join(s) if s else "—"


def format_markdown(sweep: Dict[str, Any]) -> str:
    lines: List[str] = ["# M30 · Per-strategy research sweep (P2 driver)", ""]
    lines.append(
        f"- source: `{sweep['source_db']}` · cohort **{sweep['cohort']}** · "
        f"power-floor **{sweep['power_floor']}**"
    )
    lines.append(
        f"- panel: **{sweep['panel_row_count']}** rows · "
        f"**{sweep['group_count']}** analyzable groups · "
        f"**{len(sweep['underpowered'])}** underpowered"
    )
    if sweep.get("features_common_core"):
        lines.append(f"- multivariate fit restricted to: `{sweep['features_common_core']}`")
    lines.append("")
    lines.append(f"> {sweep['coin_flip_prior']}")
    lines.append("")

    if not sweep["groups"]:
        lines.append("_No group cleared the power floor — nothing to analyze._")
        if sweep["underpowered"]:
            lines.append("")
            lines.append("## Underpowered (below floor, reported not dropped)")
            for u in sweep["underpowered"]:
                lines.append(f"- `{u['key']}` — {u['reason']} · {u['strategies']}")
        return "\n".join(lines) + "\n"

    for outcome in sweep["outcomes"]:
        lines.append(f"## Outcome = `{outcome}`")
        lines.append("")
        lines.append("| group | kind | n | verdict | FDR survivors | OOS |")
        lines.append("|---|---|---|---|---|---|")
        for gr in sweep["groups"]:
            ob = gr["outcomes"].get(outcome, {})
            v = ob.get("verdict", {})
            oos = v.get("oos_value")
            oos_s = (
                f"{v.get('oos_metric')}={oos}"
                if v.get("regression_computed") and oos is not None
                else "not computed"
            )
            lines.append(
                f"| `{gr['key']}` | {gr['kind']} | {gr['n']} | "
                f"**{v.get('verdict','?')}** | {_fmt_survivors(v)} | {oos_s} |"
            )
        lines.append("")

    if sweep["underpowered"]:
        lines.append("## Underpowered (below floor, reported not dropped)")
        for u in sweep["underpowered"]:
            lines.append(
                f"- `{u['key']}` — {u['reason']} · strategies {u['strategies']}"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def write_sweep(sweep: Dict[str, Any], out_dir: Path) -> Tuple[Path, Path]:
    """Write the consolidated ``sweep.json`` (+ ``sweep.md``) and one full C2
    report per group/outcome under ``out_dir/groups/``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    groups_dir = out_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    # Per-group full reports (drill-down), then strip the heavy `report` blobs
    # from the consolidated summary so it stays skimmable.
    slim = json.loads(json.dumps(sweep, default=str))  # deep copy
    for gr in slim["groups"]:
        safe_key = str(gr["key"]).replace("/", "_").replace(":", "_")
        for outcome, ob in gr["outcomes"].items():
            report = ob.pop("report", None)
            if report is not None:
                p = groups_dir / f"{safe_key}__{outcome}.json"
                p.write_text(
                    json.dumps(_c2._json_safe(report), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

    sweep_json = out_dir / "sweep.json"
    sweep_json.write_text(
        json.dumps(_c2._json_safe(slim), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sweep_md = out_dir / "sweep.md"
    sweep_md.write_text(format_markdown(slim), encoding="utf-8")
    return sweep_json, sweep_md


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M30 per-strategy research sweep driver: run C1→C2 across every "
            "strategy above a power floor, pooling thin books by asset class, "
            "and roll up the FDR×OOS verdict per group. Read-only, Tier-1."
        )
    )
    parser.add_argument("--db", default=None,
                        help="trade_journal.db path (default: canonical resolver).")
    parser.add_argument("--cohort", choices=["real", "paper"], default="real",
                        help="Funding cohort to sweep (real or paper; never blended).")
    parser.add_argument("--power-floor", type=int, default=30,
                        help="Min closed trades for a strategy to get its own "
                             "group; thinner books pool by asset class.")
    parser.add_argument("--outcome", choices=["win", "r", "both"], default="both",
                        help="Which outcome(s) to analyze per group.")
    parser.add_argument("--features", default=None,
                        help="Comma-separated graded common-core cols passed "
                             "through to the C2 --features selector for every "
                             "group's multivariate fit (helps sparse pools).")
    parser.add_argument("--n-buckets", type=int, default=4)
    parser.add_argument("--min-bucket", type=int, default=10)
    parser.add_argument("--fdr-alpha", type=float, default=0.1)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--out-dir", default="runtime_logs/research/sweep",
                        help="Output dir (sweep.json + sweep.md + groups/).")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the human summary line (still writes files).")
    args = parser.parse_args(argv)

    db_path = args.db or str(trade_journal_db_path())
    outcomes = ["win", "r"] if args.outcome == "both" else [args.outcome]
    features = (
        [c.strip() for c in args.features.split(",") if c.strip()]
        if args.features else None
    )
    sweep = run_sweep(
        db_path=db_path,
        cohort=args.cohort,
        power_floor=args.power_floor,
        outcomes=outcomes,
        features=features,
        n_buckets=args.n_buckets,
        min_bucket=args.min_bucket,
        fdr_alpha=args.fdr_alpha,
        cv_folds=args.cv_folds,
        seed=args.seed,
    )
    sweep_json, sweep_md = write_sweep(sweep, Path(args.out_dir))
    if not args.quiet:
        print(
            f"sweep: {sweep['panel_row_count']} panel rows → "
            f"{sweep['group_count']} groups "
            f"({len(sweep['underpowered'])} underpowered) → {sweep_json} (+ {sweep_md.name})"
        )
        if sweep["group_count"]:
            print(f"  verdicts: {sweep['verdict_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
