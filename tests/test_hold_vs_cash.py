"""`src/runtime/hold_vs_cash.py` — does this position still earn the risk it occupies?

The bias these tests exist to pin: every prior lever returned "hold" because it
compared holding against NOTHING. The alternative is CASH — zero return, zero
variance — so a position does not have to be expected to lose to be worse than
it. And an input we could not read must never resolve to "hold", because being
unable to grade a position is otherwise the cheapest way to keep it open.
"""
import pytest

from src.runtime import hold_vs_cash as hvc


# --- the risk pricing -------------------------------------------------------

@pytest.mark.parametrize("tgt,stop,want", [
    (1.0, 1.0, 0.5),        # symmetric -> a coin flip is the bar
    (2.0, 1.0, 1 / 3),      # 2:1 upside -> only 33% needed
    (0.5, 1.0, 2 / 3),      # upside halved -> the bar jumps to 67%
    (0.71, 1.0, 0.584795),  # the live XRP geometry
])
def test_breakeven_rises_as_upside_shrinks(tgt, stop, want):
    """This IS the risk pricing — no volatility model, just the trade's levels."""
    assert hvc.breakeven_p(r_to_target=tgt, r_to_stop=stop) == pytest.approx(want, abs=1e-5)


def test_breakeven_is_none_not_zero_when_there_is_nothing_to_price():
    """0.0 would read as 'holding is free', which is the opposite of unknown."""
    assert hvc.breakeven_p(r_to_target=0.0, r_to_stop=0.0) is None
    assert hvc.breakeven_p(r_to_target=-1.0, r_to_stop=0.5) is None


def test_breakeven_is_monotonic_in_remaining_upside():
    prev = 1.1
    for tgt in (0.1, 0.25, 0.5, 1.0, 2.0, 4.0):
        p = hvc.breakeven_p(r_to_target=tgt, r_to_stop=1.0)
        assert p < prev, "required hit rate must fall as upside grows"
        prev = p


# --- unmeasured is never hold -----------------------------------------------

def test_absent_hit_rate_is_unmeasured_not_hold():
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0)
    assert v.state == hvc.STATE_UNMEASURED
    assert v.should_liquidate is False
    # ...but the geometry it COULD compute is still reported, so the reader can
    # see what would be required once the hit rate is measured.
    assert v.breakeven_p == pytest.approx(1 / 3)


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan"), float("inf")])
def test_unreadable_hit_rate_is_unmeasured(bad):
    assert hvc.evaluate(r_to_target=2.0, r_to_stop=1.0,
                        observed_p=bad).state == hvc.STATE_UNMEASURED


@pytest.mark.parametrize("bad", [-0.1, 1.5, 2.0])
def test_out_of_range_hit_rate_is_unmeasured_not_clamped(bad):
    """Clamping would turn a caller's bug into a confident verdict."""
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=bad)
    assert v.state == hvc.STATE_UNMEASURED and v.reason == "hit_rate_out_of_range"


def test_unmeasured_and_ungradeable_are_different_facts():
    """One is a missing input; the other is a position with no expressible R:R."""
    missing = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0)
    broken = hvc.evaluate(r_to_target=2.0, r_to_stop=0.0, observed_p=0.9)
    assert missing.state == hvc.STATE_UNMEASURED
    assert broken.state == hvc.STATE_UNGRADEABLE
    assert missing.state != broken.state


# --- the decision -----------------------------------------------------------

def test_holds_when_observed_clears_the_required_rate():
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.50)
    assert v.state == hvc.STATE_HOLD
    assert v.edge_p == pytest.approx(0.50 - 1 / 3)


def test_liquidates_when_the_required_rate_is_not_met():
    v = hvc.evaluate(r_to_target=0.71, r_to_stop=1.0, observed_p=0.40)
    assert v.state == hvc.STATE_LIQUIDATE and v.should_liquidate is True
    assert "0.585" in v.reason and "0.400" in v.reason


def test_exactly_breakeven_liquidates():
    """Paying variance for a zero edge is worse than cash, which has none."""
    v = hvc.evaluate(r_to_target=1.0, r_to_stop=1.0, observed_p=0.5)
    assert v.state == hvc.STATE_LIQUIDATE
    assert v.edge_p == pytest.approx(0.0)


def test_a_positive_edge_can_still_lose_the_capital_test():
    """The grind case: likely to reach target, still the wrong place for money."""
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.90,
                     open_r=3.0, bars_held=240, bars_per_day=12,
                     redeploy_r_per_day=0.50)
    assert v.state == hvc.STATE_LIQUIDATE
    assert "r_per_day" in v.reason
    assert v.edge_p > 0, "the probability edge is positive; capital is what fails"


def test_the_two_tests_are_separate_and_named_separately():
    same = dict(r_to_target=2.0, r_to_stop=1.0, open_r=3.0,
                bars_held=240, bars_per_day=12)
    prob_fail = hvc.evaluate(**same, observed_p=0.10, redeploy_r_per_day=0.0)
    cap_fail = hvc.evaluate(**same, observed_p=0.90, redeploy_r_per_day=0.50)
    assert prob_fail.state == cap_fail.state == hvc.STATE_LIQUIDATE
    assert prob_fail.reason != cap_fail.reason


def test_absent_redeploy_rate_does_not_decide_the_capital_test():
    """Reported, not used — an unsupplied alternative cannot condemn a trade."""
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.90,
                     open_r=0.01, bars_held=2400, bars_per_day=12)
    assert v.state == hvc.STATE_HOLD
    assert v.r_per_day is not None and v.decays_below is None


# --- time axis --------------------------------------------------------------

def test_r_per_day_is_computed_from_supplied_bars():
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.9,
                     open_r=3.0, bars_held=240, bars_per_day=12)
    assert v.r_per_day == pytest.approx(3.0 / 20.0)


@pytest.mark.parametrize("kw", [
    {"open_r": 3.0, "bars_held": 240},                       # no bars_per_day
    {"open_r": 3.0, "bars_per_day": 12},                     # no bars_held
    {"bars_held": 240, "bars_per_day": 12},                  # no open_r
    {"open_r": 3.0, "bars_held": 0, "bars_per_day": 12},     # zero elapsed
])
def test_r_per_day_is_none_when_it_cannot_be_computed(kw):
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.9, **kw)
    assert v.r_per_day is None


# --- degenerate positions ---------------------------------------------------

def test_flat_position_is_not_applicable():
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.9, qty=0)
    assert v.state == hvc.STATE_NOT_APPLICABLE


@pytest.mark.parametrize("tgt,stop,reason", [
    (2.0, 0.0, "stop_at_or_through_price"),
    (2.0, -0.5, "stop_at_or_through_price"),
    (0.0, 1.0, "target_behind_price"),
    (-1.0, 1.0, "target_behind_price"),
])
def test_broken_geometry_is_ungradeable_never_hold(tgt, stop, reason):
    v = hvc.evaluate(r_to_target=tgt, r_to_stop=stop, observed_p=0.99)
    assert v.state == hvc.STATE_UNGRADEABLE and v.reason == reason
    assert v.should_liquidate is False


def test_verdicts_carry_their_inputs():
    v = hvc.evaluate(r_to_target=2.0, r_to_stop=1.0, observed_p=0.9, open_r=3.0)
    assert v.inputs["r_to_target"] == 2.0 and v.inputs["open_r"] == 3.0


def test_no_order_path_caller():
    """Reading this verdict to CLOSE a position is Tier-3 and needs evidence."""
    import ast
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    hits = []
    for p in list((repo / "src/units").rglob("*.py")) + \
            list((repo / "src/core").rglob("*.py")) + [repo / "src/main.py"]:
        if not p.exists() or "hold_vs_cash" not in p.read_text():
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            names = ([a.name for a in n.names] if isinstance(n, ast.Import)
                     else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
            if any("hold_vs_cash" in x for x in names):
                hits.append(str(p.relative_to(repo)))
    assert not hits, f"hold_vs_cash gained an order-path caller: {hits}"
