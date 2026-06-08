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


class TestTemporalOrdering:
    """Issue #1419 fix: when both entry_price and opened_at_ms are
    supplied, prefer the EARLIEST close after the trade opened.
    Critical when several trades share (side, qty, entry±10bps) —
    entry_price alone can't disambiguate them; their open
    timestamps can."""

    def test_picks_earliest_close_after_open(self):
        """Issue #1419 reproduction: trade #1540's actual data.
        5 records pass the entry±10bps filter; the correct match
        is the EARLIEST one whose createdTime ≥ opened_at_ms,
        not the closest entry-price match."""
        # Trade #1540 opened at 06:32:09 UTC = 1779085929000 ms.
        opened_at_ms = 1779085929000
        # Records mirror the issue #1423 diag output for #1540's
        # search window. Each entry within 10 bps of 76719.7.
        records = [
            # [09] later trade — entry 76750.9 (+4.1 bps), pnl=-0.171
            _rec(side="Sell", qty=0.004, entry=76750.9, exit_=76792.6,
                 pnl=-0.171, updated=1779100600475),
            # [10] later trade — entry 76733.7 (+1.8 bps), pnl=-0.200
            _rec(side="Sell", qty=0.004, entry=76733.7, exit_=76768.2,
                 pnl=-0.200, updated=1779099857757),
            # [11] later trade — entry 76727.6 (+1.0 bps, closest!), pnl=-0.468
            _rec(side="Sell", qty=0.004, entry=76727.6, exit_=76694.9,
                 pnl=-0.468, updated=1779099560698),
            # [12] later trade — entry 76750.1 (+4.0 bps), pnl=-0.488
            _rec(side="Sell", qty=0.004, entry=76750.1, exit_=76712.5,
                 pnl=-0.488, updated=1779099260893),
            # [15] THE ACTUAL CLOSE for trade #1540 — entry 76681.9
            # (+4.9 bps, NOT the closest), exit 76977.6, pnl=+0.845.
            # createdTime=07:26:37 = 1779089197750ms — the earliest
            # close after opened_at_ms.
            _rec(side="Sell", qty=0.004, entry=76681.9, exit_=76977.6,
                 pnl=0.8447, updated=1779089197750),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779085869000, end_ts_ms=1779109708919,
            qty_target=0.004,
            entry_price_target=76719.7,
            opened_at_ms=opened_at_ms,
        )
        assert rec is not None
        # Must pick record [15], NOT [11] (which is what the
        # entry-price-only matcher picked in #1419).
        assert abs(float(rec["closedPnl"]) - 0.8447) < 1e-4
        assert abs(float(rec["avgEntryPrice"]) - 76681.9) < 1e-3
        # Sanity: verify the older entry-price-only matcher would
        # have picked record [11] (the wrong one).
        rec_wrong = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779085869000, end_ts_ms=1779109708919,
            qty_target=0.004,
            entry_price_target=76719.7,
            # No opened_at_ms → falls back to closest-entry
        )
        assert abs(float(rec_wrong["closedPnl"]) - (-0.468)) < 1e-3

    def test_filters_out_closes_before_open(self):
        """A close that happened BEFORE the trade opened can't be
        this trade's close. The 60s slack permits intra-tick skew
        but anything older than that must be filtered out."""
        opened_at_ms = 1779100000000
        records = [
            # Pre-open close (5 min before open) — must be skipped
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76800.0,
                 pnl=0.4, updated=1779099700000),
            # Post-open close — should win
            _rec(side="Sell", qty=0.004, entry=76710.0, exit_=76900.0,
                 pnl=0.76, updated=1779100100000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004,
            entry_price_target=76705.0,
            opened_at_ms=opened_at_ms,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - 0.76) < 1e-3

    def test_2s_slack_admits_near_open_records(self):
        """Bybit's createdTime can be 1-2 s off opened_at_ms due to
        clock skew between the VM's wall clock and Bybit's exec
        engine. 2s slack permits this. A 30s-before-open close
        from a separate trade is rejected (tested below)."""
        opened_at_ms = 1779100000000
        records = [
            # 1s before opened_at_ms — within 2s slack, should match
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76800.0,
                 pnl=0.4, updated=1779099999000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004,
            entry_price_target=76700.0,
            opened_at_ms=opened_at_ms,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - 0.4) < 1e-3

    def test_30s_before_open_close_is_rejected(self):
        """A close 30s before opened_at_ms is from a different
        (earlier) trade — too far outside the 2s clock-skew
        slack to be this trade's close."""
        opened_at_ms = 1779100000000
        records = [
            # 30s before — outside slack, must be rejected
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76800.0,
                 pnl=0.4, updated=1779099970000),
            # 5s after — the legitimate match
            _rec(side="Sell", qty=0.004, entry=76702.0, exit_=76850.0,
                 pnl=0.6, updated=1779100005000),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004,
            entry_price_target=76700.0,
            opened_at_ms=opened_at_ms,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - 0.6) < 1e-3

    def test_consecutive_trades_share_entry_get_distinct_closes(self):
        """Three VWAP shorts that all entered at the SAME price
        (VWAP mean-revert hits the same level) get correctly
        paired with their own distinct closes by temporal ordering.
        This is the #1419 collapse pattern that entry_price alone
        couldn't resolve. Realistic 5-min spacing matches the
        production VWAP cadence."""
        # All entered at 76700.0.
        # Trade A: opened t=0,    closed t=1min
        # Trade B: opened t=5min, closed t=8min
        # Trade C: opened t=10min, closed t=13min
        records = [
            # Close for trade C (closed t=13min = 780_000 ms after base)
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76900.0,
                 pnl=0.8, updated=1779100780000),
            # Close for trade B (closed t=8min)
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76850.0,
                 pnl=0.6, updated=1779100480000),
            # Close for trade A (closed t=1min)
            _rec(side="Sell", qty=0.004, entry=76700.0, exit_=76800.0,
                 pnl=0.4, updated=1779100060000),
        ]
        client = _make_client(records)

        # Trade A: opened at t=0
        rec_a = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004, entry_price_target=76700.0,
            opened_at_ms=1779100000000,
        )
        # Trade B: opened at t=5min
        rec_b = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004, entry_price_target=76700.0,
            opened_at_ms=1779100300000,
        )
        # Trade C: opened at t=10min
        rec_c = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1779099000000, end_ts_ms=1779101000000,
            qty_target=0.004, entry_price_target=76700.0,
            opened_at_ms=1779100600000,
        )
        # Each trade gets a DIFFERENT close — no collapse.
        assert abs(float(rec_a["closedPnl"]) - 0.4) < 1e-3
        assert abs(float(rec_b["closedPnl"]) - 0.6) < 1e-3
        assert abs(float(rec_c["closedPnl"]) - 0.8) < 1e-3


class TestDemoWideFallback:
    """BL-20260601-001 prong 1: ``allow_wide_fallback`` (demo-only).

    Bybit DEMO closed-pnl rows carry placeholder / zeroed
    ``avgEntryPrice``, so the live-money entry-price disambiguator
    (#1411) over-filters every record and strands the realised PnL as
    NULL (5/5 demo ``htf_pullback`` closes in the 2026-06-08 window).
    The wide fallback re-filters on side alone and takes the most
    recent close — but ONLY when the flag is set (demo). Live accounts
    keep the strict NULL-on-no-match contract.
    """

    def _zeroed_entry_records(self):
        """Two side-matching demo closes with avgEntryPrice=0 (the demo
        venue's placeholder) — both fail the strict entry filter."""
        return [
            _rec(side="Sell", qty=0.059, entry=0.0, exit_=60500.0,
                 pnl=-3.20, updated=1780777000000),
            # most recent
            _rec(side="Sell", qty=0.059, entry=0.0, exit_=60400.0,
                 pnl=-5.00, updated=1780777459294),
        ]

    def test_strict_returns_none_when_entry_zeroed(self):
        """Without the flag, zeroed-entry demo records → None (the
        bug: pnl strands NULL). This is the LIVE-account contract."""
        client = _make_client(self._zeroed_entry_records())
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1780770000000, end_ts_ms=1780780000000,
            qty_target=0.059, entry_price_target=60568.6,
            opened_at_ms=1780776000000,
            allow_wide_fallback=False,
        )
        assert rec is None

    def test_wide_fallback_recovers_most_recent(self):
        """With the flag set (demo), the same records resolve to the
        most-recent side-matching close."""
        client = _make_client(self._zeroed_entry_records())
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1780770000000, end_ts_ms=1780780000000,
            qty_target=0.059, entry_price_target=60568.6,
            opened_at_ms=1780776000000,
            allow_wide_fallback=True,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - (-5.00)) < 1e-6  # most recent
        assert int(rec["updatedTime"]) == 1780777459294

    def test_wide_fallback_still_respects_side(self):
        """The wide fallback drops qty/entry/open filters but KEEPS the
        close-side filter — a Buy close can't satisfy a Sell lookup."""
        records = [
            _rec(side="Buy", qty=0.059, entry=0.0, exit_=60400.0,
                 pnl=99.0, updated=1780777459294),  # wrong side
            _rec(side="Sell", qty=0.059, entry=0.0, exit_=60500.0,
                 pnl=-3.20, updated=1780777000000),  # right side
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1780770000000, end_ts_ms=1780780000000,
            qty_target=0.059, entry_price_target=60568.6,
            opened_at_ms=1780776000000,
            allow_wide_fallback=True,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - (-3.20)) < 1e-6
        assert str(rec["side"]) == "Sell"

    def test_wide_fallback_returns_none_when_no_side_match(self):
        """Even with the flag, zero side-matching records → None (no
        record to attribute)."""
        records = [
            _rec(side="Buy", qty=0.059, entry=0.0, exit_=60400.0,
                 pnl=99.0, updated=1780777459294),
        ]
        client = _make_client(records)
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1780770000000, end_ts_ms=1780780000000,
            qty_target=0.059, entry_price_target=60568.6,
            opened_at_ms=1780776000000,
            allow_wide_fallback=True,
        )
        assert rec is None


class TestReduceLegLookup:
    """BL-20260601-001 prong 2: an intent-reduce / close leg whose
    journal direction + entry are the PRIMARY leg's intent.

    The reduce caller passes ``side=""`` (skip the close-side filter)
    and ``entry_price_target=None`` (skip the entry filter), matching by
    qty + close-window + opened_at only. Verified against live trade
    #2491: a buy-to-reduce on a held short, journaled direction='long'
    (→ would translate to a Sell lookup) with entry 60774.6 (the htf
    primary-leg intent, ~29 bps off the reduced short's real entry).
    """

    def _reduce_records(self):
        """The reduce close on Bybit: side='Buy' (closing a short),
        avgEntryPrice ~ the short's real entry (60756.7), NOT the
        recorded 60774.6."""
        return [
            _rec(side="Buy", qty=0.003, entry=60756.7, exit_=60050.0,
                 pnl=2.0988, updated=1780793499383),
        ]

    def test_strict_long_lookup_misses_the_reduce_close(self):
        """A direction='long' strict lookup → close_side='Sell' +
        entry 60774.6: the real reduce close is a Buy at a 29-bps-off
        entry, so the strict filters strand it as NULL."""
        client = _make_client(self._reduce_records())
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="Sell",
            start_ts_ms=1780790000000, end_ts_ms=1780800000000,
            qty_target=0.003, entry_price_target=60774.6,
            opened_at_ms=1780793499000,
        )
        assert rec is None

    def test_reduce_leg_lookup_matches_by_position_movement(self):
        """Skipping the side + entry filters (the reduce-leg path)
        recovers the close by qty + window."""
        client = _make_client(self._reduce_records())
        rec = _bybit_closed_pnl_lookup(
            client, category="linear", symbol="BTCUSDT", side="",
            start_ts_ms=1780790000000, end_ts_ms=1780800000000,
            qty_target=0.003, entry_price_target=None,
            opened_at_ms=1780793499000,
        )
        assert rec is not None
        assert abs(float(rec["closedPnl"]) - 2.0988) < 1e-4
