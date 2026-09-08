"""Choose WHICH Bybit position row a symbol-scoped read is about.

WHY THIS EXISTS (2026-09-08, MI-204; operator-approved Tier-2).
``order_monitor._bybit_position_protection`` graded a symbol off **``rows[0]``**
of a symbol-scoped ``get_positions`` — with no zero-size skip and no book
selection — and returned a ``_flat`` dict (``size 0.0``, ``side ""``, an EMPTY
``sl_leg_ids``) whenever that first row read empty or zero.

That was sound while every symbol was one-way netting: a symbol had ONE book, so
``rows[0]`` was the only candidate and "the first row is zero" really did mean
flat. It stopped being sound when HEDGE mode was armed (2026-08-30,
``BYBIT_HEDGE_MODE_SYMBOLS``), because a hedge symbol returns a row per book and
**a zero-size sibling book can be listed first**. The symbol then reads flat
while the venue holds a live position.

**MEASURED HARM, not a hypothetical.** ``_reconcile_netting_partial_closes``
consumes that read as::

    backed = size if (size > 0 and exch_side == direction) else 0.0
    excess = journal_qty - backed

so a ``_flat`` verdict makes ``backed`` 0.0, makes ``excess`` the WHOLE journal
row, and attributes it. Trade 5568 (``bybit_1`` ETHUSDT long 26.05) was closed on
``exchange_qty: 0.0`` with a fabricated ``+$63.8225`` while the venue still held
the position and 5568's own tracked SL leg was still resting. Twelve of 21
applied soak rows read ``exchange_qty == 0.0``, and **7 of 49
``netting_attributed`` closes are on real-money ``bybit_2``**. Evidence:
``docs/claude/work/BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md``; backlog
``BL-20260908-BYBIT-POSITION-PROTECTION-GRADES-A-SYMBOL-OFF-ROWS0-SO-A-HEDGE-BOOK-READS-FLAT-AND-A-LIVE-POSITION-IS-CLOSED``.

⚠️ **THE READ DID NOT ERROR, WHICH IS THE WHOLE POINT.** The caller's documented
fail-safe — *"an unreadable exchange read is skipped (never attribute on an
unconfirmed read)"* — is true of an **erroring** read and was FALSE of this
**wrong-book** one: nothing raised, so nothing was skipped. A confident wrong
answer defeated a guard written against a loud one.

**This module is PURE**, deliberately, so the policy is arguable in tests rather
than against a live position — the lesson of
``BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG``, and
the same shape as :mod:`src.runtime.bybit_leg_sides`,
:mod:`src.runtime.over_cover_decision` and :mod:`src.runtime.stray_oca_groups`.
It selects; it never reads a venue, closes a row, or places an order.

FIVE STATES, NEVER COLLAPSED — the distinction the old code could not express is
between *"the venue says every book is empty"* and *"we could not establish what
this symbol holds"*, which ``_flat`` merged into one value:

``selected``
    Exactly one book carries size. That book is the subject of the read.
``flat``
    Rows WERE returned and every one parses to size 0. A genuine MEASUREMENT —
    the venue enumerated the books and none holds anything.
``no_rows``
    The list came back EMPTY. ⚠️ **This is NOT flat.** A symbol-scoped
    ``get_positions`` returns a row per book for a symbol the account trades, so
    an empty list means the venue told us nothing — an unrecognised symbol, a
    filtered response, a partial result. Grading it flat is what closed a live
    position, so it maps to a caller SKIP.
``ambiguous_multi_book``
    TWO OR MORE books carry size. ⚠️ Refuse rather than pick. The old code
    picked ``rows[0]``; picking the *other* book is the same defect with better
    luck, because a symbol-scoped read carries no caller direction to choose by.
``size_unreadable``
    A row's ``size`` will not parse. Fatal for the whole symbol, deliberately:
    with one size unknown we cannot prove *exactly one* book is live, so
    ``selected`` is unprovable and every other verdict would be a guess.

⚠️ **WHAT A REFUSAL COSTS, STATED RATHER THAN HIDDEN.** Both callers treat
``None`` as skip, so a refusal means the netting reconciler does not attribute
(safe — it is the false close being prevented) **and the naked sweep does not
re-arm** (a protection gap). That trade is deliberate and it is not free: on an
ambiguous symbol the previous behaviour did not protect both books either — it
graded ONE arbitrary book's size against a side-blind coverage sum, so its
re-arm decision was already incoherent. The difference is that a refusal is
LOUD. The caller logs each refusal with its state; a silent skip here would
convert a false-close defect into an invisible protection gap, which is the
worse of the two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["BookSelection", "select_position_row", "STATES"]

#: Every state this module can return. Registered here so a consumer can assert
#: it branches on all of them rather than discovering a new one at runtime.
STATES = (
    "selected",
    "flat",
    "no_rows",
    "ambiguous_multi_book",
    "size_unreadable",
)


@dataclass(frozen=True)
class BookSelection:
    """The verdict for one symbol-scoped ``get_positions`` response.

    ``row``/``size``/``position_idx`` are populated ONLY for ``selected``. On
    every other state ``row`` is ``None`` and ``size`` is ``0.0`` — and on
    ``no_rows`` / ``ambiguous_multi_book`` / ``size_unreadable`` that ``0.0``
    must **never** be read as "the symbol is flat"; read :attr:`state` first.
    """

    state: str
    row: Optional[Dict[str, Any]] = None
    size: float = 0.0
    position_idx: Any = None
    #: Books parsed as carrying size. 0 on ``flat``, 1 on ``selected``, >=2 on
    #: ``ambiguous_multi_book``. Meaningless on ``no_rows``/``size_unreadable``.
    live_count: int = 0
    #: Rows the venue returned, so a reader can tell an empty response from a
    #: fully-enumerated flat one without re-reading the payload.
    row_count: int = 0
    #: Human-facing reason, for the caller's log line. Never parsed.
    detail: str = ""

    @property
    def is_usable(self) -> bool:
        """True only for ``selected`` — a live book we can grade.

        ``flat`` is deliberately NOT usable-by-this-name: it is a real answer,
        but it names no book, so a caller must branch on it separately rather
        than fall into a ``row``-shaped path with ``row is None``.
        """
        return self.state == "selected"


def _parse_size(raw: Any) -> Optional[float]:
    """Absolute position size, or ``None`` when the venue's value will not parse.

    ``None`` is *we could not read this*, never 0.0 — the same discipline the
    rest of this file is about. An absent key is treated as unparseable rather
    than as zero: Bybit always sends ``size`` on a position row, so its absence
    means the payload is not the shape we think it is.
    """
    if raw is None:
        return None
    try:
        return abs(float(raw))
    except (TypeError, ValueError):
        return None


def select_position_row(rows: Optional[Sequence[Any]]) -> BookSelection:
    """Pick the one live book from a symbol-scoped ``get_positions`` list.

    Pure. See the module docstring for why each state exists and what a refusal
    costs the caller.
    """
    if not rows:
        return BookSelection(
            state="no_rows",
            row_count=0,
            detail=(
                "symbol-scoped get_positions returned no rows — the venue "
                "enumerated nothing, which is NOT evidence the symbol is flat"
            ),
        )

    live: List[Dict[str, Any]] = []
    live_sizes: List[float] = []
    row_count = 0
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            return BookSelection(
                state="size_unreadable",
                row_count=len(rows),
                detail=f"position row is {type(raw_row).__name__}, not a mapping",
            )
        row_count += 1
        size = _parse_size(raw_row.get("size"))
        if size is None:
            return BookSelection(
                state="size_unreadable",
                row_count=len(rows),
                detail=(
                    f"row positionIdx={raw_row.get('positionIdx')!r} has "
                    f"size={raw_row.get('size')!r}, which will not parse — so "
                    "we cannot prove exactly one book is live"
                ),
            )
        if size > 0:
            live.append(raw_row)
            live_sizes.append(size)

    if not live:
        return BookSelection(
            state="flat",
            row_count=row_count,
            detail=f"all {row_count} enumerated book(s) parse to size 0",
        )

    if len(live) > 1:
        books = ", ".join(
            f"positionIdx={r.get('positionIdx')!r} side={r.get('side')!r} size={s}"
            for r, s in zip(live, live_sizes)
        )
        return BookSelection(
            state="ambiguous_multi_book",
            live_count=len(live),
            row_count=row_count,
            detail=(
                f"{len(live)} books carry size ({books}) — refusing to pick, "
                "because a symbol-scoped read carries no direction to choose by"
            ),
        )

    chosen = live[0]
    return BookSelection(
        state="selected",
        row=chosen,
        size=live_sizes[0],
        position_idx=chosen.get("positionIdx"),
        live_count=1,
        row_count=row_count,
        detail=(
            f"one live book: positionIdx={chosen.get('positionIdx')!r} "
            f"side={chosen.get('side')!r} size={live_sizes[0]}"
        ),
    )
