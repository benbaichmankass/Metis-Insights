"""Tests for the prop account-status request ping.

Covers the trigger condition (an absent/stale ``prop_account_status`` snapshot
on a declared prop account — **with or without an open position**), the
freshness gate, the cooldown, the pause knob, cadence-state pruning, and the
reply-template content — all against an isolated ``trade_journal.db`` +
runtime-logs dir with the notification emitter monkeypatched (no FCM /
Telegram I/O).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch):
    """Pin the declared prop-account set so tests don't ride real config.

    Returns a setter; call it with the ids a test wants declared, or with
    ``None`` to simulate an unreadable ``accounts.yaml``. Left at the default
    ``["breakout_1"]`` when a test doesn't care.
    """
    def _set(ids):
        from src.prop import prop_status_request

        monkeypatch.setattr(prop_status_request, "declared_prop_account_ids",
                            lambda *, live_only=False: ids)

    _set(["breakout_1"])
    return _set


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Capture emit_prop_status_request calls instead of sending."""
    calls: List[Dict[str, Any]] = []

    def _fake(account_id: str, open_positions, *, age_hours=None,
              push: bool = True, telegram: bool = True):
        calls.append({"account_id": account_id,
                      "open_positions": open_positions,
                      "age_hours": age_hours})
        return {"push": True, "telegram": True}

    from src.prop import breakout_notify

    monkeypatch.setattr(breakout_notify, "emit_prop_status_request", _fake)
    return calls


def _open_fill() -> Dict[str, Any]:
    return {
        "account_id": "breakout_1", "ticket_id": "prop-1",
        "symbol": "ETHUSDT", "direction": "long", "qty": 1.87,
        "entry_price": 1613.78, "status": "filled",
    }


def test_pings_when_no_snapshot_ever(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    pinged = run_prop_status_request()
    assert pinged == ["breakout_1"]
    assert captured[0]["age_hours"] is None  # never reported


def test_fresh_snapshot_suppresses(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 5040, "equity": 5010,
    })
    assert run_prop_status_request() == []
    assert captured == []


def test_cooldown_prevents_nagging(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]
    # immediate second tick: still stale, but inside the cooldown
    assert run_prop_status_request() == []
    assert len(captured) == 1


def test_reasks_after_cooldown(isolated_env: Path, captured) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    now = datetime.now(timezone.utc)
    assert run_prop_status_request(now=now) == ["breakout_1"]
    later = now + timedelta(hours=13)  # past the 12h default cooldown
    assert run_prop_status_request(now=later) == ["breakout_1"]
    assert len(captured) == 2


def test_flat_declared_account_is_still_asked(isolated_env: Path, captured,
                                              declared) -> None:
    """A FLAT prop account with no snapshot must still be asked.

    This test used to assert the exact opposite — ``run_prop_status_request()
    == []`` and the cadence state pruned to ``{}`` when nothing was open — and
    it passed against a real defect. The old implementation bailed on
    ``if not positions: return []``, so the instant the prop book went flat the
    bot stopped asking and the snapshot aged without bound.

    That is wrong because the two prop limits are not both position-scoped:
    ``config/prop_rulesets/breakout.yaml`` declares ``drawdown_type: static``
    with ``max_drawdown_pct: 0.06`` on a ``$5,000`` account — a **$4,700
    account-level floor** that binds while flat. A flat account is precisely
    when the next ticket is about to be sized against a cushion nobody has
    measured. Do not restore the old assertion.
    """
    from src.prop.prop_status_request import run_prop_status_request, _load_state

    assert run_prop_status_request() == ["breakout_1"]
    assert len(captured) == 1
    # Flat is stated, not implied: `[]` (looked, book is empty), never `None`.
    assert captured[0]["open_positions"] == []
    # And the cadence state SURVIVES, or the cooldown cannot bound the re-ask.
    assert "breakout_1" in _load_state()


def test_undeclared_flat_account_is_not_asked(isolated_env: Path, captured,
                                              declared) -> None:
    """Widening the trigger must not turn into asking about everything.

    The ask is scoped to declared prop accounts (union open positions). With
    nothing declared and nothing open there is no one to ask, and the state
    prunes — which is what keeps a retired account from nagging forever.
    """
    from src.prop.prop_status_request import run_prop_status_request, _load_state

    declared([])
    assert run_prop_status_request() == []
    assert captured == []
    assert _load_state() == {}


def test_position_holder_asked_even_when_not_declared(isolated_env: Path,
                                                      captured, declared) -> None:
    """A position we can see is a position to protect.

    An id that config no longer declares but that still holds an open prop
    position is covered by the union — otherwise removing a line from
    ``accounts.yaml`` would silently blind the guard on a live position.
    """
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    declared([])
    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]


def test_unreadable_config_still_covers_open_positions(isolated_env: Path,
                                                       captured, declared) -> None:
    """``None`` from the enumerator is "we could not look", not "none exist".

    Coverage degrades to the position-holding subset — strictly smaller, and
    logged — rather than silently becoming zero.
    """
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    declared(None)
    prop_journal.insert_fill(_open_fill())
    assert run_prop_status_request() == ["breakout_1"]


def test_failed_position_scan_is_not_reported_as_flat(
        isolated_env: Path, captured, declared,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan failure must reach the operator as ``None``, never ``[]``.

    "We could not read your positions" and "you hold nothing" are opposite
    statements, and the ping renders them differently.
    """
    from src.prop import prop_monitor_pulse
    from src.prop.prop_status_request import run_prop_status_request

    def _boom():
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(prop_monitor_pulse, "find_open_prop_positions", _boom)

    assert run_prop_status_request() == ["breakout_1"]
    assert captured[0]["open_positions"] is None


def test_real_config_declares_the_live_prop_account() -> None:
    """Positive control: the enumerator finds a real account in real config.

    Every other test here pins the declared set, so without this one a broken
    enumerator returning ``[]`` against production config would leave the whole
    file green — the unasserted-denominator shape.
    """
    from src.prop.prop_identity import declared_prop_account_ids

    ids = declared_prop_account_ids(live_only=True)
    assert ids is not None, "accounts.yaml must be readable from the repo root"
    assert "breakout_1" in ids


def test_pause_knob(isolated_env: Path, captured,
                    monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "0")
    assert run_prop_status_request() == []
    assert captured == []


def test_template_contains_both_reply_formats(isolated_env: Path) -> None:
    """The ping body must carry the exact formats the report handler parses."""
    from src.prop.breakout_notify import render_status_request_message

    text = render_status_request_message(
        "breakout_1",
        [{"symbol": "ETHUSDT", "direction": "long", "qty": 1.87,
          "entry_price": 1613.78}],
        age_hours=None,
    )
    assert "bal <balance> <equity>" in text
    assert '"kind":"account_status"' in text
    assert '"account_id":"breakout_1"' in text
    assert "ETHUSDT" in text


def test_stale_snapshot_reasks(isolated_env: Path, captured,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    from src.prop import prop_journal
    from src.prop.prop_status_request import run_prop_status_request

    prop_journal.insert_fill(_open_fill())
    prop_journal.insert_account_status({
        "account_id": "breakout_1", "balance": 5040, "equity": 5010,
    })
    # a 1h-max-age knob makes the just-written snapshot stale 2h from now
    monkeypatch.setenv("PROP_STATUS_REQUEST_MAX_AGE_HOURS", "1")
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert run_prop_status_request(now=later) == ["breakout_1"]
    assert captured[0]["age_hours"] is not None
    assert captured[0]["age_hours"] >= 1.9
