"""Netted closed-pnl proration — BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS.

Under one-way netting the broker returns ONE closed-pnl record for the whole
netted position. Booking its full value onto each journal row that shared that
position fabricates: measured on the live journal 2026-08-06, ``pnl = -2970.99``
landed on three htf_pullback_trend_2h/BTCUSDT rows of qty 0.012 / 0.717 / 0.728 —
the first implying a ~$247,000 BTC move.

Two of the three sites that persist a broker close already prorated; the third
(``_recover_close_from_broker_pnl``) did not, and stamped the broker source, so
the fabricated figure classified MEASURED. These tests pin the shared helper's
contract and that third site's fix.
"""
from __future__ import annotations

import pytest

from src.runtime import order_monitor as om
from src.runtime import provenance


class TestProrateHelper:
    def test_prorates_when_record_covers_a_bigger_netted_position(self):
        # record flattened 1.0 unit; this row owned 0.25 of it
        pnl, prorated = om._prorate_netted_broker_pnl(-1000.0, 1.0, 0.25)
        assert prorated is True
        assert pnl == pytest.approx(-250.0)

    def test_the_real_incident_row(self):
        """Trade 2343: qty 0.012 booked the full -2970.99 of a 0.717 close."""
        pnl, prorated = om._prorate_netted_broker_pnl(-2970.99, 0.717, 0.012)
        assert prorated is True
        assert pnl == pytest.approx(-2970.99 * (0.012 / 0.717))
        assert abs(pnl) < 50.0  # sane for 0.012 BTC; the raw figure was not

    def test_one_to_one_close_keeps_the_brokers_exact_figure(self):
        """A normal close must NOT be re-derived through a division that can
        only lose precision — the broker's number is the measured one."""
        pnl, prorated = om._prorate_netted_broker_pnl(-123.4567, 0.5, 0.5)
        assert prorated is False
        assert pnl == -123.4567

    def test_rounding_slack_does_not_trigger_proration(self):
        # 3% bigger — inside NETTED_PRORATE_QTY_RATIO, treated as the same close
        pnl, prorated = om._prorate_netted_broker_pnl(-100.0, 1.03, 1.0)
        assert prorated is False
        assert pnl == -100.0

    def test_just_past_the_ratio_does_trigger(self):
        pnl, prorated = om._prorate_netted_broker_pnl(-100.0, 1.10, 1.0)
        assert prorated is True
        assert pnl == pytest.approx(-100.0 * (1.0 / 1.10))

    def test_always_flag_prorates_even_at_equal_qty(self):
        """Cascade semantics: siblings of a known netted flatten. The ratio test
        would wrongly skip a sibling whose qty happens to match the record."""
        pnl, prorated = om._prorate_netted_broker_pnl(
            -100.0, 1.0, 1.0, always=True,
        )
        assert prorated is True
        assert pnl == pytest.approx(-100.0)

    def test_unusable_inputs_return_none_never_a_zero(self):
        assert om._prorate_netted_broker_pnl(None, 1.0, 1.0) == (None, False)
        assert om._prorate_netted_broker_pnl("nonsense", 1.0, 1.0) == (None, False)

    def test_missing_quantities_book_the_raw_value_unprorated(self):
        """No qty on either side means the split is not derivable. Book the
        broker's figure as-is rather than inventing a ratio — an unprorated
        value is at least the broker's own number."""
        for rq, wq in ((0, 1.0), (1.0, 0), (None, None)):
            pnl, prorated = om._prorate_netted_broker_pnl(-50.0, rq, wq)
            assert (pnl, prorated) == (-50.0, False)

    def test_ratio_constant_is_above_one(self):
        """Below 1.0 would prorate every close, including exact 1:1 ones."""
        assert om.NETTED_PRORATE_QTY_RATIO > 1.0


class TestProvenanceOfAProratedRow:
    def test_prorated_source_classifies_FABRICATED_not_measured(self):
        """The whole point of the fix. The exit PRICE is still the broker's,
        but the SPLIT is an assumption about attribution, so the row must not
        keep a bare broker source and read as MEASURED."""
        measured = provenance.classify_pnl(
            {"exit_price_source": "bybit_closed_pnl"})[0]
        fabricated = provenance.classify_pnl(
            {"exit_price_source": "bybit_closed_pnl_prorated"})[0]
        assert measured == provenance.MEASURED
        assert fabricated == provenance.FABRICATED

    def test_a_prorated_row_is_not_trustworthy_for_calibration(self):
        """`pnl_is_trustworthy` gates the fidelity-calibration set. A prorated
        row must be excluded from it — admitting one is what let a −99.5R
        outlier into the trust map."""
        assert not provenance.pnl_is_trustworthy(
            {"exit_price_source": "bybit_closed_pnl_prorated"})
        assert provenance.pnl_is_trustworthy(
            {"exit_price_source": "bybit_closed_pnl"})
