"""Protection price grading — BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND.

The controls that matter keep three different "no answer" reasons apart, and
stop a direction from being read as a magnitude.
"""
from __future__ import annotations

import pytest

from src.runtime.protection_price import (
    PRICE_AGREES, PRICE_DIVERGES, PRICE_NO_DECLARED, PRICE_NO_LEG,
    PRICE_NO_RESTING, PRICE_NO_TICK_SIZE, grade_protection_price as grade,
)

MES_TICK = 0.25


class TestTheLiveCase:
    def test_MES_4350_reproduces(self):
        """Journal 7533.696429 vs resting 7516.5 — 68.8 ticks, more exposed."""
        g = grade(declared=7533.69642857, resting_prices=[7516.5],
                  direction="long", tick_size=MES_TICK)
        assert g["state"] == PRICE_DIVERGES
        assert g["ticks"] == pytest.approx(68.79, abs=0.01)
        assert g["diff"] == pytest.approx(-17.196, abs=0.001)
        assert g["side_of_declared"] == "below"
        assert g["exposure"] == "more_exposed"

    def test_the_dollar_gap_is_derivable_from_what_it_returns(self):
        g = grade(declared=7533.69642857, resting_prices=[7516.5],
                  direction="long", tick_size=MES_TICK)
        assert abs(g["diff"]) * 5.0 * 15 == pytest.approx(1289.73, abs=0.05)

    def test_a_leg_on_the_declared_level_agrees(self):
        g = grade(declared=7533.75, resting_prices=[7533.75],
                  direction="long", tick_size=MES_TICK)
        assert g["state"] == PRICE_AGREES and g["ticks"] == 0


class TestNoAnswerReasonsStayApart:
    def test_nothing_declared_is_not_agreement(self):
        g = grade(declared=None, resting_prices=[7516.5], direction="long",
                  tick_size=MES_TICK)
        assert g["state"] == PRICE_NO_DECLARED
        assert g["state"] != PRICE_AGREES

    def test_NO_leg_resting_is_a_naked_finding_not_a_price_one(self):
        """Double-counting one condition as two would inflate the finding count."""
        g = grade(declared=7533.75, resting_prices=[], direction="long",
                  tick_size=MES_TICK)
        assert g["state"] == PRICE_NO_LEG
        assert g["state"] != PRICE_DIVERGES

    def test_a_leg_with_no_readable_price_is_could_not_look(self):
        g = grade(declared=7533.75, resting_prices=[None, "", 0],
                  direction="long", tick_size=MES_TICK)
        assert g["state"] == PRICE_NO_RESTING
        assert g["state"] not in (PRICE_AGREES, PRICE_NO_LEG)

    def test_unreadable_legs_is_distinct_from_none_at_all(self):
        assert grade(declared=1.0, resting_prices=None, direction="long",
                     tick_size=1.0)["state"] == PRICE_NO_RESTING
        assert grade(declared=1.0, resting_prices=[], direction="long",
                     tick_size=1.0)["state"] == PRICE_NO_LEG

    def test_no_tick_size_refuses_to_guess_the_grid(self):
        g = grade(declared=7533.75, resting_prices=[7516.5], direction="long")
        assert g["state"] == PRICE_NO_TICK_SIZE
        assert g["ticks"] is None
        # the raw diff is still reported — we DID measure that much
        assert g["diff"] == pytest.approx(-17.25)


class TestDirectionIsNotAMagnitude:
    def test_a_long_stop_BELOW_declared_is_more_exposed(self):
        g = grade(declared=100.0, resting_prices=[95.0], direction="long",
                  tick_size=0.25)
        assert g["side_of_declared"] == "below" and g["exposure"] == "more_exposed"

    def test_a_long_stop_ABOVE_declared_exits_EARLIER_not_safer(self):
        g = grade(declared=100.0, resting_prices=[105.0], direction="long",
                  tick_size=0.25)
        assert g["side_of_declared"] == "above" and g["exposure"] == "exits_earlier"

    def test_a_SHORT_mirrors_it(self):
        assert grade(declared=100.0, resting_prices=[105.0], direction="short",
                     tick_size=0.25)["exposure"] == "more_exposed"
        assert grade(declared=100.0, resting_prices=[95.0], direction="short",
                     tick_size=0.25)["exposure"] == "exits_earlier"

    def test_two_equal_magnitudes_grade_to_OPPOSITE_exposures(self):
        """The whole reason direction is reported separately from size."""
        a = grade(declared=100.0, resting_prices=[95.0], direction="long",
                  tick_size=0.25)
        b = grade(declared=100.0, resting_prices=[105.0], direction="long",
                  tick_size=0.25)
        assert abs(a["diff"]) == abs(b["diff"])
        assert a["exposure"] != b["exposure"]


class TestNearestLegWins:
    def test_the_NEAREST_resting_price_is_the_one_compared(self):
        """With several legs the position is protected at the closest first."""
        g = grade(declared=100.0, resting_prices=[80.0, 99.9, 60.0],
                  direction="long", tick_size=0.25)
        assert g["nearest_resting"] == 99.9
        assert g["state"] == PRICE_AGREES  # 0.1 / 0.25 = 0.4 ticks
        assert g["resting_count"] == 3

    def test_tolerance_is_in_ticks(self):
        g = grade(declared=100.0, resting_prices=[100.25], direction="long",
                  tick_size=0.25, tick_tolerance=1.0)
        assert g["state"] == PRICE_AGREES
        g = grade(declared=100.0, resting_prices=[100.75], direction="long",
                  tick_size=0.25, tick_tolerance=1.0)
        assert g["state"] == PRICE_DIVERGES


class TestPurity:
    def test_it_never_raises(self):
        for bad in (None, "x", float("nan"), -1, 0):
            grade(declared=bad, resting_prices=[bad], direction=bad,
                  tick_size=bad)

    def test_it_imports_nothing_from_the_repo(self):
        import ast
        tree = ast.parse(open("src/runtime/protection_price.py",
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert not (getattr(node, "module", None) or "").startswith("src")


class TestSweepWiring:
    """The grader must actually be reachable from the live sweep, and the
    coverage read must carry the prices it needs."""

    def test_the_sweep_summary_declares_the_key_up_front(self):
        """Declared, not created on first use — 'we looked and found none' must
        not read as 'we did not look'."""
        import inspect

        from src.runtime import order_monitor
        src = inspect.getsource(order_monitor._check_broker_naked_ib_positions)
        assert '"stop_price_diverges": 0,' in src

    def test_the_sweep_grades_through_the_shared_module(self):
        """Not a second, drifting copy of the comparison."""
        import inspect

        from src.runtime import order_monitor
        src = inspect.getsource(order_monitor._check_broker_naked_ib_positions)
        assert "from src.runtime.protection_price import" in src
        assert "grade_protection_price" in src

    def test_the_sweep_never_repairs_a_divergence(self):
        """Detect-only. Choosing which leg to touch is what went wrong on
        2026-08-20; a re-arm or cancel here would repeat it."""
        import inspect

        from src.runtime import order_monitor
        src = inspect.getsource(order_monitor._check_broker_naked_ib_positions)
        block = src[src.index("STOP PRICE DIVERGES"):]
        block = block[:block.index("except Exception")]
        for forbidden in ("cancel", "modify_open_order", "place_protective",
                          "_attempt_naked_autoprotect"):
            assert forbidden not in block, forbidden

    def test_coverage_exposes_the_prices_the_grader_needs(self):
        import inspect

        from src.units.accounts.ib_client import IBClient
        src = inspect.getsource(IBClient._locked_protection_coverage)
        assert '"stop_prices"' in src and '"target_prices"' in src

    def test_a_stop_limit_reports_its_TRIGGER_not_its_limit(self):
        """A STP LMT carries both; auxPrice is the trigger, and comparing a
        declared stop against the limit would be the wrong price."""
        from src.units.accounts.ib_client import IBClient

        class _O:
            auxPrice = 7533.75
            lmtPrice = 7530.00
        assert IBClient._protective_leg_price(_O(), "stop") == 7533.75
        assert IBClient._protective_leg_price(_O(), "target") == 7530.00

    def test_an_unreadable_price_is_None_not_zero(self):
        """A zero would compare against a declared level as a catastrophic
        divergence when the truth is that we could not look."""
        from src.units.accounts.ib_client import IBClient

        class _O:
            auxPrice = 0.0
            lmtPrice = None
        assert IBClient._protective_leg_price(_O(), "stop") is None


class TestTickResolver:
    def test_it_resolves_the_live_futures(self):
        from src.core.profile_loader import tick_size_for
        assert tick_size_for("MES") == 0.25
        assert tick_size_for("MGC") == 0.1

    def test_an_unknown_symbol_REFUSES_rather_than_defaulting(self):
        """A wrong tick turns a real divergence into 'agrees' or the reverse."""
        from src.core.profile_loader import tick_size_for
        assert tick_size_for("NOT_A_SYMBOL") is None
        assert tick_size_for("") is None
