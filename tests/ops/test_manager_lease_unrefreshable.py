"""A live, heartbeating manager read as EXPIRED because `main` was red.

MEASURED 2026-09-09T08:05:10Z (`BL-20260909-A-BASE-BRANCH-RED-CAN-EXPIRE-THE-
MANAGER-LEASE-OF-A-SESSION-THAT-IS-ALIVE-AND-HEARTBEATING`): `status` read
`heartbeat_at=07:34:57Z` against a 90-minute TTL while the holder had
heartbeated THREE more times (07:46:27, 07:54:48, 07:56:42) — every one trapped
in PR #11515, which could not merge because an unrelated guard was red **on
clean main**. A session arriving cold would have found the lease claimable.

⚠️ **THE COUPLING NOBODY DESIGNED.** The lease lives in the repo so it survives
its holder's DEATH and is readable COLD — correct, and why it cannot be session
state. But that makes its refresh a MERGE, and a merge is gated on green. Lease
liveness therefore depends on CI health, an unrelated property.

⚠️ **WHAT THIS UNIT IS AND IS NOT.** It is the row's option **(b)**: suspend the
TTL and RECORD THE REASON, so the register says *"could not refresh"* rather
than silently reading *expired*. It is **not** (a)/(c) — a heartbeat still
cannot reach `main` while `main` is red. And it is emphatically **not** a TTL
widening or a guard bypass, both of which that row rules out by name: the TTL is
unchanged at 90 minutes and no guard is skipped.

Run: ``python3 -m pytest tests/ops/test_manager_lease_unrefreshable.py``
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ops" / "manager_lease.py"


def _load():
    spec = importlib.util.spec_from_file_location("_mlease", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()
NOW = datetime(2026, 9, 9, 8, 5, 10, tzinfo=timezone.utc)


def _held(minutes_ago: float, holder="session_OTHER"):
    return {"holder": holder, "state": "held",
            "claimed_at": M._iso(NOW - timedelta(minutes=minutes_ago + 10)),
            "heartbeat_at": M._iso(NOW - timedelta(minutes=minutes_ago))}


def _grade(lease, main_ci):
    return M.grade(lease, True, "session_ME", now=NOW, main_ci=main_ci)


# ── THE DEFECT ──────────────────────────────────────────────────────────────

def test_past_ttl_with_main_RED_is_unrefreshable_and_NOT_claimable():
    """The 2026-09-09 case. The holder may be alive and simply blocked."""
    state, msg = _grade(_held(131), M.MAIN_RED)
    assert state == "unrefreshable"
    assert state not in M.CLAIMABLE
    assert state in M.REFUSING
    assert "could not refresh" in msg or "could not have landed" in msg


def test_past_ttl_with_main_GREEN_is_still_expired_and_claimable():
    """POSITIVE CONTROL, and the one that matters most: a change that graded
    everything `unrefreshable` would pass the test above and break takeover
    entirely — the failure mode worse than the bug."""
    state, _ = _grade(_held(131), M.MAIN_GREEN)
    assert state == "expired"
    assert state in M.CLAIMABLE


def test_the_green_message_says_a_heartbeat_COULD_have_landed():
    """Otherwise `expired` carries no evidence that the holder is really gone."""
    _, msg = _grade(_held(131), M.MAIN_GREEN)
    assert "GREEN" in msg and "could have landed" in msg.lower()


def test_unknown_is_claimable_but_says_it_could_not_rule_out_the_red_case():
    """Failing closed here would make the lease unclaimable exactly when a
    manager has DIED and CI is unreachable — the case takeover exists for."""
    state, msg = _grade(_held(131), M.MAIN_UNKNOWN)
    assert state == "expired_unverified"
    assert state in M.CLAIMABLE, "a dead manager must still be replaceable"
    assert "not ruled out" in msg


def test_a_FRESH_lease_is_untouched_by_a_red_main():
    """The split must apply only past the TTL. A holder beating on cadence is
    `held_fresh` whatever CI is doing."""
    for ci in M.MAIN_STATES:
        state, _ = _grade(_held(5), ci)
        assert state == "held_fresh", ci


def test_held_by_me_is_untouched_by_a_red_main():
    lease = _held(5, holder="session_ME")
    for ci in M.MAIN_STATES:
        assert M.grade(lease, True, "session_ME", now=NOW, main_ci=ci)[0] == "held_by_me"


def test_an_unparseable_heartbeat_ALSO_respects_a_red_main():
    """The no-readable-timestamp path used to return `expired` directly. It is
    the same hazard: unreadable freshness plus a blocked merge is not evidence
    the holder is gone."""
    lease = {"holder": "session_OTHER", "state": "held", "heartbeat_at": "nonsense"}
    assert M.grade(lease, True, "me", now=NOW, main_ci=M.MAIN_RED)[0] == "unrefreshable"
    assert M.grade(lease, True, "me", now=NOW, main_ci=M.MAIN_GREEN)[0] == "expired"


def test_unreadable_still_fails_closed_regardless_of_ci():
    """The pre-existing refusal must not be weakened by any of this."""
    for ci in M.MAIN_STATES:
        assert M.grade(None, False, "me", now=NOW, main_ci=ci)[0] == "unreadable"


def test_absent_and_released_are_claimable_regardless_of_ci():
    """A bootstrap or a deliberate stand-down is not a refresh failure."""
    for ci in M.MAIN_STATES:
        assert M.grade(None, True, "me", now=NOW, main_ci=ci)[0] == "absent"
        rel = {"holder": "x", "state": "released", "released_at": "t"}
        assert M.grade(rel, True, "me", now=NOW, main_ci=ci)[0] == "released"


def test_the_ttl_is_NOT_widened():
    """The row rules out widening the TTL by name. A lease 1 minute past it is
    still past it when main is green."""
    assert M.TTL_MINUTES == 90
    assert _grade(_held(91), M.MAIN_GREEN)[0] == "expired"
    assert _grade(_held(89), M.MAIN_GREEN)[0] == "held_fresh"


def test_the_default_is_unknown_so_an_uninformed_caller_never_reads_green():
    """A caller that does not pass `main_ci` must not be told main is fine."""
    state, _ = M.grade(_held(131), True, "me", now=NOW)
    assert state == "expired_unverified", "the default must not be MAIN_GREEN"


def test_all_states_are_reachable_so_none_is_decorative():
    reached = {
        M.grade(None, False, "me", now=NOW)[0],
        M.grade(None, True, "me", now=NOW)[0],
        _grade(_held(131), M.MAIN_GREEN)[0],
        _grade(_held(131), M.MAIN_RED)[0],
        _grade(_held(131), M.MAIN_UNKNOWN)[0],
        _grade(_held(5), M.MAIN_GREEN)[0],
    }
    assert {"unreadable", "absent", "expired", "unrefreshable",
            "expired_unverified", "held_fresh"} <= reached


# ── THE CI READER ───────────────────────────────────────────────────────────

def _reader(monkeypatch, payload=None, boom=None, status_by_name=None):
    import json as _json

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps(payload).encode()

    def fake(req, timeout=None):
        if boom:
            raise boom
        return _Resp()

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return M.main_ci_state()


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "conclusion": conclusion, "status": status}


def test_reader_green(monkeypatch):
    st, why = _reader(monkeypatch, {"check_runs": [_run("guards"), _run("pytest-run")]})
    assert st == M.MAIN_GREEN, why


def test_reader_red_names_the_failing_check(monkeypatch):
    st, why = _reader(monkeypatch, {"check_runs": [
        _run("guards"), _run("check_trainer_capture_watch", "failure")]})
    assert st == M.MAIN_RED
    assert "check_trainer_capture_watch" in why, "a reader must know WHICH"


def test_reader_ZERO_check_runs_is_unknown_not_green(monkeypatch):
    """The zero-check trap CLAUDE.md documents: no runs renders identically to
    'all passed' and to 'nothing ran'. MEASURED live on 2026-09-12 — main's head
    really did report n=0, so this is the common case, not a corner."""
    st, why = _reader(monkeypatch, {"check_runs": []})
    assert st == M.MAIN_UNKNOWN
    assert st != M.MAIN_GREEN
    assert "ZERO" in why


def test_reader_pending_is_unknown_not_green(monkeypatch):
    st, _ = _reader(monkeypatch, {"check_runs": [
        _run("guards"), _run("pytest-run", None, "in_progress")]})
    assert st == M.MAIN_UNKNOWN


def test_reader_no_token_is_unknown(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    st, why = M.main_ci_state()
    assert st == M.MAIN_UNKNOWN and "token" in why.lower()


def test_reader_network_failure_is_unknown_not_green(monkeypatch):
    st, why = _reader(monkeypatch, boom=OSError("no route to host"))
    assert st == M.MAIN_UNKNOWN
    assert st != M.MAIN_GREEN


def test_reader_malformed_payload_is_unknown(monkeypatch):
    st, _ = _reader(monkeypatch, {"nope": 1})
    assert st == M.MAIN_UNKNOWN


def test_the_opt_out_grades_unknown_which_can_only_be_more_permissive():
    class A:
        no_check_main = True
    st, why = M._resolve_main_ci(A())
    assert st == M.MAIN_UNKNOWN
    assert st in {M.MAIN_UNKNOWN}
    assert "did not look" in why
    # and unknown is claimable, so opting out cannot block a takeover
    assert M.grade(_held(131), True, "me", now=NOW, main_ci=st)[0] in M.CLAIMABLE
