"""MI-236 — the `in_flight` claim grader.

The tests that matter here are the ones pinning DESIGN DECISIONS that a later
session could plausibly "tidy" into uselessness, each with the measurement that
decided it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ol = _load("owner_liveness", "scripts/ops/owner_liveness.py")
# OBS_* come from their OWNER module, never via a re-export from `ol`.
from src.runtime.manager_status import (  # noqa: E402
    OBS_RECENT, OBS_STALE, OBS_UNKNOWN,
)
wip = _load("check_wip_ceiling", "scripts/ci/check_wip_ceiling.py")

NOW = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)

REGISTRY = {
    "session_active00": {"state": "working",
                         "state_observed_at": "2026-09-11T05:30:00Z"},
    "session_blocked0": {"state": "blocked",
                         "state_observed_at": "2026-09-11T05:30:00Z"},
    "session_idle0000": {"state": "idle",
                         "state_observed_at": "2026-09-09T05:30:00Z"},
    "session_deliver0": {"state": "idle_delivered"},
    "session_archive0": {"state": "archived"},
    "session_done0000": {"state": "completed"},
    "session_novel000": {"state": "some_new_word"},
    "session_nostate0": {"other": 1},
}


def grade(owner, registry=REGISTRY):
    return ol.grade_owner_activity(owner, registry, now=NOW)


# ─────────────────────────────────────────────────────────────────────────────
# The decision the whole mechanism rests on
# ─────────────────────────────────────────────────────────────────────────────

def test_idle_is_unsupported_this_is_the_whole_mechanism():
    """⚠️ DO NOT "FIX" THIS TO `supported` BECAUSE AN IDLE SESSION CAN BE WOKEN.

    That is true and is beside the point. `in_flight` claims somebody is
    ACTIVELY WORKING the row; an idle owner is not. Measured on `main`
    2026-09-11 over the six rows MI-236 named, FIVE owners sit at `idle` and one
    at `archived` — so grading only strict terminality catches 1 of 6 and the
    mechanism does essentially nothing.
    """
    assert grade("session_idle0000").activity == ol.DORMANT
    assert grade("session_idle0000").support == ol.UNSUPPORTED
    assert grade("session_deliver0").support == ol.UNSUPPORTED


def test_dormant_and_terminal_stay_apart_despite_sharing_a_verdict():
    """Same verdict, opposite remedies: poke it vs re-route it."""
    assert grade("session_idle0000").activity == ol.DORMANT
    assert grade("session_archive0").activity == ol.TERMINAL
    assert grade("session_done0000").activity == ol.TERMINAL
    assert (grade("session_idle0000").support
            == grade("session_archive0").support == ol.UNSUPPORTED)


def test_blocked_is_active_and_is_mi235s_subject_not_this_modules():
    """A blocked session is ALIVE and waiting. Grading it unsupported would
    double-report one condition and invite re-routing work off a lane that is
    still holding it."""
    assert grade("session_blocked0").activity == ol.ACTIVE
    assert grade("session_blocked0").support == ol.SUPPORTED


# ─────────────────────────────────────────────────────────────────────────────
# "We could not look" is never a pass, and its three flavours never merge
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("owner,expected", [
    ("manager", ol.UNGRADEABLE_OWNER),
    ("null", ol.UNGRADEABLE_OWNER),
    ("None", ol.UNGRADEABLE_OWNER),
    ("", ol.UNGRADEABLE_OWNER),
    (None, ol.UNGRADEABLE_OWNER),
    ("session_absent00", ol.UNKNOWN_TO_REGISTRY),
    ("session_novel000", ol.UNRECOGNISED_STATE),
    ("session_nostate0", ol.UNRECOGNISED_STATE),
])
def test_ungradeable_shapes(owner, expected):
    assert grade(owner).activity == expected
    assert grade(owner).support == ol.COULD_NOT_ESTABLISH


def test_registry_unread_is_not_a_finding_and_not_a_pass():
    """*We could not look* must reach the caller as its own state. Reporting it
    as `unsupported` would fabricate a finding about every owner at once."""
    g = ol.grade_owner_activity("session_active00", None, now=NOW)
    assert g.activity == ol.REGISTRY_UNREAD
    assert g.support == ol.COULD_NOT_ESTABLISH
    assert g.session_id == "session_active00"


def test_every_declared_state_is_reachable():
    """A declared state nothing can produce is a lie in the vocabulary."""
    produced = {
        grade("session_active00").activity, grade("session_idle0000").activity,
        grade("session_archive0").activity, grade("session_novel000").activity,
        grade("session_absent00").activity, grade("manager").activity,
        ol.grade_owner_activity("session_active00", None, now=NOW).activity,
    }
    assert produced == set(ol.OWNER_ACTIVITIES)
    assert {grade("session_active00").support,
            grade("session_idle0000").support,
            grade("manager").support} == set(ol.CLAIM_SUPPORTS)


# ─────────────────────────────────────────────────────────────────────────────
# Owner extraction
# ─────────────────────────────────────────────────────────────────────────────

def test_first_session_id_in_prose_wins():
    """MEASURED over the three prose-owner `in_flight` rows on `main`
    2026-09-11: the CURRENT owner is first in 3 of 3, and a later id is
    provenance. `MI-238`'s real owner string is the worked example — taking the
    last id would grade the row against the session it was taken AWAY from.
    n=3 is small and is stated; this test is what makes a counter-example
    change the rule deliberately rather than silently.
    """
    real = ("session_01G4VneSjgv5QT4rMGj4izi5 (ENGINEERING LANE) — re-laned "
            "2026-09-11T00:28:20Z by the manager, inheriting from "
            "session_01MQ2EzPomkBsM6uxJMCrJbF (idle since 2026-09-10T18:13Z)")
    assert ol.owner_session_id(real) == "session_01G4VneSjgv5QT4rMGj4izi5"


def test_anchored_matching_would_have_missed_the_prose_rows():
    """`manager_status.grade_owner` anchors its pattern, which is right for
    RENDERING and wrong for GRADING: it reads these as `not_a_session`."""
    assert grade("session_active00 (ENGINEERING LANE)").support == ol.SUPPORTED


def test_observation_freshness_is_recorded_on_every_grade():
    """A `dormant` observed two minutes ago must not read identically to one
    observed three days ago.

    ⚠️ RENAMED 2026-09-11 from `..._rides_along`, which described the defect
    rather than the design: freshness did ride along, unread, and
    `_support_for` banked a stale `active` as `supported`. It is now BRANCHED
    on — see `test_a_stale_active_owner_cannot_support_the_claim`.
    """
    g = grade("session_idle0000")
    assert g.observation_state == OBS_STALE
    assert g.observation_age_minutes == pytest.approx(2 * 24 * 60 + 30, abs=1)
    assert grade("session_active00").observation_state == OBS_RECENT
    # A row with no observation field at all is `unknown`, never `recent`.
    assert grade("session_archive0").observation_state == OBS_UNKNOWN


# ─────────────────────────────────────────────────────────────────────────────
# The two readers of `lifecycle` must never disagree
# ─────────────────────────────────────────────────────────────────────────────

def test_agrees_with_check_wip_ceiling_about_which_objects_are_in_flight():
    """This module is a SECOND reader of `lifecycle:` on the same files, and the
    ceiling guard is the authority. If the two ever disagree, this module would
    grade a different population than the one actually holding the ceiling shut
    — which is the one thing a second reader must never do."""
    in_flight_ids, _problems, _counts = wip.scan(REPO_ROOT / ol.OBJECTS_RELDIR)
    registry = ol.read_registry(REPO_ROOT)
    mine = [r.row_id for r in ol.in_flight_rows(REPO_ROOT, registry)
            if r.register == ol.OBJECTS_REGISTER]
    assert sorted(mine) == sorted(in_flight_ids)


def test_guard_self_test_passes():
    rc = subprocess.run(
        [sys.executable, "scripts/ci/check_stale_in_flight.py", "--self-test"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    assert rc.returncode == 0, rc.stdout + rc.stderr


# ─────────────────────────────────────────────────────────────────────────────
# The UNRELIABLE POSITIVE — a stale `active` is not evidence (2026-09-11)
#
# This module's whole reason for being CI-runnable is its docstring argument
# that the registry is a reliable negative and an unreliable positive. For its
# first day the code did not implement it: `_support_for` banked a recorded
# `active` as `supported` however old the observation behind it was.
#
# MEASURED 2026-09-11T20:1xZ. Population: all 27 rows this module grades
# `in_flight` (25 MANAGER-CHECKLIST.json + 2 objects/*.yaml) at `886c93e`,
# re-graded against `list_sessions(mine=true, limit=100)` — evidence this
# module cannot reach, which is why the gap survived its own review. SIX rows
# graded `supported`; NOT ONE had a live-RUNNING owner. Four (MI-215, MI-217,
# MI-241, MI-254) were IDLE/COMPLETED on the platform and two (MI-183b,
# MI-196) idle-but-wakeable, on observations 2166-4328 minutes old — 24x to
# 48x this module's own 90-minute `stale_minutes`.
# ─────────────────────────────────────────────────────────────────────────────

STALE_ACTIVE_REGISTRY = {
    # MI-215's real shape: `working`, last observed 43.5h before NOW.
    "session_staleact": {"state": "working",
                         "state_observed_at": "2026-09-09T10:30:00Z"},
    # MI-241's real shape: `review_ready`, and its ONLY timestamp is the spawn
    # confirmation -- i.e. nobody has looked at it since it was created.
    "session_spawnonly": {"state": "review_ready",
                          "confirmed_at": "2026-09-10T07:57:32Z"},
    # A genuinely live lane: observed 30 minutes ago.
    "session_freshact": {"state": "working",
                         "state_observed_at": "2026-09-11T05:30:00Z"},
    # The negative half, observed just as long ago as the stale positive.
    "session_staleidle": {"state": "idle",
                          "state_observed_at": "2026-09-09T10:30:00Z"},
    "session_staledead": {"state": "archived",
                          "state_observed_at": "2026-09-09T10:30:00Z"},
}


def stale_grade(owner):
    return ol.grade_owner_activity(owner, STALE_ACTIVE_REGISTRY, now=NOW)


def test_a_stale_active_owner_cannot_support_the_claim():
    """The fix. A recorded `working` whose observation has aged out is not
    evidence that anybody is working the row -- it is `could_not_establish`.

    Worked example is MI-215's real registry shape: `state: working`, observed
    43.5h before the read, while `list_sessions` reported that session
    IDLE/COMPLETED.
    """
    g = stale_grade("session_staleact")
    assert g.observation_state == OBS_STALE
    # The ACTIVITY still reports what the registry RECORDS -- we do not
    # falsify the register's own value...
    assert g.activity == ol.ACTIVE
    # ...but it no longer SUPPORTS the claim.
    assert g.support == ol.COULD_NOT_ESTABLISH
    assert g.support != ol.SUPPORTED


def test_an_active_owner_nobody_ever_observed_cannot_support_it_either():
    """MI-241/MI-254's real shape: the only timestamp is the spawn
    confirmation, so `unknown` -- *nobody has looked since it was created*.
    Folding that into `supported` reports an unchecked row as a checked one,
    which is the reassuring and therefore dangerous direction.
    """
    g = stale_grade("session_spawnonly")
    assert g.observation_state in (OBS_STALE, OBS_UNKNOWN)
    assert g.support == ol.COULD_NOT_ESTABLISH


def test_a_FRESH_active_owner_still_supports_the_claim():
    """The fix must not simply delete `supported`. A lane observed inside the
    staleness window is exactly the case the state exists for, and a mechanism
    that graded every row unsupported would be switched off within a day."""
    g = stale_grade("session_freshact")
    assert g.observation_state == OBS_RECENT
    assert g.support == ol.SUPPORTED


def test_staleness_does_NOT_weaken_a_NEGATIVE_which_is_the_asymmetry():
    """⚠️ THE LOAD-BEARING HALF, and the one a later session is most likely to
    "tidy" into symmetry.

    A recorded negative does not decay: somebody positively observed the owner
    not working, and a session does not spontaneously un-archive. So an old
    `idle`/`archived` is still `unsupported` -- applying the freshness test to
    it would convert this module's 10 CONFIRMED TRUE POSITIVES (verified
    against the live roster on 2026-09-11) into `could_not_establish` and
    destroy the mechanism outright.
    """
    for owner in ("session_staleidle", "session_staledead"):
        g = stale_grade(owner)
        assert g.observation_state == OBS_STALE, owner
        assert g.support == ol.UNSUPPORTED, owner


def test_freshness_can_only_ever_downgrade_a_POSITIVE_never_reach_unsupported():
    """Mutation-style invariant over the FULL cross-product, because this is
    what keeps `check_stale_in_flight.py`'s ratchet safe: the ratchet counts
    `unsupported`, so if freshness could ever produce that verdict this change
    could red a PR over pre-existing debt.

    Asserted as a property rather than by example so that adding an activity
    or an observation state cannot silently escape it.
    """
    for activity in ol.OWNER_ACTIVITIES:
        baseline = ol._support_for(activity, OBS_RECENT)
        for obs in (OBS_RECENT, OBS_STALE, OBS_UNKNOWN):
            got = ol._support_for(activity, obs)
            if got != baseline:
                # The ONLY transition freshness may cause.
                assert baseline == ol.SUPPORTED, (activity, obs, baseline)
                assert got == ol.COULD_NOT_ESTABLISH, (activity, obs, got)
            # Whatever happens, freshness never manufactures a staleness claim.
            if baseline != ol.UNSUPPORTED:
                assert got != ol.UNSUPPORTED, (activity, obs)


def test_support_default_fails_toward_could_not_look():
    """A caller that omits the observation state must not get a pass by
    default. `OBS_UNKNOWN` is the default precisely so an unwired call site
    grades *we could not look* rather than *someone is working it*."""
    assert ol._support_for(ol.ACTIVE) == ol.COULD_NOT_ESTABLISH
    assert ol._support_for(ol.ACTIVE, OBS_RECENT) == ol.SUPPORTED


def test_supported_is_still_reachable_from_the_real_registers():
    """A state nothing can produce would be a lie in the vocabulary, so this
    pins that `supported` remains reachable from a real register row.

    ⚠️ MEASURED TWICE, AND THE SECOND READ CORRECTS THE FIRST. Over the 27
    in_flight rows at `886c93e` it was ZERO. Re-measured ~30 minutes later at
    the merged head it is **3 of 30** -- the lanes spawned 31.7 minutes
    earlier, all three genuinely RUNNING -- so the zero was an artefact of
    when that population was cut, not a property of the code, and `supported`
    has a live positive control with zero false positives. This test is what
    keeps the state honest if the live count returns to zero.
    """
    assert stale_grade("session_freshact").support == ol.SUPPORTED
    assert ol.SUPPORTED in set(ol.CLAIM_SUPPORTS)
