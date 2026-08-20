"""M30 · P2 — tests for scripts/research/sweep_research_panels.py.

Synthetic fixtures only (no live data, no network). Covers the deterministic
core — the strategy/asset-pool grouping + the FDR×OOS verdict mapping — plus an
end-to-end sweep over a synthetic DB (structure, not fragile CV numbers) and the
file/blob-stripping writer. Loaded via importlib because scripts/research is not
a package (same pattern as test_build_research_panel.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(
    os.path.dirname(_HERE), "scripts", "research", "sweep_research_panels.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("sweep_research_panels", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sweep = _load()


# ---------------------------------------------------------------------------
# group_rows — the deterministic partition
# ---------------------------------------------------------------------------


def _row(strategy, symbol, win=1):
    return {
        "strategy": strategy,
        "symbol": symbol,
        "cohort": "real",
        "pnl": 1.0 if win else -1.0,
        "win": win,
        "r": 1.0 if win else -1.0,
        "feat_confidence": 0.7,
    }


def test_group_rows_strategy_and_asset_pool_and_underpowered():
    rows = []
    # dense strategy — its own group
    rows += [_row("vwap", "BTCUSDT") for _ in range(12)]
    # two thin commodity books — pooled by asset class into one group
    rows += [_row("mgc_trend", "MGC") for _ in range(6)]
    rows += [_row("mhg_trend", "MHG") for _ in range(6)]
    # a lone equity book — pool stays below the floor → underpowered
    rows += [_row("spy_swing", "SPY") for _ in range(3)]

    groups, underpowered = sweep.group_rows(rows, power_floor=10)

    kinds = {g["key"]: g["kind"] for g in groups}
    assert kinds.get("vwap") == "strategy"
    # MGC + MHG both classify commodity → one pooled asset group of 12 >= 10
    assert kinds.get("asset:commodity") == "asset_pool"
    commodity = next(g for g in groups if g["key"] == "asset:commodity")
    assert len(commodity["rows"]) == 12
    assert set(commodity["strategies"]) == {"mgc_trend", "mhg_trend"}
    # the lone equity book (n=3) is under the floor → underpowered, not dropped
    up_keys = {u["key"] for u in underpowered}
    assert "asset:equity" in up_keys
    assert all("< power_floor" in u["reason"] for u in underpowered)


def test_group_rows_all_below_floor_is_all_underpowered():
    rows = [_row("tiny", "BTCUSDT") for _ in range(4)]
    groups, underpowered = sweep.group_rows(rows, power_floor=50)
    assert groups == []
    assert len(underpowered) == 1
    assert underpowered[0]["kind"] == "asset_pool"


# ---------------------------------------------------------------------------
# verdict_for — the platform-bar mapping
# ---------------------------------------------------------------------------


def _report(*, survivors, reg):
    return {"fdr": {"survivors": survivors}, "regression": reg}


def test_verdict_null_when_no_fdr_survivor():
    v = sweep.verdict_for(_report(survivors=[], reg={"computed": False}))
    assert v["verdict"] == "null"


def test_verdict_lead_when_survivor_but_oos_not_computed():
    v = sweep.verdict_for(
        _report(survivors=["feat_x"], reg={"computed": False, "note": "too few rows"})
    )
    assert v["verdict"] == "lead"
    assert v["oos_value"] is None
    assert v["regression_note"] == "too few rows"


def test_verdict_lead_when_survivor_but_oos_not_positive():
    reg = {"computed": True, "model": "logistic", "cv": {"oos_auc": 0.42, "oos_auc_by_fold": [0.42]}}
    v = sweep.verdict_for(_report(survivors=["feat_x"], reg=reg))
    assert v["verdict"] == "lead"
    assert v["oos_positive"] is False
    assert v["oos_metric"] == "oos_auc"


def test_verdict_candidate_finding_when_survivor_and_oos_positive():
    reg = {"computed": True, "model": "logistic", "cv": {"oos_auc": 0.71, "oos_auc_by_fold": [0.7, 0.72]}}
    v = sweep.verdict_for(_report(survivors=["feat_x"], reg=reg))
    assert v["verdict"] == "candidate_finding"
    assert v["oos_positive"] is True


def test_verdict_r_outcome_uses_r2_sign():
    pos = sweep.verdict_for(
        _report(survivors=["feat_x"], reg={"computed": True, "model": "ridge_ols", "cv": {"oos_r2": 0.1}})
    )
    neg = sweep.verdict_for(
        _report(survivors=["feat_x"], reg={"computed": True, "model": "ridge_ols", "cv": {"oos_r2": -3.0}})
    )
    assert pos["verdict"] == "candidate_finding"
    assert neg["verdict"] == "lead"


def test_verdict_error_report():
    v = sweep.verdict_for({"error": "leakage", "fdr": {}, "regression": {}})
    assert v["verdict"] == "error"


# ---------------------------------------------------------------------------
# End-to-end sweep over a synthetic DB (structure, not fragile CV numbers)
# ---------------------------------------------------------------------------


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
    tid = 0
    op = 0

    def add(strategy, symbol, n, win_frac, conf):
        nonlocal tid, op
        for i in range(n):
            tid += 1
            op += 1
            win = 1 if (i / max(n - 1, 1)) < win_frac else 0
            pnl = 40.0 if win else -20.0
            ts = f"2026-07-{(tid % 27) + 1:02d}T{(tid % 24):02d}:00:00Z"
            c.execute(
                "INSERT INTO trades VALUES "
                "(?,?,?,'closed',?,0,'real_money',0,80000,79800,0.01,?,?,NULL,NULL)",
                (tid, strategy, symbol, pnl, ts, ts),
            )
            c.execute(
                "INSERT INTO order_packages VALUES (?,?,?,?,NULL,?)",
                (op, tid, json.dumps({"deviation_std": 1.0 + i * 0.1}), conf, ts),
            )

    # dense strategy well above the floor
    add("vwap", "BTCUSDT", 40, 0.35, 0.7)
    # two thin commodity books → pooled
    add("mgc_trend", "MGC", 6, 0.5, 0.6)
    add("mhg_trend", "MHG", 6, 0.5, 0.6)
    c.commit()
    c.close()


def test_run_sweep_end_to_end(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    result = sweep.run_sweep(db_path=db, cohort="real", power_floor=10, outcomes=["win", "r"])
    assert result["panel_row_count"] == 52
    keys = {g["key"]: g for g in result["groups"]}
    # vwap is its own group; MGC+MHG pool into commodity (12 >= 10)
    assert keys["vwap"]["kind"] == "strategy"
    assert keys["asset:commodity"]["kind"] == "asset_pool"
    # every group carries both requested outcomes with a known verdict
    known = {"null", "lead", "candidate_finding", "error", "underpowered"}
    for g in result["groups"]:
        assert set(g["outcomes"]) == {"win", "r"}
        for outcome in ("win", "r"):
            assert g["outcomes"][outcome]["verdict"]["verdict"] in known
    assert result["verdict_counts"]  # non-empty roll-up


def test_write_sweep_strips_report_blobs_and_writes_groups(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    result = sweep.run_sweep(db_path=db, cohort="real", power_floor=10, outcomes=["win"])
    out_dir = tmp_path / "sweep"
    sweep_json, sweep_md = sweep.write_sweep(result, out_dir)
    assert sweep_json.exists() and sweep_md.exists()
    slim = json.loads(sweep_json.read_text())
    # the heavy per-group C2 `report` blobs are stripped from the summary...
    for g in slim["groups"]:
        for outcome, ob in g["outcomes"].items():
            assert "report" not in ob
            assert "verdict" in ob
    # ...and written to groups/ for drill-down
    assert (out_dir / "groups").exists()
    group_files = list((out_dir / "groups").glob("*.json"))
    assert group_files, "expected per-group full C2 reports"


def test_missing_db_is_empty_sweep_not_crash(tmp_path):
    result = sweep.run_sweep(db_path=str(tmp_path / "nope.db"), cohort="real")
    assert result["panel_row_count"] == 0
    assert result["groups"] == []


def test_main_cli_runs(tmp_path):
    db = str(tmp_path / "tj.db")
    _make_db(db)
    out_dir = tmp_path / "out"
    rc = sweep.main(["--db", db, "--power-floor", "10", "--out-dir", str(out_dir), "--quiet"])
    assert rc == 0
    assert (out_dir / "sweep.json").exists()
    assert (out_dir / "sweep.md").exists()
