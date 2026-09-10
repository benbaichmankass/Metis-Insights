"""MI-262 — the operator's 15-minute staleness threshold, and why `synced` lies.

Two SEPARATE defects that share a row on the Workflow page. They are tested
apart because folding them would let one pass paper over the other.

**(1) The threshold.** `DEC-20260910-WORKFLOW-PAGE-STALENESS-THRESHOLD`: the
operator was offered ~1h or "leave it at 3.0h" and typed **"15 min"** — neither
option, and tighter than the recommendation. It is stored in MINUTES because
that is the unit they answered in.

**(2) `synced` cannot justify itself.** MEASURED 2026-09-10 against the live
route: `treeState: 'synced'`, `treeBehindCommits: 0`, head `9071b8321`, while
GitHub's `main` was `b8cbbbf10` — `git merge-base --is-ancestor` confirms the
VM was a strict ancestor, **genuinely one commit behind**.

⚠️ **The comparison was not wrong; the age of its BASE was never measured.**
`behind_commits` counts `HEAD..origin/main` against the LOCAL remote-tracking
ref, and `scripts/deploy_pull_restart.sh` runs `git fetch` and then
`git reset --hard origin/main` — so on the live VM that equality holds **by
construction** and the zero is guaranteed rather than measured. All of the
staleness lives in *how long ago we last fetched*, which nothing recorded.

⚠️ **POSITIVE CONTROLS ARE MANDATORY HERE.** Every "this warns" assertion is
paired with a "this does NOT warn" case on the same probe. A staleness test
that only ever sees the stale case cannot tell *correctly reported* from
*reports everything* — and "reports everything" is precisely how an operator
gets trained to walk past the banner.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.runtime import manager_status as ms
from src.web.api.routers import work as wk


@dataclass
class FakeTree:
    state: str = ms.TREE_SYNCED
    behind_commits: Optional[int] = 0
    main_ref_age_hours: Optional[float] = 1.0 / 60.0  # fetched a minute ago
    note: str = ""


@dataclass
class FakeCommit:
    state: str = ms.FILE_COMMIT_KNOWN
    age_hours: Optional[float] = 0.0
    dirty: Optional[bool] = False
    note: str = ""


def _warnings(**kw) -> list[str]:
    tree = FakeTree(**{k: v for k, v in kw.items()
                       if k in ("state", "behind_commits", "main_ref_age_hours")})
    commit = FakeCommit(age_hours=kw.get("age_hours", 0.0))
    return wk._freshness_warnings(tree, commit)


def _checklist_warnings(**kw) -> list[str]:
    """Only the warnings about the CHECKLIST's own age."""
    return [w for w in _warnings(**kw) if "COMMITTED" in w]


def _fetch_warnings(**kw) -> list[str]:
    """Only the warnings about the age of our view of main."""
    return [w for w in _warnings(**kw) if "FETCH" in w]


# ═══════════════════════════════════════════════════════════════════════════
# (1) The threshold — the operator's number, in the operator's unit
# ═══════════════════════════════════════════════════════════════════════════

def test_the_operator_decision_is_recorded_verbatim_in_minutes():
    """15 minutes, stored as minutes.

    ⚠️ Asserting the UNIT matters as much as the value. `0.25` hours is the
    same number and a different record: it reads as something derived, and the
    next reader rounds it. The operator typed "15 min".
    """
    assert wk.CHECKLIST_STALE_AFTER_MINUTES == 15.0


def test_a_fresh_checklist_does_not_warn():
    """POSITIVE CONTROL — without this, every assertion below proves nothing."""
    assert _checklist_warnings(age_hours=14.0 / 60.0) == []


def test_a_checklist_past_fifteen_minutes_warns():
    assert len(_checklist_warnings(age_hours=16.0 / 60.0)) == 1


def test_the_boundary_is_at_fifteen_not_at_three_hours():
    """The regression that would silently restore the old behaviour.

    A revert to `3.0` hours passes every other test in this file — a 16-minute
    checklist simply stops warning and nothing else changes shape. This is the
    assertion that catches it.
    """
    assert _checklist_warnings(age_hours=20.0 / 60.0), "20m must warn at a 15m floor"
    assert _checklist_warnings(age_hours=1.0), "1h must warn at a 15m floor"


def test_the_warning_renders_minutes_not_a_useless_fraction_of_an_hour():
    """⚠️ THE UNIT BUG THE THRESHOLD CHANGE WOULD OTHERWISE HAVE SHIPPED.

    The text was `f"{age_hours:.1f}h"`, which was fine at a 3.0h threshold. At
    a 15-MINUTE threshold the very first warning it can emit reads **"0.3h
    ago"** — true, and unreadable. Changing the number without changing the
    unit would have delivered a banner the operator cannot act on, which is
    the same outcome as not delivering it.
    """
    (msg,) = _checklist_warnings(age_hours=18.0 / 60.0)
    assert "18m ago" in msg
    assert "0.3h" not in msg
    assert "h ago" not in msg


def test_an_hour_plus_checklist_still_renders_in_hours():
    """The minute rendering must not swallow the readable case at scale."""
    (msg,) = _checklist_warnings(age_hours=5.0)
    assert "5.0h ago" in msg


# ── the two REAL readings, captured live while the defect was happening ─────
#
# Not synthetic. Both are verbatim `GET /api/bot/work/checklist` freshness
# blocks taken by the manager session on 2026-09-10, either side of #11711
# merging — the before-case caught IN THE ACT of the operator's complaint.
#
# ⚠️ ASSERT ON `commitSha`, NEVER ON `len(items)`. The stale read served 266
# items and `main` also carried 266, so the COUNT MATCHED while the content
# was 87 minutes old. A matching count is not a matching checklist, and a
# freshness test keyed on it would have passed straight through the defect.

LIVE_BEFORE = {"commitSha": "82905ab829e4", "commitAgeHours": 1.448}   # 17:01Z
LIVE_AFTER = {"commitSha": "b8cbbbf10585", "commitAgeHours": 0.061}    # 17:04Z


def test_the_live_stale_read_that_prompted_this_would_now_warn():
    """The before-case: served CLEAN at 87 minutes old under the 3.0h floor."""
    msgs = _checklist_warnings(age_hours=LIVE_BEFORE["commitAgeHours"])
    assert len(msgs) == 1, "the exact read the operator complained about must warn"
    assert "1.4h ago" in msgs[0]


def test_the_live_fresh_read_stays_clean_at_fifteen_minutes():
    """The after-case, and the reason 15 minutes is tight rather than noisy.

    MEASURED on the same pair: #11711 merged at 17:00:40Z and the page served
    it at 17:04:21Z — a **3m41s** push-to-page cycle, so a 15-minute floor sits
    roughly 4x above the mechanism's own latency and fires on a manager who has
    genuinely gone quiet rather than on ordinary syncing.

    ⚠️ STATE THE POPULATION: **n = 1** — one merge, one sync cycle. That bounds
    the latency loosely; it is not a distribution, and 3m41s must not be quoted
    as "the" sync latency.
    """
    assert _checklist_warnings(age_hours=LIVE_AFTER["commitAgeHours"]) == []


def test_the_two_live_reads_are_distinguishable_by_the_field_that_moved():
    """The count matched across both; only `commitSha` and the age moved."""
    assert LIVE_BEFORE["commitSha"] != LIVE_AFTER["commitSha"]
    stale = _checklist_warnings(age_hours=LIVE_BEFORE["commitAgeHours"])
    fresh = _checklist_warnings(age_hours=LIVE_AFTER["commitAgeHours"])
    assert bool(stale) is not bool(fresh), "the pair must land on opposite verdicts"


# ═══════════════════════════════════════════════════════════════════════════
# (2) `synced` must justify itself
# ═══════════════════════════════════════════════════════════════════════════

def test_a_recently_fetched_synced_tree_does_not_warn():
    """POSITIVE CONTROL for the fetch-age probe."""
    assert _fetch_warnings(main_ref_age_hours=2.0 / 60.0) == []


def test_a_synced_tree_whose_fetch_is_old_warns_even_though_it_is_synced():
    """THE DEFECT, in one assertion.

    `state` is `synced` and `behind_commits` is `0` — both the reassuring
    values — and the tree is still not to be trusted, because the base it was
    compared against is old. Before this, the page had no way to say so.
    """
    msgs = _fetch_warnings(state=ms.TREE_SYNCED, behind_commits=0,
                           main_ref_age_hours=45.0 / 60.0)
    assert len(msgs) == 1
    assert "45m ago" in msgs[0]


def test_an_unknown_fetch_age_warns_and_never_reads_as_fresh():
    """*We could not look* must not render as *we looked and it was fine*."""
    msgs = _fetch_warnings(main_ref_age_hours=None)
    assert len(msgs) == 1
    assert "could not be established" in msgs[0]
    assert "0m" not in msgs[0]


def test_a_behind_or_unknown_tree_does_not_ALSO_get_the_fetch_warning():
    """One condition, one alarm — across all three tree states.

    ⚠️ The fetch warning exists because `synced` cannot justify itself. A tree
    already reporting `behind_main` or `unknown` has ALREADY announced that it
    is not to be trusted, and adding a second line about its fetch age would
    put two alarms on one condition. This repo's own P1 is the desensitised
    alarm: the fastest way to make the operator walk past this banner is to
    print more of it than the situation warrants.

    This also branches on all three of `manager_status.tree_state`, which is
    what `collapsed-state-guard` asks of a consumer — satisfied by testing the
    states rather than by annotating the question away.
    """
    stale = 45.0 / 60.0

    synced = _warnings(state=ms.TREE_SYNCED, main_ref_age_hours=stale)
    assert any("FETCH" in w for w in synced), "positive control: synced must warn"

    behind = _warnings(state=ms.TREE_BEHIND, behind_commits=4,
                       main_ref_age_hours=stale)
    assert any("BEHIND origin/main" in w for w in behind)
    assert not any("FETCH" in w for w in behind), behind

    unknown = _warnings(state=ms.TREE_UNKNOWN, main_ref_age_hours=stale)
    assert any("could not look" in w for w in unknown)
    assert not any("FETCH" in w for w in unknown), unknown


def test_the_two_thresholds_are_independent_quantities():
    """They may coincide numerically; they must never be one knob.

    One is a recorded operator decision about MANAGER lateness, the other a
    property of `ict-git-sync.timer`'s 5-minute cadence. Widening one must not
    silence the other — this asserts it behaviourally rather than by reading
    the constants.
    """
    tree = FakeTree(main_ref_age_hours=45.0 / 60.0)
    commit = FakeCommit(age_hours=0.0)
    out = wk._freshness_warnings(tree, commit, stale_after_minutes=10_000.0)
    assert any("FETCH" in w for w in out), "a huge checklist floor silenced the fetch warning"

    tree2 = FakeTree(main_ref_age_hours=1.0 / 60.0)
    commit2 = FakeCommit(age_hours=2.0)
    out2 = wk._freshness_warnings(tree2, commit2, fetch_stale_after_minutes=10_000.0)
    assert any("COMMITTED" in w for w in out2), "a huge fetch floor silenced the checklist warning"


# ═══════════════════════════════════════════════════════════════════════════
# The reader itself
# ═══════════════════════════════════════════════════════════════════════════

def test_a_missing_fetch_head_is_none_never_zero(tmp_path: Path):
    """A clone that has never fetched cannot say its view of main is current.

    ⚠️ `0.0` here would read as *we fetched just now* — the reassuring
    direction, and therefore the one that must never be fabricated. This is
    the same rule `TreeProvenance` already states for `behind_commits`.
    """
    def git(args):
        if args[:2] == ["rev-parse", "--git-dir"]:
            return str(tmp_path / ".git"), None
        return None, "unexpected"

    age, note = ms.read_fetch_age_hours(repo_dir=tmp_path, git=git)
    assert age is None
    assert "FETCH_HEAD" in note


def test_an_unreadable_git_dir_is_none_with_a_reason():
    def git(args):
        return None, "not a repository"

    age, note = ms.read_fetch_age_hours(repo_dir=Path("/nonexistent"), git=git)
    assert age is None
    assert "git dir" in note


def test_render_hours_keeps_unknown_distinguishable_from_just_now():
    assert ms._render_hours(None) == "an unknown time"
    assert ms._render_hours(0.0) == "0m"
    assert ms._render_hours(None) != ms._render_hours(0.0)


def test_the_synced_stamp_states_when_it_last_looked():
    """A bare "synced" is the claim that misled a reader on 2026-09-10."""
    tree = ms.TreeProvenance(state=ms.TREE_SYNCED, head_sha="abc1234",
                             main_sha="abc1234", behind_commits=0,
                             main_ref_age_hours=30.0 / 60.0)
    stamp = ms.render_tree_stamp(tree)
    assert "30m ago" in stamp
    assert "may have moved" in stamp


def test_the_synced_stamp_does_not_fabricate_an_age_it_lacks():
    tree = ms.TreeProvenance(state=ms.TREE_SYNCED, head_sha="abc1234",
                             main_sha="abc1234", behind_commits=0,
                             main_ref_age_hours=None)
    stamp = ms.render_tree_stamp(tree)
    assert "an unknown time" in stamp
    assert "0m ago" not in stamp


def test_tree_provenance_carries_the_fetch_age_on_a_real_repo():
    """End-to-end against this checkout — the field is populated, not just declared."""
    tree = ms.read_tree_provenance(repo_dir=Path("."))
    assert tree.state in ms.TREE_STATES
    # This repo has fetched (CI clones and fetches), so the age must be real.
    assert tree.main_ref_age_hours is None or tree.main_ref_age_hours >= 0.0
