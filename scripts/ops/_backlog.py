#!/usr/bin/env python3
# wiring: manual-only - this is a LIBRARY. Its consumers are
# scripts/ops/check_backlog_criteria.py (a CI guard, run per-diff) and any
# session or renderer that needs to ITERATE a backlog row's resolution
# criteria. It has no schedule of its own because reading a backlog row is not
# a job, it is something other jobs do.
"""Canonical accessors for the three review-backlog corpora.

WHY THIS EXISTS
---------------
``resolution_criteria`` carries TWO shapes across the backlogs — a bare
``str`` on the large majority of rows and a ``list[str]`` on the rest — and
nothing pinned it. The hazard is not the polymorphism, it is that the obvious
read is *silently wrong on the majority shape*::

    for c in row["resolution_criteria"]:   # correct for a list
        ...                                # yields ONE CHARACTER for a string

It does not raise. A session rendering a row's criteria gets a column of
single letters, and either notices (wasted time) or does not (a criteria list
reported as ~900 empty bullets). That is the unprovenanced-diagnostic
sub-class A shape from ``CLAUDE.md``: the accessor does not compute what the
label says, and nothing in the output reveals the substitution. Filed as
``BL-20260823-RESOLUTION-CRITERIA-HAS-TWO-TYPES-AND-ITERATING-IT-YIELDS-CHARACTERS``.

THE CORPUS DECISION, recorded here because that row required one either way
-------------------------------------------------------------------------
**The string shape is PERMANENTLY SUPPORTED and this accessor is mandatory.**
The alternative — a mechanical pass normalising every string row to a
one-element list — was considered and rejected on measurement, not taste:

* String is the DOMINANT convention, not a legacy tail. Measured 2026-08-25
  over all three backlogs: health 672 str / 36 list / 203 absent;
  performance 72 str / 0 list; ml 78 str / 1 list. The string rows span the
  full date range *including the same day as the newest list rows*, so there
  is no "new rows have grown out of it" story to tell.
* A rewrite of ~750 rows of prose is a large diff across files that every
  diff-scoped guard reads, for zero behavioural gain over one accessor.
* The row's own criterion 4 warns that normalising must not mangle prose: a
  string with sentence-final periods is ONE criterion, and splitting on
  ``". "`` would invent criteria nobody wrote. Wrapping each string in a
  one-element list is safe but then the corpus carries ~750 one-element lists,
  which is the string shape with extra brackets.

So: **wrap at READ time, never at rest.** New rows may use either shape.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not judge whether the criteria are any GOOD — that is
``check_backlog_criteria.py``'s job, and keeping the two apart is why this
module can be imported by a renderer that has no opinion about quality.
"""
from __future__ import annotations

from typing import Any, List, Mapping

__all__ = [
    "criteria_list", "criteria_text", "CRITERIA_FIELD", "UnsupportedCriteriaShape",
    "DATE_FIELDS", "RowDate", "DATE_STATED", "DATE_UNPARSEABLE", "DATE_UNSTATED",
    "row_date", "is_open_status",
]

CRITERIA_FIELD = "resolution_criteria"


class UnsupportedCriteriaShape(TypeError):
    """The field is neither ``str``, ``list``, nor absent.

    Raised rather than coerced. A dict or an int stringifies happily to
    something that clears a length floor, so silently accepting it is how a
    malformed row passes a guard that exists to catch malformed rows.
    """


def criteria_list(row: Mapping[str, Any]) -> List[str]:
    """Return this row's resolution criteria as a list of strings.

    * ``list``   -> each element ``str()``-ed and stripped; empty entries dropped
    * ``str``    -> a ONE-element list (never split — see the module docstring)
    * absent/None-> ``[]``
    * anything else -> :class:`UnsupportedCriteriaShape`

    ``[]`` means "this row states no criteria", which is a real and common
    state (203 health rows) and is distinct from "the field is malformed" —
    hence the raise rather than a third silent empty.
    """
    raw = row.get(CRITERIA_FIELD)
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        out: List[str] = []
        for item in raw:
            if item is None:
                continue
            if not isinstance(item, (str, int, float)):
                raise UnsupportedCriteriaShape(
                    f"{CRITERIA_FIELD} list contains a {type(item).__name__}; "
                    f"only scalars are supported (row id={row.get('id')!r})"
                )
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    raise UnsupportedCriteriaShape(
        f"{CRITERIA_FIELD} is a {type(raw).__name__}; expected str, list or "
        f"absent (row id={row.get('id')!r})"
    )


def criteria_text(row: Mapping[str, Any]) -> str:
    """The criteria as one string, for length/placeholder checks.

    Joined with a newline so a length floor measures the PROSE and not the
    ``repr`` punctuation of a list — ``str(['a','b'])`` counts brackets and
    quotes toward the floor, which is exactly the accidental pass this
    accessor replaces.
    """
    return "\n".join(criteria_list(row))


# ---------------------------------------------------------------------------
# WHEN WAS THIS ROW OPENED?
# ---------------------------------------------------------------------------
# The second question every backlog reader asks, and — until this accessor —
# the one each reader answered for itself. Filed as
# BL-20260823-BACKLOG-TRIAGE-SEES-47-PERCENT-OF-THE-LIVE-ROWS, whose first
# resolution criterion asks for exactly one helper answering "which rows are
# LIVE?" and "when was this row opened?".
#
# MEASURED 2026-09-12, and STATE THE POPULATION: 982 LIVE rows (status `open`
# or `kept_open`) across all three review backlogs.
#
#     ['opened_at']                                     587  59.8%
#     ['opened_at', 'opened']                           930  94.7%
#     ['opened_at', 'opened', 'filed_at']               932  94.9%
#     ['opened_at', 'opened', 'filed_at', 'date']       938  95.5%
#     + 'filed', 'filed_on'                             942  95.9%
#
# So `opened` is not a typo tail — it is 352 rows, a second row SCHEMA living
# in the same file, and a reader keyed on `opened_at` alone silently treats
# 40.2% of the live corpus as undateable. The four long-tail keys buy 1.2pp
# between them and are included because excluding them would be a choice to
# drop rows we can in fact date, not because they are common.
#
# FOUR definitions were live in the tree when this was written and they
# disagree by that much: `opened_at` only (backlog_drain_candidates,
# constraint_readout, migrate_backlog_to_work_objects), `opened` only
# (check_open_items, over a different register), a two-key map
# (check_register_ids) and a four-key tuple (system_review_checklist).
#
# ⚠️ **NO TIEBREAKER, ON MEASUREMENT.** 15 live rows carry more than one date
# key and **all 15 agree on the day** — zero disagreements. So first-key-wins
# is not a policy papering over a conflict; there is no conflict. If that ever
# stops being true the accessor should GROW A STATE rather than pick a winner
# silently, and the self-test pins the current fact so the change is noticed.
#
# ⚠️ **40 LIVE ROWS ARE UNDATEABLE UNDER EVERY KEY.** They get DATE_UNSTATED,
# which is *we could not establish it*, never a substituted date and never a
# definite negative. That distinction is the whole point: the caller this was
# written for, `backlog_drain_candidates._added_after`, answered "was this file
# added after the row opened?" with `False` — a definite NO — for a row whose
# date it had never established, in a function that had already invented
# `None` for that exact meaning one branch earlier.

#: Date keys in use across the three review backlogs, in descending measured
#: frequency. ⚠️ ORDER IS LOAD-BEARING: the first key PRESENT wins, so adding a
#: key at the front changes which value every multi-keyed row reports. Append,
#: never prepend, without re-measuring the agreement fact above.
DATE_FIELDS = ("opened_at", "opened", "filed_at", "date", "filed", "filed_on")

#: A date key was present and parsed to a day.
DATE_STATED = "stated"
#: A date key was present and its value will not parse as a date. **We looked
#: and it is broken** — a real defect in the row, and deliberately NOT folded
#: into `unstated`, which would report a malformed row as an absent one.
DATE_UNPARSEABLE = "unparseable"
#: No recognised date key at all. **We did not look**, never "the row is new"
#: and never "the file was not added after it".
DATE_UNSTATED = "unstated"


class RowDate:
    """A row's open date, with the reason when there is not one.

    Three states, never collapsed: :data:`DATE_STATED`,
    :data:`DATE_UNPARSEABLE`, :data:`DATE_UNSTATED`.

    ``day`` is ``None`` unless ``state == DATE_STATED``. It is never a
    substituted default — an epoch or a "today" fallback would make an
    undateable row look brand new to an ageing consumer, which is the
    fabricated-measurement class this repo keeps paying for.
    """

    __slots__ = ("state", "day", "field", "raw")

    def __init__(self, state: str, day=None, field=None, raw=None):
        self.state, self.day, self.field, self.raw = state, day, field, raw

    @property
    def ok(self) -> bool:
        """True only for :data:`DATE_STATED`. Both other states are falsey and
        that is deliberate — but read ``state``, never ``ok``, when the two
        not-ok reasons need telling apart."""
        return self.state == DATE_STATED

    def age_days(self, today) -> Any:
        """Days since the stated date, or ``None`` when there is no date.

        ``None``, never ``0``: zero is a real reading (a row filed today).
        """
        return None if self.day is None else (today - self.day).days

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (f"RowDate({self.state!r}, day={self.day!r}, "
                f"field={self.field!r}, raw={self.raw!r})")


def _as_day(value: Any):
    """Parse a backlog date value to a ``date``, or ``None`` if it will not.

    Accepts the two shapes the corpora actually carry — a bare ``YYYY-MM-DD``
    and a full ISO timestamp, with or without a ``Z`` suffix — by taking the
    leading 10 characters. Anything else is a parse FAILURE and says so rather
    than guessing.
    """
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def row_date(row: Mapping[str, Any]) -> RowDate:
    """When was this backlog row opened? See :data:`DATE_FIELDS`.

    The FIRST key present wins (see the order note on ``DATE_FIELDS``). A key
    present but unparseable stops the search and grades
    :data:`DATE_UNPARSEABLE` rather than falling through to the next key:
    falling through would silently substitute a different field's date for a
    broken one, which is how a reader ends up confidently quoting a number
    whose provenance nobody can reconstruct.
    """
    for field in DATE_FIELDS:
        if field not in row:
            continue
        raw = row.get(field)
        if raw in (None, ""):
            continue
        day = _as_day(raw)
        if day is None:
            return RowDate(DATE_UNPARSEABLE, None, field, raw)
        return RowDate(DATE_STATED, day, field, raw)
    return RowDate(DATE_UNSTATED)


def is_open_status(raw: object) -> bool:
    """Is this row LIVE? Re-exported from the ONE owner, never re-derived.

    The predicate lives in ``scripts/reports/backlog_counts.py``; it is
    surfaced here so a reader needing both halves of
    BL-20260823-BACKLOG-TRIAGE-SEES-47-PERCENT-OF-THE-LIVE-ROWS has a single
    import site instead of two. ⚠️ Defining a second copy here would BE that
    row's defect, one level up.
    """
    import importlib.util
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "reports" / "backlog_counts.py")
    spec = importlib.util.spec_from_file_location("_backlog_counts_for_backlog", src)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging fault
        raise RuntimeError(
            "_backlog: cannot load the canonical open-status predicate from "
            f"{src} — refusing to guess it rather than shipping a second "
            "definition of which rows are live"
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return bool(mod.is_open_status(raw))
