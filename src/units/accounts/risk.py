"""Per-account risk manager — units layer (S-008 PR #122).

Translates an OrderPackage into a sized quantity using the account's
configured risk percentage and the current USDT balance.

Sizing formula (fixed-fractional / risk-per-trade):

    risk_usdt = balance_usdt × risk_pct
    qty       = risk_usdt / abs(entry - sl)

where ``entry`` and ``sl`` come from the OrderPackage.

Constraints
-----------
- qty is clipped to [min_qty, max_qty].
- When entry == sl (degenerate), raises ValueError (division by zero guard).
- qty is rounded to ``qty_precision`` decimal places (default 3 for BTC).
"""
from __future__ import annotations

from src.core.coordinator import OrderPackage


_DEFAULT_MIN_QTY = 0.001    # BTC minimum lot size
_DEFAULT_MAX_QTY = 100.0    # hard cap; override via account cfg
_DEFAULT_QTY_PRECISION = 3


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
