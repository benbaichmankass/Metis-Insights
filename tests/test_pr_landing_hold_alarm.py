"""Tests for scripts/ops/pr_landing_hold_alarm.py (JC-CA-04,
docs/audits/code-audit-2026-09-27.md §6 `A09-hold-merge-unenforced`).

Wires the module's own ``_self_test`` into pytest (same shape as
tests/test_pr_mergeability.py — the self-tests are the contract; running
them here means pytest fails when they do) and adds MUTATION checks: each
load-bearing predicate is broken in isolation and the suite is proved to
NOTICE. A green run over an unmutated tree proves a test ran, never that it
discriminates.

Run: python3 -m pytest tests/test_pr_landing_hold_alarm.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import pr_landing_hold_alarm as pla  # noqa: E402


def test_self_test_passes():
    ok, fails = pla._self_test(quiet=True)
    assert ok, f"pr_landing_hold_alarm self-test failures: {fails}"


def test_alarm_fires_for_tier3_hold_with_no_reviews():
    v = pla.grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
                   reviews=[], reviews_unreadable=False,
                   pr_number=100, pr_title="flip a gate")
    assert v.state == pla.ALARM
    assert "#100" in v.ping_message()
    assert "flip a gate" in v.ping_message()


def test_clean_for_tier3_hold_with_human_approval():
    v = pla.grade(
        {"tier": 3, "landing": "hold"}, decl_unreadable=False,
        reviews=[{"state": "APPROVED", "user": {"login": "benbaichmankass", "type": "User"}}],
        reviews_unreadable=False,
    )
    assert v.state == pla.CLEAN


def test_bot_approval_does_not_clear_the_alarm():
    v = pla.grade(
        {"tier": 3, "landing": "hold"}, decl_unreadable=False,
        reviews=[{"state": "APPROVED", "user": {"login": "github-actions[bot]", "type": "Bot"}}],
        reviews_unreadable=False,
    )
    assert v.state == pla.ALARM


def test_out_of_scope_landings_never_alarm():
    for decl in (
        {"tier": 1, "landing": "hold"},
        {"tier": 2, "landing": "self", "approved_by": "x"},
        {"tier": 3, "landing": "mandate", "mandate": "MD-X"},
    ):
        v = pla.grade(decl, decl_unreadable=False, reviews=[], reviews_unreadable=False)
        assert v.state == pla.CLEAN, decl


def test_missing_declaration_alarms_rather_than_staying_quiet():
    v = pla.grade(None, decl_unreadable=False, reviews=[], reviews_unreadable=False)
    assert v.state == pla.ALARM


def test_unreadable_is_never_collapsed_into_clean_or_alarm():
    v = pla.grade(None, decl_unreadable=True, reviews=None, reviews_unreadable=True)
    assert v.state == pla.UNREADABLE
    v2 = pla.grade({"tier": 3, "landing": "hold"}, decl_unreadable=False,
                    reviews=None, reviews_unreadable=True)
    assert v2.state == pla.UNREADABLE


def test_cli_exit_codes(tmp_path, capsys):
    decl_path = tmp_path / "decl.json"
    decl_path.write_text(json.dumps({"tier": 3, "landing": "hold"}))
    reviews_path = tmp_path / "reviews.json"
    reviews_path.write_text(json.dumps([]))

    rc = pla.main(["--decl-json", str(decl_path), "--reviews-json", str(reviews_path),
                   "--pr-number", "42", "--pr-title", "test pr"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "#42" in out

    reviews_path.write_text(json.dumps(
        [{"state": "APPROVED", "user": {"login": "op", "type": "User"}}]))
    rc = pla.main(["--decl-json", str(decl_path), "--reviews-json", str(reviews_path)])
    assert rc == 0

    rc = pla.main(["--decl-json", "UNREADABLE", "--reviews-json", "UNREADABLE"])
    assert rc == 3

    rc = pla.main(["--decl-json", "MISSING", "--reviews-json", str(reviews_path)])
    assert rc == 2  # no declaration at all is itself an alarm


# ---------------------------------------------------------------------------
# MUTATION CHECKS — break a load-bearing predicate and prove _self_test
# notices.
# ---------------------------------------------------------------------------
def test_mutation_widening_alarm_tiers_is_load_bearing(monkeypatch):
    # If tier 2/3 stopped being the checked set (e.g. someone narrows it to
    # tier 3 only), the self-test's tier-2 case must go red.
    monkeypatch.setattr(pla, "_ALARM_TIERS", (3,))
    v = pla.grade({"tier": 2, "landing": "hold"}, decl_unreadable=False,
                  reviews=[{"state": "CHANGES_REQUESTED", "user": {"login": "op", "type": "User"}}],
                  reviews_unreadable=False)
    assert v.state == pla.CLEAN, "sanity: the mutation took (tier 2 no longer checked)"
    ok, _ = pla._self_test(quiet=True)
    assert not ok, "the self-test must go RED on this mutation"


def test_mutation_bot_login_counting_as_human_is_load_bearing(monkeypatch):
    # The exact self-land-failure shape JC-CA-04/A09 exists to catch: a bot
    # approving its own PR must never clear the alarm.
    monkeypatch.setattr(pla, "_is_human_approval", lambda review: review.get("state") == "APPROVED")
    v = pla.grade(
        {"tier": 3, "landing": "hold"}, decl_unreadable=False,
        reviews=[{"state": "APPROVED", "user": {"login": "github-actions[bot]", "type": "Bot"}}],
        reviews_unreadable=False,
    )
    assert v.state == pla.CLEAN, "sanity: the mutation took (bot now counts as human)"
    ok, _ = pla._self_test(quiet=True)
    assert not ok, "the self-test must go RED on this mutation"


def test_mutation_missing_declaration_reading_as_clean_is_load_bearing(monkeypatch):
    original_grade = pla.grade

    def _leaky_grade(decl, **kwargs):
        if decl is None and not kwargs.get("decl_unreadable"):
            return pla.Verdict(pla.CLEAN, "mutated: missing decl treated as clean",
                                kwargs.get("pr_number"), kwargs.get("pr_title"))
        return original_grade(decl, **kwargs)

    monkeypatch.setattr(pla, "grade", _leaky_grade)
    v = pla.grade(None, decl_unreadable=False, reviews=[], reviews_unreadable=False)
    assert v.state == pla.CLEAN, "sanity: the mutation took"
    ok, _ = pla._self_test(quiet=True)
    assert not ok, "the self-test must go RED on this mutation"
