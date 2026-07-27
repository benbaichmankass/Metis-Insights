"""M36 Track D — tests for the conviction_meta BACKTEST row-source (source axis).

Covers the M30→M16 backbone wiring: _row_from_panel mapping, the panel-glob
reader, and iter_rows under source_mode ∈ {live, backtest, union}. Offline +
deterministic — a temp M30 panel JSONL + (for union) a tiny temp trade_journal.db.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from ml.datasets.families.conviction_meta import (
    ConvictionMetaBuilder,
    _iter_backtest_payloads,
    _row_from_panel,
)


def _panel_row(**over):
    row = {
        "strategy": "ict_scalp_5m", "symbol": "BTCUSDT", "direction": "long",
        "cat_regime": "chop", "cat_vol_regime": "calm", "feat_adx_14": 18.5,
        "feat_confidence": 0.82, "win": 1, "r": 0.4, "pnl": 0.4,
        "closed_at": "2024-01-01T00:05:00Z",
    }
    row.update(over)
    return row


def test_row_from_panel_maps_schema_and_source():
    p = _row_from_panel(_panel_row(), risk_pct=1.0, r_cap=3.0)
    assert p["source"] == "backtest"
    assert p["strategy_name"] == "ict_scalp_5m"
    assert p["symbol"] == "BTCUSDT"
    assert p["direction"] == "long"
    assert p["regime"] == "chop"
    assert p["vol_regime"] == "calm"
    assert p["adx_14"] == 18.5
    assert p["won"] is True
    assert p["r_multiple"] == 0.4
    # c_strat comes from build_conviction_inputs (identity-normalized confidence)
    assert math.isclose(p["c_strat"], 0.82, rel_tol=1e-6)
    # no order package / journal trade on the backtest substrate
    assert p["order_package_id"] == "" and p["trade_id"] == -1


def test_row_from_panel_clips_r_and_handles_loss():
    p = _row_from_panel(_panel_row(r=-5.0, win=0), risk_pct=1.0, r_cap=3.0)
    assert p["won"] is False
    assert p["r_multiple"] == -3.0  # clipped to -r_cap
    assert p["pnl"] == -5.0         # unclipped raw R proxy


def test_row_from_panel_none_on_missing_outcome():
    assert _row_from_panel({"strategy": "s", "r": None, "pnl": None}, risk_pct=1.0, r_cap=3.0) is None


def test_iter_backtest_payloads_scopes_and_reads(tmp_path: Path):
    panel = tmp_path / "p.jsonl"
    with panel.open("w") as fh:
        fh.write(json.dumps(_panel_row(symbol="BTCUSDT")) + "\n")
        fh.write(json.dumps(_panel_row(symbol="ETHUSDT", r=-1.0, win=0)) + "\n")
        fh.write("not json\n")  # tolerated
    got = list(_iter_backtest_payloads(
        str(panel), risk_pct=1.0, r_cap=3.0, strategy_name=None, symbol=None))
    assert len(got) == 2
    # symbol scoping
    only_btc = list(_iter_backtest_payloads(
        str(panel), risk_pct=1.0, r_cap=3.0, strategy_name=None, symbol="BTCUSDT"))
    assert len(only_btc) == 1 and only_btc[0]["symbol"] == "BTCUSDT"


def test_iter_rows_backtest_only_skips_db(tmp_path: Path):
    panel = tmp_path / "p.jsonl"
    panel.write_text(json.dumps(_panel_row()) + "\n")
    b = ConvictionMetaBuilder()
    # no db_path given — a live/union build would raise; backtest-only must not
    rows = list(b.iter_rows(source_mode="backtest", backtest_panels=str(panel)))
    assert len(rows) == 1
    assert rows[0]["source"] == "backtest"
    # embedding block attached (inert 0.0)
    assert rows[0].get("tsfm_emb_0") == 0.0


def _make_live_db(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT, is_backtest INT, "
        "pnl REAL, pnl_percent REAL, order_package_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (order_package_id TEXT PRIMARY KEY, strategy_name TEXT, "
        "symbol TEXT, direction TEXT, confidence REAL, signal_logic TEXT, created_at TEXT, "
        "meta TEXT, model_scores TEXT, linked_trade_id INTEGER)"
    )
    conn.execute("INSERT INTO trades VALUES (1,'closed',0,12.5,2.0,'op1')")
    conn.execute(
        "INSERT INTO order_packages VALUES ('op1','ict_scalp_5m','BTCUSDT','long',0.7,'{}',"
        "'2024-01-02T00:00:00Z','{\"regime\":\"trend\",\"adx_14\":27.5}','{}',NULL)"
    )
    conn.commit()
    conn.close()


def test_iter_rows_union_merges_live_and_backtest(tmp_path: Path):
    db = tmp_path / "tj.db"
    _make_live_db(db)
    panel = tmp_path / "p.jsonl"
    panel.write_text(json.dumps(_panel_row()) + "\n" + json.dumps(_panel_row(r=-1.0, win=0)) + "\n")
    b = ConvictionMetaBuilder()
    rows = list(b.iter_rows(source_mode="union", backtest_panels=str(panel), db_path=str(db)))
    sources = sorted(r["source"] for r in rows)
    assert sources == ["backtest", "backtest", "live"]  # 2 bt + 1 live, not wiped


def test_iter_rows_live_default_unchanged(tmp_path: Path):
    db = tmp_path / "tj.db"
    _make_live_db(db)
    b = ConvictionMetaBuilder()
    rows = list(b.iter_rows(db_path=str(db)))  # default source_mode="live"
    assert len(rows) == 1 and rows[0]["source"] == "live"


def test_iter_rows_bad_source_mode_raises(tmp_path: Path):
    b = ConvictionMetaBuilder()
    try:
        list(b.iter_rows(source_mode="bogus", db_path=str(tmp_path / "x.db")))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "source_mode" in str(e)
