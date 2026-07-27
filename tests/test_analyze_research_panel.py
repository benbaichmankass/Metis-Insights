"""M30 · C2 — tests for scripts/research/analyze_research_panel.py.

Synthetic in-memory panel (no live data, no network) with ONE genuinely
predictive graded feature (`feat_signal`) plus noise features / a random gate /
a random categorical. Verifies:

  - panel load + manifest split + leakage assertion (and the refusal path),
  - the pure-python edge tables + univariate AUC/p + Benjamini-Hochberg FDR
    (the real signal survives; noise does not),
  - the numpy-gated regression + permutation importance under purged WF-CV
    (OOS AUC > 0.5, `feat_signal` is the top permutation-importance feature),
  - the collinearity/VIF map (a duplicated feature is flagged high-VIF),
  - the honest not_computed / empty / too-few-rows envelopes.

numpy-gated tests skip cleanly when numpy / the splitter is unavailable, matching
the tool's own graceful degradation (same pattern as test_component_edge_report).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(
    os.path.dirname(_HERE), "scripts", "research", "analyze_research_panel.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("analyze_research_panel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tk = _load()


# ---------------------------------------------------------------------------
# Deterministic synthetic panel (pure python — no numpy needed to BUILD it)
# ---------------------------------------------------------------------------


def _lcg(seed):
    state = seed & 0x7FFFFFFF

    def nxt():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return nxt


def _make_rows(n=200, seed=12345, dup=False):
    """n rows; `feat_signal` drives P(win); the rest are noise.

    If ``dup`` is set, add ``feat_dup`` ≈ ``feat_signal`` (a near-collinear
    feature) so the VIF map has something to flag.
    """
    rng = _lcg(seed)
    rows = []
    for i in range(n):
        sig = rng()
        p_win = 0.1 + 0.8 * sig  # strong monotone signal
        win = 1 if rng() < p_win else 0
        noise1 = rng()
        noise2 = rng()
        r = (1.5 if win else -1.0) + (rng() - 0.5) * 0.3
        row = {
            "strategy": "synthetic",
            "symbol": "BTCUSDT",
            "cohort": "real",
            # strictly increasing, equal-length numeric string → correct sort
            "closed_at": str(1_800_000_000 + i * 3600),
            "pnl": round(r * 100.0, 4),
            "win": win,
            "r": round(r, 4),
            "feat_signal": round(sig, 6),
            "feat_noise1": round(noise1, 6),
            "feat_noise2": round(noise2, 6),
            "gate_g": bool(rng() < 0.5),
            "cat_regime": ["bull", "bear", "chop"][int(rng() * 3) % 3],
        }
        if dup:
            row["feat_dup"] = round(sig + (rng() - 0.5) * 0.001, 6)
        rows.append(row)
    return rows


def _feature_cols(dup=False):
    cols = ["cat_regime", "feat_noise1", "feat_noise2", "feat_signal", "gate_g"]
    if dup:
        cols.append("feat_dup")
    return sorted(cols)


def _manifest(dup=False):
    return {
        "feature_cols": _feature_cols(dup),
        "outcome_cols": ["pnl", "win", "r"],
        "key_cols": ["strategy", "symbol", "cohort", "closed_at"],
    }


def _write_panel(tmp_path, rows, manifest, name="panel.jsonl"):
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    if manifest is not None:
        (tmp_path / (name + ".manifest.json")).write_text(json.dumps(manifest))
    return p


# ---------------------------------------------------------------------------
# Load / columns / leakage
# ---------------------------------------------------------------------------


def test_load_panel_and_manifest(tmp_path):
    rows = _make_rows(40)
    p = _write_panel(tmp_path, rows, _manifest())
    loaded, manifest = tk.load_panel(p)
    assert len(loaded) == 40
    assert manifest is not None
    feat, out, leak = tk.resolve_columns(loaded, manifest)
    assert "feat_signal" in feat and "win" in out
    assert leak["clean"] and leak["manifest_asserted"]


def test_missing_panel_is_tolerant(tmp_path):
    loaded, manifest = tk.load_panel(tmp_path / "nope.jsonl")
    assert loaded == [] and manifest is None


def test_resolve_columns_prefix_fallback(tmp_path):
    rows = _make_rows(20)
    p = _write_panel(tmp_path, rows, None)  # no manifest
    loaded, manifest = tk.load_panel(p)
    assert manifest is None
    feat, out, leak = tk.resolve_columns(loaded, manifest)
    assert set(feat) == set(_feature_cols())
    assert not leak["manifest_asserted"] and leak["clean"]


def test_leakage_violation_refuses(tmp_path):
    rows = _make_rows(40)
    bad_manifest = {"feature_cols": ["feat_signal", "win"], "outcome_cols": ["win", "pnl", "r"]}
    report = tk.analyze(
        rows, bad_manifest, outcome="win", n_buckets=4, min_bucket=5,
        fdr_alpha=0.1, cv_folds=5, min_train_fraction=0.5, label_horizon=1,
        embargo_fraction=0.02, perm_repeats=3, seed=1,
    )
    assert "error" in report and "win" in report["error"]
    assert "regression" not in report  # refused before any modelling


# ---------------------------------------------------------------------------
# Pure-python statistics
# ---------------------------------------------------------------------------


def test_auc_win_bounds():
    # perfect separator
    assert tk.auc_win([0, 1, 2, 3], [0, 0, 1, 1]) == 1.0
    # perfectly reversed
    assert tk.auc_win([0, 1, 2, 3], [1, 1, 0, 0]) == 0.0
    # single class → None
    assert tk.auc_win([0, 1, 2], [1, 1, 1]) is None


def test_two_proportion_p():
    assert tk._two_proportion_p(50, 100, 50, 100) > 0.5  # identical → not significant
    assert tk._two_proportion_p(90, 100, 10, 100) < 0.01  # very different → significant


def test_benjamini_hochberg_monotone_and_survivors():
    pvals = [("a", 0.001), ("b", 0.2), ("c", 0.9), ("d", 0.04)]
    res = tk.benjamini_hochberg(pvals, 0.1)
    q = res["q_values"]
    # q-values monotone in p-rank
    ordered = sorted(pvals, key=lambda t: t[1])
    qs = [q[name] for name, _ in ordered]
    assert all(qs[i] <= qs[i + 1] + 1e-9 for i in range(len(qs) - 1))
    assert "a" in res["survivors"]  # tiny p always survives
    assert "c" not in res["survivors"]  # p=0.9 never survives


def test_benjamini_hochberg_empty():
    res = tk.benjamini_hochberg([], 0.1)
    assert res["m"] == 0 and res["survivors"] == []


def test_conditional_edge_tables_signal_vs_noise():
    rows = _make_rows(200)
    tables, pvals = tk.conditional_edge_tables(
        rows, _feature_cols(), n_buckets=4, min_bucket=10
    )
    pmap = dict(pvals)
    # one p-value per feature that produced a table
    assert set(pmap) <= set(_feature_cols())
    # the real signal discriminates far better than the noise features
    assert pmap["feat_signal"] < 0.01
    assert pmap["feat_noise1"] > pmap["feat_signal"]
    sig_table = next(t for t in tables if t["feature"] == "feat_signal")
    assert sig_table["auc"] > 0.6
    # win-rate rises across the signal's quantile buckets (monotone-ish)
    wrs = [b["win_rate"] for b in sig_table["buckets"] if b["win_rate"] is not None]
    assert wrs[-1] > wrs[0]


def test_fdr_signal_survives_noise_does_not():
    rows = _make_rows(200)
    _, pvals = tk.conditional_edge_tables(rows, _feature_cols(), n_buckets=4, min_bucket=10)
    res = tk.benjamini_hochberg(pvals, 0.1)
    assert "feat_signal" in res["survivors"]
    assert res["q_values"]["feat_signal"] < res["q_values"]["feat_noise1"]


# ---------------------------------------------------------------------------
# numpy-gated: regression + permutation importance + VIF
# ---------------------------------------------------------------------------


def test_regression_recovers_signal():
    pytest.importorskip("numpy")
    if not tk._SPLITTERS_OK:
        pytest.skip("ml.experiments.splitters unavailable")
    rows = _make_rows(240)
    reg = tk.regression_and_importance(
        rows, _feature_cols(), outcome="win", cv_folds=5, min_train_fraction=0.5,
        label_horizon=1, embargo_fraction=0.02, perm_repeats=5, seed=1729,
    )
    assert reg["computed"], reg.get("note")
    assert reg["cv"]["folds_usable"] >= 2
    assert reg["cv"]["oos_auc"] > 0.5  # genuine OOS discrimination
    ranked = reg["permutation_importance_ranked"]
    assert ranked, "expected ranked permutation importances"
    assert ranked[0][0] == "feat_signal"  # the real driver tops importance


def test_regression_r_outcome_linear():
    pytest.importorskip("numpy")
    if not tk._SPLITTERS_OK:
        pytest.skip("splitters unavailable")
    rows = _make_rows(240)
    reg = tk.regression_and_importance(
        rows, _feature_cols(), outcome="r", cv_folds=5, min_train_fraction=0.5,
        label_horizon=1, embargo_fraction=0.02, perm_repeats=3, seed=7,
    )
    assert reg["computed"], reg.get("note")
    assert reg["model"] == "ridge_ols"
    assert "oos_r2" in reg["cv"]


def test_regression_drops_null_time_rows():
    pytest.importorskip("numpy")
    if not tk._SPLITTERS_OK:
        pytest.skip("splitters unavailable")
    rows = _make_rows(240)
    # A real book carries some rows with no resolvable close time; they cannot
    # be chronologically ordered for the purged CV and must be dropped, not
    # crash it (the end-to-end smoke that caught this).
    for r in rows[:30]:
        r["closed_at"] = None
    reg = tk.regression_and_importance(
        rows, _feature_cols(), outcome="win", cv_folds=5, min_train_fraction=0.5,
        label_horizon=1, embargo_fraction=0.02, perm_repeats=3, seed=1729,
    )
    assert reg["computed"], reg.get("note")
    assert reg["cv"]["dropped_null_time"] == 30
    assert reg["cv"]["cv_rows"] == 210


def test_regression_all_null_time_is_honest():
    pytest.importorskip("numpy")
    if not tk._SPLITTERS_OK:
        pytest.skip("splitters unavailable")
    rows = _make_rows(240)
    for r in rows:
        r["closed_at"] = None
    reg = tk.regression_and_importance(
        rows, _feature_cols(), outcome="win", cv_folds=5, min_train_fraction=0.5,
        label_horizon=1, embargo_fraction=0.02, perm_repeats=3, seed=1,
    )
    assert reg["computed"] is False and "null-time" in reg["note"]


def test_regression_too_few_rows_is_honest():
    pytest.importorskip("numpy")
    rows = _make_rows(8)
    reg = tk.regression_and_importance(
        rows, _feature_cols(), outcome="win", cv_folds=5, min_train_fraction=0.5,
        label_horizon=1, embargo_fraction=0.0, perm_repeats=3, seed=1,
    )
    assert reg["computed"] is False and "too few" in reg["note"]


def test_collinearity_flags_duplicate():
    pytest.importorskip("numpy")
    rows = _make_rows(200, dup=True)
    col = tk.collinearity_map(rows, _feature_cols(dup=True), top_interactions=3)
    assert col["computed"], col.get("note")
    # feat_dup ≈ feat_signal → at least one of the pair flagged high-VIF
    assert "feat_signal" in col["high_vif_features"] or "feat_dup" in col["high_vif_features"]


# ---------------------------------------------------------------------------
# analyze() + CLI end-to-end
# ---------------------------------------------------------------------------


def test_analyze_empty_panel():
    report = tk.analyze(
        [], None, outcome="win", n_buckets=4, min_bucket=10, fdr_alpha=0.1,
        cv_folds=5, min_train_fraction=0.5, label_horizon=1, embargo_fraction=0.02,
        perm_repeats=3, seed=1,
    )
    assert report["row_count"] == 0 and "note" in report
    # coin-flip prior is always stamped
    assert "coin_flip_prior" in report and report["coin_flip_prior"]


def test_analyze_full_report_shape():
    rows = _make_rows(200)
    report = tk.analyze(
        rows, _manifest(), outcome="win", n_buckets=4, min_bucket=10, fdr_alpha=0.1,
        cv_folds=5, min_train_fraction=0.5, label_horizon=1, embargo_fraction=0.02,
        perm_repeats=3, seed=1,
    )
    assert report["leakage"]["clean"]
    assert "conditional_edge_tables" in report
    assert "fdr" in report and "regression" in report and "collinearity" in report
    # markdown renders without raising
    md = tk.format_markdown(report)
    assert "coin-flip prior" in md.lower() or "coin_flip" in md.lower()


def test_cli_end_to_end(tmp_path):
    rows = _make_rows(200)
    p = _write_panel(tmp_path, rows, _manifest())
    out = tmp_path / "analysis.json"
    rc = tk.main([
        "--panel", str(p), "--out", str(out), "--quiet",
        "--n-buckets", "4", "--fdr-alpha", "0.1", "--cv-folds", "5",
    ])
    assert rc == 0
    assert out.exists() and out.with_suffix(".md").exists()
    report = json.loads(out.read_text())
    assert report["row_count"] == 200
    assert report["feature_count"] == len(_feature_cols())
