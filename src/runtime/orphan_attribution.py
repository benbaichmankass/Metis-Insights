"""Can we SUPPORT the strategy name an orphan adopt is about to write?

``order_monitor._recover_orphan_order_package`` picks the order package that
originally opened an exchange orphan, and the adopt then writes that package's
``strategy_name`` into ``trades.strategy_name`` — after which **that strategy's
``monitor()`` runs against the position**: break-even trail, level-cross exit,
time-decay. A wrong name is therefore not a mislabel. It is the wrong exit
logic on a live position.

THE MATCHER HAS NO SIZE TERM. It matches on ``(symbol, direction, entry_price)``
and takes the first candidate within ``max_rel_diff``. Its own docstring claims
the entry-price tolerance means "we never mis-attribute a position to the wrong
strategy (which would apply the wrong exit rules)" — **that claim is false, and
this module exists because it was measured false** (field beats comment; the
docstring is corrected in the same change).

MEASURED, MI-204, 2026-09-08 (``BL-20260908-THE-ORPHAN-ADOPT-REATTACH-WRITES-A-STRATEGY-NAME-IT-CANNOT-SUPPORT-AND-THAT-STRATEGY-THEN-ACTS-ON-THE-ROW``), and
re-verified against the live journal 2026-09-10. Trade 5453: ``bybit_1``
ETHUSDT long **17.67**, adopted and attributed to ``pairs_sol_eth_b``, closed
**14.09 s** later by ``pairs_half_open_cleanup`` — the pairs executor flattened
a position it did not open, because the journal told it the leg was its own.
17.67 is an exact match for trade 5420, ``trend_donchian_eth``, closed 3264 s
earlier; ``pairs_sol_eth_b``'s own 80 ETHUSDT legs in that window top out at
**2.10**.

⚠️ **THE DENOMINATOR HERE READ "Of 5 judgeable adopts" AND NAMED NO POPULATION
— CORRECTED 2026-09-10 BY THIS MODULE'S OWN AUTHOR.** That is the rule this repo
promoted to top level (*"ALWAYS STATE THE POPULATION"*), broken in the file that
justifies a refusal on a live order path. Re-measured by grading **every** named
adopt with this module over a population that IS stated — the 1000 rows
``/api/diag/journal`` returns for ``trades`` (ids 4653..5652): **15** carry
``setup_type='adopted_orphan'``, all 15 name a strategy, **zero are bare**, and
the verdicts are **13 supported / 2 size_implausible (5448, 5453) / 0 no_history
/ 0 unreadable**. So it is **2 of 15**, and both wrong ones named a pairs sleeve.

⚠️ **THE CORRECTION MAKES THIS GATE RARER, NOT COMMONER, AND THAT DIRECTION IS
THE POINT** — the rarer the refusal, the WEAKER "no refusals seen" is as
evidence the gate works. ⚠️ **AND THE OLD FIGURE IS NOT THEREBY WRONG**: its
population was never recorded, so the two cannot be compared; that absence IS
the defect. ⚠️ The hand-scored set behind it also included a row that is **not
an adopt at all** (trade 5568, ``exit_reason: netting_attributed``), so its
denominator was additionally off by one — see
``tests/test_orphan_attribution_size_gate.py``'s correction header.

⚠️ **THIS SIZES A CONTAMINATION, NOT A LOSS.** Trade 5453's ``pnl`` is
**+612.705681** — phantom *profit* booked onto a sleeve whose real legs that
window are −10.455 and −1.3144 — and all three rows carry ``is_demo: 1``, i.e.
``bybit_1``, the DEMO book. It is not realised P&L and it is not real-money
harm. The defect is nonetheless account-blind: the matcher reads no account
field at all, and ``bybit_2`` is real money on the same code path.

WHY QUANTITY, and why this is the cheapest honest discriminator: the adopted
position's size is already in hand at both attribution sites, the claimed
strategy's own history on that symbol is one indexed read, and the check would
have refused **both** measured mis-attributions (8.4x and 21.5x outside range)
while passing all three correct ones. It is not a proof of ownership — nothing
available to a reconciler is — it is a REFUSAL TEST: it can say *this
attribution cannot be supported*, never *this attribution is right*.

THE REFUSAL IS A BARE ORPHAN, NEVER A DIFFERENT GUESS. When support fails the
caller falls back to the row it already writes 6 times in 13 — ``strategy_name=
'orphan_adopt'``, ``setup_type='adopted_orphan'``, no synthesised stops. A bare
orphan is the honest state; a wrong attribution is worse than none because it
reads as known and is acted on. This module never proposes a second candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

__all__ = ["SizeSupport", "assess_size_support", "STATES",
           "DEFAULT_BAND", "DEFAULT_MIN_HISTORY"]

#: Every state this module can return, registered so a consumer can assert it
#: branches on all of them rather than meeting a new one at runtime.
STATES = (
    "supported",
    "size_implausible",
    "no_history",
    "unreadable",
)

#: Multiplicative tolerance around the claimed strategy's OWN observed range on
#: that symbol: support requires ``min/BAND <= size <= max*BAND``.
#:
#: ⚠️ **A CHOSEN VALUE, NOT A DERIVED ONE**, and it must not be quoted as tuned.
#: What justifies it is that the verdict is ROBUST to the choice over the only
#: population that exists: the two mis-attributions sit **8.4x** and **21.5x**
#: outside their claimed strategy's range, so every band from ~1.5 to ~8 refuses
#: both and passes every correct one in the hand-scored set.
#:
#: ⚠️ **THIS COMMENT READ "n = 5 judgeable adopts is small" UNTIL 2026-09-10 AND
#: THAT WAS THE UNSTATED DENOMINATOR THE MODULE DOCSTRING NOW CORRECTS.** Over a
#: STATED population (the 1000-row journal page, ids 4653..5652) it is **2 of 15**
#: named adopts, not 2 of 5. The band is still CHOSEN rather than derived and the
#: robustness argument above is untouched — what changes is that the evidence
#: behind it now names what it was measured over.
#:
#: ⚠️ **2-of-15 IS AN UPPER BOUND ON REFUSALS, and the bound has a direction:**
#: history enters only through ``min`` and ``max``, so MORE history can only
#: WIDEN the band — a ``supported`` verdict is stable under more history and a
#: ``size_implausible`` one can only soften. The journal page truncates history
#: and the runtime caller reads its own ``limit=200`` per (strategy, symbol) — a
#: THIRD basis again. None of the three is *the* population; name which one any
#: quoted number came from.
DEFAULT_BAND = 2.0

#: Below this many historical sizes the verdict is ``no_history`` — *we could
#: not look* — and the caller PROCEEDS.
#:
#: ⚠️ **THIS IS DELIBERATELY PERMISSIVE AND THE TRADE-OFF IS STATED RATHER THAN
#: HIDDEN.** With one or two samples ``min`` and ``max`` collapse onto nearly
#: the same number, so a band around them is not a distribution — it is a point,
#: and refusing on it would bare-orphan a young strategy's first adopt for no
#: evidence. A bare orphan is not free either: it runs on a static stop with no
#: ``monitor()``. So an ungradeable attribution is allowed through and RECORDED,
#: never silently treated as supported.
DEFAULT_MIN_HISTORY = 3


@dataclass(frozen=True)
class SizeSupport:
    """The verdict for one proposed orphan→strategy attribution.

    ``lo``/``hi`` are populated only when a band was actually computed
    (``supported`` / ``size_implausible``); on the other two states they are
    ``None`` — never ``0.0``, which would read as a real bound.
    """

    state: str
    #: True only for ``size_implausible``. ⚠️ Read THIS, never ``state ==
    #: "supported"``: ``no_history`` and ``unreadable`` are also not "supported"
    #: and must NOT refuse, so a caller testing for equality with "supported"
    #: would bare-orphan every ungradeable adopt.
    refuses: bool = False
    size: Optional[float] = None
    n_history: int = 0
    lo: Optional[float] = None
    hi: Optional[float] = None
    #: Human-facing reason for the caller's log line. Never parsed.
    detail: str = ""


def _positive_float(raw: Any) -> Optional[float]:
    """Absolute value of *raw*, or ``None`` when it will not parse or is <= 0.

    ``None`` is *this is not a usable size*, never ``0.0`` — the same discipline
    the rest of this path is about. A zero or negative quantity is not a
    position we can score.
    """
    if raw is None:
        return None
    try:
        val = abs(float(raw))
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def sizes_from_trades(
    rows: Optional[Iterable[Any]],
    *,
    exclude_setup_types: Sequence[str] = ("adopted_orphan",),
) -> Optional[list[float]]:
    """Pull usable ``position_size`` values out of journal ``trades`` rows.

    Returns ``None`` when *rows* is ``None`` — *we could not read the journal* —
    and a (possibly empty) list otherwise. Those are different facts and the
    caller grades them differently.

    ⚠️ **Adopted rows are excluded from the history by default, and that is
    load-bearing rather than tidiness.** A previous wrong attribution writes its
    own size into the claimed strategy's history; counting it would widen the
    band by exactly the outlier the check exists to catch, so the second
    identical mis-attribution would pass. The measured population was built the
    same way — "non-adopted rows" — so this matches how the threshold was
    validated.
    """
    if rows is None:
        return None
    excluded = {str(s) for s in exclude_setup_types}
    out: list[float] = []
    for row in rows:
        try:
            setup = str((row or {}).get("setup_type") or "")
            if setup in excluded:
                continue
            if (row or {}).get("is_backtest"):
                continue
            val = _positive_float((row or {}).get("position_size"))
        except AttributeError:
            continue
        if val is not None:
            out.append(val)
    return out


def assess_size_support(
    *,
    size: Any,
    history_sizes: Optional[Sequence[Any]],
    band: float = DEFAULT_BAND,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> SizeSupport:
    """Can the claimed strategy's own size history support *size*?

    Pure. ``history_sizes`` is ``None`` for *we could not read it* and a
    sequence otherwise; an empty or too-short sequence is ``no_history``.

    Only ``size_implausible`` sets :attr:`SizeSupport.refuses`.
    """
    want = _positive_float(size)
    if want is None:
        return SizeSupport(
            state="unreadable",
            detail=(
                f"adopted size {size!r} will not parse as a positive quantity, "
                "so no attribution can be scored against it"
            ),
        )

    if history_sizes is None:
        return SizeSupport(
            state="unreadable",
            size=want,
            detail=(
                "the claimed strategy's size history could not be read — this "
                "is *we did not look*, and it does NOT refuse"
            ),
        )

    usable = [v for v in (_positive_float(h) for h in history_sizes)
              if v is not None]
    if len(usable) < max(1, int(min_history)):
        return SizeSupport(
            state="no_history",
            size=want,
            n_history=len(usable),
            detail=(
                f"only {len(usable)} usable historical size(s) for this "
                f"strategy on this symbol (floor {min_history}) — too few to "
                "form a distribution, so the attribution is UNGRADED and "
                "proceeds"
            ),
        )

    try:
        factor = float(band)
    except (TypeError, ValueError):
        factor = DEFAULT_BAND
    if not factor > 1.0:
        # A band at or below 1.0 would refuse anything but the exact observed
        # extremes. Fall back rather than silently arming a near-total refusal
        # on an order path — the `CANDLE_CACHE_TTL_FRACTION` discipline.
        factor = DEFAULT_BAND

    lo = min(usable) / factor
    hi = max(usable) * factor

    if lo <= want <= hi:
        return SizeSupport(
            state="supported", size=want, n_history=len(usable),
            lo=lo, hi=hi,
            detail=(
                f"{want} sits inside this strategy's own observed range on "
                f"this symbol widened by {factor}x ({lo:.10g}..{hi:.10g}, "
                f"n={len(usable)})"
            ),
        )

    ratio = want / hi if want > hi else lo / want
    return SizeSupport(
        state="size_implausible", refuses=True, size=want,
        n_history=len(usable), lo=lo, hi=hi,
        detail=(
            f"{want} is {ratio:.4g}x outside this strategy's own observed "
            f"range on this symbol widened by {factor}x "
            f"({lo:.10g}..{hi:.10g}, n={len(usable)}) — the attribution "
            "cannot be supported, so the row stays a BARE orphan"
        ),
    )
