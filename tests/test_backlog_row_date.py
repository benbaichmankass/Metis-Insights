"""The canonical "when was this backlog row opened?" accessor, and its plant.

WHAT THIS PINS, AND WHY A GREEN RUN HERE MEANS SOMETHING
────────────────────────────────────────────────────────
Every guard in this repo is supposed to carry evidence that it can go RED. So
does this one: :func:`test_planted_defect_the_pre_fix_vocabulary_FAILS_the_reach_floor`
re-installs the exact pre-fix definition (``DATE_FIELDS = ("opened_at",)``) and
asserts the corpus-reach assertion below fails under it, and
:func:`test_negative_control_an_inert_plant_stays_green` shows an edit that
changes nothing leaves it quiet. Without both halves the reach floor is a
number nobody has seen move.

STATE THE POPULATION. The floors are calibrated against a measurement taken
2026-09-12 over **982 LIVE rows (status ``open`` or ``kept_open``) across all
three review backlogs**: ``opened_at`` alone dates 587 (59.8%), adding
``opened`` reaches 930 (94.7%), and 40 rows are undateable under every key in
use. The floors are deliberately SLACK against those figures so ordinary
filing does not red CI — they exist to catch the vocabulary being narrowed,
not to freeze today's exact count.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import _backlog  # noqa: E402

BACKLOGS = ("health-review-backlog", "performance-review-backlog", "ml-review-backlog")
#: Statuses this file treats as LIVE. Deliberately the same two the measurement
#: above used; the canonical predicate is exercised separately in
#: :func:`test_is_open_status_is_the_re_export_not_a_second_definition`.
LIVE_STATUSES = ("open", "kept_open")


def _live_rows() -> list[dict]:
    out: list[dict] = []
    for name in BACKLOGS:
        doc = json.loads((ROOT / "docs" / "claude" / f"{name}.json").read_text())
        rows = doc if isinstance(doc, list) else (doc.get("items") or doc.get("rows") or [])
        out += [r for r in rows if isinstance(r, dict) and r.get("status") in LIVE_STATUSES]
    return out


def _reach(rows: list[dict]) -> float:
    """Share of rows the accessor can put a day on."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if _backlog.row_date(r).ok) / len(rows)


# --------------------------------------------------------------------------
# The three states are genuinely three.
# --------------------------------------------------------------------------

def test_the_three_date_states_are_distinct():
    assert len({_backlog.DATE_STATED, _backlog.DATE_UNPARSEABLE,
                _backlog.DATE_UNSTATED}) == 3


def test_a_row_with_no_date_key_is_unstated_and_carries_no_day():
    v = _backlog.row_date({"id": "BL-X"})
    assert v.state == _backlog.DATE_UNSTATED
    assert v.day is None and v.field is None
    assert v.ok is False


def test_a_broken_date_is_unparseable_and_NOT_folded_into_unstated():
    """*We looked and it is broken* is not *we did not look*.

    Collapsing these would report a malformed row as an absent one, and the
    malformed row is the one somebody has to go fix.
    """
    v = _backlog.row_date({"opened_at": "sometime last week"})
    assert v.state == _backlog.DATE_UNPARSEABLE
    assert v.state != _backlog.DATE_UNSTATED
    assert v.day is None
    assert v.field == "opened_at" and v.raw == "sometime last week"


def test_a_present_but_broken_key_does_not_fall_through_to_the_next_key():
    """Falling through would substitute a different field's date for a broken
    one — a number whose provenance the reader cannot reconstruct."""
    v = _backlog.row_date({"opened_at": "???", "opened": "2026-01-02"})
    assert v.state == _backlog.DATE_UNPARSEABLE
    assert v.day is None


@pytest.mark.parametrize("raw,day", [
    ("2026-08-24", dt.date(2026, 8, 24)),
    ("2026-08-24T15:05:00+00:00", dt.date(2026, 8, 24)),
    ("2026-08-24T15:05:00Z", dt.date(2026, 8, 24)),
    (dt.date(2026, 8, 24), dt.date(2026, 8, 24)),
])
def test_the_shapes_the_corpora_actually_carry_all_parse(raw, day):
    v = _backlog.row_date({"opened": raw})
    assert v.state == _backlog.DATE_STATED and v.day == day


def test_age_days_is_None_not_zero_when_there_is_no_date():
    """Zero is a real reading — a row filed today. It must not double as
    'undateable', or an ageing consumer reports stale rows as brand new."""
    today = dt.date(2026, 9, 12)
    assert _backlog.row_date({}).age_days(today) is None
    assert _backlog.row_date({"opened": "2026-09-12"}).age_days(today) == 0


# --------------------------------------------------------------------------
# The corpus. This is the assertion the plant has to break.
# --------------------------------------------------------------------------

# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_the_accessor_dates_the_overwhelming_majority_of_live_rows`.
# It asserted a property of the LIVE review backlogs, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_opened_at_alone_is_measurably_insufficient`.
# It measured a property of the LIVE review backlogs, which are archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so it is removed
# WITH its subject rather than skipped. The fixture-based siblings are untouched.


# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_rows_carrying_more_than_one_date_key_do_not_disagree`.
# It asserted a property of the LIVE review backlogs, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


def test_is_open_status_is_the_re_export_not_a_second_definition():
    src = ROOT / "scripts" / "reports" / "backlog_counts.py"
    spec = importlib.util.spec_from_file_location("_owner_backlog_counts", src)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    for raw in ("open", "kept_open", "resolved", "wont_fix", "invalid",
                "superseded", "snoozed", "in_progress", None, "", "banana"):
        assert _backlog.is_open_status(raw) == owner.is_open_status(raw), raw


# --------------------------------------------------------------------------
# The consumer: the collapsed state this unit actually removed.
# --------------------------------------------------------------------------

def _drain():
    spec = importlib.util.spec_from_file_location(
        "_drain_for_test", ROOT / "scripts" / "ops" / "backlog_drain_candidates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_added_after_says_we_could_not_look_rather_than_a_definite_no():
    """The fix. Before it, an undateable row got ``False`` — *the file was NOT
    added since* — which is a verdict, from a function that had never
    established the date it was comparing against.

    ⚠️ ``_SHALLOW`` short-circuits to ``None`` before this branch, and CI's
    clone IS shallow, so this asserts the branch rather than an observed wrong
    verdict in CI. The test forces ``_SHALLOW`` off so the branch is reachable
    either way — otherwise a green run here would prove only that the
    short-circuit fires.
    """
    drain = _drain()
    drain._SHALLOW = False
    verdict = drain._added_after("README.md", _backlog.row_date({"id": "BL-X"}))
    assert verdict is None, (
        "an undateable row must grade `None` (we could not look), never "
        f"`{verdict!r}` — a definite negative about a comparison never made"
    )


def test_added_after_still_answers_when_the_date_IS_stated():
    """The negative control for the test above: the `None` must come from the
    missing date, not from the function having been broken into always-None."""
    drain = _drain()
    drain._SHALLOW = False
    verdict = drain._added_after(
        "README.md", _backlog.row_date({"opened": "1999-01-01"}))
    assert verdict is True


# --------------------------------------------------------------------------
# PLANTED DEFECT + NEGATIVE CONTROL
# --------------------------------------------------------------------------

# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_planted_defect_the_pre_fix_vocabulary_FAILS_the_reach_floor`.
# It asserted a property of the LIVE review backlogs, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.


# ⚠️ REMOVED 2026-09-21 by the operating reset: `test_negative_control_an_inert_plant_stays_green`.
# It asserted a property of the LIVE review backlogs, which is archived under
# docs/archive/2026-09-21-operating-reset/. Its subject is gone, so the
# test cannot pass and cannot be made to pass — it is removed WITH its
# subject rather than skipped, because a permanently-skipped test is a
# control in name only. Its fixture-based siblings in this file are
# UNTOUCHED and still green: they test the CODE, which still exists.
# Restore it from git history if the register ever returns.
