"""Broker-account-down latched alert (BL-20260629-ACCOUNT-DOWN-ALERT).

A supposed-to-be-live broker account reading unreachable should fire its
own loud, latched Telegram/WARNING alert — one on a confirmed cross-into-
down (>= N consecutive checks), one on recovery — instead of going
unflagged. These cover the account filter, the consecutive-down latch, the
recovery ping, the cadence gate, and the fail-safe prober-exception path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import account_reachability_alert as ara


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Point the state file at a tmp dir and capture alerts instead of sending."""
    monkeypatch.setattr(ara, "runtime_logs_dir", lambda: tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(ara, "_send_alert", lambda msg: sent.append(msg))
    return sent


# config: bybit_2 (live real), ib_paper (live), alpaca_paper (live),
# ib_live (dry), oanda_practice (dry), breakout_1 (prop).
_CFGS = {
    "bybit_2": {"account_id": "bybit_2", "exchange": "bybit", "mode": "live"},
    "ib_paper": {"account_id": "ib_paper", "exchange": "interactive_brokers", "mode": "live"},
    "alpaca_paper": {"account_id": "alpaca_paper", "exchange": "alpaca", "mode": "live"},
    "ib_live": {"account_id": "ib_live", "exchange": "interactive_brokers", "mode": "dry_run"},
    "oanda_practice": {"account_id": "oanda_practice", "exchange": "oanda", "mode": "dry_run"},
    "breakout_1": {"account_id": "breakout_1", "exchange": "breakout", "mode": "live"},
}


def test_checkable_accounts_filters_dry_prop_and_skip(monkeypatch):
    ids = {aid for aid, _ in ara._checkable_accounts(_CFGS)}
    assert ids == {"bybit_2", "ib_paper", "alpaca_paper"}  # dry + breakout excluded
    # explicit skip env
    monkeypatch.setenv("ACCOUNT_DOWN_ALERT_SKIP", "ib_paper, alpaca_paper")
    ids = {aid for aid, _ in ara._checkable_accounts(_CFGS)}
    assert ids == {"bybit_2"}


def test_down_requires_threshold_then_alerts(_isolate_state):
    sent = _isolate_state
    # ib_paper unreachable (None), the others reachable ([])
    def prober(cfg):
        return None if cfg["account_id"] == "ib_paper" else []

    t0 = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    # check 1: ib_paper down once -> below threshold (2) -> no alert
    s1 = ara.run_account_reachability_check(prober=prober, cfgs=_CFGS, now=t0, force=True)
    assert s1["checked"] == 3 and s1["newly_down"] == 0 and sent == []
    assert ara.down_accounts() == {}  # not latched yet

    # check 2 (forced): second consecutive down -> crosses, fires ONE alert
    s2 = ara.run_account_reachability_check(
        prober=prober, cfgs=_CFGS, now=t0 + timedelta(minutes=10), force=True
    )
    assert s2["newly_down"] == 1 and s2["alerted"] == 1
    assert len(sent) == 1 and "DOWN" in sent[0] and "ib_paper" in sent[0]
    assert set(ara.down_accounts()) == {"ib_paper"}

    # check 3: still down -> latched, NO repeat alert
    s3 = ara.run_account_reachability_check(
        prober=prober, cfgs=_CFGS, now=t0 + timedelta(minutes=20), force=True
    )
    assert s3["newly_down"] == 0 and len(sent) == 1


def test_recovery_fires_ok_ping(_isolate_state):
    sent = _isolate_state
    state = {"down": True, "consecutive_down": 3, "last_change": "x"}
    ara._save_state({"ib_paper": state, ara._LAST_CHECK_KEY: "2000-01-01T00:00:00+00:00"})

    s = ara.run_account_reachability_check(
        prober=lambda cfg: [], cfgs={"ib_paper": _CFGS["ib_paper"]},
        now=datetime(2026, 6, 29, 13, 0, tzinfo=timezone.utc), force=True,
    )
    assert s["recovered"] == 1 and len(sent) == 1
    assert "recovered" in sent[0].lower() and "ib_paper" in sent[0]
    assert ara.down_accounts() == {}


def test_no_spurious_recovery_on_first_reachable(_isolate_state):
    sent = _isolate_state
    s = ara.run_account_reachability_check(
        prober=lambda cfg: [], cfgs=_CFGS,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc), force=True,
    )
    assert s["recovered"] == 0 and sent == []


def test_cadence_gate_skips_within_interval(_isolate_state, monkeypatch):
    monkeypatch.setenv("ACCOUNT_REACHABILITY_CHECK_SECONDS", "600")
    t0 = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    ara.run_account_reachability_check(prober=lambda cfg: [], cfgs=_CFGS, now=t0)
    # 5 min later (< 600s) -> skipped
    s = ara.run_account_reachability_check(
        prober=lambda cfg: [], cfgs=_CFGS, now=t0 + timedelta(minutes=5)
    )
    assert s == {"skipped": "cadence"}
    # 11 min later -> runs
    s2 = ara.run_account_reachability_check(
        prober=lambda cfg: [], cfgs=_CFGS, now=t0 + timedelta(minutes=11)
    )
    assert "checked" in s2


def test_disabled_when_interval_non_positive(_isolate_state, monkeypatch):
    monkeypatch.setenv("ACCOUNT_REACHABILITY_CHECK_SECONDS", "0")
    s = ara.run_account_reachability_check(prober=lambda cfg: [], cfgs=_CFGS)
    assert s == {"skipped": "disabled"}


def test_prober_exception_counts_as_down(_isolate_state):
    sent = _isolate_state

    def boom(cfg):
        raise RuntimeError("network")

    t0 = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    ara.run_account_reachability_check(
        prober=boom, cfgs={"bybit_2": _CFGS["bybit_2"]}, now=t0, force=True
    )
    s = ara.run_account_reachability_check(
        prober=boom, cfgs={"bybit_2": _CFGS["bybit_2"]},
        now=t0 + timedelta(minutes=10), force=True,
    )
    assert s["newly_down"] == 1 and len(sent) == 1 and "bybit_2" in sent[0]


def test_down_alert_message_has_remediation(_isolate_state):
    sent = _isolate_state
    t0 = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)
    for i in range(2):
        ara.run_account_reachability_check(
            prober=lambda cfg: None, cfgs={"ib_paper": _CFGS["ib_paper"]},
            now=t0 + timedelta(minutes=10 * i), force=True,
        )
    assert "vm-ib-gateway-recover" in sent[0]
    assert "/health-review" in sent[0]
