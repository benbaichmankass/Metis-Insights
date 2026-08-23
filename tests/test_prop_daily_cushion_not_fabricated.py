"""A missing ``realized_today`` must not become a full daily-loss cushion.

MEASURED 2026-08-23 on the live prop account (breakout_1), snapshot id 13:

    realized_today = None      <- never reported by anyone
    unrealized     = 0.0       <- correctly reported; the book was flat

``compute_rule_distance`` guarded with ``if realized_today is not None or
unrealized is not None``, so ONE non-None term let it through, and then
``(realized_today or 0.0)`` turned *"we did not look"* into *"nothing was
realized today"*. day_pnl came out 0.0, daily_loss_used 0.0, and the panel
published a FULL $142.92 daily cushion.

The same database held two closed prop fills for that day totalling
**-$218.79** -- 1.53x the daily limit -- while the account sat **$64** above
its static DD floor. The failure direction is the dangerous one: it reports
MORE cushion than exists on an account-killer limit.

The sum of an unknown and a known is unknown. Both terms are required.
"""
from __future__ import annotations

import pytest

from src.prop import prop_reconcile


def _rd(monkeypatch, **status):
    """Compute rule-distance for the real breakout ruleset off one snapshot.

    ``compute_rule_distance`` takes an account_id STRING (not a dict) and
    resolves the ruleset itself from config/prop_rulesets/. Passing a dict
    makes `_ruleset_for` raise `unhashable type: 'dict'`, every limit resolve
    to None, and the None-assertions below pass for entirely the wrong reason —
    so this fixture asserts the ruleset actually loaded.
    """
    base = {"balance": 4764.0, "equity": 4764.0, "day_start_balance": None}
    base.update(status)
    rd = prop_reconcile.compute_rule_distance("breakout_1", base)
    assert rd.get("daily_loss_limit_usd") is not None, (
        "the breakout ruleset did not resolve — these tests would then pass "
        "vacuously, since every derived value would be None regardless"
    )
    return rd


def test_the_measured_case_does_not_publish_a_cushion(monkeypatch):
    """THE REGRESSION: realized unknown, unrealized 0.0 -> NOT a full cushion."""
    rd = _rd(monkeypatch, realized_today=None, unrealized=0.0)
    assert rd["day_pnl"] is None, (
        "a missing realized_today must not be summed as zero -- that is what "
        "published a full $142.92 cushion over -$218.79 of recorded losses"
    )
    assert rd["daily_loss_used_usd"] is None
    assert rd["distance_to_daily_loss_usd"] is None
    assert rd["day_pnl_state"] == "realized_unreported"


def test_a_genuinely_reported_zero_still_publishes_a_cushion(monkeypatch):
    """The honest-null must not swallow a real 'no loss today' report."""
    rd = _rd(monkeypatch, realized_today=0.0, unrealized=0.0)
    assert rd["day_pnl"] == 0.0
    assert rd["daily_loss_used_usd"] == 0.0
    assert rd["distance_to_daily_loss_usd"] == pytest.approx(0.03 * 4764.0)
    assert rd["day_pnl_state"] == "measured"


def test_a_reported_loss_consumes_the_cushion(monkeypatch):
    rd = _rd(monkeypatch, realized_today=-218.79, unrealized=0.0)
    assert rd["day_pnl"] == pytest.approx(-218.79)
    assert rd["daily_loss_used_usd"] == pytest.approx(218.79)
    # Past the limit -> the distance goes NEGATIVE rather than clamping at 0,
    # so a breach is visible as a breach.
    assert rd["distance_to_daily_loss_usd"] < 0


def test_neither_term_reported_is_its_own_state(monkeypatch):
    rd = _rd(monkeypatch, realized_today=None, unrealized=None)
    assert rd["day_pnl"] is None
    assert rd["day_pnl_state"] == "unreported"


def test_missing_unrealized_is_also_unknown_not_zero(monkeypatch):
    """Symmetric: the sum of a known and an unknown is still unknown."""
    rd = _rd(monkeypatch, realized_today=-50.0, unrealized=None)
    assert rd["day_pnl"] is None
    assert rd["day_pnl_state"] == "unrealized_unreported"


def test_the_dd_floor_is_unaffected_by_the_daily_loss_change(monkeypatch):
    """The static DD floor is balance-based and must keep working regardless.

    It is the limit that binds while FLAT, so losing it to this change would
    trade one blind spot for another.
    """
    rd = _rd(monkeypatch, realized_today=None, unrealized=0.0)
    assert rd["static_dd_floor_usd"] == pytest.approx(4700.0)
    assert rd["distance_to_dd_floor_usd"] == pytest.approx(64.0)
