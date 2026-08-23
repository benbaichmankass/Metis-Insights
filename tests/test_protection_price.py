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


class TestEveryVenueExposesPrices:
    """Criterion 5 of BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND: the row
    was filed on IB and left Alpaca and Bybit explicitly unchecked. All three
    now expose prices, and all three grade through the SAME module — no venue
    gets its own definition of 'diverges'."""

    def test_bybit_every_return_path_carries_stop_prices(self):
        """Including the flat one. A path that omits the key makes 'no price'
        indistinguishable from 'we never looked at this branch'."""
        import inspect

        from src.runtime import order_monitor
        src = inspect.getsource(order_monitor._bybit_position_protection)
        assert src.count('"stop_prices"') == 3

    def test_bybit_full_mode_no_longer_grades_on_the_STRING_alone(self):
        """Any non-empty, non-'0' stopLoss graded FULLY covered — and that
        branch governs mainnet bybit_2, a larger blast radius than the paper
        ib_paper the row was filed on.

        Asserted BEHAVIOURALLY. An earlier version of this test pinned the
        source text `_coerce_float(pos_sl)`, and broke the moment that line was
        improved into a named helper — a test that fails on a refactor of
        correct code is noise, and one that PASSES on broken code (which the
        source-only tests did, over a NameError) is worse.
        """
        from src.runtime.order_monitor import _bybit_sl_leg_trigger
        assert _bybit_sl_leg_trigger({"price": "7516.5"}) == 7516.5
        assert _bybit_sl_leg_trigger({"price": "banana"}) is None
        assert _bybit_sl_leg_trigger({"price": "0"}) is None

    def test_alpaca_protection_state_carries_prices(self):
        import inspect

        from src.units.accounts.alpaca_client import AlpacaClient
        src = inspect.getsource(AlpacaClient.protection_state)
        assert '"stop_prices"' in src and '"target_prices"' in src

    def test_alpaca_stop_limit_reports_its_TRIGGER(self):
        from src.units.accounts.alpaca_client import AlpacaClient
        o = {"stop_price": "101.5", "limit_price": "101.0"}
        assert AlpacaClient._leg_price(o, "stop") == 101.5
        assert AlpacaClient._leg_price(o, "target") == 101.0

    def test_alpaca_a_trailing_stop_with_no_absolute_price_is_None(self):
        """None, never 0.0 — a zero would grade as catastrophic divergence."""
        from src.units.accounts.alpaca_client import AlpacaClient
        assert AlpacaClient._leg_price({"stop_price": None}, "stop") is None
        assert AlpacaClient._leg_price({"stop_price": ""}, "stop") is None
        assert AlpacaClient._leg_price({"stop_price": "0"}, "stop") is None

    def test_all_three_venues_would_grade_the_same_gap_identically(self):
        """The point of the shared module: one definition, three callers."""
        from src.runtime.protection_price import (
            PRICE_DIVERGES, grade_protection_price,
        )
        for venue_prices in ([7516.5], [7516.5], [7516.5]):
            g = grade_protection_price(
                declared=7533.69642857, resting_prices=venue_prices,
                direction="long", tick_size=0.25,
            )
            assert g["state"] == PRICE_DIVERGES
            assert g["ticks"] == pytest.approx(68.79, abs=0.01)


class TestBybitPathActuallyRuns:
    """⚠️ The class above inspects SOURCE TEXT, which cannot catch a NameError.

    It didn't: the first cut of the Bybit change referenced `_coerce_float`
    (not defined in that module) and `leg` (not the loop variable), and every
    source-inspection test still passed. Ruff caught it. These EXECUTE the path.
    """

    def _client(self, pos, legs):
        class _C:
            def get_positions(self, **kw):
                return {"result": {"list": [pos]}} if pos else {"result": {"list": []}}

            def get_open_orders(self, **kw):
                return {"result": {"list": legs}}
        return _C()

    def test_full_mode_returns_the_position_level_stop_price(self):
        from src.runtime.order_monitor import _bybit_position_protection
        out = _bybit_position_protection(
            self._client({"size": "10", "side": "Buy", "stopLoss": "95.5"}, []),
            "linear", "SOLUSDT",
        )
        assert out["source"] == "full_position_stop"
        assert out["covered_qty"] == 10.0          # quantity verdict unchanged
        assert out["stop_prices"] == [95.5]        # and now the price too

    def test_full_mode_with_an_unparseable_stop_yields_NO_price_not_zero(self):
        from src.runtime.order_monitor import _bybit_position_protection
        out = _bybit_position_protection(
            self._client({"size": "10", "side": "Buy", "stopLoss": "banana"}, []),
            "linear", "SOLUSDT",
        )
        # "banana" is non-empty and not "0", so the LEGACY string test still
        # grades it covered — the quantity verdict is deliberately unchanged.
        assert out["covered_qty"] == 10.0
        assert out["stop_prices"] == []            # never [0.0]

    def test_partial_mode_sums_qty_and_collects_each_leg_trigger(self):
        from src.runtime.order_monitor import _bybit_position_protection
        legs = [
            {"stopOrderType": "StopLoss", "qty": "4", "triggerPrice": "95.5",
             "orderId": "a"},
            {"stopOrderType": "StopLoss", "qty": "6", "triggerPrice": "94.0",
             "orderId": "b"},
        ]
        out = _bybit_position_protection(
            self._client({"size": "10", "side": "Buy", "stopLoss": ""}, legs),
            "linear", "SOLUSDT",
        )
        assert out["source"] == "partial_sl_legs"
        assert out["covered_qty"] == 10.0
        assert out["stop_prices"] == [94.0, 95.5]   # sorted

    def test_a_flat_position_still_carries_the_key(self):
        from src.runtime.order_monitor import _bybit_position_protection
        out = _bybit_position_protection(self._client(None, []), "linear", "X")
        assert out["source"] == "flat"
        assert out["stop_prices"] == []

    def test_the_prices_feed_the_shared_grader_end_to_end(self):
        from src.runtime.order_monitor import _bybit_position_protection
        from src.runtime.protection_price import (
            PRICE_DIVERGES, grade_protection_price,
        )
        out = _bybit_position_protection(
            self._client({"size": "10", "side": "Buy", "stopLoss": "90.0"}, []),
            "linear", "SOLUSDT",
        )
        g = grade_protection_price(
            declared=95.5, resting_prices=out["stop_prices"],
            direction="long", tick_size=0.01,
        )
        assert g["state"] == PRICE_DIVERGES
        assert g["exposure"] == "more_exposed"


# ---------------------------------------------------------------------------
# An UNREADABLE direction earns no exposure verdict (2026-08-23).
#
# `exposure` inverts on direction: a stop BELOW its declared level is
# more_exposed for a long and exits_earlier for a short. An earlier draft of
# this module resolved direction with
#
#     is_long = str(direction or "").lower() in ("long", "buy")
#
# which is a two-state read of a three-state fact: an absent, empty or
# unrecognised direction fell through to False and was graded as a SHORT. The
# label was then confidently backwards for every long whose direction the
# caller failed to supply — the diagnostic-provenance sub-class A shape, where
# the accessor does not compute what the label says and nothing in the output
# reveals the substitution.
#
# Found by a test fixture that omitted the key, not by reading the code.
# ---------------------------------------------------------------------------
class TestDirectionIsThreeState:
    def _below(self, direction):
        return grade(
            declared=7533.696429, resting_prices=[7516.5],
            direction=direction, side="stop", tick_size=0.25)

    @pytest.mark.parametrize("d", ["long", "buy", "LONG", " Buy "])
    def test_long_spellings_grade_a_lower_stop_more_exposed(self, d):
        v = self._below(d)
        assert v["direction_known"] is True
        assert v["exposure"] == "more_exposed"

    @pytest.mark.parametrize("d", ["short", "sell", "SHORT", " Sell "])
    def test_short_spellings_grade_the_same_stop_exits_earlier(self, d):
        v = self._below(d)
        assert v["direction_known"] is True
        assert v["exposure"] == "exits_earlier"

    @pytest.mark.parametrize("d", [None, "", "   ", "flat", "b", 0, 1])
    def test_unreadable_direction_earns_NO_exposure_verdict(self, d):
        """Not 'exits_earlier'. Not 'more_exposed'. None."""
        v = self._below(d)
        assert v["direction_known"] is False
        assert v["exposure"] is None

    def test_the_geometry_survives_an_unreadable_direction(self):
        """`side_of_declared` needs no direction, so it must still be published.

        Suppressing the whole verdict would be the opposite error: the
        divergence is real and measurable regardless of which way the trade
        faces. Only its CONSEQUENCE is unknown.
        """
        v = self._below(None)
        assert v["state"] == PRICE_DIVERGES
        assert v["side_of_declared"] == "below"
        assert round(v["ticks"]) == 69

    def test_direction_known_is_present_on_every_early_return(self):
        """A key that appears only on the happy path is one a consumer cannot
        rely on — `.get()` would silently return None, which is neither True
        nor False and reads as 'unknown' for a verdict that never ran."""
        for kwargs in (
            dict(declared=None, resting_prices=[1.0]),          # no_declared
            dict(declared=1.0, resting_prices=None),            # no_resting
            dict(declared=1.0, resting_prices=[]),              # no_resting_leg
            dict(declared=1.0, resting_prices=[1.5], tick_size=None),  # no_tick
        ):
            v = grade(direction="long", side="stop", **kwargs)
            assert "direction_known" in v
            assert "exposure" in v

    def test_a_TARGET_never_carries_an_exposure_verdict(self):
        """Exposure is a stop-side concept: a target sitting off its declared
        level is a missed exit, not un-agreed risk. Reusing the word there
        would make two different conditions read as one."""
        v = grade(
            declared=8390.59, resting_prices=[8500.0], direction="long",
            side="target", tick_size=0.25)
        assert v["state"] == PRICE_DIVERGES
        assert v["exposure"] is None
        assert v["direction_known"] is True
