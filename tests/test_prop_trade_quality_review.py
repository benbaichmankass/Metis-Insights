"""P4 — the prop trade-quality review.

The load-bearing controls are the ones that keep "we could not grade this" from
reading as a substantive outcome, and the one that pins the bridge/strategy
split — the whole reason the tool exists rather than a win-rate.
"""
from __future__ import annotations

import pytest

from scripts.prop import trade_quality_review as tq


def _ticket(tid="t1", entry=100.0, sl=95.0, tp=110.0, direction="long",
            strategy="trend_donchian_sol_prop"):
    return {"ticket_id": tid, "entry": entry, "sl": sl, "tp": tp,
            "direction": direction, "strategy": strategy, "symbol": "SOLUSDT"}


def _fill(tid="t1", entry=100.0, exit_price=95.0, pnl=-50.0, direction="long",
          status="closed", fid=1):
    return {"id": fid, "ticket_id": tid, "entry_price": entry,
            "exit_price": exit_price, "pnl": pnl, "direction": direction,
            "status": status, "symbol": "SOLUSDT", "reported_at": "2026-08-19T00:00:00Z"}


class TestExitClassification:
    def test_an_exit_on_the_stop_is_at_stop(self):
        g = tq.classify_exit(_fill(exit_price=95.0), _ticket(), tolerance_bps=15)
        assert g["state"] == tq.AT_STOP

    def test_an_exit_on_the_target_is_at_target(self):
        g = tq.classify_exit(_fill(exit_price=110.0, pnl=100.0), _ticket(),
                             tolerance_bps=15)
        assert g["state"] == tq.AT_TARGET

    def test_an_exit_PAST_the_stop_is_its_own_state_not_at_stop(self):
        """A gap through the stop is a real outcome and must not be filed as a
        clean stop-out — the slippage is the finding."""
        g = tq.classify_exit(_fill(exit_price=90.0, pnl=-100.0), _ticket(),
                             tolerance_bps=15)
        assert g["state"] == tq.BEYOND_STOP
        assert g["exit_slip_bps"] < 0

    def test_an_exit_past_the_target_is_its_own_state(self):
        g = tq.classify_exit(_fill(exit_price=120.0, pnl=200.0), _ticket(),
                             tolerance_bps=15)
        assert g["state"] == tq.BEYOND_TARGET

    def test_an_exit_between_the_levels_splits_on_pnl(self):
        assert tq.classify_exit(_fill(exit_price=104.0, pnl=40.0), _ticket(),
                                tolerance_bps=15)["state"] == tq.MANUAL_IN_PROFIT
        assert tq.classify_exit(_fill(exit_price=98.0, pnl=-20.0), _ticket(),
                                tolerance_bps=15)["state"] == tq.MANUAL_IN_LOSS
        assert tq.classify_exit(_fill(exit_price=100.0, pnl=0.0), _ticket(),
                                tolerance_bps=15)["state"] == tq.MANUAL_FLAT

    def test_a_SHORT_is_graded_on_the_right_side_of_each_level(self):
        tk = _ticket(entry=100.0, sl=105.0, tp=90.0, direction="short")
        # past the stop for a short means ABOVE it
        g = tq.classify_exit(_fill(exit_price=112.0, pnl=-120.0, direction="short"),
                             tk, tolerance_bps=15)
        assert g["state"] == tq.BEYOND_STOP
        g = tq.classify_exit(_fill(exit_price=85.0, pnl=150.0, direction="short"),
                             tk, tolerance_bps=15)
        assert g["state"] == tq.BEYOND_TARGET


class TestUnclassifiedIsNeverAnOutcome:
    """The whole point: missing data must not manufacture a manual-exit rate."""

    def test_no_ticket_link_is_unclassified_not_manual(self):
        g = tq.classify_exit(_fill(exit_price=104.0, pnl=40.0), None,
                             tolerance_bps=15)
        assert g["state"] == tq.UNCLASSIFIED_NO_TICKET
        assert g["state"] not in (tq.MANUAL_IN_PROFIT, tq.MANUAL_IN_LOSS)

    def test_a_ticket_with_no_levels_is_unclassified_not_manual(self):
        g = tq.classify_exit(_fill(exit_price=104.0, pnl=40.0),
                             _ticket(sl=None, tp=None), tolerance_bps=15)
        assert g["state"] == tq.UNCLASSIFIED_NO_LEVELS

    def test_a_missing_exit_price_is_unclassified(self):
        g = tq.classify_exit(_fill(exit_price=None, pnl=40.0), _ticket(),
                             tolerance_bps=15)
        assert g["state"] == tq.UNCLASSIFIED_NO_EXIT

    def test_every_unclassified_state_is_excluded_from_graded(self):
        fills = [
            _fill(fid=1, tid=None, exit_price=104.0, pnl=40.0),
            _fill(fid=2, tid="t1", exit_price=95.0),
        ]
        res = tq.review(fills, [_ticket()], tolerance_bps=15)
        assert res["population"]["closed"] == 2
        assert res["population"]["graded"] == 1
        assert res["population"]["unclassified"] == 1


class TestBridgeVsStrategySplit:
    def test_entry_slippage_is_signed_so_positive_is_always_WORSE(self):
        # long filled ABOVE the ticketed entry = worse
        g = tq.classify_exit(_fill(entry=101.0), _ticket(entry=100.0),
                             tolerance_bps=15)
        assert g["entry_slip_bps"] == pytest.approx(100.0)
        # short filled BELOW the ticketed entry = worse
        tk = _ticket(entry=100.0, sl=105.0, tp=90.0, direction="short")
        g = tq.classify_exit(_fill(entry=99.0, direction="short", exit_price=105.0,
                                   pnl=-60.0), tk, tolerance_bps=15)
        assert g["entry_slip_bps"] == pytest.approx(100.0)

    def test_a_faithful_fill_scores_near_zero_slippage(self):
        g = tq.classify_exit(_fill(entry=100.0), _ticket(entry=100.0),
                             tolerance_bps=15)
        assert g["entry_slip_bps"] == pytest.approx(0.0)

    def test_the_two_halves_are_reported_separately(self):
        res = tq.review([_fill()], [_ticket()], tolerance_bps=15)
        assert set(res) >= {"bridge", "strategy", "population"}
        assert "entry_slip_bps_median" in res["bridge"]
        assert "by_exit_state" in res["strategy"]
        # A bridge number must never appear inside the strategy block.
        assert "entry_slip_bps_median" not in res["strategy"]


class TestTolerance:
    def test_a_row_near_the_boundary_is_flagged(self):
        # 20 bps from the stop: outside a 15bps tolerance, inside 2x it.
        g = tq.classify_exit(_fill(exit_price=95.19, pnl=-48.0), _ticket(),
                             tolerance_bps=15)
        assert g["state"] != tq.AT_STOP
        assert g["near_boundary"] is True

    def test_a_row_far_from_every_level_is_not_flagged(self):
        g = tq.classify_exit(_fill(exit_price=104.0, pnl=40.0), _ticket(),
                             tolerance_bps=15)
        assert g["near_boundary"] is False

    def test_the_tolerance_used_is_reported(self):
        assert tq.review([_fill()], [_ticket()], tolerance_bps=42.0)[
            "tolerance_bps"] == 42.0

    def test_widening_the_tolerance_can_move_a_row_and_that_is_visible(self):
        f = _fill(exit_price=95.19, pnl=-48.0)
        assert tq.classify_exit(f, _ticket(), tolerance_bps=15)["state"] != tq.AT_STOP
        assert tq.classify_exit(f, _ticket(), tolerance_bps=40)["state"] == tq.AT_STOP


class TestPopulation:
    def test_only_CLOSED_fills_are_reviewed(self):
        fills = [_fill(fid=1, status="open", exit_price=None, pnl=None),
                 _fill(fid=2, status="closed")]
        res = tq.review(fills, [_ticket()], tolerance_bps=15)
        assert res["population"]["fills_seen"] == 2
        assert res["population"]["closed"] == 1

    def test_the_render_warns_on_a_small_denominator(self):
        res = tq.review([_fill()], [_ticket()], tolerance_bps=15)
        out = tq.render(res, "breakout_1")
        assert "n = 1" in out and "small denominator" in out

    def test_the_render_states_the_population_and_the_tolerance(self):
        out = tq.render(tq.review([_fill()], [_ticket()], tolerance_bps=15),
                        "breakout_1")
        assert "Population:" in out and "tolerance 15 bps" in out


class TestLiveShapeReproduces:
    """The 2026-08-23 hand review, as a fixture: 9 stops, 1 target, 2 manual."""

    def test_the_recorded_split_reproduces(self):
        tickets, fills = [], []
        for i in range(9):
            tickets.append(_ticket(tid=f"s{i}"))
            fills.append(_fill(tid=f"s{i}", exit_price=95.0, pnl=-50.0, fid=i))
        tickets.append(_ticket(tid="tp1"))
        fills.append(_fill(tid="tp1", exit_price=110.0, pnl=100.0, fid=90))
        for i in range(2):
            tickets.append(_ticket(tid=f"m{i}"))
            fills.append(_fill(tid=f"m{i}", exit_price=103.0, pnl=30.0, fid=100 + i))
        fills.append(_fill(tid=None, exit_price=101.0, pnl=5.0, fid=200))

        res = tq.review(fills, tickets, tolerance_bps=15)
        st = res["strategy"]["by_exit_state"]
        assert st[tq.AT_STOP] == 9
        assert st[tq.AT_TARGET] == 1
        assert st[tq.MANUAL_IN_PROFIT] == 2
        assert st[tq.UNCLASSIFIED_NO_TICKET] == 1
        assert res["population"]["closed"] == 13
