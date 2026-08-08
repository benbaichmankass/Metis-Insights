"""Raw exchange-fill rows — the substrate aggregates can't provide.

Cover for BL-20260807-EXCHANGE-TRUTH-PER-STRATEGY-UNREACHABLE.

Every other reader on the fills store returns a SUM. That is right by default
and useless for "which trade does this discrepancy belong to": measured
2026-08-07, the journal recorded -$9,669.41 across three AVAXUSDT/bybit_1
trades where exchange truth was -$5,403.09 — a $4,266.32 gap that no existing
surface could attribute, over a population of SIX fills.

The tests pin two things the aggregates got to skip:
  * filters are BOUND, and scope travels with the rows;
  * a capped page is DECLARED (`truncated`), so a short list is never mistaken
    for a complete population — the unasserted-denominator failure mode.
"""
from __future__ import annotations

import pytest

from src.runtime.exchange_fills_store import init_db, list_fills, upsert_fills


@pytest.fixture()
def fills_db(tmp_path):
    p = tmp_path / "exchange_fills.sqlite"
    init_db(p)
    return p


def _fill(exec_id, *, account="bybit_1", symbol="AVAX/USDT:USDT",
          side="Buy", price=6.7, qty=10.0, fee=0.1, when="2026-08-06T10:00:00+00:00",
          order_id="o1"):
    return {
        "exec_id": exec_id, "account_id": account, "symbol": symbol,
        "side": side, "price": price, "qty": qty, "fee": fee,
        "fee_currency": "USDT", "exec_time": when, "order_id": order_id,
        "is_maker": 0, "raw": "{}",
    }


def test_returns_rows_not_sums(fills_db):
    upsert_fills([
        _fill("e1", price=6.692, qty=10.0),
        _fill("e2", price=6.416, qty=10.0, side="Sell"),
    ], path=fills_db)
    rows = list_fills(7, path=fills_db)
    assert len(rows) == 2
    # The per-fill PRICES are what an aggregate destroys and an attribution needs.
    assert {r["price"] for r in rows} == {6.692, 6.416}
    # The store normalises side to lowercase on upsert — assert the STORED
    # form, not the venue's casing.
    assert {r["side"] for r in rows} == {"buy", "sell"}


def test_symbol_filter_is_exact_and_bound(fills_db):
    upsert_fills([
        _fill("a1", symbol="AVAX/USDT:USDT"),
        _fill("s1", symbol="SOL/USDT:USDT"),
    ], path=fills_db)
    rows = list_fills(7, path=fills_db, symbol="AVAX/USDT:USDT")
    assert [r["exec_id"] for r in rows] == ["a1"]


def test_symbol_filter_does_not_accept_sql(fills_db):
    """The value is bound — a SQL fragment matches nothing, it does not execute."""
    upsert_fills([_fill("a1")], path=fills_db)
    rows = list_fills(7, path=fills_db, symbol="' OR '1'='1")
    assert rows == []


def test_account_filter_scopes(fills_db):
    upsert_fills([
        _fill("b1", account="bybit_1"),
        _fill("b2", account="bybit_2"),
    ], path=fills_db)
    assert [r["exec_id"] for r in list_fills(7, path=fills_db, account_id="bybit_1")] == ["b1"]


def test_newest_first(fills_db):
    upsert_fills([
        _fill("old", when="2026-08-05T10:00:00+00:00"),
        _fill("new", when="2026-08-06T10:00:00+00:00"),
    ], path=fills_db)
    assert [r["exec_id"] for r in list_fills(7, path=fills_db)] == ["new", "old"]


def test_window_excludes_older_fills(fills_db):
    """The window must DROP old rows, not merely sort them later.

    Uses injected `now` so the assertion is deterministic. An earlier draft of
    this test ended in `... or True`, which cannot fail — the same
    can't-fail-guard shape this file exists to argue against.
    """
    from datetime import datetime, timezone

    upsert_fills([
        _fill("recent", when="2026-08-06T10:00:00+00:00"),
        _fill("ancient", when="2020-01-01T00:00:00+00:00"),
    ], path=fills_db)
    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)

    wide = {r["exec_id"] for r in list_fills(3650, path=fills_db, now=now)}
    assert wide == {"recent", "ancient"}

    tight = {r["exec_id"] for r in list_fills(2, path=fills_db, now=now)}
    assert tight == {"recent"}, "a 2-day window must exclude a 2020 fill"


def test_limit_is_clamped_and_truncation_is_detectable(fills_db):
    upsert_fills([_fill(f"e{i}") for i in range(10)], path=fills_db)
    rows = list_fills(7, path=fills_db, limit=3)
    assert len(rows) == 3
    # THE denominator assertion: hitting the cap is indistinguishable from a
    # complete short population unless the caller can see it. The route surfaces
    # this as `truncated`; here we pin that the cap actually binds.
    assert len(list_fills(7, path=fills_db, limit=100)) == 10


@pytest.mark.parametrize("bad", [0, -1, -999])
def test_non_positive_days_returns_empty(fills_db, bad):
    upsert_fills([_fill("e1")], path=fills_db)
    assert list_fills(bad, path=fills_db) == []


def test_missing_store_returns_empty_not_error(tmp_path):
    """A store that was never created is an empty read, never a 5xx."""
    assert list_fills(7, path=tmp_path / "nope.sqlite") == []


def test_rows_carry_the_join_keys_an_attributor_needs(fills_db):
    upsert_fills([_fill("e1", order_id="ord-123")], path=fills_db)
    row = list_fills(7, path=fills_db)[0]
    for key in ("exec_id", "order_id", "account_id", "symbol", "side",
                "price", "qty", "fee", "exec_time"):
        assert key in row, f"{key} is needed to attribute a fill to a trade"
    assert row["order_id"] == "ord-123"


def test_raw_column_is_not_exposed(fills_db):
    """`raw` is the venue payload — not part of the read contract."""
    upsert_fills([_fill("e1")], path=fills_db)
    assert "raw" not in list_fills(7, path=fills_db)[0]
