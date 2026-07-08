"""Trainer-VM-down latched alert (operator-requested 2026-07-08).

The trainer VM going SSH-dead / OOM-hung must fire its own loud, latched
Telegram + WARNING alert (and surface on /api/bot/notifications for the app
banners) — one on the mirror crossing into stale, one on recovery — instead of
going unflagged until a review days later. These cover the fresh/stale/missing
mirror states, the latch (no repeat alert), the recovery ping, the cadence
gate, the skip env, and the fail-safe status() read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import trainer_reachability_alert as tra


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Point the state file at a tmp dir and capture alerts instead of sending."""
    monkeypatch.setattr(tra, "runtime_logs_dir", lambda: tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(tra, "_send_alert", lambda msg: sent.append(msg))
    return sent


def test_fresh_mirror_is_not_down(_isolate_state):
    sent = _isolate_state
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    s = tra.run_trainer_reachability_check(age_seconds=120.0, now=t0, force=True)
    assert s["down"] is False and s["alerted"] == 0
    assert sent == []
    assert tra.is_down() is False


def test_stale_mirror_alerts_once_then_latches_then_recovers(_isolate_state):
    sent = _isolate_state
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)

    # mirror 30 min stale (> 20 min default threshold) -> DOWN, one alert
    s1 = tra.run_trainer_reachability_check(age_seconds=1800.0, now=t0, force=True)
    assert s1["down"] is True and s1["newly_down"] == 1 and s1["alerted"] == 1
    assert len(sent) == 1 and "DOWN" in sent[0] and "Trainer" in sent[0]
    assert tra.is_down() is True

    # still stale -> latched, NO repeat alert
    s2 = tra.run_trainer_reachability_check(
        age_seconds=3600.0, now=t0 + timedelta(minutes=10), force=True
    )
    assert s2["down"] is True and s2["newly_down"] == 0 and s2["alerted"] == 0
    assert len(sent) == 1

    # fresh again -> recovery, one OK alert
    s3 = tra.run_trainer_reachability_check(
        age_seconds=90.0, now=t0 + timedelta(minutes=20), force=True
    )
    assert s3["down"] is False and s3["recovered"] == 1 and s3["alerted"] == 1
    assert len(sent) == 2 and "OK" in sent[1] and "recovered" in sent[1]
    assert tra.is_down() is False


def test_missing_mirror_counts_as_down(_isolate_state):
    sent = _isolate_state
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    # age None == no trainer_status.json in the mirror at all -> DOWN
    s = tra.run_trainer_reachability_check(age_seconds=None, now=t0, force=True)
    assert s["down"] is True and s["alerted"] == 1
    assert "never published" in sent[0] or "mirror missing" in sent[0]


def test_cadence_gate_skips_between_checks(_isolate_state):
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    # first non-forced check runs (default 300s cadence, no prior stamp)
    s1 = tra.run_trainer_reachability_check(age_seconds=90.0, now=t0)
    assert "skipped" not in s1
    # 60s later -> within cadence -> skipped
    s2 = tra.run_trainer_reachability_check(
        age_seconds=90.0, now=t0 + timedelta(seconds=60)
    )
    assert s2 == {"skipped": "cadence"}


def test_skip_env_disables_alert(_isolate_state, monkeypatch):
    monkeypatch.setenv("TRAINER_DOWN_ALERT_SKIP", "true")
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    s = tra.run_trainer_reachability_check(age_seconds=9999.0, now=t0, force=True)
    assert s == {"skipped": "TRAINER_DOWN_ALERT_SKIP"}


def test_disabled_when_interval_non_positive(_isolate_state, monkeypatch):
    monkeypatch.setenv("TRAINER_HEARTBEAT_CHECK_SECONDS", "0")
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    s = tra.run_trainer_reachability_check(age_seconds=9999.0, now=t0)
    assert s == {"skipped": "disabled"}


def test_status_is_read_only_and_never_raises(_isolate_state):
    t0 = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    tra.run_trainer_reachability_check(age_seconds=1800.0, now=t0, force=True)
    st = tra.status()
    assert st["down"] is True
    assert "stale_threshold_seconds" in st
    assert st["since"] is not None
