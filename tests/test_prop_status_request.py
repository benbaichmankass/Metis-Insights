"""Tests for the prop account-status request ping.

Covers the trigger condition (open prop position + absent/stale
``prop_account_status`` snapshot), the freshness gate, the cooldown, the
pause knob, state pruning when flat, and the reply-template content — all
against an isolated ``trade_journal.db`` + runtime-logs dir with the
notification emitter monkeypatched (no FCM / Telegram I/O).
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
def captured(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Capture emit_prop_status_request calls instead of sending."""
    calls: List[Dict[str, Any]] = []

    def _fake(account_id: str, open_positions: list, *, age_hours=None,
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


def test_flat_account_never_pings_and_prunes_state(isolated_env: Path,
                                                   captured) -> None:
    from src.prop.prop_status_request import run_prop_status_request, _load_state

    assert run_prop_status_request() == []
    assert captured == []
    assert _load_state() == {}


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
