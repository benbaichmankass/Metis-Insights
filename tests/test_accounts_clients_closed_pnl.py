"""Tests for ``_bybit_closed_pnl_lookup`` selection logic — in
particular the ``entry_price_target`` discriminator added 2026-05-18
after the issue #1411 backfill incident.

The bug pattern that motivated this:
  * ``account_closed_pnl_for_trade`` opens a [opened_at_ms - 60s, now()]
    Bybit window for a backfilled trade
  * Bybit returns up to 50 records in that window
  * Pre-fix: filter by side + qty (5% tol), return most-recent by
    updatedTime
  * For VWAP-on-BTCUSDT where every trade has identical
    (symbol, direction, qty=0.004), every backfill call returned
    the same most-recent close — 15+ distinct trades collapsed onto
    the same pnl value, including trade #1332 which swung from
    +$10.48 to -$0.17 (the matcher returned a completely different
    trade's close).

The fix is a primary discriminator: ``avgEntryPrice``. Each trade
has a unique entry fill price; matching on that disambiguates the
records inside the window. ``(side, qty)`` filters stay as defense.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.units.accounts.clients import _bybit_closed_pnl_lookup


def _make_client(records):
    """Stub Bybit client whose ``get_closed_pnl`` returns *records*
    wrapped in the V5 response envelope."""
    client = MagicMock()
    client.get_closed_pnl.return_value = {
        "result": {"list": records},
    }
    return client


def _rec(*, side, qty, entry, exit_, pnl, updated):
    """Helper to build a single closed-pnl record."""
    return {
        "side": side,
        "qty": str(qty),
        "avgEntryPrice": str(entry),
        "avgExitPrice": str(exit_),
        "closedPnl": str(pnl),
        "updatedTime": str(updated),
        "createdTime": str(updated),
    }


class TestEntryPriceDiscriminator:
    """The new ``entry_price_target`` filter is the primary defence
    against the issue #1411 collapse pattern."""

    def test_picks_record_matching_entry_price(self):
        """Three records, same side+qty, different avgEntryPrice.
        Caller asks for the trade with entry=76700.0 — only that
        record should be returned."""
        records = [
            # Most recent — different entry price (would have won pre-fix)
            _rec(side="Sell", qty=0.004, entry=77100.0, exit_=77150.0,
                 pnl=-0.171, updated=1763400000000),
            # Older — matches the entry price we want
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76977.6,
                 pnl=0.5723, updated=1763300000000),
            # Oldest — different entry price
            _rec(side="Sell", qty=0.004, entry=76500.0, exit_=76600.0,
                 pnl=0.32, updated=1763200000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=76700.0,
        )
        assert rec is not None
        assert abs(float(rec["avgEntryPrice"]) - 76700.0) < 1e-6
        assert abs(float(rec["closedPnl"]) - 0.5723) < 1e-6

    def test_no_match_within_tolerance_returns_none(self):
        """Entry price 80000 — none of the records match within 10
        bps. Helper returns None rather than falling back to
        side/qty most-recent."""
        records = [
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76977.6,
                 pnl=0.5723, updated=1763300000000),
            _rec(side="Sell", qty=0.004, entry=76500.0, exit_=76600.0,
                 pnl=0.32, updated=1763200000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=80000.0,
        )
        assert rec is None

    def test_within_tolerance_but_off_by_a_few_bps(self):
        """avgEntryPrice can differ from the bot's stored entry_price
        by a few bps due to fill-rounding and Bybit's display
        precision. The 10-bps tolerance (default) accepts that."""
        records = [
            # 5 bps off — should match within the 10-bps tolerance
            _rec(side="Sell", qty=0.004, entry=76703.84, exit_=76977.6,
                 pnl=0.5723, updated=1763300000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=76700.0,
        )
        assert rec is not None
        assert abs(float(rec["avgEntryPrice"]) - 76703.84) < 1e-3

    def test_outside_tolerance_returns_none(self):
        """0.5% off → way outside the 10-bps tolerance → skipped."""
        records = [
            _rec(side="Sell", qty=0.004, entry=77083.5, exit_=77150.0,
                 pnl=0.2, updated=1763300000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=76700.0,
        )
        assert rec is None

    def test_multiple_within_tolerance_picks_closest(self):
        """Two records both within 10 bps of target. Prefer the
        closest avgEntryPrice."""
        records = [
            # 8 bps off — most recent, but not the closest
            _rec(side="Sell", qty=0.004, entry=76761.4, exit_=76900.0,
                 pnl=0.4, updated=1763400000000),
            # 1 bp off — the right answer
            _rec(side="Sell", qty=0.004, entry=76707.7, exit_=76977.6,
                 pnl=0.5723, updated=1763300000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=76700.0,
        )
        assert rec is not None
        assert abs(float(rec["avgEntryPrice"]) - 76707.7) < 1e-3

    def test_exact_tie_breaks_to_most_recent(self):
        """Two records with the SAME avgEntryPrice — extremely rare
        but possible if a position was re-opened at the same exact
        price. Prefer the more recent."""
        records = [
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76800.0,
                 pnl=0.3, updated=1763400000000),
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76900.0,
                 pnl=0.7, updated=1763300000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004, entry_price_target=76700.0,
        )
        assert rec is not None
        assert int(rec["updatedTime"]) == 1763400000000  # most recent


class TestBackwardsCompatibility:
    """Existing callers that don't supply ``entry_price_target``
    must keep getting the most-recent record (the orphan-reconciler
    path: fires within ~60s of the close, so most-recent IS the
    right answer)."""

    def test_no_entry_target_returns_most_recent(self):
        records = [
            _rec(side="Sell", qty=0.004, entry=76500.0, exit_=76600.0,
                 pnl=0.32, updated=1763200000000),
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76977.6,
                 pnl=0.5723, updated=1763300000000),
            _rec(side="Sell", qty=0.004, entry=77100.0, exit_=77150.0,
                 pnl=-0.171, updated=1763400000000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1763100000000, end_ts_ms=1763500000000,
            qty_target=0.004,  # entry_price_target=None
        )
        assert rec is not None
        # Most recent — preserves the pre-fix orphan-reconciler behaviour
        assert int(rec["updatedTime"]) == 1763400000000


class TestIssue1411Scenario:
    """End-to-end reproduction of the issue #1411 bug, before AND
    after the fix. Demonstrates the collapse pattern would have
    persisted without ``entry_price_target``."""

    def _records_15_trades(self):
        """15 distinct long BTCUSDT closes, each with a unique
        entry_price, all with qty=0.004 (mirrors the production
        VWAP-on-BTCUSDT shape from issue #1411)."""
        return [
            _rec(side="Sell", qty=0.004,
                 entry=76700.0 + i * 50.0,
                 exit_=76800.0 + i * 50.0,
                 pnl=round(0.1 + i * 0.05, 4),
                 updated=1763300000000 + i * 60_000)
            for i in range(15)
        ]

    def test_with_entry_target_picks_each_trade_correctly(self):
        """Looping over each trade's entry_price returns its actual
        close — no collapse."""
        records = self._records_15_trades()
        client = _make_client(records)
        results = []
        for i in range(15):
            rec = _bybit_closed_pnl_lookup(
                client, category="linear", symbol="BTCUSDT", side="Sell",
                start_ts_ms=1763100000000, end_ts_ms=1763500000000,
                qty_target=0.004,
                entry_price_target=76700.0 + i * 50.0,
            )
            results.append(float(rec["closedPnl"]))
        # All 15 distinct values — no collapse.
        assert len(set(results)) == 15

    def test_without_entry_target_reproduces_the_collapse(self):
        """Same 15 records, no entry_price_target → every call
        returns the same most-recent close. This is the bug the
        fix prevents new callers from triggering."""
        records = self._records_15_trades()
        client = _make_client(records)
        results = []
        for _ in range(15):
            rec = _bybit_closed_pnl_lookup(
                client, category="linear", symbol="BTCUSDT", side="Sell",
                start_ts_ms=1763100000000, end_ts_ms=1763500000000,
                qty_target=0.004,  # NO entry_price_target
            )
            results.append(float(rec["closedPnl"]))
        # All collapse onto the same value — the issue #1411 pattern.
        assert len(set(results)) == 1
