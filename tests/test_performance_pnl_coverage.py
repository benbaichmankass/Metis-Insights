"""``/api/bot/performance`` PnL-provenance coverage.

The 2026-07-30 audit found the codebase applying the right discipline to the
WRONG metric: ``rCoverage`` correctly refused to let partial R-measurement
masquerade as full, while the ``pnl`` that R is derived FROM was silently
fabricated for 64.9% of July's closed trades (+$247,683.78 of `local_markprice`
money). These tests pin the base metric's honest denominator.

The load-bearing assertion is the last one: a window can report a large,
confident ``totalPnl`` while NOTHING in it was measured. If ``pnlCoverage``
ever stops surfacing that, the defect is back.

Exercises the real SQL + aggregation against a synthetic journal — not mocks —
so the ``notes``-JSON read path is covered too.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.web.api.routers.performance import _aggregate, _empty, _query

_SCHEMA = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    account_id TEXT,
    strategy_name TEXT,
    symbol TEXT,
    direction TEXT,
    entry_price REAL,
    stop_loss REAL,
    position_size REAL,
    pnl REAL,
    status TEXT,
    is_backtest INTEGER DEFAULT 0,
    is_demo INTEGER DEFAULT 0,
    account_class TEXT,
    setup_type TEXT,
    reconcile_status TEXT,
    exit_reason TEXT,
    closed_at TEXT,
    timestamp TEXT,
    notes TEXT
);
CREATE TABLE order_packages (
    id INTEGER PRIMARY KEY,
    linked_trade_id INTEGER,
    updated_at TEXT
);
"""


def _mk_db(tmp_path, rows):
    """Build a journal with *rows* = [(strategy, symbol, pnl, pnl_source)]."""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    for i, (strategy, symbol, pnl, pnl_source) in enumerate(rows, start=1):
        notes = json.dumps({"pnl_source": pnl_source}) if pnl_source else "{}"
        conn.execute(
            "INSERT INTO trades (id, account_id, strategy_name, symbol, "
            "direction, entry_price, stop_loss, position_size, pnl, status, "
            "is_backtest, is_demo, account_class, closed_at, timestamp, notes) "
            "VALUES (?,?,?,?,'long',100.0,99.0,1.0,?, 'closed',0,0,"
            "'real_money',?,?,?)",
            (i, "bybit_2", strategy, symbol, pnl,
             f"2026-07-{10 + i:02d}T12:00:00Z", f"2026-07-{10 + i:02d}T11:00:00Z",
             notes),
        )
    conn.commit()
    conn.close()
    return db


def _agg(tmp_path, rows):
    db = _mk_db(tmp_path, rows)
    return _aggregate(_query(db, since=None), "all", None)


# ---------------------------------------------------------------- happy path
def test_all_measured_is_full_coverage(tmp_path):
    agg = _agg(tmp_path, [
        ("ict_scalp", "BTCUSDT", 10.0, "bybit_closed_pnl"),
        ("ict_scalp", "BTCUSDT", -4.0, "bybit_closed_pnl"),
    ])
    assert agg["totalTrades"] == 2
    assert agg["pnlCoverage"] == 1.0
    assert agg["pnlMeasuredCount"] == 2
    assert agg["pnlFabricatedCount"] == 0


def test_mixed_provenance_reports_the_real_split(tmp_path):
    agg = _agg(tmp_path, [
        ("vwap", "BTCUSDT", 10.0, "bybit_closed_pnl"),   # measured
        ("vwap", "BTCUSDT", -2500.0, "local_markprice"),  # fabricated
        ("vwap", "BTCUSDT", 3.0, "candle_at_close"),      # estimated
        ("vwap", "BTCUSDT", 1.0, None),                   # unverified
    ])
    assert agg["totalTrades"] == 4
    assert agg["pnlMeasuredCount"] == 1
    assert agg["pnlFabricatedCount"] == 1
    assert agg["pnlEstimatedCount"] == 1
    assert agg["pnlUnverifiedCount"] == 1
    assert agg["pnlCoverage"] == 0.25


def test_unverified_is_never_counted_as_measured(tmp_path):
    """Absence of a provenance record is not evidence of measurement — the 247
    legacy rows must not inflate coverage."""
    agg = _agg(tmp_path, [("vwap", "BTCUSDT", 5.0, None)] * 3)
    assert agg["pnlCoverage"] == 0.0
    assert agg["pnlUnverifiedCount"] == 3


def test_estimated_is_not_measured(tmp_path):
    """A candle-anchored reconstruction is closer to truth than a stale mark,
    but it is still not a fill — it must not count toward coverage."""
    agg = _agg(tmp_path, [("vwap", "BTCUSDT", 5.0, "candle_at_close")] * 2)
    assert agg["pnlCoverage"] == 0.0
    assert agg["pnlEstimatedCount"] == 2


# ------------------------------------------------------- the load-bearing one
def test_a_large_confident_pnl_can_be_entirely_unmeasured(tmp_path):
    """THE regression this endpoint field exists to prevent.

    This is the shape of the real incident: a paper book reporting a huge
    profit, every number of which came from `_sweep_local_pnl_for_unpriced`
    substituting a mark price hours after the close. `totalPnl` looks
    authoritative and is meaningless. Nothing may be tuned on it.
    """
    agg = _agg(tmp_path, [
        ("ict_scalp", "MES", 120_000.0, "local_markprice"),
        ("ict_scalp", "MES", 127_683.78, "local_markprice"),
    ])
    assert agg["totalPnl"] == pytest.approx(247_683.78)
    assert agg["pnlCoverage"] == 0.0          # <-- the tell
    assert agg["pnlFabricatedCount"] == 2
    assert agg["pnlMeasuredCount"] == 0


# ------------------------------------------------------------- per-strategy
def test_per_strategy_coverage_is_reported(tmp_path):
    """Per-strategy is where tuning decisions are made, so the split has to be
    visible at that granularity — not just in the headline."""
    agg = _agg(tmp_path, [
        ("clean", "BTCUSDT", 5.0, "bybit_closed_pnl"),
        ("clean", "BTCUSDT", 6.0, "bybit_closed_pnl"),
        ("dirty", "MES", 9000.0, "local_markprice"),
        ("dirty", "MES", 8000.0, "local_markprice"),
    ])
    by_name = {s["name"]: s for s in agg["perStrategy"]}
    assert by_name["clean"]["pnlCoverage"] == 1.0
    assert by_name["clean"]["pnlMeasuredCount"] == 2
    assert by_name["dirty"]["pnlCoverage"] == 0.0
    assert by_name["dirty"]["pnlMeasuredCount"] == 0


# ------------------------------------------------------------------ envelope
def test_empty_window_reports_null_coverage_not_zero(tmp_path):
    """None (not 0.0) so 'no trades' stays distinguishable from 'nothing was
    measured' — the exact distinction whose absence made the bug invisible."""
    agg = _agg(tmp_path, [])
    assert agg["totalTrades"] == 0
    assert agg["pnlCoverage"] is None


def test_error_envelope_also_carries_the_fields():
    """A consumer must never have to guess whether the field is missing because
    of an outage or because coverage is genuinely zero."""
    env = _empty("7d", None, error=True)
    assert env["pnlCoverage"] is None
    assert env["pnlMeasuredCount"] == 0
    assert env["pnlFabricatedCount"] == 0


def test_coverage_fields_present_on_every_aggregate(tmp_path):
    """Guards against a future refactor dropping the split from one code path
    (the way `exit_price_source` was recorded but never surfaced)."""
    agg = _agg(tmp_path, [("vwap", "BTCUSDT", 1.0, "bybit_closed_pnl")])
    for key in ("pnlCoverage", "pnlMeasuredCount", "pnlEstimatedCount",
                "pnlFabricatedCount", "pnlUnverifiedCount"):
        assert key in agg, f"{key} missing from the aggregate envelope"
