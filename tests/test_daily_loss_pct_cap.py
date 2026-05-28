"""Percentage-based daily-loss cap (operator-approved 2026-05-28).

The hardcoded ``daily_usd`` cap did not scale with account size: the
bybit demo account (~$274k paper balance) tripped a $100 cap on nearly
every signal and then size-refused all day. ``daily_loss_pct`` makes the
daily-loss budget ``daily_loss_pct × equity`` when set, falling back to
the absolute ``daily_usd`` only when no equity figure is available.

Pins:
1. percentage cap scales with equity (5% of 274k ≈ 13.7k, not $100);
2. a small daily loss no longer zeroes the size on a large account;
3. exhaustion is computed against the percentage cap;
4. absent ``daily_loss_pct`` → the absolute ``daily_usd`` is unchanged
   (prop-account behaviour preserved);
5. no equity available → falls back to the absolute floor.
"""
from __future__ import annotations

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager


def _pkg() -> OrderPackage:
    # entry/sl 100 apart → risk_distance = 100; cvu = 1.0 (BTCUSDT crypto).
    return OrderPackage(
        strategy="vwap",
        symbol="BTCUSDT",
        direction="short",
        entry=80_000.0,
        sl=80_100.0,
        tp=79_000.0,
        confidence=1.0,
        meta={"strategy_name": "vwap"},
    )


def _rm(**risk) -> RiskManager:
    base = {
        "max_dd_pct": 0.05,
        "daily_usd": 100,
        "pos_size": 500,
        "risk_pct": 0.01,
        "min_balance_usd": 50,
        "leverage": 3,
    }
    base.update(risk)
    # account_id="" keeps it in-memory (no DB persistence / reconcile).
    return RiskManager(base, account_id="")


def test_effective_cap_is_percentage_of_equity_when_set():
    rm = _rm(daily_loss_pct=0.05)
    assert rm.effective_daily_loss_usd(274_000.0) == 0.05 * 274_000.0  # 13_700
    # No equity → absolute fallback.
    assert rm.effective_daily_loss_usd(None) == 100.0


def test_effective_cap_absolute_when_pct_unset():
    rm = _rm()  # no daily_loss_pct
    assert rm.daily_loss_pct == 0.0
    assert rm.effective_daily_loss_usd(274_000.0) == 100.0
    assert rm.effective_daily_loss_usd(None) == 100.0


def test_small_loss_does_not_zero_size_on_large_balance():
    """A -$100 day on a $274k account: under the old fixed $100 cap the
    loss budget was exhausted (size→0); under 5%-of-equity it is not."""
    rm = _rm(daily_loss_pct=0.05)
    rm.daily_pnl = -100.0  # simulate today's realized loss
    qty = rm.position_size(
        _pkg(), 274_000.0, market_type="linear", total_account_usd=274_000.0,
    )
    assert qty > 0.0, "5%-of-equity cap should leave ample budget at -$100"


def test_pct_cap_still_zeroes_when_truly_exhausted():
    rm = _rm(daily_loss_pct=0.05)
    rm.daily_pnl = -13_700.0  # exactly 5% of 274k → budget == 0
    qty = rm.position_size(
        _pkg(), 274_000.0, market_type="linear", total_account_usd=274_000.0,
    )
    assert qty == 0.0


def test_is_daily_cap_exhausted_uses_percentage():
    rm = _rm(daily_loss_pct=0.05)
    rm.daily_pnl = -100.0
    assert rm.is_daily_cap_exhausted(274_000.0) is False
    rm.daily_pnl = -20_000.0
    assert rm.is_daily_cap_exhausted(274_000.0) is True


def test_evaluate_daily_loss_cap_uses_percentage():
    rm = _rm(daily_loss_pct=0.05)
    rm.current_equity = 274_000.0
    rm.daily_pnl = -100.0
    ok, reason = rm.evaluate(_pkg())
    assert ok, reason  # -100 is well within 5% of 274k
    rm.daily_pnl = -20_000.0
    ok, reason = rm.evaluate(_pkg())
    assert not ok and reason == "DAILY_LOSS_CAP"


def test_prop_style_absolute_cap_unchanged():
    """Account with no daily_loss_pct (the prop profile) keeps the
    absolute USD cap exactly as before."""
    rm = _rm(daily_usd=50, daily_loss_pct=0.0)
    rm.daily_pnl = -60.0
    # -60 below a -50 absolute cap → exhausted regardless of equity.
    assert rm.is_daily_cap_exhausted(100_000.0) is True
    rm.current_equity = 100_000.0
    ok, reason = rm.evaluate(_pkg())
    assert not ok and reason == "DAILY_LOSS_CAP"
