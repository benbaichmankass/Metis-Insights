"""Tests for the conviction_meta dataset family (v2 conviction meta-model).

Builds against a tiny temp `trade_journal.db` fixture (order_packages JOIN
trades) and asserts row filtering, the calibrated-lens + context feature space,
and the won / r_multiple labels.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ml.datasets.families.conviction_meta import ConvictionMetaBuilder
from ml.datasets.registry import get_builder, list_families


def _make_db(db: Path) -> None:
    """A minimal trade_journal.db: trades + order_packages, one row per scenario.

    Scenarios:
      tid=1  op=op_live_win   closed live winner (won, has model_scores + context)
      tid=2  op=op_live_loss  closed live loser  (won=False)
      tid=3  op=op_backtest   closed but is_backtest=1 -> EXCLUDED
      tid=4  op=op_open       trade still open       -> EXCLUDED (status<>closed)
      (op_unlinked: order package with no linked trade -> EXCLUDED by the JOIN)
    """
    conn = sqlite3.connect(str(db))
    # Mirror the real schema: `trades.order_package_id` is the populated
    # back-reference; `order_packages.linked_trade_id` exists but is (almost
    # always) NULL. The family joins on the back-reference.
    conn.execute(
        "CREATE TABLE trades ("
        "  id INTEGER PRIMARY KEY, status TEXT, is_backtest INT, "
        "  pnl REAL, pnl_percent REAL, order_package_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE order_packages ("
        "  order_package_id TEXT PRIMARY KEY, strategy_name TEXT, symbol TEXT, "
        "  direction TEXT, confidence REAL, signal_logic TEXT, created_at TEXT, "
        "  meta TEXT, model_scores TEXT, linked_trade_id INTEGER)"
    )

    trades = [
        # id, status, is_backtest, pnl, pnl_percent, order_package_id
        (1, "closed", 0, 12.5, 2.0, "op_live_win"),   # live winner
        (2, "closed", 0, -8.0, -1.0, "op_live_loss"),  # live loser
        (3, "closed", 1, 5.0, 1.0, "op_backtest"),    # backtest -> excluded
        (4, "open", 0, None, None, "op_open"),        # open -> excluded
        (5, "closed", 0, 3.0, 0.5, None),             # NULL op_id -> excluded
    ]
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?)", trades)

    meta_win = json.dumps(
        {"regime": "trend", "adx_14": 27.5, "vol_regime": "volatile", "setup_type": "ob"}
    )
    # trade-outcome head -> c_wr; setup-quality head -> c_setup; regime head
    # alone is skipped without a calibrator (matches conviction_inputs default).
    scores_win = json.dumps(
        {
            "trade-outcome-winrate-baseline-v0": {"stage": "shadow", "score": 0.7},
            "setup-quality-baseline-v0": {"stage": "shadow", "score": 0.0},  # R0 -> 0.5
            "btc-regime-1h-lgbm-v2": {"stage": "advisory", "score": 0.9},    # skipped
        }
    )
    ops = [
        # op_id, strat, symbol, dir, conf, signal_logic, created_at, meta, scores,
        # linked_trade_id (NULL in reality — the trade->package back-ref is the link)
        (
            "op_live_win", "trend_donchian", "BTCUSDT", "long", 0.62,
            "{}", "2026-06-10T01:00:00Z", meta_win, scores_win, None,
        ),
        (
            "op_live_loss", "vwap", "BTCUSDT", "short", 0.30,
            json.dumps({"regime": "range"}), "2026-06-10T02:00:00Z",
            "{}", None, None,
        ),
        (
            "op_backtest", "vwap", "BTCUSDT", "long", 0.5,
            "{}", "2026-06-10T03:00:00Z", "{}", None, None,
        ),
        (
            "op_open", "vwap", "BTCUSDT", "long", 0.5,
            "{}", "2026-06-10T04:00:00Z", "{}", None, None,
        ),
        (
            "op_unlinked", "vwap", "BTCUSDT", "long", 0.5,
            "{}", "2026-06-10T05:00:00Z", "{}", None, None,
        ),
    ]
    conn.executemany(
        "INSERT INTO order_packages VALUES (?,?,?,?,?,?,?,?,?,?)", ops
    )
    conn.commit()
    conn.close()


def _rows(db: Path, **kwargs) -> list[dict]:
    return list(ConvictionMetaBuilder().iter_rows(db_path=db, **kwargs))


def test_family_registered():
    assert "conviction_meta" in list_families()
    assert isinstance(get_builder("conviction_meta"), ConvictionMetaBuilder)


def test_only_closed_filled_live_rows(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    rows = _rows(db)
    op_ids = {r["order_package_id"] for r in rows}
    # winner + loser only; backtest / open / unlinked all excluded.
    assert op_ids == {"op_live_win", "op_live_loss"}
    assert all(r["source"] == "live" for r in rows)


def test_won_and_r_multiple_labels(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    by_op = {r["order_package_id"]: r for r in _rows(db, risk_pct=1.0, r_cap=3.0)}

    win = by_op["op_live_win"]
    assert win["won"] is True
    assert win["pnl"] == pytest.approx(12.5)
    # pnl_percent 2.0 / risk_pct 1.0 = 2.0 R
    assert win["r_multiple"] == pytest.approx(2.0)

    loss = by_op["op_live_loss"]
    assert loss["won"] is False
    assert loss["r_multiple"] == pytest.approx(-1.0)


def test_r_multiple_respects_risk_pct_and_cap(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    # risk_pct 0.5 doubles R; cap at 3.0 clips the winner (2.0/0.5 = 4.0 -> 3.0).
    win = {r["order_package_id"]: r for r in _rows(db, risk_pct=0.5, r_cap=3.0)}["op_live_win"]
    assert win["r_multiple"] == pytest.approx(3.0)


def test_features_include_lens_inputs(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    win = {r["order_package_id"]: r for r in _rows(db)}["op_live_win"]
    # c_strat from raw confidence (no calibrator -> identity).
    assert win["c_strat"] == pytest.approx(0.62)
    # c_wr from the trade-outcome head (already P(win) in [0,1]).
    assert win["c_wr"] == pytest.approx(0.7)
    # c_setup from setup-quality R-multiple 0 -> (0+3)/6 = 0.5.
    assert win["c_setup"] == pytest.approx(0.5)
    # regime head alone is NOT a usable alignment prob without a calibrator,
    # so c_reg is dropped (mirrors build_conviction_inputs / the live stamp).
    assert "c_reg" not in win


def test_features_include_context(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    win = {r["order_package_id"]: r for r in _rows(db)}["op_live_win"]
    assert win["regime"] == "trend"
    assert win["adx_14"] == pytest.approx(27.5)
    assert win["vol_regime"] == "volatile"
    assert win["symbol"] == "BTCUSDT"
    assert win["direction"] == "long"
    assert win["strategy_name"] == "trend_donchian"


def test_context_falls_back_to_signal_logic(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    # op_live_loss has empty meta but regime in signal_logic.
    loss = {r["order_package_id"]: r for r in _rows(db)}["op_live_loss"]
    assert loss["regime"] == "range"
    # No model_scores -> only c_strat present (from confidence 0.30).
    assert loss["c_strat"] == pytest.approx(0.30)
    assert "c_wr" not in loss
    assert "c_setup" not in loss


def test_strategy_filter(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    rows = _rows(db, strategy_name="vwap")
    assert {r["order_package_id"] for r in rows} == {"op_live_loss"}


def test_missing_db_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _rows(tmp_path / "nope.db")


# --- M19 T0.3: optional pretrained-TSFM embedding block ---------------------

from ml.datasets.embedding_features import EMBEDDING_FEATURE_COLUMNS  # noqa: E402


def test_embedding_columns_present_and_zero_without_path(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    rows = _rows(db)
    assert rows
    # Every row carries the full 32-col embedding block, all neutral 0.0 (the
    # v1 feature space is unchanged — v1's manifest never selects these).
    for r in rows:
        for col in EMBEDDING_FEATURE_COLUMNS:
            assert r[col] == 0.0


def _stage_emb_sidestream(tmp_path: Path, ts_to_val: dict[str, float]) -> Path:
    out = tmp_path / "emb" / "BTCUSDT" / "15m" / "v001"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "data.jsonl").open("w", encoding="utf-8") as fh:
        for ts, val in ts_to_val.items():
            fh.write(json.dumps({"ts": ts, **{c: val for c in EMBEDDING_FEATURE_COLUMNS}}) + "\n")
    return out


def test_embedding_asof_join_when_path_given(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    # Two embeddings; the live rows are at 01:00 and 02:00. As-of (past-only):
    # a 00:30 embedding covers the 01:00 row; a 01:30 embedding covers 02:00.
    emb = _stage_emb_sidestream(
        tmp_path,
        {"2026-06-10T00:30:00Z": 0.5, "2026-06-10T01:30:00Z": 0.9},
    )
    by_op = {r["order_package_id"]: r for r in _rows(db, embedding_path=emb)}
    assert by_op["op_live_win"]["tsfm_emb_0"] == pytest.approx(0.5)   # 01:00 -> 00:30
    assert by_op["op_live_loss"]["tsfm_emb_0"] == pytest.approx(0.9)  # 02:00 -> 01:30
    # The lens/context features are untouched by the join.
    assert by_op["op_live_win"]["c_strat"] == pytest.approx(0.62)


def test_embedding_asof_is_past_only(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _make_db(db)
    # Only a FUTURE embedding (after both live rows) -> nothing is carried,
    # every row stays neutral 0.0 (a bar never sees a future observation).
    emb = _stage_emb_sidestream(tmp_path, {"2026-06-10T09:00:00Z": 0.7})
    rows = _rows(db, embedding_path=emb)
    for r in rows:
        assert r["tsfm_emb_0"] == 0.0
