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


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ol = _load("owner_liveness", "scripts/ops/owner_liveness.py")
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


def test_observation_freshness_rides_along():
    """A `dormant` observed two minutes ago must not read identically to one
    observed three days ago."""
    g = grade("session_idle0000")
    assert g.observation_state == ol.OBS_STALE
    assert g.observation_age_minutes == pytest.approx(2 * 24 * 60 + 30, abs=1)
    assert grade("session_active00").observation_state == ol.OBS_RECENT
    # A row with no observation field at all is `unknown`, never `recent`.
    assert grade("session_archive0").observation_state == ol.OBS_UNKNOWN


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
