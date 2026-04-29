"""Per-account risk manager — units layer (S-008 PR #122 / S-010 PR #1 /
S-012 PR E3a max_dd_pct enforcement).

Two interfaces:
  - Functional (S-008): size_order() / size_order_from_cfg() — used by execute_pkg()
  - Class-based (S-010 + S-012 E3a): RiskManager — used by
    TradingAccount.place_order()

The class-based interface tracks per-account state:
  - daily_pnl: USD PnL since the last reset
  - current_equity / daily_high_equity: equity tracking for intra-day
    drawdown (PM § 8 #6 — resets at UTC midnight on the next approve()
    or update_equity() call).

Hard limits (from accounts.yaml ``risk`` section):
  - max_dd_pct: max intra-day equity drawdown from today's high (S-012 PR E3a)
  - daily_usd: max daily loss in USD (S-010)
  - pos_size: max single-position size in USD (S-010)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from src.core.coordinator import OrderPackage


_DEFAULT_MIN_QTY = 0.001    # BTC minimum lot size
_DEFAULT_MAX_QTY = 100.0    # hard cap; override via account cfg
_DEFAULT_QTY_PRECISION = 3


# ---------------------------------------------------------------------------
# Functional interface (S-008 — backward-compatible)
# ---------------------------------------------------------------------------


def size_order(
    pkg: OrderPackage,
    risk_pct: float,
    balance_usdt: float,
    *,
    min_qty: float = _DEFAULT_MIN_QTY,
    max_qty: float = _DEFAULT_MAX_QTY,
    qty_precision: int = _DEFAULT_QTY_PRECISION,
) -> float:
    """Return the position size (qty) for *pkg* given the account constraints.

    Parameters
    ----------
    pkg : OrderPackage
        Contains entry and sl prices for risk calculation.
    risk_pct : float
        Fraction of balance to risk (e.g., 0.01 = 1 %).
    balance_usdt : float
        Current USDT balance of the account.
    min_qty : float
        Minimum tradeable quantity (default 0.001 BTC).
    max_qty : float
        Maximum allowed quantity.
    qty_precision : int
        Number of decimal places for rounding.

    Returns
    -------
    float
        Sized, clipped, and rounded quantity.

    Raises
    ------
    ValueError
        When balance or risk_pct are non-positive, or when entry == sl.
    """
    if balance_usdt <= 0:
        raise ValueError(f"balance_usdt must be positive, got {balance_usdt}")
    if risk_pct <= 0:
        raise ValueError(f"risk_pct must be positive, got {risk_pct}")

    risk_distance = abs(pkg.entry - pkg.sl)
    if risk_distance == 0:
        raise ValueError(
            f"OrderPackage entry ({pkg.entry}) equals sl ({pkg.sl}); "
            "cannot compute position size (division by zero)."
        )

    risk_usdt = balance_usdt * risk_pct
    raw_qty = risk_usdt / risk_distance

    qty = round(max(min_qty, min(raw_qty, max_qty)), qty_precision)
    return qty


def size_order_from_cfg(
    pkg: OrderPackage,
    account_cfg: dict,
    balance_usdt: float,
) -> float:
    """Convenience wrapper: extract risk params from account_cfg dict."""
    risk_pct = float(account_cfg.get("risk_pct") or 0.01)
    min_qty = float(account_cfg.get("min_qty") or _DEFAULT_MIN_QTY)
    max_qty = float(account_cfg.get("max_qty") or _DEFAULT_MAX_QTY)
    qty_precision = int(account_cfg.get("qty_precision") or _DEFAULT_QTY_PRECISION)
    return size_order(
        pkg, risk_pct, balance_usdt,
        min_qty=min_qty, max_qty=max_qty, qty_precision=qty_precision,
    )


# ---------------------------------------------------------------------------
# Class-based interface (S-010 — stateful per-account risk enforcement)
# ---------------------------------------------------------------------------


class RiskManager:
    """Per-account risk gate with stateful daily-PnL tracking.

    Parameters (from accounts.yaml ``risk`` section)
    -------------------------------------------------
    max_dd_pct : float
        Maximum drawdown as a fraction of starting equity (e.g., 0.05 = 5 %).
    daily_usd : float
        Maximum allowed daily loss in USD (e.g., 100).
    pos_size : float
        Maximum single-position size in USD (e.g., 500).
        Checked against ``order.meta['estimated_value']`` when present.
    """

    def __init__(self, config: dict) -> None:
        self.max_dd_pct: float = float(config.get("max_dd_pct", 0.05))
        self.max_daily_loss_usd: float = float(config.get("daily_usd", 100.0))
        self.max_pos_size_usd: float = float(config.get("pos_size", 500.0))
        self.daily_pnl: float = 0.0       # updated by record_trade_result()
        # S-012 PR E3a — intra-day drawdown tracking. None until the
        # caller seeds equity via update_equity(); the drawdown check
        # is skipped while equity is unknown so the field remains
        # backwards-compatible with callers that don't track equity.
        self.current_equity: Optional[float] = None
        self.daily_high_equity: Optional[float] = None
        self._last_reset_utc_date: Optional[Any] = self._today_utc()

    @staticmethod
    def _today_utc():
        """Return today's UTC date (timezone-aware)."""
        return datetime.now(timezone.utc).date()

    def _maybe_roll_daily(self) -> None:
        """If the UTC date has advanced past the last reset, reset daily state.

        Called at the top of approve() / update_equity() so the rollover
        happens lazily without a scheduler. PM § 8 #6: resets at UTC
        midnight.
        """
        today = self._today_utc()
        if self._last_reset_utc_date is None or today > self._last_reset_utc_date:
            self.daily_pnl = 0.0
            # Re-anchor the intra-day high to current_equity (or None).
            self.daily_high_equity = self.current_equity
            self._last_reset_utc_date = today

    def update_equity(self, equity_usd: float) -> None:
        """Set the account's current equity in USD.

        Bumps daily_high_equity when the new value is a fresh intra-day
        high. Idempotent on the same equity value. Roll the daily window
        first so a new UTC day re-anchors the high.
        """
        self._maybe_roll_daily()
        self.current_equity = float(equity_usd)
        if (
            self.daily_high_equity is None
            or self.current_equity > self.daily_high_equity
        ):
            self.daily_high_equity = self.current_equity

    def intraday_drawdown(self) -> Optional[float]:
        """Return the current intra-day drawdown as a fraction (0.0 .. 1.0).

        Returns None when equity has not been seeded (no signal). When
        current_equity exceeds daily_high_equity, drawdown is clamped at 0.
        """
        if self.daily_high_equity is None or self.current_equity is None:
            return None
        if self.daily_high_equity <= 0:
            return None
        if self.current_equity >= self.daily_high_equity:
            return 0.0
        return (self.daily_high_equity - self.current_equity) / self.daily_high_equity

    def approve(self, order: OrderPackage) -> bool:
        """Return True when the order passes all risk checks.

        Checks (in order):
          1. UTC daily rollover (resets daily_pnl + re-anchors high).
          2. Daily loss limit: ``daily_pnl < -max_daily_loss_usd`` → reject.
          3. Position size: ``order.meta['estimated_value'] >
             max_pos_size_usd`` → reject.
          4. Intra-day drawdown (S-012 PR E3a): when equity is known,
             ``(daily_high - current) / daily_high >= max_dd_pct`` → reject.
             Skipped when equity has not been seeded via update_equity().
        """
        self._maybe_roll_daily()

        if self.daily_pnl < -self.max_daily_loss_usd:
            return False

        estimated_value = order.meta.get("estimated_value") if order.meta else None
        if estimated_value is not None and float(estimated_value) > self.max_pos_size_usd:
            return False

        dd = self.intraday_drawdown()
        if dd is not None and dd >= self.max_dd_pct:
            return False

        return True

    def record_trade_result(self, pnl_usd: float) -> None:
        """Update daily PnL after a trade closes.  Call this from the accounts unit.

        When equity is being tracked, update_equity() should also be
        called by the caller with the post-trade equity value so the
        intra-day high stays current.
        """
        self._maybe_roll_daily()
        self.daily_pnl += pnl_usd

    def reset_daily(self) -> None:
        """Manually reset daily PnL + intra-day high (UTC-day-independent).

        The lazy UTC rollover via _maybe_roll_daily() handles normal
        midnight resets. This method is kept for explicit operator
        intervention (Telegram /reset_daily etc.) and for tests.
        """
        self.daily_pnl = 0.0
        self.daily_high_equity = self.current_equity
        self._last_reset_utc_date = self._today_utc()

    def report(self) -> dict:
        """Return a human-readable status dict."""
        dd = self.intraday_drawdown()
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_pos_size_usd": self.max_pos_size_usd,
            "max_dd_pct": self.max_dd_pct,
            "daily_loss_remaining": round(
                self.max_daily_loss_usd + self.daily_pnl, 2
            ),
            "current_equity": (
                round(self.current_equity, 2) if self.current_equity is not None else None
            ),
            "daily_high_equity": (
                round(self.daily_high_equity, 2) if self.daily_high_equity is not None else None
            ),
            "intraday_drawdown_pct": round(dd, 4) if dd is not None else None,
            "halted": (
                self.daily_pnl < -self.max_daily_loss_usd
                or (dd is not None and dd >= self.max_dd_pct)
            ),
        }

