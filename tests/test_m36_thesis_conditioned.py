"""M36 Track C · C4 — tests for the conditioned-lifecycle exit + equity/maxDD.

Pure, offline, injected synthetic price paths. Verifies the conditioned exit
drives the shipped C2/C3 functions correctly: it exits EARLY when the move
reaches target before horizon (and overshoot), never LATER than hold-to-horizon,
and the crowding fold only ever advances an exit (reductive-only). Plus the
equity/maxDD risk helper.
"""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis_backtest import equity_and_maxdd
from src.units.strategies.macro_thesis.thesis_conditioned import conditioned_exit_on_path


def _path(*closes, start="2020-01-02"):
    """Ascending [(date, close)] on consecutive calendar days from ``start``."""
    import datetime as _dt

    d0 = _dt.date.fromisoformat(start)
    return [((d0 + _dt.timedelta(days=i)).isoformat(), float(c)) for i, c in enumerate(closes)]


# --------------------------------------------------------------------------- #
# conditioned exit — the C2/C3 lifecycle over a path
# --------------------------------------------------------------------------- #
def test_long_exits_early_when_target_reached_before_horizon():
    # entry 100, target = 100*(1+0.03)=103. Path hits 103 on day 3 of a 30d horizon.
    path = _path(101, 102, 103, 104, 105)  # 5 days << 30d horizon
    res = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.03, use_crowding=False,
    )
    assert res["exit_price"] == 103.0          # exited AT target, not at horizon
    assert res["exit_index"] == 2              # 3rd path day (0-based)
    assert "advance exit" in res["exit_reason"]
    assert res["hold_days"] < 30.0             # earlier than horizon


def test_no_trigger_holds_to_horizon():
    # move never reaches the 5% target → falls through to the last path close.
    path = _path(100.5, 101.0, 100.8, 101.2)
    res = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.05, use_crowding=False,
    )
    assert res["exit_price"] == 101.2          # last path close = hold-to-horizon
    assert res["exit_index"] == len(path) - 1
    assert "held to horizon" in res["exit_reason"]


def test_overshoot_triggers_exit():
    # target 102 (2%), but day-1 gaps to 108 → move_progress = 4.0 >= overshoot 1.25.
    path = _path(108, 109)
    res = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.02, use_crowding=False,
    )
    assert res["exit_index"] == 0
    assert "overshoot" in res["exit_reason"]


def test_short_direction_is_sign_correct():
    # short: entry 100, expected 2% DOWN → target 98. Price falls to 98 on day 2.
    path = _path(99.5, 98.0, 97.0)
    res = conditioned_exit_on_path(
        thesis_id="t", direction="short", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.02, use_crowding=False,
    )
    assert res["exit_price"] == 98.0
    assert "advance exit" in res["exit_reason"]


def test_crowding_advances_exit_at_least_as_early():
    # A move that reaches ~0.8 progress (below target 1.0) but is crowded (extension
    # high) → crowding upgrades the near-target hold to a trim; without crowding it
    # would only exit at/after target. Crowded exit index <= plain exit index.
    path = _path(101.6, 101.6, 101.6, 103.0)  # 0.8 progress for 3 days, then target
    plain = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.02, use_crowding=False,
    )
    crowded = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.02, use_crowding=True,
    )
    # expected_move_pct 0.02 → target 102; 101.6 is 0.8 progress (>= near_target 0.7).
    assert crowded["exit_index"] <= plain["exit_index"]


def test_empty_path_or_bad_entry_returns_none():
    assert conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=[], horizon_days=30.0, expected_move_pct=0.02) is None
    assert conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=0.0, as_of="2020-01-01",
        path=_path(101), horizon_days=30.0, expected_move_pct=0.02) is None


def test_conditioned_never_exits_later_than_horizon():
    # Whatever the params, the exit index is bounded by the path length.
    path = _path(*[100 + i * 0.1 for i in range(10)])
    res = conditioned_exit_on_path(
        thesis_id="t", direction="long", entry_price=100.0, as_of="2020-01-01",
        path=path, horizon_days=30.0, expected_move_pct=0.5, use_crowding=True,
    )
    assert res["exit_index"] <= len(path) - 1


# --------------------------------------------------------------------------- #
# equity + max drawdown
# --------------------------------------------------------------------------- #
def test_equity_maxdd_basic():
    outs = [{"net_return": 0.10}, {"net_return": -0.30}, {"net_return": 0.05}]
    r = equity_and_maxdd(outs)
    assert r["n"] == 3
    assert abs(r["final_equity"] - (-0.15)) < 1e-9
    assert abs(r["peak_equity"] - 0.10) < 1e-9
    # peak 0.10 → trough 0.10-0.30=-0.20 → maxDD 0.30
    assert abs(r["max_drawdown"] - 0.30) < 1e-9


def test_equity_maxdd_monotone_up_is_zero_dd():
    r = equity_and_maxdd([{"net_return": 0.02}, {"net_return": 0.03}])
    assert abs(r["max_drawdown"] - 0.0) < 1e-9


def test_equity_maxdd_empty_is_honest_null():
    r = equity_and_maxdd([])
    assert r["n"] == 0 and r["max_drawdown"] is None and r["final_equity"] is None
