"""Tests for scripts/check_ib_gateway.py — IB Gateway auto-heal watchdog.

Covers the two pure functions that carry the logic:
  * classify_probe() — maps an ib_connect_check JSON payload to healthy/wedged,
    including the key wedge signature (connected but net_liquidation=None).
  * decide() — the detect / restart / cooldown / capped-backoff / red-flag /
    recover state machine, including the alert-only (auto_restart=False) path
    and the no-tight-loop guard rails. NO-HALT (operator 2026-10-09): there
    is no exhausted state — past --max-restarts it red-flags ONCE and keeps
    restarting on a capped backoff.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def wd():
    spec = importlib.util.spec_from_file_location(
        "check_ib_gateway",
        Path(__file__).resolve().parents[1] / "scripts" / "check_ib_gateway.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# classify_probe
# --------------------------------------------------------------------------


def test_classify_healthy(wd):
    payload = json.dumps({"ok": True, "results": [
        {"connected": True, "net_liquidation": 1_000_000.0, "accounts": ["DUQ325724"]}]})
    v = wd.classify_probe(payload)
    assert v["healthy"] is True


def test_classify_logged_out_connected_but_no_netliq(wd):
    # The actual wedge signature: API handshake OK, upstream read dead.
    payload = json.dumps({"ok": True, "results": [
        {"connected": True, "net_liquidation": None, "accounts": []}]})
    v = wd.classify_probe(payload)
    assert v["healthy"] is False
    assert "net_liquidation" in v["reason"].lower()


def test_classify_not_connected(wd):
    payload = json.dumps({"ok": False, "results": [
        {"connected": False, "net_liquidation": None, "error": "TimeoutError"}]})
    v = wd.classify_probe(payload)
    assert v["healthy"] is False
    assert "connect failed" in v["reason"]


def test_classify_no_results(wd):
    assert wd.classify_probe(json.dumps({"results": []}))["healthy"] is False


def test_classify_garbage(wd):
    assert wd.classify_probe("not json")["healthy"] is False


def test_classify_actionable_flags(wd):
    # Affirmative gateway-unhealthy signatures a restart could fix → actionable.
    wedge = json.dumps({"results": [{"connected": True, "net_liquidation": None}]})
    nocon = json.dumps({"results": [{"connected": False, "error": "TimeoutError"}]})
    assert wd.classify_probe(wedge)["actionable"] is True
    assert wd.classify_probe(nocon)["actionable"] is True
    # Probe couldn't produce a usable verdict → NOT actionable (restart can't fix
    # a broken probe environment).
    assert wd.classify_probe("not json")["actionable"] is False
    assert wd.classify_probe(json.dumps({"results": []}))["actionable"] is False


def test_classify_connect_failure_actionability(wd):
    # A connect failure from a PROBE-SIDE error (missing client lib) is NOT
    # restartable — restarting the gateway can't install ib_insync. (The exact
    # 2026-06-22 gateway-VM signature.)
    dep = json.dumps({"results": [{"connected": False,
                                   "error": "ib_insync is not installed — add 'ib_insync'..."}]})
    v = wd.classify_probe(dep)
    assert v["healthy"] is False and v["actionable"] is False
    # A genuine transport failure (timeout / refused / down) IS actionable.
    for e in ("TimeoutError", "Connection refused", "not connected"):
        v = wd.classify_probe(json.dumps({"results": [{"connected": False, "error": e}]}))
        assert v["actionable"] is True, e


# --------------------------------------------------------------------------
# ib_gateway_local_probe (dep-free docker-logs detector)
# --------------------------------------------------------------------------


@pytest.fixture
def lp():
    spec = importlib.util.spec_from_file_location(
        "ib_gateway_local_probe",
        Path(__file__).resolve().parents[1] / "scripts" / "ops" / "ib_gateway_local_probe.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_docker(*, running="true", logs="", ver_rc=0, inspect_rc=0, logs_rc=0):
    def _d(args):
        head = args[:1]
        if head == ["version"]:
            return (ver_rc, "27.0" if ver_rc == 0 else "no docker")
        if head == ["inspect"]:
            return (inspect_rc, running)
        if head == ["logs"]:
            return (logs_rc, logs)
        return (1, "")
    return _d


def test_local_probe_healthy(lp):
    lp._docker = _fake_docker(logs="normal line\nanother normal line")
    snap = lp.diagnose()["results"][0]
    assert snap["connected"] is True and snap["net_liquidation"] == 1


def test_local_probe_wedged_on_socat_refused(lp):
    refused = "socat[1] E connect(5, AF=2 127.0.0.1:4002, 16): Connection refused"
    lp._docker = _fake_docker(logs="\n".join([refused] * 3))
    snap = lp.diagnose()["results"][0]
    assert snap["connected"] is True and snap["net_liquidation"] is None


def test_local_probe_recent_login_overrides_wedge(lp):
    lp._docker = _fake_docker(
        logs="socat ...127.0.0.1:4002... Connection refused\nIBC: Login has completed")
    assert lp.diagnose()["results"][0]["net_liquidation"] == 1


def test_local_probe_container_down(lp):
    lp._docker = _fake_docker(running="false")
    assert lp.diagnose()["results"][0]["connected"] is False


def test_local_probe_no_docker_is_inconclusive(lp):
    # No docker → empty results → classify_probe maps to NON-actionable.
    lp._docker = _fake_docker(ver_rc=1)
    assert lp.diagnose()["results"] == []


# --------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------


def _decide(wd, *, healthy, state, auto_restart=True, now=10_000.0,
            restart_after=2, max_restarts=3, cooldown_s=1200.0,
            max_backoff_s=3600.0, actionable=True, in_window=False):
    return wd.decide(healthy=healthy, state=state, restart_after=restart_after,
                     max_restarts=max_restarts, cooldown_s=cooldown_s, now=now,
                     auto_restart=auto_restart,
                     max_backoff_s=max_backoff_s,
                     actionable=actionable, in_window=in_window)


def test_healthy_from_clean_is_noop(wd):
    d = _decide(wd, healthy=True, state={})
    assert d["action"] == "none" and d["alert"] is False


def test_healthy_after_wedge_recovers(wd):
    d = _decide(wd, healthy=True, state={"last_status": "wedged", "wedged_streak": 5})
    assert d["action"] == "recovered" and d["alert"] is True
    assert d["new_state"]["wedged_streak"] == 0
    assert d["new_state"]["restart_attempts"] == 0


def test_first_wedge_detection_alerts_but_does_not_restart(wd):
    d = _decide(wd, healthy=False, state={})
    assert d["action"] == "detected" and d["alert"] is True
    assert d["new_state"]["wedged_streak"] == 1


def test_sustained_wedge_triggers_restart(wd):
    # streak was 1, this run makes it 2 == restart_after.
    d = _decide(wd, healthy=False, state={"last_status": "wedged", "wedged_streak": 1})
    assert d["action"] == "restart" and d["alert"] is True
    assert d["new_state"]["restart_attempts"] == 1
    assert d["new_state"]["last_restart_ts"] == 10_000.0


def test_inconclusive_probe_never_restarts_even_when_sustained(wd):
    # A non-actionable (probe-broken) read must NOT restart, even past
    # restart_after, and must reset the consecutive-wedge streak.
    d = _decide(wd, healthy=False, actionable=False,
                state={"last_status": "wedged", "wedged_streak": 9})
    assert d["action"] == "inconclusive" and d["alert"] is True
    assert d["new_state"]["wedged_streak"] == 0
    assert "last_restart_ts" not in d["new_state"]


def test_inconclusive_repeated_alerts_once(wd):
    d = _decide(wd, healthy=False, actionable=False,
                state={"last_status": "inconclusive", "wedged_streak": 0})
    assert d["action"] == "none" and d["alert"] is False


def test_inconclusive_then_actionable_needs_full_streak(wd):
    # Inconclusive reset the streak, so the NEXT actionable wedge is only the
    # first confirmed one → detect, not restart (restart needs restart_after=2).
    d = _decide(wd, healthy=False, actionable=True,
                state={"last_status": "inconclusive", "wedged_streak": 0})
    assert d["action"] == "detected"
    assert d["new_state"]["wedged_streak"] == 1


def test_alert_only_never_restarts(wd):
    d = _decide(wd, healthy=False, auto_restart=False,
                state={"last_status": "wedged", "wedged_streak": 9})
    assert d["action"] in ("detected", "none")
    assert "restart_attempts" not in d["new_state"] or d["new_state"]["restart_attempts"] == 0
    assert "last_restart_ts" not in d["new_state"]


def test_cooldown_blocks_back_to_back_restart(wd):
    state = {"last_status": "wedged", "wedged_streak": 3,
             "restart_attempts": 1, "last_restart_ts": 9_500.0}
    # now=10_000, cooldown 1200s → only 500s elapsed → blocked.
    d = _decide(wd, healthy=False, state=state, now=10_000.0, cooldown_s=1200.0)
    assert d["action"] in ("detected", "none")
    assert d["new_state"]["restart_attempts"] == 1  # unchanged


def test_cooldown_elapsed_allows_restart(wd):
    state = {"last_status": "wedged", "wedged_streak": 3,
             "restart_attempts": 1, "last_restart_ts": 8_000.0}
    d = _decide(wd, healthy=False, state=state, now=10_000.0, cooldown_s=1200.0)
    assert d["action"] == "restart"
    assert d["new_state"]["restart_attempts"] == 2


def test_past_max_restarts_red_flags_once_and_keeps_restarting(wd):
    """NO-HALT: the old bound no longer ends the episode. First eligible check
    past it raises ONE red flag AND restarts (backoff elapsed); per-restart
    alerts stop; later checks restart again after the capped backoff without
    a second flag."""
    state = {"last_status": "wedged", "wedged_streak": 8,
             "restart_attempts": 3, "last_restart_ts": 0.0}
    d1 = _decide(wd, healthy=False, state=state, max_restarts=3, now=10_000.0)
    assert d1["action"] == "restart"
    assert d1["red_flag"] is True
    assert d1["alert"] is False  # per-restart ping is journal-only now
    assert d1["new_state"]["restart_attempts"] == 4
    assert d1["new_state"]["red_flag_alerted"] is True

    # Inside the backoff (4 attempts, bound 3 -> 1200 * 2**2 = 4800s, capped
    # at max_backoff_s=3600s): wait.
    d2 = _decide(wd, healthy=False, state=d1["new_state"], max_restarts=3,
                 now=10_000.0 + 3_000.0)
    assert d2["action"] == "none" and d2["red_flag"] is False
    assert d2["new_state"]["restart_attempts"] == 4

    # Capped backoff elapsed: restart again, no second red flag.
    d3 = _decide(wd, healthy=False, state=d2["new_state"], max_restarts=3,
                 now=10_000.0 + 3_600.0)
    assert d3["action"] == "restart" and d3["red_flag"] is False
    assert d3["new_state"]["restart_attempts"] == 5


def test_red_flag_raised_even_while_in_backoff(wd):
    # The check right after the last bounded restart is inside the backoff:
    # the red flag still goes out then (once), the restart waits.
    state = {"last_status": "wedged", "wedged_streak": 8,
             "restart_attempts": 3, "last_restart_ts": 9_900.0}
    d = _decide(wd, healthy=False, state=state, max_restarts=3, now=10_000.0)
    assert d["action"] == "none" and d["red_flag"] is True
    d2 = _decide(wd, healthy=False, state=d["new_state"], max_restarts=3,
                 now=10_100.0)
    assert d2["red_flag"] is False


def test_restart_backoff_doubles_and_is_capped(wd):
    f = wd.restart_backoff_s
    cd, cap = 1200.0, 3600.0
    assert [f(a, 3, cd, cap) for a in range(3)] == [cd] * 3
    assert f(3, 3, cd, cap) == 2400.0
    assert f(4, 3, cd, cap) == cap
    assert f(10_000, 3, cd, cap) == cap


def test_never_terminal_far_past_bound(wd):
    state = {"last_status": "wedged", "wedged_streak": 999,
             "restart_attempts": 400, "red_flag_alerted": True,
             "last_restart_ts": 0.0}
    d = _decide(wd, healthy=False, state=state, now=3_601.0)
    assert d["action"] == "restart" and d["red_flag"] is False


def test_recovery_after_red_flag_announces_once_and_resets(wd):
    state = {"last_status": "wedged", "restart_attempts": 6,
             "red_flag_alerted": True}
    d = _decide(wd, healthy=True, state=state)
    assert d["action"] == "recovered" and d["alert"] is True
    assert d["red_flag_cleared"] is True
    assert d["new_state"]["restart_attempts"] == 0
    assert d["new_state"]["red_flag_alerted"] is False
    msg = wd.render("recovered", account="ib_paper", reason="ok", streak=0,
                    attempt=0, max_restarts=3, restart=None,
                    red_flag_cleared=True)
    assert "red flag cleared" in msg
    d2 = _decide(wd, healthy=True, state=d["new_state"])
    assert d2["action"] == "none" and d2["alert"] is False


def test_legacy_exhausted_latch_ignored_and_removed(wd, capsys):
    # State from the old code: budget spent, exhausted latch set. It must not
    # block restarts; it is dropped with a log line, and carries over as the
    # red flag having been raised (the old EXHAUSTED page already went out).
    state = {"last_status": "wedged", "wedged_streak": 8,
             "restart_attempts": 3, "last_restart_ts": 0.0,
             "exhausted_alerted": True}
    d = _decide(wd, healthy=False, state=state, now=10_000.0)
    assert d["action"] == "restart"
    assert d["red_flag"] is False
    assert "exhausted_alerted" not in d["new_state"]
    assert d["new_state"]["red_flag_alerted"] is True
    assert "ignoring legacy exhausted_alerted" in capsys.readouterr().out


def test_main_past_bound_sends_one_red_flag_and_restarts(wd, tmp_path):
    """End-to-end through main(): probe wedged, budget spent, backoff elapsed
    -> exactly one Telegram (the red flag) and the restart still runs."""
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"last_status": "wedged", "wedged_streak": 5,
                                 "restart_attempts": 3, "last_restart_ts": 0.0}))
    wd.run_probe = lambda *a, **k: {"healthy": False, "actionable": True,
                                    "reason": "net_liquidation=None"}
    restarts = []
    wd.try_restart = lambda *a, **k: restarts.append(1) or {
        "ran": True, "returncode": 0, "login_completed": False, "tail": ""}
    sent = []
    wd.send_alert = lambda m: sent.append(m) or True
    rc = wd.main(["--state", str(state), "--auto-restart", "--max-restarts", "3",
                  "--exhaustion-reset-min", "120"])  # deprecated flag still parses
    assert rc == 0
    assert restarts == [1]
    assert len(sent) == 1 and "[RED FLAG]" in sent[0]
    saved = json.loads(state.read_text())
    assert saved["restart_attempts"] == 4 and saved["red_flag_alerted"] is True


# --------------------------------------------------------------------------
# in_suppression_window (2026-07-02, BL-20260623-002)
# --------------------------------------------------------------------------


def test_suppression_window_parses_and_checks_utc_range(wd):
    from datetime import datetime, timezone

    inside = datetime(2026, 7, 2, 4, 0, tzinfo=timezone.utc)
    before = datetime(2026, 7, 2, 3, 44, tzinfo=timezone.utc)
    at_end = datetime(2026, 7, 2, 5, 45, tzinfo=timezone.utc)  # end is exclusive
    after = datetime(2026, 7, 2, 6, 5, tzinfo=timezone.utc)

    assert wd.in_suppression_window("03:45-05:45", inside) is True
    assert wd.in_suppression_window("03:45-05:45", before) is False
    assert wd.in_suppression_window("03:45-05:45", at_end) is False
    assert wd.in_suppression_window("03:45-05:45", after) is False


def test_suppression_window_disabled_or_malformed_fails_open_to_false(wd):
    from datetime import datetime, timezone

    now = datetime(2026, 7, 2, 4, 0, tzinfo=timezone.utc)
    assert wd.in_suppression_window("", now) is False
    assert wd.in_suppression_window(None, now) is False
    assert wd.in_suppression_window("garbage", now) is False
    assert wd.in_suppression_window("03:45", now) is False


# --------------------------------------------------------------------------
# decide — suppression window (2026-07-02, BL-20260623-002): a wedge inside
# IBKR's own reset window is logged but must never drive a restart, and the
# streak must be frozen (not reset) so it resumes seamlessly once the window
# closes.
# --------------------------------------------------------------------------


def test_wedge_inside_window_suppresses_and_freezes_streak(wd):
    d = _decide(wd, healthy=False, state={}, in_window=True)
    assert d["action"] == "suppressed" and d["alert"] is True
    assert d["new_state"].get("wedged_streak", 0) == 0  # never touched/initialised
    assert d["new_state"].get("restart_attempts", 0) == 0
    assert d["new_state"]["last_status"] == "suppressed"


def test_wedge_inside_window_alerts_once_then_silent(wd):
    state = {"last_status": "suppressed"}
    d = _decide(wd, healthy=False, state=state, in_window=True)
    assert d["action"] == "none" and d["alert"] is False


def test_wedge_never_restarts_inside_window_even_past_restart_after(wd):
    # Streak would be well past restart_after outside the window, but inside
    # it a restart must never fire.
    state = {"last_status": "suppressed", "wedged_streak": 10, "restart_attempts": 0}
    d = _decide(wd, healthy=False, state=state, in_window=True, restart_after=2)
    assert d["action"] in ("none", "suppressed")
    assert d["new_state"]["restart_attempts"] == 0


def test_wedge_spanning_window_close_resumes_streak_without_duplicate_alert(wd):
    # Wedge detected inside the window (alerts once)...
    d1 = _decide(wd, healthy=False, state={}, in_window=True)
    assert d1["action"] == "suppressed" and d1["alert"] is True
    # ...stays wedged as the window closes: no duplicate "detected" alert,
    # and the streak resumes counting from 1, not from a reset 0.
    d2 = _decide(wd, healthy=False, state=d1["new_state"], in_window=False)
    assert d2["action"] == "none" and d2["alert"] is False
    assert d2["new_state"]["wedged_streak"] == 1
    # One more wedged tick reaches restart_after=2 and becomes eligible.
    d3 = _decide(wd, healthy=False, state=d2["new_state"], in_window=False,
                 now=20_000.0, cooldown_s=0.0)
    assert d3["action"] == "restart"


def test_healthy_inside_window_still_recovers_normally(wd):
    # Health checks are unaffected by the suppression window — only a wedge
    # verdict is suppressed.
    state = {"last_status": "suppressed", "wedged_streak": 4}
    d = _decide(wd, healthy=True, state=state, in_window=True)
    assert d["action"] == "recovered" and d["alert"] is True
    assert d["new_state"]["wedged_streak"] == 0
