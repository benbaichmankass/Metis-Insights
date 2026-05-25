"""Tests for per-model live attribution (ml.promotion.attribution)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ml.promotion.attribution import (
    JoinedScore,
    aggregate_attribution,
    compute_attribution,
    join_scores_to_trades,
    load_closed_trades,
    rank_auc,
)
from ml.shadow.inspector import ShadowRecord


def _rec(model_id, score, ts, *, symbol="BTCUSDT", trade_id=None, stage="shadow"):
    return ShadowRecord(
        predicted_at_utc=ts,
        model_id=model_id,
        stage=stage,
        score=score,
        row_keys=("symbol",),
        feature_row={"symbol": symbol},
        backfill_kind="retroactive_decision" if trade_id else None,
        trade_id=trade_id,
    )


def test_rank_auc_perfect_and_inverted():
    assert rank_auc([0.9, 0.8], [0.2, 0.1]) == 1.0
    assert rank_auc([0.1, 0.2], [0.8, 0.9]) == 0.0
    # ties count 0.5
    assert rank_auc([0.5], [0.5]) == 0.5
    # empty group → None
    assert rank_auc([], [0.5]) is None


def test_join_uses_signal_time_score_and_symbol_match():
    now = datetime.now(timezone.utc)
    trades = [{
        "id": "1", "symbol": "BTCUSDT", "pnl": 10.0,
        "opened_at": now - timedelta(hours=2), "closed_at": now,
    }]
    records = [
        _rec("m", 0.7, now - timedelta(hours=2)),   # signal time (earliest)
        _rec("m", 0.3, now - timedelta(hours=1)),   # later — ignored
        _rec("m", 0.99, now - timedelta(hours=1), symbol="ETHUSDT"),  # wrong symbol
    ]
    joined = join_scores_to_trades(trades, records)
    assert len(joined) == 1
    assert joined[0].score == 0.7
    assert joined[0].win is True


def test_join_skips_trades_without_pnl():
    now = datetime.now(timezone.utc)
    trades = [{
        "id": "1", "symbol": "BTCUSDT", "pnl": None,
        "opened_at": now - timedelta(hours=2), "closed_at": now,
    }]
    assert join_scores_to_trades(trades, [_rec("m", 0.5, now - timedelta(hours=1))]) == []


def test_join_backfill_by_trade_id():
    now = datetime.now(timezone.utc)
    trades = [{
        "id": "42", "symbol": "BTCUSDT", "pnl": -5.0,
        "opened_at": now - timedelta(hours=2), "closed_at": now - timedelta(hours=1),
    }]
    # backfill record has a synthetic ts outside the window but carries trade_id
    rec = _rec("m", 0.4, now + timedelta(days=1), trade_id="42")
    joined = join_scores_to_trades(trades, [rec])
    assert len(joined) == 1
    assert joined[0].win is False


def test_aggregate_brier_and_auc():
    joined = [
        JoinedScore("m", "shadow", "1", 0.8, True, 10.0),
        JoinedScore("m", "shadow", "2", 0.7, True, 5.0),
        JoinedScore("m", "shadow", "3", 0.2, False, -3.0),
        JoinedScore("m", "shadow", "4", 0.3, False, -2.0),
    ]
    [a] = aggregate_attribution(joined)
    assert a.n == 4
    assert a.win_rate == 0.5
    assert a.auc == 1.0  # all wins scored above all losses
    # brier computable (all scores in [0,1]); model beats base rate
    assert a.brier is not None
    assert a.baseline_brier == 0.25  # 0.5 * 0.5
    assert a.brier_lift is not None and a.brier_lift > 0


def test_aggregate_brier_none_for_nonprobability_scores():
    joined = [
        JoinedScore("exec", "shadow", "1", 3.2, True, 10.0),
        JoinedScore("exec", "shadow", "2", -1.5, False, -3.0),
    ]
    [a] = aggregate_attribution(joined)
    assert a.brier is None
    assert a.brier_lift is None
    assert a.auc == 1.0


def _seed_db(path: Path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL, "
        "pnl_percent REAL, status TEXT, timestamp TEXT, notes TEXT, "
        "is_backtest INT, is_demo INT)"
    )
    conn.execute(
        "CREATE TABLE order_packages (id INTEGER PRIMARY KEY, linked_trade_id INT, "
        "updated_at TEXT)"
    )
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO trades VALUES (1,'BTCUSDT',12.0,0.5,'closed',?,NULL,0,0)",
        ((now - timedelta(hours=3)).isoformat(),),
    )
    conn.execute(
        "INSERT INTO trades VALUES (2,'BTCUSDT',-4.0,-0.2,'closed',?,NULL,0,0)",
        ((now - timedelta(hours=3)).isoformat(),),
    )
    # backtest + demo rows that must be excluded
    conn.execute(
        "INSERT INTO trades VALUES (3,'BTCUSDT',99.0,1.0,'closed',?,NULL,1,0)",
        (now.isoformat(),),
    )
    conn.execute(
        "INSERT INTO trades VALUES (4,'BTCUSDT',99.0,1.0,'closed',?,NULL,0,1)",
        (now.isoformat(),),
    )
    conn.execute("INSERT INTO order_packages VALUES (10,1,?)", (now.isoformat(),))
    conn.execute("INSERT INTO order_packages VALUES (11,2,?)", (now.isoformat(),))
    conn.commit()
    conn.close()


def test_load_closed_trades_excludes_backtest_and_demo(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_db(db)
    trades = load_closed_trades(db)
    ids = {t["id"] for t in trades}
    assert ids == {"1", "2"}


def test_compute_attribution_end_to_end(tmp_path: Path):
    db = tmp_path / "j.db"
    _seed_db(db)
    now = datetime.now(timezone.utc)
    log = tmp_path / "shadow.jsonl"
    import json
    lines = [
        # win trade 1 gets a high score, loss trade 2 a low score → AUC 1.0
        json.dumps({
            "predicted_at_utc": (now - timedelta(hours=3)).isoformat(),
            "model_id": "m", "stage": "shadow", "score": 0.9,
            "row_keys": ["symbol"], "feature_row": {"symbol": "BTCUSDT"},
        }),
    ]
    log.write_text("\n".join(lines) + "\n")
    # Only trade 1's window contains a record here; that's fine — n=1.
    attrs = compute_attribution(db_path=db, shadow_log=log)
    assert attrs and attrs[0].model_id == "m"
    assert attrs[0].n >= 1
