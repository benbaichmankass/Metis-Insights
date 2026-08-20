"""M30 · C1 — tests for scripts/research/build_research_panel.py.

Synthetic fixture DB (no live data, no network). Verifies the panel join, the
feature/categorical/gate/model-score columns, the R-multiple, the cohort split
+ filter, the leakage-stamped manifest, and the missing-DB tolerance. Loaded via
importlib because scripts/research is not a package (same pattern as
test_component_edge_report.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(
    os.path.dirname(_HERE), "scripts", "research", "build_research_panel.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("build_research_panel", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the module defines @dataclass fields whose type
    # resolution reads sys.modules[cls.__module__] (fails with None otherwise).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


panel = _load()


def _make_db(path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT, status TEXT,
          pnl REAL, is_backtest INT, account_class TEXT, is_demo INT,
          entry_price REAL, stop_loss REAL, position_size REAL,
          closed_at TEXT, timestamp TEXT, setup_type TEXT, reconcile_status TEXT);
        CREATE TABLE order_packages (
          order_package_id TEXT PRIMARY KEY, linked_trade_id INT, signal_logic TEXT,
          confidence REAL, model_scores TEXT, updated_at TEXT);
        """
    )
    # 1: real winner (ict_scalp) with a full component set + model scores
    c.execute(
        "INSERT INTO trades VALUES "
        "(1,'ict_scalp_5m','BTCUSDT','closed',50.0,0,'real_money',0,80000,79800,0.01,"
        "'2026-07-25T00:00:00Z','2026-07-25T00:00:00Z',NULL,NULL)"
    )
    c.execute(
        "INSERT INTO order_packages VALUES (10,1,?,0.8,?, '2026-07-25T00:00:00Z')",
        (
            json.dumps(
                {
                    "sweep_extreme": 79700,
                    "sweep_level": 79750,
                    "atr": 100,
                    "displacement_body_to_range": 0.7,
                    "regime": "trend",
                    "vol_regime": "calm",
                    "htf_filter_active": True,
                }
            ),
            json.dumps(
                {"m1": {"stage": "advisory", "score": 0.62},
                 "m2": {"stage": "shadow", "score": 0.44}}
            ),
        ),
    )
    # 2: real loser (vwap), no model scores
    c.execute(
        "INSERT INTO trades VALUES "
        "(2,'vwap','BTCUSDT','closed',-20.0,0,'real_money',0,80000,80200,0.01,"
        "'2026-07-25T01:00:00Z','2026-07-25T01:00:00Z',NULL,NULL)"
    )
    c.execute(
        "INSERT INTO order_packages VALUES (11,2,?,0.5,NULL,'2026-07-25T01:00:00Z')",
        (json.dumps({"deviation_std": 2.1, "regime": "range", "vol_regime": "volatile"}),),
    )
    # 3: paper trade (htf_pullback) — should be EXCLUDED from the real cohort
    c.execute(
        "INSERT INTO trades VALUES "
        "(3,'htf_pullback_trend_2h','MES','closed',10.0,0,'paper',1,5000,4990,1,"
        "'2026-07-25T02:00:00Z','2026-07-25T02:00:00Z',NULL,NULL)"
    )
    c.execute(
        "INSERT INTO order_packages VALUES (12,3,?,0.6,NULL,'2026-07-25T02:00:00Z')",
        (json.dumps({"pullback_pos_in_range": 0.3, "atr": 10, "regime": "trend"}),),
    )
    # 4: a backtest row (must be excluded) + 5: an open row (must be excluded)
    c.execute(
        "INSERT INTO trades VALUES "
        "(4,'ict_scalp_5m','BTCUSDT','closed',999.0,1,'real_money',0,1,1,1,"
        "'2026-07-25T03:00:00Z','2026-07-25T03:00:00Z',NULL,NULL)"
    )
    c.execute(
        "INSERT INTO trades VALUES "
        "(5,'ict_scalp_5m','BTCUSDT','open',NULL,0,'real_money',0,1,1,1,"
        "NULL,'2026-07-25T04:00:00Z',NULL,NULL)"
    )
    c.commit()
    c.close()


def test_real_cohort_join_and_features(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="real")
    # 2 real closed non-backtest rows (paper/backtest/open all excluded)
    assert manifest["row_count"] == 2
    assert manifest["rows_by_cohort"] == {"real": 2}
    by_strat = {r["strategy"]: r for r in rows}
    scalp = by_strat["ict_scalp_5m"]
    # graded structure features
    assert abs(scalp["feat_sweep_depth_atr"] - 0.5) < 1e-9  # |79700-79750|/100
    assert abs(scalp["feat_displacement_strength"] - 0.7) < 1e-9
    assert abs(scalp["feat_confidence"] - 0.8) < 1e-9
    # categorical + gate
    assert scalp["cat_regime"] == "trend"
    assert scalp["cat_vol_regime"] == "calm"
    assert scalp["gate_htf_bias_aligned"] is True
    # model-score summary
    assert scalp["model_score_count"] == 2
    assert abs(scalp["feat_model_score_max"] - 0.62) < 1e-9
    assert abs(scalp["feat_model_score_mean"] - 0.53) < 1e-9
    # outcome + R (50 / (|80000-79800| * 0.01 * 1) = 25)
    assert scalp["win"] == 1
    assert abs(scalp["r"] - 25.0) < 1e-9


def test_paper_cohort_only(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="paper")
    assert manifest["row_count"] == 1
    assert rows[0]["strategy"] == "htf_pullback_trend_2h"
    assert rows[0]["cohort"] == "paper"
    # the filled htf_pullback stub surfaces as pullback_depth
    assert abs(rows[0]["feat_pullback_depth"] - 0.3) < 1e-9


def test_both_cohorts(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="both")
    assert manifest["row_count"] == 3
    assert manifest["rows_by_cohort"] == {"real": 2, "paper": 1}


def test_strategy_filter(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="real", strategy="vwap")
    assert manifest["row_count"] == 1
    assert rows[0]["strategy"] == "vwap"
    assert abs(rows[0]["feat_vwap_deviation_std"] - 2.1) < 1e-9


def test_leakage_manifest_partitions_outcome_from_features(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    _, manifest = panel.build_panel(db_path=db, cohort="both")
    assert manifest["outcome_cols"] == ["pnl", "win", "r"]
    # No outcome column ever appears in the feature set.
    assert not (set(manifest["outcome_cols"]) & set(manifest["feature_cols"]))
    # Every feature col is a decision-time prefix (feat_/cat_/gate_).
    assert all(
        c.startswith(("feat_", "cat_", "gate_")) for c in manifest["feature_cols"]
    )
    assert "leakage_contract" in manifest


def test_missing_db_is_empty_not_crash(tmp_path):
    rows, manifest = panel.build_panel(db_path=str(tmp_path / "nope.db"), cohort="both")
    assert rows == []
    assert manifest["row_count"] == 0
    assert manifest["db_present"] is False


def test_write_panel_emits_jsonl_and_manifest(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="both")
    out = tmp_path / "sub" / "panel.jsonl"
    out_path, manifest_path = panel.write_panel(rows, manifest, out)
    assert out_path.exists() and manifest_path.exists()
    lines = out_path.read_text().strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(ln) for ln in lines)  # every line valid JSON
    m = json.loads(manifest_path.read_text())
    assert m["row_count"] == 3


def test_main_cli_runs(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    out = tmp_path / "panel.jsonl"
    rc = panel.main(["--db", db, "--cohort", "both", "--out", str(out), "--quiet"])
    assert rc == 0
    assert out.exists()
    assert out.with_suffix(".jsonl.manifest.json").exists()


# ---------------------------------------------------------------------------
# P4 — order_packages.meta (killzone / bias / setup_type) capture
# ---------------------------------------------------------------------------


def _make_db_with_meta(path):
    """A fixture whose order_packages carries the separate `meta` column (the
    P4 source of killzone / bias / setup_type)."""
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY, strategy_name TEXT, symbol TEXT, status TEXT,
          pnl REAL, is_backtest INT, account_class TEXT, is_demo INT,
          entry_price REAL, stop_loss REAL, position_size REAL,
          closed_at TEXT, timestamp TEXT, setup_type TEXT, reconcile_status TEXT);
        CREATE TABLE order_packages (
          order_package_id TEXT PRIMARY KEY, linked_trade_id INT, signal_logic TEXT,
          confidence REAL, model_scores TEXT, meta TEXT, updated_at TEXT);
        """
    )
    c.execute(
        "INSERT INTO trades VALUES "
        "(1,'vwap','BTCUSDT','closed',30.0,0,'real_money',0,80000,79800,0.01,"
        "'2026-07-25T00:00:00Z','2026-07-25T00:00:00Z',NULL,NULL)"
    )
    # signal_logic carries the strategy edge + regime; meta carries the
    # session/bias context that ONLY lives in the meta column.
    c.execute(
        "INSERT INTO order_packages VALUES (10,1,?,0.7,NULL,?, '2026-07-25T00:00:00Z')",
        (
            json.dumps({"deviation_std": 1.8, "regime": "range"}),
            json.dumps({"killzone": "NY_AM", "bias": "bullish", "setup_type": "mean_revert"}),
        ),
    )
    c.commit()
    c.close()


def test_meta_column_captures_killzone_bias_setup_type(tmp_path):
    db = str(tmp_path / "tj_meta.db")
    _make_db_with_meta(db)
    rows, manifest = panel.build_panel(db_path=db, cohort="real")
    assert manifest["row_count"] == 1
    row = rows[0]
    # meta-only session/bias context now instruments the panel
    assert row["cat_killzone"] == "ny_am"
    assert row["cat_bias"] == "bullish"
    assert row["cat_setup_type"] == "mean_revert"
    # signal_logic still wins on its own keys (regime came from signal_logic)
    assert row["cat_regime"] == "range"
    assert abs(row["feat_vwap_deviation_std"] - 1.8) < 1e-9
    # and the new categoricals are registered decision-time feature columns
    assert "cat_killzone" in manifest["feature_cols"]
    assert "cat_bias" in manifest["feature_cols"]
