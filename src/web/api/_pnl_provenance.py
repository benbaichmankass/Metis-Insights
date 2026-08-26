"""One owner for "how much of this journal-`pnl` aggregate was ever MEASURED?"

GATE 0 / G3 (`docs/claude/WORKPLAN-2026-08-26.md`): *"A consumer receiving a sum
or a rate must receive its coverage beside it — the `rCoverage`/`pnlCoverage`
discipline, applied to the rest."*

`/api/bot/performance` has carried `pnlCoverage` since 2026-07-31. Every other
aggregate over journal `pnl` published a sum and a rate with no statement of
provenance at all. This module is what the rest of them call, so the answer is
computed in ONE place rather than re-derived per router — the fourth bespoke
copy is how two surfaces end up disagreeing about the same population.

⚠️ **THE COUNT AND THE SUM ARE OVER DIFFERENT POPULATIONS, DELIBERATELY.**
Copied from `/performance` rather than reinvented, because two surfaces
reporting the same population under the same key must not disagree:

* ``pnlCoverage`` / ``pnlMeasuredCount`` — **MEASURED-only**. ESTIMATED is *not*
  "covered"; that is the canonical `provenance.coverage` population.
* ``totalPnLMeasured`` — sums **MEASURED + ESTIMATED**.

Neither may be "harmonised" to the other. `/performance`'s own note records that
the R4 promotion gate depends on exactly that asymmetry.

⚠️ **THREE STATES, NEVER COLLAPSED.**

* all ``None`` — **we could not look**. An older journal with no ``notes``
  column cannot be graded at all, and reporting zeros there would assert an
  observation nobody made.
* real zero counts with a ``None`` ratio — **we looked and the population is
  empty**. A coverage ratio over zero rows does not exist.
* ``0.0`` coverage — **we looked and nothing was measured**. Reachable, and a
  different statement from either of the above.

The keys are always PRESENT. A key that vanishes makes a consumer branch on
absence, and absence is not one of the states.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from src.runtime.provenance import (
    ESTIMATED,
    MEASURED,
    classify_pnl,
    coverage,
)

logger = logging.getLogger(__name__)

#: The four keys, always present, so a consumer never branches on absence.
KEYS: Tuple[str, ...] = (
    "pnlCoverage", "pnlMeasuredCount", "pnlEstimatedCount", "totalPnLMeasured",
)


def could_not_look() -> Dict[str, Any]:
    """We could not grade this population at all — every key ``None``."""
    return {k: None for k in KEYS}


def looked_and_found_nothing() -> Dict[str, Any]:
    """We looked; the population is empty. Counts are REAL zeros.

    Distinct from :func:`could_not_look`. The ratio is ``None`` in both — a
    coverage ratio over zero rows does not exist — so the COUNTS are the only
    thing that separates them, which is why they are not ``None`` here.
    """
    return {"pnlCoverage": None, "pnlMeasuredCount": 0,
            "pnlEstimatedCount": 0, "totalPnLMeasured": 0.0}


def block_for_rows(rows: Iterable[Any]) -> Dict[str, Any]:
    """Grade an already-fetched iterable of rows carrying ``pnl`` + ``notes``.

    Use this when the caller already holds the rows (a per-day or per-strategy
    grouping); use :func:`block_for_query` when it does not.
    """
    rows = list(rows)
    if not rows:
        return looked_and_found_nothing()
    counts: Dict[str, int] = {}
    measured_sum = 0.0
    for r in rows:
        bucket = classify_pnl(r)[0]
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket in (MEASURED, ESTIMATED):
            try:
                measured_sum += float(r["pnl"])
            except (TypeError, ValueError, KeyError, IndexError):
                pass
    return {
        "pnlCoverage": coverage({**counts, "total": len(rows)}),
        "pnlMeasuredCount": counts.get(MEASURED, 0),
        "pnlEstimatedCount": counts.get(ESTIMATED, 0),
        "totalPnLMeasured": round(measured_sum, 2),
    }


def fetch_rows(
    db_path: Path, where: str, params: Sequence[Any] = (),
) -> Optional[list]:
    """Read ``(pnl, notes)`` for the population *where* describes.

    Returns ``None`` — **we could not look** — on any read failure, the
    realistic one being a journal with no ``notes`` column. Opens the DB
    strictly ``mode=ro``: this is a read path and must never be able to write.
    """
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:  # allow-silent: returns None = COULD-NOT-LOOK, never an empty/zero answer; the three states are pinned by tests/test_pnl_provenance_helper.py
        logger.warning("pnl-provenance: could not open %s", db_path, exc_info=True)
        return None
    try:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(
            f"SELECT pnl, notes FROM trades WHERE {where}", list(params)))
    except sqlite3.Error:  # allow-silent: returns None = COULD-NOT-LOOK, never an empty/zero answer; the three states are pinned by tests/test_pnl_provenance_helper.py
        logger.warning("pnl-provenance: read failed", exc_info=True)
        return None
    finally:
        conn.close()


def block_for_query(
    db_path: Path, where: str, params: Sequence[Any] = (),
    *, missing_db_is_empty: bool = False,
) -> Dict[str, Any]:
    """The whole thing: read the population and grade it.

    *missing_db_is_empty* selects which of the two "no rows" answers a MISSING
    DB FILE gets, and callers genuinely differ. ``/stats`` already reads a
    missing file as "no trades yet on a fresh install" and returns zeros, so
    passing ``True`` keeps its caveat consistent with the numbers beside it.
    A caller with no such convention should leave it ``False`` and say
    *we could not look*, which is the safer default.
    """
    if missing_db_is_empty and not Path(db_path).exists():
        return looked_and_found_nothing()
    rows = fetch_rows(db_path, where, params)
    if rows is None:
        return could_not_look()
    return block_for_rows(rows)
