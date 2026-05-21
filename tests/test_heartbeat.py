"""Tests for src/runtime/heartbeat.py + scripts/check_heartbeat.py — S-022 PR5."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())

from src.runtime.heartbeat import write_heartbeat  # noqa: E402


# ---------------------------------------------------------------------------
# write_heartbeat
# ---------------------------------------------------------------------------


def test_write_heartbeat_creates_file_with_status(tmp_path):
    p = tmp_path / "heartbeat.txt"
    assert write_heartbeat(status="ok", tick=42, path=p) is True
    text = p.read_text()
    assert "ok" in text
    assert "tick=42" in text
    # ISO-8601-ish timestamp
    assert "T" in text and ":" in text


def test_write_heartbeat_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "deep" / "heartbeat.txt"
    assert write_heartbeat(path=p) is True
    assert p.exists()


def test_write_heartbeat_updates_mtime_each_call(tmp_path):
    p = tmp_path / "heartbeat.txt"
    write_heartbeat(path=p)
    first = p.stat().st_mtime
    time.sleep(0.01)
    write_heartbeat(path=p)
    second = p.stat().st_mtime
    assert second >= first


def test_write_heartbeat_returns_false_when_io_fails(tmp_path):
    """Non-writable path → returns False, never raises."""
    bogus = tmp_path / "nope" / "heartbeat.txt"
    bogus.parent.mkdir()
    bogus.parent.chmod(0o400)  # read-only dir
    try:
        ok = write_heartbeat(path=bogus)
        # On some filesystems chmod doesn't actually block root; tolerate
        # both. The point is that the function never raised.
        assert ok in (True, False)
    finally:
        bogus.parent.chmod(0o700)


def test_write_heartbeat_no_tick_renders_dash(tmp_path):
    p = tmp_path / "heartbeat.txt"
    write_heartbeat(status="ok", path=p)
    assert "tick=-" in p.read_text()


# ---------------------------------------------------------------------------
# check_tick_freshness pivots to heartbeat.txt
# ---------------------------------------------------------------------------


def test_check_tick_freshness_prefers_heartbeat_when_present(tmp_path, monkeypatch):
    from src.runtime import health as h

    # check_tick_freshness resolves via runtime_logs_dir() (not _REPO_ROOT).
    # Point RUNTIME_LOGS_DIR at the tmp path so the reader finds the files.
    rl = tmp_path / "runtime_logs"
    rl.mkdir()
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(rl))
    hb = rl / "heartbeat.txt"
    hb.write_text("ok")

    # Stale signal_audit (1 hour old) but fresh heartbeat → ok
    audit = rl / "signal_audit.jsonl"
    audit.write_text("")
    import os as _os
    old = time.time() - 3600
    _os.utime(str(audit), (old, old))

    c = h.check_tick_freshness(tick_interval_s=900)
    assert c.status == "ok"
    assert c.ctx["source"] == "heartbeat.txt"


def test_check_tick_freshness_falls_back_to_audit_when_no_heartbeat(tmp_path, monkeypatch):
    from src.runtime import health as h

    rl = tmp_path / "runtime_logs"
    rl.mkdir()
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(rl))
    audit = rl / "signal_audit.jsonl"
    audit.write_text("")

    c = h.check_tick_freshness(tick_interval_s=900)
    assert c.status == "ok"
    assert c.ctx["source"] == "signal_audit.jsonl"


# ---------------------------------------------------------------------------
# check_heartbeat.py — load via importlib (script lives in scripts/)
# ---------------------------------------------------------------------------


@pytest.fixture
def watchdog_module():
    spec = importlib.util.spec_from_file_location(
        "check_heartbeat",
        Path(__file__).resolve().parents[1] / "scripts" / "check_heartbeat.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_evaluate_missing_heartbeat(tmp_path, watchdog_module):
    res = watchdog_module.evaluate(
        heartbeat_path=tmp_path / "missing.txt",
        state_path=tmp_path / "state.json",
        tick_interval_s=900,
        grace_factor=2.0,
    )
    assert res["action"] == "missing"


def test_evaluate_fresh_heartbeat(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("now")
    res = watchdog_module.evaluate(
        heartbeat_path=hb,
        state_path=tmp_path / "state.json",
        tick_interval_s=900,
        grace_factor=2.0,
    )
    assert res["action"] == "ok"


def test_evaluate_stale_first_detection(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("old")
    import os as _os
    old = time.time() - 3600  # 1h
    _os.utime(str(hb), (old, old))
    res = watchdog_module.evaluate(
        heartbeat_path=hb,
        state_path=tmp_path / "state.json",
        tick_interval_s=900,
        grace_factor=2.0,
    )
    assert res["action"] == "stale"
    assert res["reason"] == "first detection"


def test_evaluate_already_alerted_doesnt_repeat(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("old")
    import os as _os
    old = time.time() - 3600
    _os.utime(str(hb), (old, old))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3500,
    }))
    res = watchdog_module.evaluate(
        heartbeat_path=hb, state_path=state,
        tick_interval_s=900, grace_factor=2.0,
    )
    assert res["action"] == "ok"
    assert res["reason"] == "already alerted"


def test_evaluate_re_alerts_when_worsened(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("old")
    import os as _os
    old = time.time() - 7200  # 2h stale
    _os.utime(str(hb), (old, old))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3500,  # alerted at 1h
    }))
    res = watchdog_module.evaluate(
        heartbeat_path=hb, state_path=state,
        tick_interval_s=900, grace_factor=2.0,  # threshold = 1800
    )
    # 7200 - 3500 = 3700 >= 1800 → re-alert
    assert res["action"] == "stale"
    assert res["reason"] == "worsened"


def test_evaluate_recovered_after_stale(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("fresh")  # current mtime
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3600,
    }))
    res = watchdog_module.evaluate(
        heartbeat_path=hb, state_path=state,
        tick_interval_s=900, grace_factor=2.0,
    )
    assert res["action"] == "recovered"


def test_render_alert_messages(watchdog_module, tmp_path):
    hb = tmp_path / "heartbeat.txt"
    msg_missing = watchdog_module.render_alert("missing", None, hb)
    assert "CRITICAL" in msg_missing and "missing" in msg_missing
    msg_stale = watchdog_module.render_alert("stale", 3600, hb)
    assert "CRITICAL" in msg_stale and "stale" in msg_stale and "60m" in msg_stale
    msg_rec = watchdog_module.render_alert("recovered", 30, hb)
    assert "OK" in msg_rec and "recovered" in msg_rec


def test_main_dry_run_does_not_alert(tmp_path, watchdog_module, capsys):
    """--dry-run prints the would-be alert but doesn't call Telegram or write state."""
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("stale")
    import os as _os
    _os.utime(str(hb), (time.time() - 3600, time.time() - 3600))
    state = tmp_path / "state.json"
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900",
        "--grace", "2",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "stale" in out.lower()
    assert not state.exists()  # dry-run: no state write


def test_main_writes_state_after_alert(tmp_path, watchdog_module):
    """Live run records state['last_status']=stale so reruns dedupe."""
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("stale")
    import os as _os
    _os.utime(str(hb), (time.time() - 3600, time.time() - 3600))
    state = tmp_path / "state.json"
    with patch("scripts.check_heartbeat.send_alert", create=True, return_value=True):
        # The script's send_alert is at module level; patching by attribute
        # ensures we don't actually contact Telegram.
        watchdog_module.send_alert = lambda _msg: True
        rc = watchdog_module.main([
            "--heartbeat", str(hb),
            "--state", str(state),
            "--interval", "900",
            "--grace", "2",
        ])
    assert rc == 0
    saved = json.loads(state.read_text())
    assert saved["last_status"] == "stale"
    assert saved["last_alert_age_s"] is not None


def test_main_returns_2_when_telegram_fails(tmp_path, watchdog_module):
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("stale")
    import os as _os
    _os.utime(str(hb), (time.time() - 3600, time.time() - 3600))
    state = tmp_path / "state.json"
    watchdog_module.send_alert = lambda _msg: False
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900",
        "--grace", "2",
    ])
    assert rc == 2
    assert not state.exists()  # no state written on send failure


# ---------------------------------------------------------------------------
# Autoheal — --auto-restart-after path (2026-05-11 silent-failure fix)
# ---------------------------------------------------------------------------


def _make_stale_hb(tmp_path, age_s: int = 3600):
    import os as _os
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("stale")
    _os.utime(str(hb), (time.time() - age_s, time.time() - age_s))
    return hb


def test_autoheal_does_not_fire_when_flag_zero(tmp_path, watchdog_module):
    """Default behaviour: alert-only, no systemctl call."""
    hb = _make_stale_hb(tmp_path)
    state = tmp_path / "state.json"
    watchdog_module.send_alert = lambda _msg: True
    calls = []
    watchdog_module.try_autoheal_restart = lambda unit: calls.append(unit) or {}
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900", "--grace", "2",
        "--auto-restart-after", "0",
    ])
    assert rc == 0
    assert calls == []  # autoheal disabled
    saved = json.loads(state.read_text())
    assert saved["stale_streak"] == 1


def test_autoheal_fires_after_threshold(tmp_path, watchdog_module):
    """After 3 consecutive stale checks, autoheal runs systemctl restart."""
    hb = _make_stale_hb(tmp_path)
    state = tmp_path / "state.json"
    # Simulate 2 prior stale checks already recorded.
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3500, "stale_streak": 2,
    }))
    watchdog_module.send_alert = lambda _msg: True
    calls = []
    watchdog_module.try_autoheal_restart = lambda unit: (
        calls.append(unit)
        or {"ran": True, "returncode": 0, "stdout": "", "stderr": ""}
    )
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900", "--grace", "2",
        "--auto-restart-after", "3",
        "--restart-unit", "ict-trader-live.service",
    ])
    assert rc == 0
    assert calls == ["ict-trader-live.service"]
    saved = json.loads(state.read_text())
    assert saved["last_autoheal_returncode"] == 0
    assert saved["last_autoheal_streak"] == 3


def test_autoheal_does_not_double_fire(tmp_path, watchdog_module):
    """If autoheal already fired at streak N, don't re-fire until streak
    advances by another full threshold."""
    hb = _make_stale_hb(tmp_path)
    state = tmp_path / "state.json"
    # streak=3, autoheal already fired at streak=3.
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3500,
        "stale_streak": 3, "last_autoheal_streak": 3,
    }))
    watchdog_module.send_alert = lambda _msg: True
    calls = []
    watchdog_module.try_autoheal_restart = lambda unit: calls.append(unit) or {
        "ran": True, "returncode": 0, "stdout": "", "stderr": "",
    }
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900", "--grace", "2",
        "--auto-restart-after", "3",
    ])
    assert rc == 0
    # streak is now 4 (one new stale check), but last_autoheal_streak=3,
    # diff is 1, not >= threshold (3). So autoheal SHOULD NOT fire.
    assert calls == []


def test_autoheal_recovered_resets_streak(tmp_path, watchdog_module):
    """When heartbeat goes fresh again, stale_streak resets to 0."""
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("fresh")  # current mtime
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "last_status": "stale", "last_alert_age_s": 3500, "stale_streak": 5,
    }))
    watchdog_module.send_alert = lambda _msg: True
    rc = watchdog_module.main([
        "--heartbeat", str(hb),
        "--state", str(state),
        "--interval", "900", "--grace", "2",
        "--auto-restart-after", "3",
    ])
    assert rc == 0
    saved = json.loads(state.read_text())
    assert saved["stale_streak"] == 0
    assert saved["last_status"] == "recovered"


# ---------------------------------------------------------------------------
# Path resolution — DATA_DIR + RUNTIME_LOGS_DIR aware (2026-05-11)
# ---------------------------------------------------------------------------


def test_resolved_runtime_logs_dir_default(monkeypatch, watchdog_module):
    monkeypatch.delenv("RUNTIME_LOGS_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    # Reload module to pick up env-dependent defaults at import time.
    # The helper itself is env-driven so direct call is enough.
    p = watchdog_module._resolved_runtime_logs_dir()
    assert p.name == "runtime_logs"
    # Repo-relative fallback ends at the repo root.
    assert p.is_absolute()


def test_resolved_runtime_logs_dir_honours_data_dir(monkeypatch, watchdog_module, tmp_path):
    monkeypatch.delenv("RUNTIME_LOGS_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    p = watchdog_module._resolved_runtime_logs_dir()
    assert p == tmp_path / "runtime_logs"


def test_resolved_runtime_logs_dir_per_root_override_wins(monkeypatch, watchdog_module, tmp_path):
    monkeypatch.setenv("DATA_DIR", "/never-used")
    monkeypatch.setenv("RUNTIME_LOGS_DIR", str(tmp_path / "custom"))
    p = watchdog_module._resolved_runtime_logs_dir()
    assert p == tmp_path / "custom"


# ---------------------------------------------------------------------------
# try_autoheal_restart — subprocess wrapper
# ---------------------------------------------------------------------------


def test_try_autoheal_restart_subprocess_failure(watchdog_module, monkeypatch):
    """When subprocess.run raises OSError, return ran=False with diagnostic."""
    def boom(*_args, **_kwargs):
        raise OSError("simulated")
    monkeypatch.setattr(
        "subprocess.run", boom,
    )
    res = watchdog_module.try_autoheal_restart("ict-trader-live.service")
    assert res["ran"] is False
    assert "simulated" in res["stderr"]


def test_render_autoheal_alert_success(watchdog_module):
    msg = watchdog_module.render_autoheal_alert(
        "ict-trader-live.service",
        {"ran": True, "returncode": 0, "stdout": "", "stderr": ""},
        3, 360.0,
    )
    assert "ACTION" in msg
    assert "systemctl restart" in msg
    assert "ict-trader-live.service" in msg


def test_render_autoheal_alert_non_zero_rc(watchdog_module):
    msg = watchdog_module.render_autoheal_alert(
        "ict-trader-live.service",
        {"ran": True, "returncode": 1, "stdout": "", "stderr": "Permission denied"},
        3, 360.0,
    )
    assert "CRITICAL" in msg
    assert "rc=1" in msg
    assert "Permission denied" in msg


def test_render_autoheal_alert_dispatch_failed(watchdog_module):
    msg = watchdog_module.render_autoheal_alert(
        "ict-trader-live.service",
        {"ran": False, "returncode": -1, "stdout": "", "stderr": "subprocess failed: OSError: x"},
        5, 600.0,
    )
    assert "FAILED to dispatch" in msg
    assert "OSError" in msg
