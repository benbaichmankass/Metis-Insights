"""Tests for scripts/check_web_api.py — the ict-web-api self-heal watchdog
(BL-20260604-003). Covers the HTTP probe classification and the restart
decision state machine (detect → restart after streak → cap → cooldown).
"""
from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_web_api",
    Path(__file__).resolve().parent.parent / "scripts" / "check_web_api.py",
)
cwa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cwa)


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, code):
        self._code = code

    def getcode(self):
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_probe_healthy_on_200(monkeypatch):
    monkeypatch.setattr(cwa.urllib.request, "urlopen", lambda *a, **k: _Resp(200))
    v = cwa.run_probe("http://127.0.0.1:8001/api/health", 5)
    assert v["healthy"] is True


def test_probe_unhealthy_on_503(monkeypatch):
    monkeypatch.setattr(cwa.urllib.request, "urlopen", lambda *a, **k: _Resp(503))
    v = cwa.run_probe("http://127.0.0.1:8001/api/health", 5)
    assert v["healthy"] is False
    assert "503" in v["reason"]


def test_probe_unhealthy_on_connection_refused(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(cwa.urllib.request, "urlopen", _boom)
    v = cwa.run_probe("http://127.0.0.1:8001/api/health", 5)
    assert v["healthy"] is False
    assert "no response" in v["reason"]


# ---------------------------------------------------------------------------
# Decision state machine
# ---------------------------------------------------------------------------
def _decide(healthy, state, *, now=1000.0, auto_restart=True,
            restart_after=2, max_restarts=3, cooldown_s=600.0):
    return cwa.decide(healthy=healthy, state=state, restart_after=restart_after,
                      max_restarts=max_restarts, cooldown_s=cooldown_s, now=now,
                      auto_restart=auto_restart)


def test_healthy_fresh_is_noop():
    d = _decide(True, {})
    assert d["action"] == "none" and d["alert"] is False


def test_healthy_after_wedge_recovers():
    d = _decide(True, {"last_status": "wedged", "wedged_streak": 3})
    assert d["action"] == "recovered" and d["alert"] is True
    assert d["new_state"]["wedged_streak"] == 0


def test_first_wedge_detects_then_restarts_on_streak():
    d1 = _decide(False, {})
    assert d1["action"] == "detected" and d1["alert"] is True
    # second consecutive failure reaches restart_after=2 → restart
    d2 = _decide(False, d1["new_state"])
    assert d2["action"] == "restart" and d2["alert"] is True
    assert d2["new_state"]["restart_attempts"] == 1


def test_alert_only_mode_never_restarts():
    d1 = _decide(False, {}, auto_restart=False)
    d2 = _decide(False, d1["new_state"], auto_restart=False)
    d3 = _decide(False, d2["new_state"], auto_restart=False)
    assert all(d["action"] != "restart" for d in (d1, d2, d3))


def test_cooldown_blocks_restart():
    # one restart already happened 100 s ago; cooldown is 600 s → no restart yet
    state = {"last_status": "wedged", "wedged_streak": 5, "restart_attempts": 1,
             "last_restart_ts": 1000.0}
    d = _decide(False, state, now=1100.0)
    assert d["action"] in ("none", "detected")
    assert d["new_state"]["restart_attempts"] == 1  # unchanged


def test_max_restarts_exhausts():
    state = {"last_status": "wedged", "wedged_streak": 9, "restart_attempts": 3,
             "last_restart_ts": 0.0}
    d = _decide(False, state, now=10_000.0)  # past cooldown, but attempts == max
    assert d["action"] == "exhausted" and d["alert"] is True
    # second exhausted check is silent (no repeat alert spam)
    d2 = _decide(False, d["new_state"], now=10_001.0)
    assert d2["action"] == "none" and d2["alert"] is False
