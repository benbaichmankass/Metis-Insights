"""Tests for the per-account prop ticket (src/prop/multi_account_ticket.py).

Locks the three render cases + the scalability contract:
  - 1 account  → single block, NO banner.
  - N accounts, identical legs → one block, "applies to: …".
  - N accounts, differing legs → loud discrepancy banner + one block per account.
And that a structurally-impossible leg is skipped, not raised.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.prop.account_rulesets import unit_for_account
from src.prop.breakout_ticket import BreakoutSignal
from src.prop.multi_account_ticket import (
    build_account_leg,
    build_account_legs,
    render_multi_account_ticket,
)

_NOW = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def _sig():
    return BreakoutSignal(
        strategy="trend_donchian", symbol="SOLUSDT", direction="long",
        entry=150.0, sl=144.0, tp=162.0, timeframe="2h",
        signal_time=datetime(2026, 6, 17, 11, 30, tzinfo=timezone.utc),
    )


def _prop_unit(aid, risk_pct):
    return unit_for_account(aid, {"exchange": "breakout", "account_class": "real_money",
                                  "risk": {"risk_pct": risk_pct}})


def test_single_account_no_banner():
    units = [_prop_unit("p1", 0.006)]
    out = render_multi_account_ticket(_sig(), build_account_legs(_sig(), units), now=_NOW)
    assert "ACCOUNTS DIFFER" not in out
    assert "applies to accounts" not in out
    assert "BREAKOUT TRADE SETUP" in out


def test_identical_accounts_collapse():
    units = [_prop_unit("p1", 0.006), _prop_unit("p2", 0.006)]   # same size + risk → same leg
    out = render_multi_account_ticket(_sig(), build_account_legs(_sig(), units), now=_NOW)
    assert "applies to accounts: p1, p2" in out
    assert "ACCOUNTS DIFFER" not in out


def test_differing_accounts_show_discrepancy_banner():
    units = [_prop_unit("p1", 0.006), _prop_unit("p2", 0.012)]   # different risk → different qty
    legs = build_account_legs(_sig(), units)
    out = render_multi_account_ticket(_sig(), legs, now=_NOW)
    assert "⚠ ACCOUNTS DIFFER" in out
    assert "── ACCOUNT: p1" in out and "── ACCOUNT: p2" in out
    # the two legs really differ in size
    assert legs[0].ticket.qty_units != legs[1].ticket.qty_units


def test_impossible_leg_is_skipped_not_raised():
    bad = BreakoutSignal(strategy="x", symbol="SOLUSDT", direction="long",
                         entry=150.0, sl=150.0, tp=160.0, timeframe="2h",
                         signal_time=_NOW)   # sl == entry → zero stop distance
    leg = build_account_leg(bad, _prop_unit("p1", 0.006))
    assert leg.decision == "skip"
    assert "invalid" in leg.reason
