"""MI-246 — a checklist row FILED and never ROUTED had no age, so nothing reported it.

These pin the properties that are load-bearing rather than the ones that are
easy. In every case the failure direction that matters is the REASSURING one: a
row we could not grade, or a history we could not read, must never render as
*nothing is stalled* — that is exactly how four measured drops stayed invisible
until the operator happened to ask a question that touched them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))

import checklist_routing_age as cra  # noqa: E402


# ── the tri-state classifier ───────────────────────────────────────────────

@pytest.mark.parametrize("owner,want", [
    ("session_01ABC", cra.ROUTE_ROUTED),
    ("manager (session_01X)", cra.ROUTE_ROUTED),
    ("ENGINEERING LANE session_01Y", cra.ROUTE_ROUTED),
    ("unassigned", cra.ROUTE_UNROUTED),
    ("unassigned — needs a session", cra.ROUTE_UNROUTED),
    ("UNASSIGNED — next free slot", cra.ROUTE_UNROUTED),
    ("nobody", cra.ROUTE_UNROUTED),
    ("TBD", cra.ROUTE_UNROUTED),
    (None, cra.ROUTE_ABSENT),
    ("", cra.ROUTE_ABSENT),
    ("   ", cra.ROUTE_ABSENT),
])
def test_routing_state(owner, want):
    assert cra.routing_state({"owner": owner} if owner is not None else {}) == want


def test_absent_owner_is_not_the_same_fact_as_unrouted():
    """Three states, never two.

    `owner: unassigned` is a manager SAYING nobody has it. A row with no owner
    field at all is nobody having looked. Both are unrouted for the purposes of
    the age, and they are different findings — 64 of 270 rows carried no owner
    field on 2026-09-11, and reporting those as deliberate would be a claim
    nobody made.
    """
    assert cra.ROUTE_ABSENT != cra.ROUTE_UNROUTED != cra.ROUTE_ROUTED
    assert len(set(cra.ROUTING_STATES)) == 3


def test_an_unknown_status_is_ungradeable_not_a_silent_default():
    """THE MUTATION THAT MATTERS.

    Folding an unrecognised status into NOT-OPEN hides a stall; folding it into
    OPEN manufactures one. Both are confident wrong answers. `MI-237` measured
    152 field names and two competing status fields, so unrecognised values are
    the norm here, not an edge case.
    """
    assert cra.openness({"state": "some_new_word"}) == cra.OPEN_UNGRADEABLE
    assert cra.openness({}) == cra.OPEN_UNGRADEABLE
    assert cra.is_unrouted_open({"owner": "unassigned", "state": "some_new_word"}) is None
    # and `None` is NOT False — a caller doing `if not flag` would drop it
    assert cra.is_unrouted_open({"owner": "unassigned", "state": "some_new_word"} ) is not False


def test_the_two_competing_status_fields_are_both_read():
    """MI-237: `state` and `status` disagree on 13 rows and this module adjudicates neither.

    A row is OPEN only if NO reading of it is terminal. The asymmetry is
    deliberate: over-reporting a finished row costs a glance, under-reporting a
    live one is the drop.
    """
    assert cra.openness({"state": "queued", "status": "done"}) == cra.OPEN_NOT_OPEN
    assert cra.openness({"state": "done", "status": "queued"}) == cra.OPEN_NOT_OPEN
    assert cra.openness({"status": "ready"}) == cra.OPEN_OPEN


@pytest.mark.parametrize("status", ["in_flight", "blocked", "review_ready",
                                    "landed_unproven", "done", "deferred"])
def test_the_siblings_classes_are_not_this_ones(status):
    """`in_flight` is MI-236, `blocked` is MI-235. Neither is a routing gap.

    Widening into them would make this the fourth near-duplicate the row itself
    warns about, and would double-report work those mechanisms already own.
    """
    assert cra.openness({"state": status}) == cra.OPEN_NOT_OPEN


# ── the stall grade ────────────────────────────────────────────────────────

def test_no_derivable_age_is_unknown_never_within():
    assert cra.grade_stall(None, reported_before=False) == cra.STALL_UNKNOWN
    assert cra.grade_stall(None, reported_before=True) == cra.STALL_UNKNOWN


def test_loud_once_then_a_count():
    """MEASURED 2026-09-11: 70 of 270 rows were already past the threshold.

    Paging the STOCK would put a 70-row block in the session brief every day,
    which is the desensitised alarm this repo calls its own worst failure mode.
    So a crossing is said once and counted thereafter.
    """
    assert cra.grade_stall(25.0, reported_before=False) == cra.STALL_NEWLY
    assert cra.grade_stall(25.0, reported_before=True) == cra.STALL_STANDING
    assert cra.grade_stall(500.0, reported_before=True) == cra.STALL_STANDING


def test_every_declared_stall_state_is_producible():
    """A state nothing can emit is a dead claim, not a guarantee."""
    produced = {
        cra.grade_stall(1.0, reported_before=False),
        cra.grade_stall(99.0, reported_before=False),
        cra.grade_stall(99.0, reported_before=True),
        cra.grade_stall(None, reported_before=False),
    }
    assert produced == set(cra.STALL_STATES)


# ── the git derivation ─────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _repo_with_history(tmp_path: Path, revisions: list) -> Path:
    """A real git repo whose checklist moves through `revisions` a day apart."""
    repo = tmp_path / "repo"
    (repo / "docs/claude/work").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for n, items in enumerate(revisions):
        # `_rev` makes every revision differ even when the item states do not.
        # Without it git records nothing (`git log -- <path>` lists only commits
        # that CHANGED the path), and the fixture would silently test a
        # one-revision history — the truncated-scope trap these tests are about.
        (repo / cra.CHECKLIST).write_text(
            json.dumps({"_rev": n, "items": items}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        when = (base + timedelta(days=n)).isoformat()
        env = dict(os.environ,
                   GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when,
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.com")
        _git(repo, "add", cra.CHECKLIST)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"rev{n}"],
                       check=True, capture_output=True, text=True, env=env)
    return repo


def test_a_row_filed_and_never_routed_acquires_an_age(tmp_path):
    """The whole point: the age exists in git even though the register has no field for it."""
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "ready", "owner": "unassigned", "title": "filed"}],
        [{"id": "MI-A", "state": "ready", "owner": "unassigned", "title": "filed"}],
        [{"id": "MI-A", "state": "ready", "owner": "unassigned", "title": "filed"}],
    ])
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    env = cra.build(repo, now=now, previous={"reported_ids": []}, seed=False)
    assert env["history_state"] == cra.HIST_DERIVED
    assert [r["id"] for r in env["newly_stalled"]] == ["MI-A"]
    # first seen 2026-09-01, now 2026-09-04 -> 72h, not 0 and not "unknown"
    assert env["newly_stalled"][0]["unrouted_hours"] == pytest.approx(72.0, abs=0.1)
    assert env["newly_stalled"][0]["stall_state"] == cra.STALL_NEWLY


def test_routing_a_row_resets_its_clock(tmp_path):
    """A row that was routed, finished and re-opened has a NEW clock, not its original one.

    Carrying the original would report a freshly re-opened row as months old and
    bury the genuinely new crossings underneath it.
    """
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
        [{"id": "MI-A", "state": "in_flight", "owner": "session_01X"}],
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
    ])
    env = cra.build(repo, now=datetime(2026, 9, 4, tzinfo=timezone.utc),
                    previous={"reported_ids": []}, seed=False)
    assert env["newly_stalled"][0]["unrouted_hours"] == pytest.approx(24.0, abs=0.1)


def test_a_row_that_becomes_ungradeable_keeps_its_clock(tmp_path):
    """Becoming unreadable is not being routed.

    Clearing the clock on an ungradeable revision would let a row escape the age
    simply by having its status field renamed — which, given MI-237's 152 field
    names, is a thing that actually happens here.
    """
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
        [{"id": "MI-A", "state": "wibble", "owner": "unassigned"}],
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
    ])
    env = cra.build(repo, now=datetime(2026, 9, 4, tzinfo=timezone.utc),
                    previous={"reported_ids": []}, seed=False)
    assert env["newly_stalled"][0]["unrouted_hours"] == pytest.approx(72.0, abs=0.1)


def test_a_routed_row_is_never_reported(tmp_path):
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "ready", "owner": "session_01X"}],
        [{"id": "MI-A", "state": "ready", "owner": "session_01X"}],
    ])
    env = cra.build(repo, now=datetime(2026, 9, 10, tzinfo=timezone.utc),
                    previous={"reported_ids": []}, seed=False)
    assert env["newly_stalled"] == []
    assert env["stall_counts"][cra.STALL_WITHIN] == 0


def test_an_ungradeable_row_is_counted_not_dropped(tmp_path):
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "wibble", "owner": "unassigned"}],
        [{"id": "MI-A", "state": "wibble", "owner": "unassigned"}],
    ])
    env = cra.build(repo, now=datetime(2026, 9, 10, tzinfo=timezone.utc),
                    previous={"reported_ids": []}, seed=False)
    assert env["stall_counts"][cra.STALL_UNKNOWN] == 1
    assert env["newly_stalled"] == []


def test_first_run_seeds_the_backlog_rather_than_paging_it(tmp_path):
    repo = _repo_with_history(tmp_path, [
        [{"id": f"MI-{i}", "state": "ready", "owner": "unassigned"} for i in range(30)],
        [{"id": f"MI-{i}", "state": "ready", "owner": "unassigned"} for i in range(30)],
    ])
    env = cra.build(repo, now=datetime(2026, 9, 10, tzinfo=timezone.utc), previous=None)
    assert env["newly_stalled"] == [], "a first run must not page 30 rows at once"
    assert env["seeded_count"] == 30
    assert len(env["seeded_ids"]) == 30, "and every seeded row is NAMED, so the set is workable"
    assert set(env["reported_ids"]) == {f"MI-{i}" for i in range(30)}

    # the SECOND run reports only what is genuinely new
    env2 = cra.build(repo, now=datetime(2026, 9, 10, tzinfo=timezone.utc), previous=env)
    assert env2["newly_stalled"] == []
    assert env2["seeded_count"] == 0
    assert env2["stall_counts"][cra.STALL_STANDING] == 30


def test_an_unreadable_history_refuses_rather_than_reporting_zero(tmp_path):
    """BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE, one level up.

    A truncated history does not error — it reports every row as young. The
    register has to say WE DID NOT LOOK, because an empty `newly_stalled` under
    a broken derivation is indistinguishable from a healthy one.
    """
    env = cra.build(tmp_path / "no-such-repo", now=datetime.now(timezone.utc))
    assert env["history_state"] == cra.HIST_COULD_NOT_READ
    assert env["newly_stalled"] == []
    lines = cra.render_brief_lines(env)
    assert any("COULD NOT BE MEASURED" in ln for ln in lines)
    assert not any("No filed checklist row crossed" in ln for ln in lines), \
        "a refusal must never render the all-clear sentence"


def test_history_usable_is_a_positive_control_not_an_absence_of_error(tmp_path):
    """It asks whether the commit that ADDED the file is reachable.

    `.git/shallow` alone is the wrong test in both directions: this session's
    own clone carries a shallow boundary at 2026-06-28 while the checklist was
    created 2026-09-02, so the history is complete for the path.
    """
    repo = _repo_with_history(tmp_path, [
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}],
    ])
    usable, note = cra.history_is_usable(repo)
    assert usable and "added the file" in note
    # one revision is not an age
    one = _repo_with_history(tmp_path / "solo", [
        [{"id": "MI-A", "state": "ready", "owner": "unassigned"}]])
    usable2, note2 = cra.history_is_usable(one)
    assert not usable2 and "at least one prior revision" in note2


# ── the artifact ───────────────────────────────────────────────────────────

def test_the_register_round_trips_through_the_repo_serialisation(tmp_path):
    env = cra.build(tmp_path / "nope", now=datetime.now(timezone.utc))
    blob = json.dumps(env, indent=2, ensure_ascii=False)
    assert json.loads(blob) == env


def test_the_live_register_is_current_and_well_formed():
    """The committed artifact, not a fixture."""
    p = Path(__file__).resolve().parents[1] / cra.OUT
    if not p.exists():
        pytest.skip(f"{cra.OUT} not committed yet")
        return
    reg = json.loads(p.read_text(encoding="utf-8"))
    assert reg["history_state"] in cra.HISTORY_STATES
    assert set(reg["stall_counts"]) == set(cra.STALL_STATES), \
        "the counts are keyed by the vocabulary, so a consumer cannot drop one"
    assert reg["threshold_basis"], "a threshold with no stated basis is folklore"
    assert "--measure" in reg["threshold_basis"], \
        "and it must say how to RE-DERIVE it, or it becomes folklore anyway"
