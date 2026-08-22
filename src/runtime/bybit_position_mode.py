"""Which Bybit position book does this order/stop belong to?

Workplan item **T.2**. `BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`.

WHY THIS EXISTS. `bybit_1` is a ONE-WAY-NETTING account: one net position per
symbol. The market-neutral pairs sleeve opens a leg that is frequently OPPOSITE
to a concurrent directional strategy's position on the same symbol, so the leg
does not open a position at all — it merely REDUCES the standing one. Measured
2026-08-21 against exchange truth: every SOLUSDT/ETHUSDT pair opened since
2026-08-18 stranded, **8 of 8**, and the arithmetic closes exactly
(`/api/diag/exchange_positions` returned SOLUSDT Buy 373.0 against a journal of
`trend_donchian_sol_4h` 367.8 + `pairs_sol_eth_a` 5.2 = 373.0). A long spread
kills the short leg and a short spread kills the long leg, which is why the rate
is 8/8 rather than intermittent.

Bybit's remedy is HEDGE MODE, where long and short books coexist per symbol and
every order names its book with `positionIdx` (1 = long, 2 = short; 0/absent =
one-way).

⚠️ **THIS MODULE SHIPS INERT, AND THAT IS THE POINT.** With an empty allowlist
`position_idx_for` returns `idx=None` at every call site, no `positionIdx` kwarg
is added anywhere, and the wire payload is **byte-for-byte** what it is today.
Turning hedge mode on for a real (account, symbol) is a separate, operator-gated
step — it requires switching the position mode AT THE VENUE, which this module
deliberately does NOT do. Code that is *ready* for hedge mode is Tier-1; changing
a venue's position mode is not, and conflating the two is how a "no behaviour
change" PR ends up changing behaviour.

⚠️ **THE CONTRACT IS THE POSITION'S DIRECTION, NEVER THE ORDER'S SIDE**, and
this is the single thing most likely to be got wrong. Closing a LONG sends
`side="Sell"` but belongs to `positionIdx=1`, because `positionIdx` names the
BOOK BEING ACTED ON, not the direction of the acting order. A resolver keyed on
order side would silently place every reduce-only close and every protective
stop against the *opposite* book — orders the venue accepts and that do nothing
the caller intended. So callers pass `position_side` ("long"/"short") and the
open path converts its own Buy/Sell exactly once, at the boundary.

FOUR STATES, NEVER COLLAPSED, because "we are one-way" and "we are hedged and
could not tell which book" are opposite statements that would otherwise both
render as an absent kwarg:

  ``one_way``      no hedge configured for this (account, symbol) — absent
                   `positionIdx` is CORRECT and is what Bybit expects.
  ``hedge_long``   idx 1.
  ``hedge_short``  idx 2.
  ``unresolved``   hedge IS configured but the position side could not be
                   determined. `idx` is None, so the order goes out WITHOUT a
                   `positionIdx` and **Bybit refuses it** — a loud, safe failure
                   that lands as `exchange_rejected`. Guessing a book here would
                   place a live order against the wrong one; a refusal will not.
                   Logged at WARNING so it is never read as `one_way`.

NOT registered with `collapsed-state-guard` — deliberately, and this is the
honest reason rather than an oversight: that guard requires every declared state
to be branched on by a REAL consumer, and while the allowlist is empty
`hedge_long` / `hedge_short` / `unresolved` have no production consumer by
construction. Registering it today would either fail the guard or teach the next
contributor to satisfy it with a decorative branch. It becomes registrable in
the same change that first makes the allowlist non-empty.
"""
from __future__ import annotations

import logging
import os
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

ONE_WAY = "one_way"
HEDGE_LONG = "hedge_long"
HEDGE_SHORT = "hedge_short"
UNRESOLVED = "unresolved"

_HEDGE_LONG_IDX = 1
_HEDGE_SHORT_IDX = 2

#: CSV of ``<account_id>:<SYMBOL>`` pairs that trade in HEDGE mode.
#: Empty (the default) = every account/symbol is one-way = no behaviour change.
#: Scoped per SYMBOL because Bybit's position mode is itself per-symbol; an
#: account-wide switch would change books for instruments nobody evaluated.
_ENV_ALLOWLIST = "BYBIT_HEDGE_MODE_SYMBOLS"


class PositionIdx(NamedTuple):
    """``idx`` is what goes on the wire; ``state`` is why."""

    idx: Optional[int]
    state: str
    reason: str = ""


def _allowlist() -> frozenset:
    """Parse the allowlist at CALL time so a VM env flip needs no redeploy.

    An unparseable entry is DROPPED rather than widening the set: the failure
    direction that matters here is accidentally hedging a book nobody chose, so
    a malformed entry must never resolve to "hedge everything".
    """
    raw = os.environ.get(_ENV_ALLOWLIST, "") or ""
    out = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        account, _, symbol = chunk.partition(":")
        account, symbol = account.strip(), symbol.strip().upper()
        if account and symbol:
            out.add((account, symbol))
    return frozenset(out)


def hedge_mode_enabled(account_id: Optional[str], symbol: Optional[str]) -> bool:
    """True when this exact (account, symbol) is declared hedge-mode."""
    if not account_id or not symbol:
        return False
    return (str(account_id), str(symbol).upper()) in _allowlist()


def position_idx_for(
    account_id: Optional[str],
    symbol: Optional[str],
    position_side: Optional[str],
) -> PositionIdx:
    """Resolve the Bybit ``positionIdx`` for the book being acted on.

    Parameters
    ----------
    position_side
        The direction of the POSITION, not of the order. ``"long"``/``"buy"``
        and ``"short"``/``"sell"`` are accepted (a caller holding an order side
        for an OPENING order may pass it directly; a closing caller must pass
        the position's own direction — see the module docstring).
    """
    if not hedge_mode_enabled(account_id, symbol):
        return PositionIdx(None, ONE_WAY, "no hedge declared for this account/symbol")

    norm = str(position_side or "").strip().lower()
    if norm in ("long", "buy"):
        return PositionIdx(_HEDGE_LONG_IDX, HEDGE_LONG, "")
    if norm in ("short", "sell"):
        return PositionIdx(_HEDGE_SHORT_IDX, HEDGE_SHORT, "")

    reason = f"position_side={position_side!r} is neither long nor short"
    logger.warning(
        "bybit_position_mode: %s/%s is declared HEDGE mode but %s — sending no "
        "positionIdx, which Bybit will refuse. Refusing is deliberate: placing "
        "against a guessed book would be a live order on the wrong position.",
        account_id, symbol, reason,
    )
    return PositionIdx(None, UNRESOLVED, reason)


def apply_position_idx(
    kwargs: dict,
    account_id: Optional[str],
    symbol: Optional[str],
    position_side: Optional[str],
) -> PositionIdx:
    """Add ``positionIdx`` to *kwargs* when — and only when — hedge mode applies.

    The single mutation helper every Bybit call site uses, so "does this payload
    carry a positionIdx?" has ONE answer in the codebase rather than one per
    call site. On ``one_way`` and ``unresolved`` the dict is left UNTOUCHED, so
    an inert allowlist cannot change a single byte of any request.
    """
    res = position_idx_for(account_id, symbol, position_side)
    if res.idx is not None:
        kwargs["positionIdx"] = res.idx
    return res


def opposite_side(position_side: Optional[str]) -> Optional[str]:
    """The position direction a reduce-only order of *this* side is closing."""
    norm = str(position_side or "").strip().lower()
    if norm in ("buy", "long"):
        return "short"
    if norm in ("sell", "short"):
        return "long"
    return None
