"""The target-naked CRITICAL cooldown must survive a trader restart.

MEASURED DEFECT (2026-08-23). `_emit_target_naked_alert` declares "one page
per 6h per (account, symbol)" -- at most 4/day/symbol -- but latched in a
module global keyed on `time.monotonic()`. Both are per-PROCESS, and the
trader restarts on every merge to `main`, so the cooldown reset on every
restart.

Live evidence, `/api/bot/logs?level=error` (the ERROR+/CRITICAL feed):
  - 202 of 376 rows -- 53.7% of every ERROR+ row over ~6.5 days -- were
    `ib_target_naked`, for TWO paper positions in an already-filed state.
  - Per day, MES and MGC alert counts pair almost exactly (16/16, 31/31,
    11/11, 24/24), the signature of one alert per symbol per process.
  - 2026-08-23: `exit_interval_soak` reports 9 distinct trader process
    starts; `ib_target_naked` fired for MES exactly 9 times. (n=1 day --
    the soak page truncates to 1000 lines, which covered only 08-23.)

CRITICAL reaches Telegram, so this trained the operator to scroll past the
one channel reserved for "a position can only stop out or run" -- the
desensitized-alarm P1 the cooldown's own comment cites as the reason it
exists. The comment was right; the implementation could not deliver it.
"""
from __future__ import annotations

import json

import pytest

import src.runtime.order_monitor as om


@pytest.fixture()
def latched(tmp_path, monkeypatch):
    """Point the latch at a temp dir and stub the outbound page."""
    monkeypatch.setattr(om, "_target_naked_state_path",
                        lambda: tmp_path / "target_naked_alert_state.json")
    return tmp_path / "target_naked_alert_state.json"


def _emit():
    return om._emit_target_naked_alert(
        account_id="ib_paper", symbol="MGC", size=95.0, target_qty=0.0,
        stop_qty=95.0, declared_tp=4393.02, trade_id=4773,
    )


def test_cooldown_survives_a_simulated_restart(latched, monkeypatch):
    """THE REGRESSION. A fresh process must still be suppressed."""
    assert _emit() is True, "first page must go out"
    assert _emit() is False, "same process, inside 6h -> suppressed"

    # Simulate a restart: every module global is rebuilt. Under the old
    # monotonic/in-memory latch this alone re-armed the alert.
    for name in dir(om):
        if "TARGET_NAKED" in name and isinstance(getattr(om, name), dict):
            getattr(om, name).clear()

    assert _emit() is False, (
        "a restart must NOT re-arm the page -- this is the defect that put "
        "202 CRITICALs on the operator's channel in 6.5 days"
    )


def test_alerts_again_once_the_cooldown_genuinely_elapses(latched, monkeypatch):
    assert _emit() is True
    real_time = om.time.time()
    monkeypatch.setattr(om.time, "time",
                        lambda: real_time + om._TARGET_NAKED_ALERT_COOLDOWN_S + 1)
    assert _emit() is True, "past 6h the condition must page again"


def test_unreadable_latch_alerts_rather_than_suppressing(latched, monkeypatch):
    """'We could not look' must never be read as 'already paged'."""
    assert _emit() is True
    latched.write_text("{ this is not json", encoding="utf-8")
    assert _emit() is True, (
        "an unreadable latch must fail LOUD -- suppressing a CRITICAL "
        "safety page on a file-read failure is the wrong direction"
    )


def test_future_dated_entry_does_not_suppress_forever(latched):
    """Clock skew must not mute the page indefinitely."""
    latched.parent.mkdir(parents=True, exist_ok=True)
    latched.write_text(json.dumps({"ib_paper|MGC": om.time.time() + 10 * 86400}),
                       encoding="utf-8")
    assert _emit() is True


def test_latch_does_not_use_monotonic(latched):
    """monotonic is meaningless across processes -- pin the wall clock."""
    import inspect
    # Strip comments first: the fix's own explanatory comment says the words
    # "time.monotonic()", and an annotation must never count as evidence for
    # the claim it annotates (the collapsed-state-guard override discipline).
    code = "\n".join(
        ln for ln in inspect.getsource(om._emit_target_naked_alert).splitlines()
        if not ln.strip().startswith("#")
    )
    assert "time.monotonic()" not in code
    assert "time.time()" in code
