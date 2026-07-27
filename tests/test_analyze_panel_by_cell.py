"""M30 · C2b — tests for scripts/research/analyze_panel_by_cell.py.

Offline + deterministic: a hand-built in-memory panel (dict rows + a manifest
stamping the leakage split) is partitioned by a cat_* cell and pushed through the
real C2 ``analyze`` per cell. No harness run, no network, no DB. Loaded via
importlib (scripts/research is not a package).
"""

from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(
    os.path.dirname(_HERE), "scripts", "research", "analyze_panel_by_cell.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("analyze_panel_by_cell", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


pc = _load()


def _panel(n_per_cell: int = 30):
    """Two regime cells; in 'chop' feat_x is perfectly informative of win, in
    'trend' it is random-ish — so a per-cell pass separates them where the pooled
    view blends. closed_at strictly increasing for the WF-CV time axis."""
    rows = []
    t = 0
    for cell, informative in (("chop", True), ("trend", False)):
        for i in range(n_per_cell):
            t += 1
            x = float(i) / n_per_cell
            win = 1 if (informative and x > 0.5) else (i % 2)
            rows.append(
                {
                    "strategy": "s", "symbol": "BTCUSDT", "cohort": "backtest",
                    "closed_at": f"2024-01-01T00:{t:02d}:00Z",
                    "cat_regime": cell,
                    "feat_x": x,
                    "pnl": 1.0 if win else -1.0, "win": win, "r": 1.0 if win else -1.0,
                }
            )
    manifest = {
        "feature_cols": ["feat_x", "cat_regime"],
        "outcome_cols": ["pnl", "win", "r"],
    }
    return rows, manifest


def test_partitions_by_cell_and_reports_each():
    rows, manifest = _panel()
    res = pc.analyze_by_cell(
        rows, manifest, cell_col="cat_regime", outcome="win",
        n_buckets=4, min_bucket=5, min_cell=10, fdr_alpha=0.1, cv_folds=3,
        cohort="auto",
    )
    assert res["cell_col"] == "cat_regime"
    assert set(res["by_cell"]) == {"chop", "trend"}
    assert res["cells_tested"] == 2
    # overall is a single pooled pass
    assert res["overall"]["n"] == len(rows)
    # each cell carries its own n
    assert res["by_cell"]["chop"]["n"] == 30
    assert res["by_cell"]["trend"]["n"] == 30


def test_small_cell_is_skipped_not_dropped():
    rows, manifest = _panel()
    # add a tiny third cell (5 rows) — below min_cell → reported skipped
    for i in range(5):
        rows.append(
            {
                "strategy": "s", "symbol": "BTCUSDT", "cohort": "backtest",
                "closed_at": f"2024-02-01T00:{i:02d}:00Z", "cat_regime": "rare",
                "feat_x": 0.1, "pnl": -1.0, "win": 0, "r": -1.0,
            }
        )
    res = pc.analyze_by_cell(
        rows, manifest, cell_col="cat_regime", outcome="win",
        n_buckets=4, min_bucket=5, min_cell=10, fdr_alpha=0.1, cv_folds=3,
        cohort="auto",
    )
    assert res["by_cell"]["rare"].get("skipped")
    assert res["by_cell"]["rare"]["n"] == 5
    assert "rare" not in {c for c in res["by_cell"] if not res["by_cell"][c].get("skipped")}


def test_missing_cell_value_bucketed_not_crashed():
    rows, manifest = _panel(n_per_cell=15)
    for r in rows[:12]:
        r.pop("cat_regime", None)  # some rows have no cell value
    res = pc.analyze_by_cell(
        rows, manifest, cell_col="cat_regime", outcome="win",
        n_buckets=4, min_bucket=5, min_cell=5, fdr_alpha=0.1, cv_folds=3,
        cohort="auto",
    )
    assert "∅missing" in res["cell_counts"]


def test_strip_full_removes_heavy_reports():
    rows, manifest = _panel()
    res = pc.analyze_by_cell(
        rows, manifest, cell_col="cat_regime", outcome="win",
        n_buckets=4, min_bucket=5, min_cell=10, fdr_alpha=0.1, cv_folds=3,
        cohort="auto",
    )
    compact = pc._strip_full(res)
    assert "full" not in compact["overall"]
    for s in compact["by_cell"].values():
        assert "full" not in s
    # the full result still carries the nested reports
    assert "full" in res["overall"]


def test_markdown_renders():
    rows, manifest = _panel()
    res = pc.analyze_by_cell(
        rows, manifest, cell_col="cat_regime", outcome="win",
        n_buckets=4, min_bucket=5, min_cell=10, fdr_alpha=0.1, cv_folds=3,
        cohort="auto",
    )
    md = pc.format_markdown(res)
    assert "Per-cell conditional discovery" in md
    assert "chop" in md and "trend" in md
