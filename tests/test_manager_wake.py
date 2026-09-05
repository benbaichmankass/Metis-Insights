"""MI-123 — tests for the manager durable wake (assess / brief / receipt).

No network, no MCP, no repo install: both scripts under test are stdlib-only and
loaded via importlib (same pattern as test_macro_producer_liveness.py).

⚠️ What these tests are FOR. The wake's whole value is that it distinguishes four
states that other surfaces collapse, and the collapse is the bug: reporting
"nobody holds the lease" as "the manager is fine" is the substitution that let a
manager sit silent for twelve hours. So the assertions below are mostly about
states NOT being merged, not about happy-path plumbing.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPS = os.path.join(_ROOT, "scripts", "ops")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_OPS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wake = _load("manager_wake")
liveness = _load("check_wake_liveness")

NOW = datetime(2026, 9, 4, 22, 0, 0, tzinfo=timezone.utc)


def _lease(tmp_path, **fields):
    path = tmp_path / "MANAGER-LEASE.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def _iso(dt):
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── assess: the four states, kept apart ─────────────────────────────────────


def test_absent_lease_is_no_manager_not_active(tmp_path):
    """An absent lease means nobody to wake — never 'the manager is fine'."""
    verdict = wake.assess(NOW, tmp_path / "nothing.json")
    assert verdict["state"] == wake.NO_MANAGER
    assert verdict["wake_session"] is None


def test_corrupt_lease_is_unreadable_not_no_manager(tmp_path):
    """'We did not look' must never be reported as 'nobody holds it'."""
    path = tmp_path / "MANAGER-LEASE.json"
    path.write_text("{not json at all", encoding="utf-8")
    verdict = wake.assess(NOW, path)
    assert verdict["state"] == wake.UNREADABLE
    assert "DID NOT LOOK" in verdict["reason"].upper()


def test_released_lease_is_no_manager(tmp_path):
    path = _lease(tmp_path, state="released", holder=None)
    assert wake.assess(NOW, path)["state"] == wake.NO_MANAGER


def test_held_with_recent_heartbeat_is_active(tmp_path):
    path = _lease(
        tmp_path,
        state="held",
        holder="session_live",
        heartbeat_at=_iso(NOW - timedelta(minutes=5)),
    )
    verdict = wake.assess(NOW, path)
    assert verdict["state"] == wake.ACTIVE
    assert verdict["silent_minutes"] == 5


def test_held_with_stale_heartbeat_is_silent_and_names_the_holder(tmp_path):
    """The case the mechanism exists for, at the measured magnitude."""
    path = _lease(
        tmp_path,
        state="held",
        holder="session_016e2k4UmsMGgpbrJ5ctqeFv",
        heartbeat_at=_iso(NOW - timedelta(minutes=720)),
        ttl_minutes=90,
    )
    verdict = wake.assess(NOW, path)
    assert verdict["state"] == wake.SILENT
    assert verdict["wake_session"] == "session_016e2k4UmsMGgpbrJ5ctqeFv"
    assert verdict["silent_minutes"] == 720
    assert verdict["lease_expired"] is True


def test_boundary_is_not_off_by_one(tmp_path):
    """Exactly at the threshold is silent; one minute under is active.

    The threshold is a bar to be past, not approached — a manager sitting
    permanently one minute under it is the case a `>` would never catch.
    """
    threshold = wake._silence_threshold()
    under = _lease(
        tmp_path,
        state="held",
        holder="s",
        heartbeat_at=_iso(NOW - timedelta(minutes=threshold - 1)),
    )
    assert wake.assess(NOW, under)["state"] == wake.ACTIVE

    at = tmp_path / "at.json"
    at.write_text(
        json.dumps(
            {"state": "held", "holder": "s", "heartbeat_at": _iso(NOW - timedelta(minutes=threshold))}
        ),
        encoding="utf-8",
    )
    assert wake.assess(NOW, at)["state"] == wake.SILENT


def test_claim_without_heartbeat_ages_rather_than_reading_fresh(tmp_path):
    """A lease claimed and never beaten must not look permanently healthy."""
    path = _lease(
        tmp_path,
        state="held",
        holder="session_never_beat",
        claimed_at=_iso(NOW - timedelta(minutes=400)),
    )
    verdict = wake.assess(NOW, path)
    assert verdict["state"] == wake.SILENT
    assert verdict["silent_minutes"] == 400


def test_holder_present_but_no_timestamps_is_unreadable(tmp_path):
    """Age uncomputable is 'we did not look', not 'active'."""
    path = _lease(tmp_path, state="held", holder="session_x")
    assert wake.assess(NOW, path)["state"] == wake.UNREADABLE


def test_threshold_tracks_the_lease_module(tmp_path):
    """The wake must not drift from the lease's own heartbeat target."""
    lease_mod = _load("manager_lease")
    assert wake._silence_threshold() == lease_mod.HEARTBEAT_TARGET_MINUTES


# ── brief: it must CARRY the contract, not just poke ────────────────────────


def test_brief_lands_on_the_status_contract_in_order():
    """checklist → recently done → next. The ORDER is the contract."""
    text = wake.brief(NOW)
    i_checklist = text.index("## 1. CHECKLIST")
    i_done = text.index("## 2. RECENTLY DONE")
    i_next = text.index("## 3. NEXT")
    assert i_checklist < i_done < i_next


def test_brief_is_self_contained_state_not_a_link_to_go_read():
    """A woken manager may have no tools to go fetch anything with."""
    text = wake.brief(NOW)
    # Real register content, not just section headers.
    assert "CY-20260903-MANAGER-CONTROL" in text
    assert "Merge queue" in text
    assert "Sub-sessions" in text
    assert len(text) > 2000


def test_brief_says_being_woken_is_not_proof_of_being_broken():
    """The false-wake path is chosen; the brief must not accuse."""
    assert "NOT proof you were broken" in wake.brief(NOW)


def test_brief_survives_last_observed_shape_drift(tmp_path, monkeypatch):
    """MEASURED over all 95 registry rows: 27 dict, 67 null, 1 bare string.

    A reader that assumes the documented dict crashes on that one row. The brief
    must normalise and COUNT the odd shapes rather than swallow them.
    """
    sessions = tmp_path / "SESSIONS.json"
    sessions.write_text(
        json.dumps(
            {
                "updated_at": _iso(NOW),
                "sessions": [
                    {"session_id": "a", "last_observed": "2026-09-04T08:20:00Z"},
                    {"session_id": "b", "last_observed": None},
                    {
                        "session_id": "c",
                        "last_observed": {"status_category": "need_input", "needs_action": "x"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wake, "SESSIONS_PATH", sessions)
    text = "\n".join(wake._next_section(NOW))
    assert "3 registered" in text
    assert "1 NEVER observed" in text
    assert "1 with a `last_observed` that is not an object" in text


def test_brief_flags_undeclared_checklist_states(tmp_path, monkeypatch):
    """A register whose rows use states its own schema does not declare."""
    checklist = tmp_path / "MANAGER-CHECKLIST.json"
    checklist.write_text(
        json.dumps(
            {
                "cycle": "CY-X",
                "updated_at": _iso(NOW),
                "states": {"done": "d", "queued": "q"},
                "items": [{"id": "1", "state": "waiting", "title": "t"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wake, "CHECKLIST_PATH", checklist)
    text = "\n".join(wake._checklist_section(NOW))
    assert "NOT declared" in text and "`waiting`" in text


def test_brief_does_not_crash_on_missing_registers(tmp_path, monkeypatch):
    """A wake that raises delivers nothing — worse than a degraded brief."""
    for attr in ("CHECKLIST_PATH", "SESSIONS_PATH", "MERGE_QUEUE_PATH"):
        monkeypatch.setattr(wake, attr, tmp_path / f"absent-{attr}.json")
    text = wake.brief(NOW)
    assert "ABSENT" in text
    assert "## 3. NEXT" in text


# ── receipt ─────────────────────────────────────────────────────────────────


def test_receipt_records_no_action_runs_too(tmp_path):
    """Without them, 'nothing needed waking' and 'the wake is dead' are one."""
    path = tmp_path / "MANAGER-WAKE.json"
    wake.record_run(wake.ACTIVE, "no_action", detail="quiet", now=NOW, path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["runs"][-1]["outcome"] == "no_action"
    assert doc["runs"][-1]["assessed_state"] == wake.ACTIVE


def test_receipt_is_bounded(tmp_path):
    """An unbounded ledger rewritten hourly is a conflict generator."""
    path = tmp_path / "MANAGER-WAKE.json"
    for i in range(wake.RECEIPT_KEEP_RUNS + 25):
        wake.record_run(wake.ACTIVE, "no_action", detail=str(i), now=NOW, path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["runs"]) == wake.RECEIPT_KEEP_RUNS
    # The newest are kept, not the oldest.
    assert doc["runs"][-1]["detail"] == str(wake.RECEIPT_KEEP_RUNS + 24)


@pytest.mark.parametrize("bad", ["nonsense", "", "ACTIVE"])
def test_receipt_refuses_undeclared_state(tmp_path, bad):
    with pytest.raises(ValueError):
        wake.record_run(bad, "no_action", now=NOW, path=tmp_path / "r.json")


def test_receipt_refuses_undeclared_outcome(tmp_path):
    with pytest.raises(ValueError):
        wake.record_run(wake.SILENT, "maybe", now=NOW, path=tmp_path / "r.json")


def test_self_test_passes():
    assert wake._self_test() == 0


# ── liveness grading of the wake itself ─────────────────────────────────────


def test_never_ran_is_distinct_from_stale(tmp_path):
    """Different fixes: create/repair the Routine vs hunt a regression."""
    assert liveness.grade(NOW, path=tmp_path / "absent.json")["state"] == liveness.NEVER_RAN

    stale = tmp_path / "stale.json"
    stale.write_text(
        json.dumps({"runs": [{"at": _iso(NOW - timedelta(hours=30))}]}), encoding="utf-8"
    )
    assert liveness.grade(NOW, path=stale)["state"] == liveness.STALE


def test_receipt_with_no_runs_is_never_ran_not_stale(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"runs": []}), encoding="utf-8")
    assert liveness.grade(NOW, path=path)["state"] == liveness.NEVER_RAN


def test_unreadable_receipt_is_not_evidence_of_a_dead_wake(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{{{", encoding="utf-8")
    result = liveness.grade(NOW, path=path)
    assert result["state"] == liveness.UNREADABLE
    assert "DID NOT LOOK" in result["detail"].upper()


def test_newest_run_decides_not_list_order(tmp_path):
    path = tmp_path / "unordered.json"
    path.write_text(
        json.dumps(
            {"runs": [{"at": _iso(NOW - timedelta(hours=1))}, {"at": _iso(NOW - timedelta(days=9))}]}
        ),
        encoding="utf-8",
    )
    assert liveness.grade(NOW, path=path)["state"] == liveness.FRESH


def test_window_tolerates_one_missed_fire(tmp_path):
    """An alarm that fires on a single miss is the desensitised-alarm bug."""
    path = tmp_path / "late.json"
    path.write_text(
        json.dumps({"runs": [{"at": _iso(NOW - timedelta(hours=2))}]}), encoding="utf-8"
    )
    assert liveness.grade(NOW, path=path)["state"] == liveness.FRESH
    assert liveness.DEFAULT_WINDOW_HOURS > 1.0


def test_liveness_self_test_passes():
    assert liveness._self_test() == 0
