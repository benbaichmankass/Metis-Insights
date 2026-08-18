"""Tests for the exit-path leg-coverage assessor.

Each case is a shape MEASURED on the live journal on 2026-08-18
(BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG), so a regression here is a
regression against something that actually happened, not an invented scenario.
"""
from __future__ import annotations

from src.runtime.package_leg_coverage import VERDICTS, assess, summarize


def _t(tid, account, pkg, sl, qty=1.0):
    return {"id": tid, "account_id": account, "order_package_id": pkg,
            "stop_loss": sl, "position_size": qty, "symbol": "X",
            "direction": "long", "strategy_name": "s"}


def _p(pkg, status, linked, sl=None, close_reason=None):
    return {"order_package_id": pkg, "status": status, "linked_trade_id": linked,
            "sl": sl, "close_reason": close_reason, "strategy_name": "s",
            "symbol": "X"}


def test_divergent_sibling_stops_are_flagged():
    """pkg-7cb8577792ca4006 (XRP): the linked leg trailed, the mirror did not."""
    trades = [_t(4163, "bybit_2", "P", 1.04193571),
              _t(4164, "bybit_portfolio", "P", 1.10786786)]
    v = assess(trades, {"P": _p("P", "open", 4163, sl=1.04193571)})
    assert v["P"]["verdict"] == "divergent"
    linked = [leg for leg in v["P"]["legs"] if leg["is_linked"]]
    assert [leg["trade_id"] for leg in linked] == [4163]


def test_open_legs_under_a_closed_package_are_stranded():
    """pkg-830fb965b6db48ff: exit_head closed the linked leg; two survived."""
    trades = [_t(4717, "bybit_1", "P", 63472.79),
              _t(4719, "bybit_portfolio", "P", 63472.79)]
    v = assess(trades, {"P": _p("P", "closed", 4718, close_reason="exit_head")})
    assert v["P"]["verdict"] == "stranded"
    assert v["P"]["leg_count"] == 2


def test_stranded_wins_over_agreeing_stops():
    """A closed package is stranded even when its surviving stops AGREE.

    The stops matching is irrelevant once the loop can no longer select the
    package — grading this `managed` is the collapse that hides the whole class.
    """
    trades = [_t(1, "a", "P", 10.0), _t(2, "b", "P", 10.0)]
    v = assess(trades, {"P": _p("P", "closed", 3, close_reason="sl_cross")})
    assert v["P"]["verdict"] == "stranded"


def test_multi_leg_with_agreeing_stops_is_managed():
    trades = [_t(4652, "alpaca_paper", "P", 698.01),
              _t(4653, "alpaca_portfolio", "P", 698.01)]
    v = assess(trades, {"P": _p("P", "open", 4652, sl=698.01)})
    assert v["P"]["verdict"] == "managed"


def test_single_leg_package_is_managed():
    v = assess([_t(4722, "alpaca_portfolio", "P", 127.77)],
               {"P": _p("P", "open", 4722, sl=127.77)})
    assert v["P"]["verdict"] == "managed"


def test_unresolvable_linked_leg_is_not_managed():
    """`we could not identify the managed leg` must never read as `managed`."""
    v = assess([_t(1, "a", "P", 1.0)], {"P": _p("P", "open", 999, sl=1.0)})
    assert v["P"]["verdict"] == "linked_unresolvable"


def test_missing_package_row_is_not_managed():
    v = assess([_t(1, "a", "P", 1.0)], {})
    assert v["P"]["verdict"] == "linked_unresolvable"


def test_trade_with_no_package_is_not_managed():
    """A loop that iterates packages cannot reach a trade that has none."""
    v = assess([_t(1, "a", None, 1.0)], {})
    assert v["(no package)"]["verdict"] == "linked_unresolvable"


def test_a_null_stop_does_not_fabricate_agreement():
    """One leg with SL=None must not be silently treated as matching."""
    trades = [_t(1, "a", "P", 10.0), _t(2, "b", "P", None)]
    v = assess(trades, {"P": _p("P", "open", 1, sl=10.0)})
    assert v["P"]["stop_unmeasured_legs"] is True


def test_summary_counts_legs_not_just_packages():
    """The headline is LEGS stranded; a package count alone understates it."""
    trades = [_t(1, "a", "P", 1.0), _t(2, "b", "P", 1.0), _t(3, "c", "Q", 2.0)]
    pkgs = {"P": _p("P", "closed", 9, close_reason="x"),
            "Q": _p("Q", "open", 3, sl=2.0)}
    sm = summarize(assess(trades, pkgs))
    assert sm["stranded_legs"] == 2
    assert sm["by_verdict"]["stranded"] == 1
    assert sm["open_legs"] == 3


def test_every_declared_verdict_is_reachable():
    """No declared state may be unreachable — an unreachable state is a lie."""
    trades = [_t(1, "a", "P1", 1.0), _t(2, "b", "P1", 2.0),      # divergent
              _t(3, "c", "P2", 1.0),                              # stranded
              _t(4, "d", "P3", 1.0),                              # managed
              _t(5, "e", "P4", 1.0)]                              # unresolvable
    pkgs = {"P1": _p("P1", "open", 1, sl=1.0),
            "P2": _p("P2", "closed", 9, close_reason="x"),
            "P3": _p("P3", "open", 4, sl=1.0),
            "P4": _p("P4", "open", 777, sl=1.0)}
    seen = {row["verdict"] for row in assess(trades, pkgs).values()}
    assert seen == set(VERDICTS)
