"""Firm rules the generic prop guards did not express (TRADEIFY-WIRE T2).

Two rules, both DECLARED in the account's ruleset (``limits:``) and absent from
``breakout.yaml``, so an account whose ruleset declares neither behaves exactly
as before:

``limits.leverage_caps``
    ``{<bot symbol>: <max notional / balance>, default: <cap>}``. A position
    whose notional exceeds ``cap x basis`` is a rule breach at the firm (Tradeify
    247: 5:1 BTC/ETH, 2:1 altcoins). The basis is the SMALLER of the nominal
    account size and the live balance/equity, so a shrinking account tightens
    the cap and nothing loosens it.

``limits.daily_loss_amount_basis: account_size``
    The daily-loss LIMIT AMOUNT is ``daily_loss_pct x account_size`` rather
    than ``daily_loss_pct x day-start balance`` (Tradeify 247: "previous day's
    closing balance minus 3%" of the account size). Absent = the old
    balance-based amount, unchanged.

Pure functions; the callers (``prop_executor.evaluate_guards`` and the ticket
emitter) decide refuse-vs-report through ``breach_guards``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

AMOUNT_BASIS_ACCOUNT_SIZE = "account_size"


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def load_limits(ruleset_path: Optional[Path]) -> Dict[str, Any]:
    """The raw ``limits:`` block of a ruleset file ({} when unreadable)."""
    if not ruleset_path:
        return {}
    import yaml

    try:
        return dict((yaml.safe_load(Path(ruleset_path).read_text()) or {}).get("limits") or {})
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return {}


def leverage_caps(limits: Mapping[str, Any]) -> Dict[str, float]:
    """``limits.leverage_caps`` as ``{SYMBOL|default: cap}``; {} = no rule."""
    raw = limits.get("leverage_caps") or {}
    out: Dict[str, float] = {}
    if isinstance(raw, Mapping):
        for k, v in raw.items():
            cap = _f(v)
            if cap is not None and cap > 0:
                out[str(k).upper() if str(k) != "default" else "default"] = cap
    return out


def leverage_cap_for(caps: Mapping[str, float], symbol: str) -> Optional[float]:
    """The cap for ``symbol``: its own entry, else ``default``, else None."""
    if not caps:
        return None
    return caps.get(str(symbol or "").upper(), caps.get("default"))


def leverage_basis(account_size_usd: Optional[float], live: Iterable[Optional[float]] = ()) -> Optional[float]:
    """The SMALLEST positive of the nominal size and any live reading."""
    vals = [v for v in (_f(account_size_usd), *(_f(x) for x in live)) if v is not None and v > 0]
    return min(vals) if vals else None


def leverage_breach(*, symbol: str, notional_usd: Optional[float], caps: Mapping[str, float],
                    basis_usd: Optional[float]) -> Optional[str]:
    """A breach reason, or None when the rule is met or not declared.

    Declared but not checkable (no notional or no basis) is a breach: could
    not look, and the direction that fails safe is not to place."""
    cap = leverage_cap_for(caps, symbol)
    if cap is None:
        return None
    if notional_usd is None or basis_usd is None:
        return f"leverage: {symbol} cap {cap:g}:1 declared but notional/basis unknown (could not look)"
    limit = cap * basis_usd
    if notional_usd > limit:
        return (f"leverage: {symbol} notional ${notional_usd:,.2f} > {cap:g}:1 x "
                f"${basis_usd:,.2f} = ${limit:,.2f}")
    return None


def daily_loss_amount(*, daily_loss_pct: Optional[float], day_start_balance: Optional[float],
                      account_size_usd: Optional[float], amount_basis: Optional[str]) -> Optional[float]:
    """The $ the account may lose from the day-start balance today."""
    pct = _f(daily_loss_pct)
    if pct is None:
        return None
    if str(amount_basis or "").strip().lower() == AMOUNT_BASIS_ACCOUNT_SIZE:
        size = _f(account_size_usd)
        return pct * size if size else None
    dsb = _f(day_start_balance)
    return pct * dsb if dsb is not None else None


def daily_floor(*, daily_loss_pct: Optional[float], day_start_balance: Optional[float],
                account_size_usd: Optional[float], amount_basis: Optional[str]) -> Optional[float]:
    """Equity at which today's daily-loss rule is breached (None = unknown)."""
    dsb = _f(day_start_balance)
    if not dsb:
        return None
    pct = _f(daily_loss_pct)
    if pct is None:
        return None
    if str(amount_basis or "").strip().lower() != AMOUNT_BASIS_ACCOUNT_SIZE:
        # the pre-existing expression, kept byte-for-byte (breakout_1)
        return dsb * (1.0 - pct)
    size = _f(account_size_usd)
    return dsb - pct * size if size else None


__all__ = [
    "AMOUNT_BASIS_ACCOUNT_SIZE", "load_limits", "leverage_caps", "leverage_cap_for",
    "leverage_basis", "leverage_breach", "daily_loss_amount", "daily_floor",
]
