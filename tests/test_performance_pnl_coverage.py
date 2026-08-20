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
    order_package_id TEXT PRIMARY KEY,
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
    for key in ("pnlCoverage", "totalPnlMeasured", "pnlMeasuredCount",
                "pnlEstimatedCount", "pnlFabricatedCount", "pnlUnverifiedCount"):
        assert key in agg, f"{key} missing from the aggregate envelope"


# ------------------------------------------ totalPnlMeasured (R4 gate input)
# The R4 research→results promotion gate reads totalPnlMeasured, NOT totalPnl:
# a leg is judged on money that was actually measured. The SUM is over
# {MEASURED, ESTIMATED} rows (a close-anchored reconstruction is a defensible
# value); FABRICATED marks and UNVERIFIED rows are excluded. Note the DELIBERATE
# asymmetry with pnlCoverage, which counts only MEASURED (an estimate is not a
# fill) — the coverage FLOOR decides whether the sum is trustworthy, the sum is
# taken over the wider measured-or-estimated subset. R4 design 2026-08-01 §3.

def test_total_pnl_measured_sums_measured_and_estimated_only(tmp_path):
    """+10 measured + (-2500 fabricated) + 3 estimated + 1 unverified → the
    measured sum is 10 + 3 = 13, NOT the -2486 raw total. The fabricated -2500
    and the unverified +1 are excluded."""
    agg = _agg(tmp_path, [
        ("vwap", "BTCUSDT", 10.0, "bybit_closed_pnl"),   # measured
        ("vwap", "BTCUSDT", -2500.0, "local_markprice"),  # fabricated (excluded)
        ("vwap", "BTCUSDT", 3.0, "candle_at_close"),      # estimated (included)
        ("vwap", "BTCUSDT", 1.0, None),                   # unverified (excluded)
    ])
    assert agg["totalPnl"] == pytest.approx(-2486.0)
    assert agg["totalPnlMeasured"] == pytest.approx(13.0)


def test_total_pnl_measured_zero_when_all_fabricated(tmp_path):
    """THE R4 point: the fabricated paper book reads a huge totalPnl but its
    measured sum is exactly 0 — the gate sees nothing to trust."""
    agg = _agg(tmp_path, [
        ("ict_scalp", "MES", 120_000.0, "local_markprice"),
        ("ict_scalp", "MES", 127_683.78, "local_markprice"),
    ])
    assert agg["totalPnl"] == pytest.approx(247_683.78)
    assert agg["totalPnlMeasured"] == 0.0


def test_total_pnl_measured_per_strategy(tmp_path):
    """Per-strategy measured sum is where the gate reads a single leg."""
    agg = _agg(tmp_path, [
        ("clean", "BTCUSDT", 5.0, "bybit_closed_pnl"),
        ("clean", "BTCUSDT", 6.0, "bybit_closed_pnl"),
        ("dirty", "MES", 9000.0, "local_markprice"),
        ("dirty", "MES", 8000.0, "local_markprice"),
    ])
    by_name = {s["name"]: s for s in agg["perStrategy"]}
    assert by_name["clean"]["totalPnlMeasured"] == pytest.approx(11.0)
    assert by_name["clean"]["totalPnl"] == pytest.approx(11.0)
    assert by_name["dirty"]["totalPnlMeasured"] == 0.0
    assert by_name["dirty"]["totalPnl"] == pytest.approx(17000.0)


def test_total_pnl_measured_present_and_zero_on_empty(tmp_path):
    """Empty/errored windows carry the field as 0.0 (measured nothing) so a
    consumer never has to guess whether it's missing."""
    assert _agg(tmp_path, [])["totalPnlMeasured"] == 0.0
    assert _empty("7d", None, error=True)["totalPnlMeasured"] == 0.0


# --- The per-strategy pair must be RECONCILABLE (2026-08-11) ----------------
#
# Found by reading the LIVE payload, not the code: two of 51 strategy rows on
# the real book returned `pnlCoverage: 0.0` beside a NON-ZERO
# `totalPnlMeasured`, with no published field able to explain it.
#
#   trend_donchian_avax_4h  cov 0.0  measured -5415.1698  totalPnl -5415.1698
#   pairs_bnb_btc_a         cov 0.0  measured    -2.9610  totalPnl  -211.0840
#
# Both are CORRECT: the count is MEASURED-only, the sum is MEASURED+ESTIMATED,
# and the R4 gate depends on that asymmetry. But with `pnlEstimatedCount`
# unpublished at this level the only available inference was "the measured sum
# falls back to the raw sum" — which the SECOND row disproves, and which is why
# one row would not have been enough to diagnose it. Hence both shapes below.


def test_per_strategy_all_estimated_is_reconcilable(tmp_path):
    """The avax shape: every row ESTIMATED, so the measured sum equals totalPnl
    while coverage is 0.0. `pnlEstimatedCount` is the field that makes that
    readable instead of looking like a fallback bug."""
    agg = _agg(tmp_path, [
        ("est_only", "AVAXUSDT", -2707.5849, "candle_at_close"),
        ("est_only", "AVAXUSDT", -2707.5849, "candle_at_close"),
    ])
    s = {x["name"]: x for x in agg["perStrategy"]}["est_only"]
    assert s["pnlMeasuredCount"] == 0
    assert s["pnlCoverage"] == 0.0
    # the sum is non-zero AND equals totalPnl — the shape that read as a fallback
    assert s["totalPnlMeasured"] == pytest.approx(-5415.1698)
    assert s["totalPnl"] == pytest.approx(-5415.1698)
    # ...and THIS is what explains it. Absent before 2026-08-11.
    assert s["pnlEstimatedCount"] == 2


def test_per_strategy_mixed_estimated_sum_differs_from_total(tmp_path):
    """The pairs shape: ESTIMATED + FABRICATED, so the measured sum does NOT
    equal totalPnl at coverage 0.0. This is the row that rules out 'fallback'."""
    agg = _agg(tmp_path, [
        ("mixed", "BNBUSDT", -2.961, "candle_at_close"),
        ("mixed", "BNBUSDT", -208.123, "local_markprice"),
    ])
    s = {x["name"]: x for x in agg["perStrategy"]}["mixed"]
    assert s["pnlMeasuredCount"] == 0
    assert s["pnlCoverage"] == 0.0
    assert s["pnlEstimatedCount"] == 1
    assert s["totalPnlMeasured"] == pytest.approx(-2.961)
    assert s["totalPnl"] == pytest.approx(-211.084)
    # the two differ — so the sum is a real subset, not a copy of the raw total
    assert s["totalPnlMeasured"] != pytest.approx(s["totalPnl"])


def test_per_strategy_counts_never_exceed_trades(tmp_path):
    """measured + estimated <= trades, on every row. A count that outran its own
    denominator would be the unasserted-denominator defect in the field added to
    prevent it."""
    agg = _agg(tmp_path, [
        ("a", "BTCUSDT", 5.0, "bybit_closed_pnl"),
        ("a", "BTCUSDT", -1.0, "candle_at_close"),
        ("a", "BTCUSDT", 9000.0, "local_markprice"),
        ("b", "ETHUSDT", 2.0, "candle_at_close"),
    ])
    assert agg["perStrategy"], "no strategy rows to check"
    for s in agg["perStrategy"]:
        assert s["pnlMeasuredCount"] + s["pnlEstimatedCount"] <= s["trades"], s
    a = {x["name"]: x for x in agg["perStrategy"]}["a"]
    assert (a["trades"], a["pnlMeasuredCount"], a["pnlEstimatedCount"]) == (3, 1, 1)
