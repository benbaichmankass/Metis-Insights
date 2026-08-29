"""Tests for the settlement-soak grader.

The behaviour under test is the DENOMINATOR, not the soak read: an empty soak
means opposite things depending on whether the writer ever had a chance to
fire, and collapsing those two is the defect the script exists to prevent.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "grade_settlement_soak",
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "grade_settlement_soak.py",
)
assert _SPEC and _SPEC.loader
gss = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gss)


BEFORE = "2026-08-29T07:32:41+00:00"   # the real last pre-deploy alpaca dispatch
AFTER = "2026-08-31T13:41:00+00:00"    # a Monday post-open dispatch


def _soak(*rows: dict) -> dict:
    return {"name": "cash_settlement_soak", "present": bool(rows),
            "lines": [json.dumps(r) for r in rows]}


def _trades(*stamps: str, account: str = "alpaca_paper") -> list[dict]:
    return [{"account_id": account, "created_at": s} for s in stamps]


def test_empty_soak_with_no_post_deploy_dispatch_is_not_a_failure():
    state, _, _ = gss.grade(_soak(), _trades(BEFORE))
    assert state == "not_yet_exercised"


def test_empty_soak_AFTER_a_dispatch_is_the_finding():
    state, newest, _ = gss.grade(_soak(), _trades(BEFORE, AFTER))
    assert state == "never_wrote"
    assert newest is not None and newest.isoformat() == AFTER


def test_the_two_empty_cases_are_not_collapsed():
    """The whole point: same empty soak, opposite verdicts."""
    empty = _soak()
    assert gss.grade(empty, _trades(BEFORE))[0] != gss.grade(empty, _trades(AFTER))[0]


def test_rows_present_reads_measured():
    state, _, rows = gss.grade(_soak({"account_id": "alpaca_paper", "state": "measured"}), _trades(BEFORE))
    assert state == "measured" and len(rows) == 1


def test_an_unreadable_diag_read_is_not_graded_as_empty():
    """`None` is 'we could not look' and must not read as 'the soak is empty'."""
    assert gss.grade(None, _trades(AFTER))[0] == "unreadable"
    assert gss.grade(_soak(), None)[0] == "unreadable"


def test_a_rejected_row_still_counts_as_a_dispatch():
    """The observation is recorded on the order path, BEFORE the risk gate —
    so a refusal still proves the writer was reached."""
    trades = [{"account_id": "alpaca_live", "created_at": AFTER, "status": "rejected"}]
    assert gss.grade(_soak(), trades)[0] == "never_wrote"


def test_non_alpaca_rows_are_not_the_denominator():
    """A busy bybit book must not make the alpaca writer look exercised."""
    trades = _trades(AFTER, account="bybit_2")
    assert gss.grade(_soak(), trades)[0] == "not_yet_exercised"


def test_only_not_yet_exercised_is_silent():
    """Every state that carries information reports; the ordinary one does not."""
    reported = {}
    for state in ("measured", "never_wrote", "not_yet_exercised", "unreadable"):
        reported[state] = state != "not_yet_exercised"
    assert reported == {"measured": True, "never_wrote": True,
                        "not_yet_exercised": False, "unreadable": True}


def test_every_declared_state_is_reachable():
    seen = {
        gss.grade(_soak({"a": 1}), _trades(BEFORE))[0],
        gss.grade(_soak(), _trades(AFTER))[0],
        gss.grade(_soak(), _trades(BEFORE))[0],
        gss.grade(None, None)[0],
    }
    assert seen == {"measured", "never_wrote", "not_yet_exercised", "unreadable"}


def test_render_names_the_alpaca_live_zero_caveat():
    """A reader must not mistake a correct 0.00 for a broken gate."""
    body = gss.render("measured", None,
                      [{"account_id": "alpaca_live", "would_have_reduced_usd": 0.0}], {})
    assert "0.0` on **alpaca_live** is CORRECT" in body


def test_unparseable_timestamp_does_not_manufacture_a_dispatch():
    trades = [{"account_id": "alpaca_paper", "created_at": "not-a-date"}]
    assert gss.newest_alpaca_dispatch(trades) is None
    assert gss.grade(_soak(), trades)[0] == "not_yet_exercised"
