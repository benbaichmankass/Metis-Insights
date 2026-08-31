"""Durable store for the venue's own wallet ledger.

The store exists so the wallet-truth figure is recomputable at any time from
rows WE pulled, instead of from a CSV someone pasted in July.
"""
from __future__ import annotations

import src.runtime.bybit_wallet_truth as wt
from src.runtime.exchange_fills_store import (
    init_db,
    list_transaction_log,
    upsert_transaction_log,
)


def _rows():
    return [
        {"id": "t1", "type": "TRADE", "currency": "USDT", "change": "-100.0",
         "fee": "-0.55", "transactionTime": 1_760_000_000_000},
        {"id": "t2", "type": "TRANSFER_IN", "currency": "USDT", "change": "5000.0",
         "transactionTime": 1_760_000_100_000},
        {"id": "t3", "type": "SETTLEMENT", "currency": "USDT", "change": "-2.5",
         "funding": "-2.5", "transactionTime": 1_760_000_200_000},
    ]


def test_round_trip_feeds_the_wallet_truth_calculator(tmp_path):
    db = tmp_path / "s.sqlite"
    assert upsert_transaction_log(_rows(), "bybit_2", path=db) == 3
    got = list_transaction_log("bybit_2", path=db)
    v = wt.compute_wallet_truth("bybit_2", got)
    assert v.state == wt.STATE_MEASURED
    assert v.realized_usd == -102.5, "transfer must be excluded end-to-end"


def test_reinsert_is_idempotent_so_money_is_never_double_counted(tmp_path):
    """Overlapping pulls are the NORMAL case for an hourly puller with a lookback.

    A duplicated row would move an account-level P&L figure, which is worse than
    a duplicated fill.
    """
    db = tmp_path / "s.sqlite"
    upsert_transaction_log(_rows(), "bybit_2", path=db)
    second = upsert_transaction_log(_rows(), "bybit_2", path=db)
    assert second == 0, "re-pulling the same window must insert nothing"
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.realized_usd == -102.5, "figure must be unchanged after a re-pull"


def test_rows_without_a_venue_id_are_skipped_not_synthesised(tmp_path):
    """A row we cannot key cannot be de-duplicated; minting a key would let the
    same money be counted again on the next overlapping pull."""
    db = tmp_path / "s.sqlite"
    n = upsert_transaction_log(
        [{"type": "TRADE", "currency": "USDT", "change": "-9.0"}], "bybit_2", path=db
    )
    assert n == 0
    assert list_transaction_log("bybit_2", path=db) == []


def test_window_filter_bounds_the_read(tmp_path):
    db = tmp_path / "s.sqlite"
    upsert_transaction_log(_rows(), "bybit_2", path=db)
    late = list_transaction_log("bybit_2", since_ms=1_760_000_150_000, path=db)
    assert [r["id"] for r in late] == ["t3"]


def test_accounts_are_isolated(tmp_path):
    db = tmp_path / "s.sqlite"
    upsert_transaction_log(_rows(), "bybit_2", path=db)
    assert list_transaction_log("bybit_1", path=db) == []


def test_empty_store_is_not_an_error_but_is_not_a_measurement(tmp_path):
    """`[]` from the store is 'nothing stored'. The caller must not read that as
    a measured flat account -- compute_wallet_truth grades it no_rows_in_window,
    which is distinct from measured_api."""
    db = tmp_path / "s.sqlite"
    init_db(db)
    v = wt.compute_wallet_truth("bybit_2", list_transaction_log("bybit_2", path=db))
    assert v.state == wt.STATE_NO_ROWS
    assert v.realized_usd is None
