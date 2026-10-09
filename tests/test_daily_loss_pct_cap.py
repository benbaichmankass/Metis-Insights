"""Percentage-based daily-loss cap (operator-approved 2026-05-28).

The hardcoded ``daily_usd`` cap did not scale with account size: the
bybit demo account (~$274k paper balance) tripped a $100 cap on nearly
every signal and then size-refused all day. ``daily_loss_pct`` makes the
daily-loss budget ``daily_loss_pct × equity`` when set, falling back to
the absolute ``daily_usd`` only when no equity figure is available.

2026-10-09 (operator directive, rule 7 exception): the always-on
DAILY_LOSS_CAP refusal and the daily-loss sizing clamp were folded into the
account-level ``daily_dd_switch`` (tests/test_daily_dd_switch.py). The
figure below is now REPORTED only (``report()``), never a refusal.

Pins:
1. the reported figure scales with equity (5% of 274k ≈ 13.7k, not $100);
2. no equity available → the absolute ``daily_usd`` floor;
3. a day past the figure neither refuses nor zeroes size.
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


def test_spent_daily_budget_no_longer_zeroes_size():
    """2026-10-09: the S-026 G3 daily-loss sizing clamp is REMOVED — at zero
    budget it was an always-on daily off switch spelled as sizing. The
    account-level ``daily_dd_switch`` is now the only daily off (operator)."""
    rm = _rm(daily_loss_pct=0.05)
    rm.daily_pnl = -13_700.0  # exactly 5% of 274k → old budget == 0
    qty = rm.position_size(
        _pkg(), 274_000.0, market_type="linear", total_account_usd=274_000.0,
    )
    assert qty > 0.0


def test_daily_loss_figure_is_reported_not_refused():
    """``daily_loss_pct`` / ``daily_usd`` remain REPORTED figures only: a day
    past them no longer refuses (DAILY_LOSS_CAP was folded into the switch)."""
    rm = _rm(daily_loss_pct=0.05)
    rm.current_equity = 274_000.0
    rm.daily_pnl = -20_000.0
    assert rm.evaluate(_pkg()) == (True, None)
    assert rm.effective_daily_loss_usd(274_000.0) == 0.05 * 274_000.0
    assert not hasattr(rm, "is_daily_cap_exhausted")


def test_absolute_figure_is_reported_not_refused():
    rm = _rm(daily_usd=50, daily_loss_pct=0.0)
    rm.daily_pnl = -60.0
    rm.current_equity = 100_000.0
    assert rm.effective_daily_loss_usd(100_000.0) == 50.0
    assert rm.evaluate(_pkg()) == (True, None)
