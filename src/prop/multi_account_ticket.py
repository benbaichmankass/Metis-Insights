"""Per-account prop ticket — one signal → per-account legs + discrepancy banner.

A single strategy signal can route to several prop accounts that have DIFFERENT
rules (different size, validity, even go/no-go). This module turns one
:class:`~src.prop.breakout_ticket.BreakoutSignal` into a list of **per-account
legs** (each sized/validated against that account's
:class:`~src.prop.account_rulesets.AccountBacktestUnit`) and renders ONE message
that:

- with a single account → renders that account's instruction block (no banner);
- with several accounts whose legs are IDENTICAL → one block, "applies to: A, B";
- with several accounts whose legs DIFFER → a loud **discrepancy banner** + one
  labelled block per account, so the executing assistant runs ONLY the block for
  the account it is on.

Built multi-account from day one (a list of legs) so adding a prop account is
config-only and never strands the renderer in a single-account assumption.

Tier-1: formatting only — places no order, not in the live path. Live
headroom-based skips (daily-loss / DD cushion) are applied by the executor at
emit time where live account state exists; here a leg is skipped only for a
structurally-impossible size.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from src.prop.account_rulesets import AccountBacktestUnit
from src.prop.breakout_ticket import (
    BreakoutSignal,
    Ticket,
    TicketConfig,
    build_ticket,
    render_ticket,
)


@dataclass(frozen=True)
class AccountLeg:
    """One account's variation of a signal."""

    account_id: str
    account_class: str
    decision: str                 # "place" | "skip"
    ticket: Optional[Ticket]      # the sized ticket when decision == "place"
    reason: str = ""              # why skipped (when decision == "skip")


def build_account_leg(
    signal: BreakoutSignal,
    unit: AccountBacktestUnit,
    *,
    dxtrade_symbol: Optional[str] = None,
    contract_value_usd_per_point: float = 1.0,
    entry_band_frac: float = 0.25,
    ttl_bars: float = 1.0,
) -> AccountLeg:
    """Size one account's leg from its :class:`AccountBacktestUnit`."""
    cfg = TicketConfig(
        account_size_usd=unit.account_size_usd,
        risk_pct=unit.risk_pct,
        dxtrade_symbol=dxtrade_symbol,
        contract_value_usd_per_point=contract_value_usd_per_point,
        entry_band_frac=entry_band_frac,
        ttl_bars=ttl_bars,
    )
    try:
        ticket = build_ticket(signal, cfg)
    except ValueError as exc:
        return AccountLeg(unit.account_id, unit.account_class, "skip", None, f"invalid: {exc}")
    if ticket.qty_units <= 0:
        return AccountLeg(unit.account_id, unit.account_class, "skip", ticket, "size rounds to zero")
    return AccountLeg(unit.account_id, unit.account_class, "place", ticket, "")


def build_account_legs(
    signal: BreakoutSignal,
    units: Sequence[AccountBacktestUnit],
    **leg_kwargs,
) -> List[AccountLeg]:
    """Build a leg per account unit (order preserved)."""
    return [build_account_leg(signal, u, **leg_kwargs) for u in units]


def _leg_signature(leg: AccountLeg):
    """The actionable fingerprint of a leg — legs with equal signatures are the
    same instruction (so they collapse to one block)."""
    if leg.decision != "place" or leg.ticket is None:
        return ("skip", leg.reason)
    t = leg.ticket
    return ("place", t.side, round(t.qty_units, 8), t.entry_min, t.entry_max,
            t.signal.sl, t.signal.tp)


def _summary_token(leg: AccountLeg) -> str:
    if leg.decision == "place" and leg.ticket is not None:
        return f"{leg.account_id}: {leg.ticket.side} {leg.ticket.qty_units}"
    return f"{leg.account_id}: SKIP ({leg.reason})"


def render_multi_account_ticket(
    signal: BreakoutSignal,
    legs: Sequence[AccountLeg],
    *,
    now: Optional[datetime] = None,
) -> str:
    """Render the per-account message (see module docstring for the three cases)."""
    legs = list(legs)
    if not legs:
        return "PROP SIGNAL — no eligible accounts."

    def _block(leg: AccountLeg, *, label: Optional[str] = None) -> str:
        head = f"── ACCOUNT: {leg.account_id} ({leg.account_class}) ──" if label else ""
        if leg.decision == "place" and leg.ticket is not None:
            body = render_ticket(leg.ticket, now=now)
        else:
            body = f"SKIP — {leg.reason}"
        return f"{head}\n{body}".strip() if head else body

    # 1 account → single block, no banner (today's behaviour).
    if len(legs) == 1:
        return _block(legs[0])

    signatures = {_leg_signature(leg) for leg in legs}

    # All accounts agree → one block, list who it applies to.
    if len(signatures) == 1:
        ids = ", ".join(leg.account_id for leg in legs)
        return f"(applies to accounts: {ids})\n\n{_block(legs[0])}"

    # Accounts DIFFER → discrepancy banner + one labelled block each.
    lines: List[str] = [
        "⚠ ACCOUNTS DIFFER — execute ONLY the block for the account you are trading.",
        "  " + " | ".join(_summary_token(leg) for leg in legs),
        "",
    ]
    lines.extend(_block(leg, label=leg.account_id) for leg in legs)
    return "\n\n".join(lines)


def build_and_render(
    signal: BreakoutSignal,
    units: Sequence[AccountBacktestUnit],
    *,
    now: Optional[datetime] = None,
    **leg_kwargs,
) -> str:
    """Convenience: legs for every prop account unit, rendered to one message."""
    legs = build_account_legs(signal, units, **leg_kwargs)
    return render_multi_account_ticket(signal, legs, now=now or datetime.now(timezone.utc))
