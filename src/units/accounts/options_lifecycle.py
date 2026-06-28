"""Options lifecycle — expiry / assignment detection + realized-PnL from activities.

Phase-1 Slice-4 of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

A debit vertical opened by ``options_overlay.place_options_expression`` rides to
expiry (Slice-3b had no close path — the journal row stayed ``open`` forever). This
module supplies the PURE pieces the monitor's options-lifecycle reconciler composes:

  * ``underlying_from_occ`` — the underlying root of an OCC option symbol.
  * ``OPTION_LIFECYCLE_ACTIVITY_TYPES`` — the ``/v2/account/activities`` event types
    that mean a position concluded (expiration / assignment / exercise).
  * ``realized_pnl_from_activities`` — realized PnL of a structure as
    *close-side cash − open debit* (the only honest definition we can derive without
    per-leg fills): every expiry/assignment/exercise records the actual cash
    credited/debited, and the open debit is what the account paid.
  * ``structure_concluded`` — given the underlyings still holding an open option
    position, decide whether a journal row's structure is done.

All pure + total (never raise on a malformed record — a bad row is skipped). The
live I/O (fetch activities / positions, close the journal row) lives in
``order_monitor._reconcile_options_expiry_and_assignment``; these functions are
unit-tested directly so the reconciler's logic is verified without a live account.

PnL fidelity: this is **soak-grade**, not fee-perfect. ``net_amount`` on Alpaca's
non-trade activities is the real cash effect, so *close-cash − open-debit* is a
correct realized number for a structure that expired/assigned; but a leg that
expires worthless emits no cash (``net_amount`` 0 / no record), so a fully-OTM
expiry resolves to the full debit loss (``−open_cost``) — which is exactly right
for a debit vertical. Rows are tagged ``pnl_source="alpaca_activity"`` so a future
fee-accurate reader can supersede them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Alpaca non-trade activity types that mean an option position concluded.
# EXP  = option expiration (worthless or auto-handled)
# OPASN = option assignment (short leg assigned)
# OPEXC = option exercise (long leg exercised, incl. ITM auto-exercise)
OPTION_LIFECYCLE_ACTIVITY_TYPES: Tuple[str, ...] = ("EXP", "OPASN", "OPEXC")

# Per-contract multiplier (US equity options). Mirrors options_sizing.OPTION_MULTIPLIER.
OPTION_MULTIPLIER = 100


def underlying_from_occ(occ_symbol: Any) -> Optional[str]:
    """The underlying root of an OCC option symbol, e.g. ``SLV260116C00025000`` → ``SLV``.

    OCC symbology is ``<ROOT><YYMMDD><C|P><strike*1000 zero-padded to 8>``; the root is
    the leading run of non-digit characters (``[A-Z.]``). Returns ``None`` for an empty
    or rootless value. Total — never raises.
    """
    s = str(occ_symbol or "").strip().upper()
    if not s:
        return None
    root_chars: List[str] = []
    for ch in s:
        if ch.isdigit():
            break
        root_chars.append(ch)
    root = "".join(root_chars).strip(".")
    return root or None


def _to_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


@dataclass
class LifecyclePnl:
    """Result of pricing a concluded structure from its lifecycle activities."""

    realized_pnl: float
    pnl_source: str
    event_count: int
    close_cash: float
    open_cost: float
    activity_ids: List[str]


def realized_pnl_from_activities(
    activities: Iterable[Dict[str, Any]],
    *,
    underlying: str,
    net_debit: float,
    contracts: int,
) -> LifecyclePnl:
    """Realized PnL of a concluded debit structure = close-side cash − open debit.

    *activities* is the account-activities payload (each a dict with ``activity_type``,
    ``symbol`` (OCC), ``net_amount``); only lifecycle types for *underlying*'s legs are
    summed. *net_debit* is the per-share spread debit the row was opened at and
    *contracts* the qty, so the open cost is ``net_debit × OPTION_MULTIPLIER × contracts``.

    Returns a :class:`LifecyclePnl`. With no matching cash activity the close cash is
    0 (a worthless expiry), so realized = ``−open_cost`` — the full debit loss, correct
    for a debit vertical. Total — a malformed activity row is skipped, never raised.
    """
    u = (underlying or "").strip().upper()
    close_cash = 0.0
    ids: List[str] = []
    for act in activities or []:
        try:
            atype = str(act.get("activity_type") or "").strip().upper()
            if atype not in OPTION_LIFECYCLE_ACTIVITY_TYPES:
                continue
            if underlying_from_occ(act.get("symbol")) != u:
                continue
            amt = _to_float(act.get("net_amount"))
            if amt is not None:
                close_cash += amt
            aid = act.get("id")
            if aid is not None:
                ids.append(str(aid))
        except Exception:  # noqa: BLE001 — total; skip a bad record
            continue
    open_cost = abs(float(net_debit or 0.0)) * OPTION_MULTIPLIER * int(contracts or 0)
    realized = round(close_cash - open_cost, 2)
    return LifecyclePnl(
        realized_pnl=realized,
        pnl_source="alpaca_activity",
        event_count=len(ids),
        close_cash=round(close_cash, 2),
        open_cost=round(open_cost, 2),
        activity_ids=ids,
    )


def underlyings_with_open_options(option_positions: Iterable[Dict[str, Any]]) -> Set[str]:
    """The set of underlyings that still hold at least one open option position.

    *option_positions* is the broker's open-option snapshot (each a dict with an OCC
    ``symbol``). Total — a rootless/blank symbol is skipped.
    """
    out: Set[str] = set()
    for pos in option_positions or []:
        try:
            u = underlying_from_occ(pos.get("symbol"))
            if u:
                out.add(u)
        except Exception:  # noqa: BLE001
            continue
    return out


def structure_concluded(
    underlying: str,
    *,
    open_option_underlyings: Set[str],
    lifecycle_event_seen: bool,
) -> bool:
    """Has *underlying*'s structure concluded?

    A structure is concluded when it no longer holds an open option position AND a
    lifecycle event (expiry/assignment/exercise) was seen for it. Requiring BOTH — not
    mere position-absence — is deliberate: position-absence alone is the fragile
    snapshot signal that, for a shared-login equity account, mis-closed live positions
    (the 2026-06-27 incident). A broker-confirmed lifecycle event is the trustworthy
    trigger. Total — never raises.
    """
    u = (underlying or "").strip().upper()
    if not u:
        return False
    return lifecycle_event_seen and u not in (open_option_underlyings or set())
