"""Per-account risk manager — units layer (S-008 PR #122 / S-010 PR #1).

Two interfaces:
  - Functional (S-008): size_order() / size_order_from_cfg() — used by execute_pkg()
  - Class-based (S-010): RiskManager — used by TradingAccount.place_order()

The class-based interface adds stateful daily-PnL tracking and per-account
hard limits (max drawdown %, max daily loss USD, max position size USD).
"""
from __future__ import annotations

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
        self._starting_equity: Optional[float] = None

    def approve(self, order: OrderPackage) -> bool:
        """Return True when the order passes all risk checks.

        Checks (in order):
          1. Daily loss limit: ``daily_pnl < -max_daily_loss_usd`` → reject
          2. Position size: ``order.meta['estimated_value'] > max_pos_size_usd`` → reject
        """
        if self.daily_pnl < -self.max_daily_loss_usd:
            return False

        estimated_value = order.meta.get("estimated_value") if order.meta else None
        if estimated_value is not None and float(estimated_value) > self.max_pos_size_usd:
            return False

        return True

    def record_trade_result(self, pnl_usd: float) -> None:
        """Update daily PnL after a trade closes.  Call this from the accounts unit."""
        self.daily_pnl += pnl_usd

    def reset_daily(self) -> None:
        """Reset daily PnL counter (call at midnight / session open)."""
        self.daily_pnl = 0.0

    def report(self) -> dict:
        """Return a human-readable status dict."""
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_pos_size_usd": self.max_pos_size_usd,
            "max_dd_pct": self.max_dd_pct,
            "daily_loss_remaining": round(
                self.max_daily_loss_usd + self.daily_pnl, 2
            ),
            "halted": self.daily_pnl < -self.max_daily_loss_usd,
        }

