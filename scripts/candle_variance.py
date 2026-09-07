#!/usr/bin/env python3
"""The ONE definition of "is this candle series degenerate?" — stdlib only.

WHY THIS EXISTS (MI-157, 2026-09-07)
------------------------------------
Five committed candle fixtures under ``data/ohlcv/`` held 300 rows with
exactly **one distinct close** each — btc 95000.0, eth 3200.0, spy 580.0,
qqq 490.0 — and the whole OHLC bar was constant, not just the close. A
harness pointed at one produced a confident, fully-green, entirely
meaningless constant-price backtest, and nothing at the call site said so:
the placeholder-ness lived only in the commit message that added them
(``29014899``, *"placeholder OHLCV data"*), while ``.gitignore`` line 75
already declared ``data/ohlcv/*.csv`` should not be committed at all.

That is the class this repo has already paid for once — the phantom
−$6,358 "exit leak" that did not exist
(``docs/sprint-logs/S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30.md``): a
manufactured figure acted on because the instrument did not declare
itself. **The remedy is that the READ refuses, not that a human
remembers.**

WHY IT IS ITS OWN MODULE
------------------------
Three callers need this verdict and they do not share a dependency set:
``scripts/candle_io.py`` (pandas, 9 research consumers),
``bin/backtest_ict.py`` (pandas, the manifest path — the only route that
ever reached ``data/ohlcv/``), and
``scripts/ci/check_candle_fixture_variance.py`` (a CI guard that must run
in the fast static-guard job, which does not install pandas). Putting the
grader in ``candle_io`` would have forced the guard to import pandas or to
re-derive the rule — and **two definitions of degeneracy would drift
exactly the way the two candle READERS drifted on JSONL**
(``BL-20260809-TWO-CANDLE-READERS-DIVERGE-ON-JSONL``). So it is stdlib
only, and everything else imports it.

THREE STATES, NEVER COLLAPSED
-----------------------------
Per ``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states" — a
zero-variance series and a series too short to *have* a return
distribution are different facts, and grading the second one clean is how
a one-row file passes as measured:

``non_degenerate``
    Graded. The close series varies. The only passing state.
``degenerate``
    Graded. The close series does not vary. **The finding.**
``not_gradeable``
    Fewer than 2 usable closes, so no return series EXISTS.
    *We could not look.* **Never a pass, and never an error either** — the
    two decisions belong to different callers.

Only ``degenerate`` raises. Refusing a 1-row file is a separate policy
call this module deliberately does not make on the caller's behalf.
"""
from __future__ import annotations

import collections
from typing import Iterable

__all__ = [
    "VarianceGrade",
    "DegenerateSeriesError",
    "grade_close_variance",
    "GRADE_STATES",
]

#: Every state :func:`grade_close_variance` can return. Registered here so a
#: consumer can assert it branches on all of them rather than on a boolean.
GRADE_STATES = ("non_degenerate", "degenerate", "not_gradeable")

VarianceGrade = collections.namedtuple(
    "VarianceGrade",
    "state rows distinct_close nonzero_returns total_returns detail",
)


class DegenerateSeriesError(ValueError):
    """Raised when a candle series has no price variation at all.

    A backtest over a constant-price series produces confident numbers that
    mean nothing: every return is exactly 0.0, so expectancy, correlation
    and rho are **undefined** rather than merely weak. Failing loudly is the
    whole point — the defect being fixed is a *silent* fallback to a flat
    series, and a fix that reproduced the silence would be no fix.
    """


def grade_close_variance(closes: Iterable) -> VarianceGrade:
    """Grade a close series for degeneracy. Pure — no IO, never raises.

    ``closes`` is any iterable of values. Anything non-numeric (or NaN) is
    dropped before grading: a row with an unparseable price is not evidence
    of variance, and is not evidence of its absence either.
    """
    usable = []
    for value in closes:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN — the one value that is not equal to itself
            continue
        usable.append(number)

    rows = len(usable)
    distinct = len(set(usable))

    if rows < 2:
        return VarianceGrade(
            state="not_gradeable",
            rows=rows,
            distinct_close=distinct,
            nonzero_returns=0,
            total_returns=0,
            detail=(
                f"only {rows} usable close(s) — fewer than 2, so no return "
                "series exists. This is 'we could not look', NOT 'the series "
                "is fine'."
            ),
        )

    returns = [
        usable[i] / usable[i - 1] - 1.0
        for i in range(1, rows)
        if usable[i - 1]
    ]
    nonzero = sum(1 for value in returns if value != 0.0)

    if distinct <= 1 or nonzero == 0:
        return VarianceGrade(
            state="degenerate",
            rows=rows,
            distinct_close=distinct,
            nonzero_returns=nonzero,
            total_returns=len(returns),
            detail=(
                f"{rows} rows carry {distinct} distinct close(s) and "
                f"{nonzero} of {len(returns)} returns are non-zero — the "
                "price series does not move. Any backtest over it is "
                "arithmetically confident and empirically empty."
            ),
        )

    return VarianceGrade(
        state="non_degenerate",
        rows=rows,
        distinct_close=distinct,
        nonzero_returns=nonzero,
        total_returns=len(returns),
        detail=(
            f"{rows} rows, {distinct} distinct closes, "
            f"{nonzero} of {len(returns)} returns non-zero."
        ),
    )
