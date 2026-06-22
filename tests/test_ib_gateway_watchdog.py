"""Tests for scripts/check_ib_gateway.py — IB Gateway auto-heal watchdog.

Covers the two pure functions that carry the logic:
  * classify_probe() — maps an ib_connect_check JSON payload to healthy/wedged,
    including the key wedge signature (connected but net_liquidation=None).
  * decide() — the detect / restart / cooldown / max-restarts / recover state
    machine, including the alert-only (auto_restart=False) path and the
    no-restart-loop guard rails.
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


# --------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------


def _decide(wd, *, healthy, state, auto_restart=True, now=10_000.0,
            restart_after=2, max_restarts=3, cooldown_s=1200.0,
            exhaustion_reset_s=0.0, actionable=True):
    return wd.decide(healthy=healthy, state=state, restart_after=restart_after,
                     max_restarts=max_restarts, cooldown_s=cooldown_s, now=now,
                     auto_restart=auto_restart,
                     exhaustion_reset_s=exhaustion_reset_s,
                     actionable=actionable)


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


def test_max_restarts_exhausts_then_silent(wd):
    state = {"last_status": "wedged", "wedged_streak": 8,
             "restart_attempts": 3, "last_restart_ts": 0.0}
    d1 = _decide(wd, healthy=False, state=state, max_restarts=3)
    assert d1["action"] == "exhausted" and d1["alert"] is True
    # Next run with the exhausted flag set stays silent.
    d2 = _decide(wd, healthy=False, state=d1["new_state"], max_restarts=3)
    assert d2["action"] == "none" and d2["alert"] is False


def test_recovery_clears_exhausted_flag(wd):
    state = {"last_status": "wedged", "restart_attempts": 3, "exhausted_alerted": True}
    d = _decide(wd, healthy=True, state=state)
    assert d["action"] == "recovered"
    assert d["new_state"]["exhausted_alerted"] is False


# --------------------------------------------------------------------------
# decide — exhaustion re-arm (BL-20260605-004: a wedge spanning IBKR's reset
# window must not strand MES for the whole episode after the budget is spent)
# --------------------------------------------------------------------------


def test_exhausted_stays_silent_when_rearm_disabled(wd):
    # exhaustion_reset_s=0 (default) → original give-up-for-the-episode.
    state = {"last_status": "wedged", "wedged_streak": 8, "restart_attempts": 3,
             "last_restart_ts": 0.0, "exhausted_alerted": True}
    d = _decide(wd, healthy=False, state=state, now=10_000.0,
                max_restarts=3, exhaustion_reset_s=0.0)
    assert d["action"] == "none" and d["alert"] is False
    assert d["new_state"]["restart_attempts"] == 3  # not re-armed


def test_exhausted_stays_silent_within_reset_window(wd):
    # Budget spent and only 1h since the last restart < 2h reset → still silent.
    state = {"last_status": "wedged", "wedged_streak": 8, "restart_attempts": 3,
             "last_restart_ts": 6_400.0, "exhausted_alerted": True}
    d = _decide(wd, healthy=False, state=state, now=10_000.0,  # 3600s elapsed
                max_restarts=3, exhaustion_reset_s=7_200.0)
    assert d["action"] == "none" and d["alert"] is False
    assert d["new_state"]["restart_attempts"] == 3  # not yet re-armed


def test_exhausted_rearms_after_reset_window_and_restarts(wd):
    # Budget spent and >2h since the last restart → re-arm and restart again.
    state = {"last_status": "wedged", "wedged_streak": 30, "restart_attempts": 3,
             "last_restart_ts": 0.0, "exhausted_alerted": True}
    d = _decide(wd, healthy=False, state=state, now=10_000.0,  # >7200s elapsed
                max_restarts=3, cooldown_s=1200.0, exhaustion_reset_s=7_200.0)
    assert d["action"] == "restart" and d["alert"] is True
    assert d["new_state"]["restart_attempts"] == 1  # re-armed (0) then +1
    assert d["new_state"]["exhausted_alerted"] is False
    assert d["new_state"]["last_restart_ts"] == 10_000.0
