"""Tests for the mergeability dimension of the PR-queue watcher.

THE DEFECT UNDER TEST, and it was live when these were written
--------------------------------------------------------------
A PR can be green on every check and impossible to merge, and the two render
identically on every surface that counts failures. MEASURED 2026-09-12 via
``mcp__github__pull_request_read`` over the COMPLETE open-PR queue (n = 5):

===========  =================  ==================  ==========================
PR           mergeable_state    checks              what the watcher said
===========  =================  ==================  ==========================
 #11738      ``dirty``          5 of 5 ``success``  ``waiting`` (12.2h)
 #11859      ``dirty``          5 of 5 ``success``  ``active`` — i.e. healthy
 #11860      ``blocked``        —                   ``active``
 #11827      ``unknown``        —                   ``active``
 #11817      ``unknown``        —                   ``waiting``
===========  =================  ==================  ==========================

#11738 is *"Arm R2 — the per-pass IB circuit breaker (MI-240, Tier-2,
**operator-approved**)"*, whose own body says *"what is outstanding is the
click, not the decision"*. The click cannot work. #11859 is the manager's own
status push, conflicted within 20 minutes of being opened and graded **active**
— so the old instrument was not merely imprecise about it, it was silent.

⚠️ **`waiting` and `conflicted` have OPPOSITE OWNERS.** `waiting` says a merge
decision is owed and points at the manager; `conflicted` says nobody can merge
it at all and points at the author. That conflation is what these tests pin
apart, in both directions.

Run: ``python3 -m pytest tests/test_pr_mergeability.py``
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ops"))

import pr_mergeability as pm  # noqa: E402
import pr_queue_latency as pq  # noqa: E402

NOW = datetime(2026, 9, 12, 6, 0, tzinfo=timezone.utc)


def _ok(name="guards"):
    return {"name": name, "status": "completed", "conclusion": "success"}


def _pr(number, ref, title="t", draft=False):
    return {"number": number, "head": {"ref": ref}, "title": title, "draft": draft}


def _ago(hours):
    return NOW - timedelta(hours=hours)


# ---------------------------------------------------------------------------
# The self-tests are the contract; running them here means pytest fails when
# they do, rather than only the scheduled workflow.
# ---------------------------------------------------------------------------
def test_mergeability_self_test_passes():
    ok, fails = pm._self_test(quiet=True)
    assert ok, f"pr_mergeability self-test failures: {fails}"


def test_queue_latency_self_test_passes():
    ok, fails = pq._self_test(quiet=True)
    assert ok, f"pr_queue_latency self-test failures: {fails}"


# ---------------------------------------------------------------------------
# THE LIVE CASE, replayed from the measured payloads above.
# ---------------------------------------------------------------------------
def test_the_two_live_conflicted_prs_are_found_and_named():
    prs = [_pr(11738, "a", "Arm R2 — the per-pass IB circuit breaker"),
           _pr(11859, "b", "manager: record the operator-requested full status"),
           _pr(11860, "c", "manager: the three-lane continuous workplan"),
           _pr(11827, "d", "Alarm on a sustained losing streak"),
           _pr(11817, "e", "R15: let an operator-approved Tier-2 PR land")]
    times = {"a": _ago(12.2), "b": _ago(0.3), "c": _ago(0.1),
             "d": _ago(1.5), "e": _ago(10.7)}
    five_green = [_ok(n) for n in ("guards", "pytest-run", "pytest-collect",
                                   "repo-inventory", "open-and-automerge")]
    merge_rows = {
        11738: pm.grade({"mergeable_state": "dirty"}, five_green),
        11859: pm.grade({"mergeable_state": "dirty"}, five_green),
        11860: pm.grade({"mergeable_state": "blocked"}),
        11827: pm.grade({"mergeable_state": "unknown"}),
        11817: pm.grade({"mergeable_state": "unknown"}),
    }
    v = pq.assess(prs, times, NOW, 6.0, merge_rows)

    assert v["conflicted"] == 2
    assert {r["pr"] for r in v["conflicted_rows"]} == {11738, 11859}
    assert v["misleading"] == 2, "both read as READY on every failure-counting surface"

    states = {r["pr"]: r["state"] for r in v["rows"]}
    # The heart of it: the quiescence axis calls one `waiting` and the other
    # `active`, and neither reading is about mergeability at all.
    assert states[11738] == pq.WAITING
    assert states[11859] == pq.ACTIVE, (
        "#11859 was pushed 0.3h ago, so the quiescence axis reports it as being "
        "worked — it would never surface, at any threshold")

    # …and the counts stay apart.
    assert v["over_threshold"] == 2
    assert v["conflicted"] == 2
    assert v["over_threshold"] != v["over_threshold"] + v["conflicted"]


def test_an_active_but_conflicted_pr_is_invisible_without_this_dimension():
    """The control that proves the dimension is load-bearing, not decorative."""
    prs = [_pr(11859, "b")]
    times = {"b": _ago(0.3)}
    without = pq.assess(prs, times, NOW, 6.0)
    assert without["over_threshold"] == 0, "nothing to report on the old axis"
    assert without["conflicted"] == 0
    assert without["merge_by_verdict"][pm.NOT_COMPUTED] == 1, (
        "and it is `not_computed`, never `mergeable` — not looking is not a pass")

    with_dim = pq.assess(prs, times, NOW, 6.0,
                         {11859: pm.grade({"mergeable_state": "dirty"}, [_ok()])})
    assert with_dim["over_threshold"] == 0, "still nothing on the old axis"
    assert with_dim["conflicted"] == 1, "and a finding on the new one"


# ---------------------------------------------------------------------------
# THE FIVE STATES, NEVER COLLAPSED — each asserted against its neighbours.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("clean", pm.MERGEABLE), ("has_hooks", pm.MERGEABLE),
    ("unstable", pm.MERGEABLE), ("behind", pm.MERGEABLE),
    ("dirty", pm.CONFLICTED), ("blocked", pm.CHECKS_BLOCKING),
    ("draft", pm.CHECKS_BLOCKING), ("unknown", pm.NOT_COMPUTED),
])
def test_every_github_value_lands_on_one_verdict(raw, want):
    assert pm.verdict_of(raw) == want


def test_an_unknown_future_github_value_is_not_read_as_mergeable():
    assert pm.verdict_of("some_state_github_adds_in_2027") == pm.NOT_COMPUTED


def test_we_did_not_look_is_never_a_pass():
    assert pm.verdict_of(None) == pm.NOT_COMPUTED
    assert pm.verdict_of("clean", read_ok=False) == pm.UNREADABLE
    assert pm.verdict_of("clean", read_ok=False) != pm.verdict_of(None), (
        "'our fetch died' and 'GitHub has not computed it' are different facts")


def test_merge_possible_is_tri_state():
    assert pm.merge_possible(pm.MERGEABLE) is True
    assert pm.merge_possible(pm.CONFLICTED) is False
    assert pm.merge_possible(pm.NOT_COMPUTED) is None, "None is not False"
    assert pm.merge_possible(pm.UNREADABLE) is None


def test_behind_is_mergeable_because_require_up_to_date_is_off_here():
    # Grading `behind` as a problem would re-create the ~9-minute CI churn the
    # 2026-08-10 removal of `require-up-to-date` was made to end.
    assert pm.merge_possible(pm.verdict_of("behind")) is True


def test_a_held_pr_is_checks_blocking_and_never_pages():
    v = pq.assess([_pr(1, "a")], {"a": _ago(30)}, NOW, 6.0,
                  {1: pm.grade({"mergeable_state": "blocked"}, [_ok()])})
    assert v["conflicted"] == 0
    due, _ = pq.conflict_escalation_due(v, None, True, NOW)
    assert not due, "a correctly-held PR paging every run is the desensitised alarm"


# ---------------------------------------------------------------------------
# `misleading` — the green-but-conflicted subset, and its NOT-MEASURED state.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("conclusion,expect_misleading", [
    ("success", True),
    ("failure", False),
    ("cancelled", False),   # produced no verdict; never reads as a pass
])
def test_misleading_only_when_the_pr_actually_looks_finished(conclusion,
                                                             expect_misleading):
    row = pm.grade({"mergeable_state": "dirty"},
                   [{"name": "guards", "status": "completed",
                     "conclusion": conclusion}])
    assert row["merge_verdict"] == pm.CONFLICTED
    assert row["misleading"] is expect_misleading


def test_unfetched_checks_leave_misleading_null_not_false():
    row = pm.grade({"mergeable_state": "dirty"})
    assert row["check_state"] is None
    assert row["misleading"] is None, (
        "False would assert we looked and it did not look ready")
    s = pm.summarise([row])
    assert s["misleading"] == 0 and s["misleading_ungraded"] == 1, (
        "the ungraded row must inflate neither the count nor the claim of absence")


def test_zero_checks_plus_dirty_is_not_misleading():
    row = pm.grade({"mergeable_state": "dirty"}, [])
    assert row["check_state"] == "no_checks"
    assert row["misleading"] is False


# ---------------------------------------------------------------------------
# THE LOADER — "we could not look" must never become "clean".
# ---------------------------------------------------------------------------
def test_an_unreadable_details_file_yields_no_rows_rather_than_clean_ones():
    assert pq.build_merge_rows("/nonexistent/definitely-not-here.json", None) == {}


def test_a_details_entry_missing_mergeable_state_grades_unreadable(tmp_path):
    """This is exactly what the workflow emits when a per-PR fetch fails."""
    p = tmp_path / "details.json"
    p.write_text(json.dumps([{"number": 7}]), encoding="utf-8")
    rows = pq.build_merge_rows(str(p), None)
    assert rows[7]["merge_verdict"] == pm.UNREADABLE
    assert rows[7]["merge_verdict"] != pm.NOT_COMPUTED


def test_checks_map_accepts_both_shapes(tmp_path):
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"number": 7, "mergeable_state": "dirty"}]),
                 encoding="utf-8")
    bare = tmp_path / "c1.json"
    bare.write_text(json.dumps({"7": [_ok()]}), encoding="utf-8")
    wrapped = tmp_path / "c2.json"
    wrapped.write_text(json.dumps({"7": {"check_runs": [_ok()]}}), encoding="utf-8")
    for path in (bare, wrapped):
        rows = pq.build_merge_rows(str(d), str(path))
        assert rows[7]["check_state"] == "green", path.name


def test_a_null_checks_entry_is_not_read_as_no_checks(tmp_path):
    """The workflow guards against writing `null`; the loader must too."""
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"number": 7, "mergeable_state": "dirty"}]),
                 encoding="utf-8")
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"7": None}), encoding="utf-8")
    rows = pq.build_merge_rows(str(d), str(c))
    assert rows[7]["check_state"] is None, "a null entry means we did not look"
    assert rows[7]["misleading"] is None


# ---------------------------------------------------------------------------
# ESCALATION — the two latches must not silence each other.
# ---------------------------------------------------------------------------
def _conflicted_verdict():
    return pq.assess([_pr(1, "a")], {"a": _ago(12)}, NOW, 6.0,
                     {1: pm.grade({"mergeable_state": "dirty"}, [_ok()])})


def test_a_conflict_pages_on_first_sighting_with_no_quiescence_threshold():
    v = pq.assess([_pr(1, "a")], {"a": _ago(0.1)}, NOW, 6.0,
                  {1: pm.grade({"mergeable_state": "dirty"}, [_ok()])})
    assert v["over_threshold"] == 0, "nothing is waiting"
    due, why = pq.conflict_escalation_due(v, None, True, NOW)
    assert due, why


def test_a_queue_page_does_not_silence_a_conflict_page():
    v = _conflicted_verdict()
    receipt = pq.build_state(v, NOW, True, "queue page", None, False, "")
    assert pq.CONFLICT_LATCH_KEY not in receipt
    due, _ = pq.conflict_escalation_due(
        v, receipt.get(pq.CONFLICT_LATCH_KEY), True, NOW)
    assert due, "the conflict must still page after an unrelated queue page"


def test_a_conflict_page_does_not_silence_a_queue_page():
    v = _conflicted_verdict()
    receipt = pq.build_state(v, NOW, False, "", None, True, "conflict page")
    assert "last_paged_at" not in receipt
    due, _ = pq.conflict_escalation_due(
        v, receipt[pq.CONFLICT_LATCH_KEY], True, NOW)
    assert not due, "…but it does silence a repeat of ITSELF, inside the cooldown"


def test_an_unreadable_latch_pages_rather_than_suppressing():
    v = _conflicted_verdict()
    fresh = {"last_paged_at": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "last_band": v["conflict_band"]}
    due, _ = pq.conflict_escalation_due(v, fresh, False, NOW)
    assert due, "a broken latch must announce itself as noise, never as silence"


def test_a_conflict_free_queue_never_pages():
    v = pq.assess([_pr(1, "a")], {"a": _ago(12)}, NOW, 6.0,
                  {1: pm.grade({"mergeable_state": "clean"}, [_ok()])})
    due, _ = pq.conflict_escalation_due(v, None, True, NOW)
    assert not due


def test_an_unreadable_queue_does_not_page_on_this_axis():
    v = pq.assess(None, {}, NOW, 6.0)
    due, _ = pq.conflict_escalation_due(v, None, True, NOW)
    assert not due, "nothing was graded, so nothing may be claimed about it"


# ---------------------------------------------------------------------------
# MUTATION CHECKS — each load-bearing predicate is broken in isolation and the
# suite is proved to NOTICE. A green run over an unmutated tree proves a test
# ran, never that it discriminates.
# ---------------------------------------------------------------------------
def test_mutation_dirty_graded_mergeable_is_load_bearing(monkeypatch):
    monkeypatch.setitem(pm._GITHUB_TO_VERDICT, "dirty", pm.MERGEABLE)
    v = _conflicted_verdict()
    assert v["conflicted"] == 0, "sanity: the mutation took"
    ok, fails = pm._self_test(quiet=True)
    assert not ok and fails, "the self-test must go RED on this mutation"


def test_mutation_pooling_blocked_into_conflicted_is_load_bearing(monkeypatch):
    monkeypatch.setitem(pm._GITHUB_TO_VERDICT, "blocked", pm.CONFLICTED)
    v = pq.assess([_pr(1, "a")], {"a": _ago(30)}, NOW, 6.0,
                  {1: pm.grade({"mergeable_state": "blocked"}, [_ok()])})
    assert v["conflicted"] == 1, "sanity: the mutation took"
    due, _ = pq.conflict_escalation_due(v, None, True, NOW)
    assert due, "sanity: pooling makes every held PR page — the alarm-fatigue failure"
    ok, _ = pm._self_test(quiet=True)
    assert not ok, "the self-test must go RED on this mutation"


def test_mutation_cancelled_reading_as_done_is_load_bearing(monkeypatch):
    monkeypatch.setattr(pm, "_LOOKS_DONE", {"green", "cancelled"})
    row = pm.grade({"mergeable_state": "dirty"},
                   [{"name": "g", "status": "completed", "conclusion": "cancelled"}])
    assert row["misleading"] is True, "sanity: the mutation took"
    ok, _ = pm._self_test(quiet=True)
    assert not ok, "the self-test must go RED on this mutation"
