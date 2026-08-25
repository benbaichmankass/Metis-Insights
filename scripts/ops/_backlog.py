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

__all__ = ["criteria_list", "criteria_text", "CRITERIA_FIELD", "UnsupportedCriteriaShape"]

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
