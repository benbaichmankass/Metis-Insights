"""
Tests for scripts/daily_heartbeat.py

Network-free; all Telegram calls and DB queries are mocked.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Import the module under test without going through the package system
# ---------------------------------------------------------------------------

def _import_heartbeat():
    spec = importlib.util.spec_from_file_location(
        "daily_heartbeat",
        Path(__file__).resolve().parents[1] / "scripts" / "daily_heartbeat.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hb = _import_heartbeat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> str:
    db = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades "
        "(id INTEGER PRIMARY KEY, pnl REAL, is_backtest INTEGER, status TEXT, timestamp TEXT)"
    )
    conn.execute(
        "CREATE TABLE signals "
        "(id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, signal_type TEXT, "
        "direction TEXT, price REAL, timeframe TEXT, reason TEXT, metadata TEXT)"
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Test 1: halted state message
# ---------------------------------------------------------------------------

def test_message_halted_state(tmp_path):
    halt = tmp_path / "trader_halt.flag"
    halt.touch()

    with patch.object(hb, "HALT_FLAG", str(halt)):
        msg = hb.build_message(
            kill_switch=hb._kill_switch_state(),
            open_positions="0",
            today_pnl="$+0.00",
            news_status="disabled",
            last_tick="no data",
        )

    assert "HALTED" in msg
    assert "Kill-switch" in msg


# ---------------------------------------------------------------------------
# Test 2: running state message
# ---------------------------------------------------------------------------

def test_message_running_state(tmp_path):
    halt = tmp_path / "trader_halt.flag"
    # deliberately not touching it

    with patch.object(hb, "HALT_FLAG", str(halt)):
        msg = hb.build_message(
            kill_switch=hb._kill_switch_state(),
            open_positions="2",
            today_pnl="$+15.50",
            news_status="enabled-active",
            last_tick="2026-04-29T12:00:00  (01:00 ago)",
        )

    assert "RUNNING" in msg
    assert "2" in msg
    assert "$+15.50" in msg
    assert "enabled-active" in msg


# ---------------------------------------------------------------------------
# Test 3: news disabled status
# ---------------------------------------------------------------------------

def test_news_status_disabled():
    with patch.dict(os.environ, {"NEWS_ENABLED": "false"}, clear=False):
        assert hb._news_status() == "disabled"


def test_news_status_enabled_no_key():
    with patch.dict(os.environ, {"NEWS_ENABLED": "true", "NEWS_API_KEY": ""}, clear=False):
        assert hb._news_status() == "enabled-no-key"


def test_news_status_enabled_active():
    with patch.dict(os.environ, {"NEWS_ENABLED": "true", "NEWS_API_KEY": "key123"}, clear=False):
        assert hb._news_status() == "enabled-active"


# ---------------------------------------------------------------------------
# Test 4: graceful handling of missing DB
# ---------------------------------------------------------------------------

def test_missing_db_returns_unavailable(tmp_path):
    # SQLite creates the file on connect so a "missing" DB has no tables.
    # _open_positions and _today_pnl hit an OperationalError → "DB unavailable".
    # _last_tick catches OperationalError per-table and returns "no data" when
    # both tables are absent (correct graceful behaviour for an empty DB).
    missing = str(tmp_path / "nonexistent.db")
    assert hb._open_positions(missing) == "DB unavailable"
    assert hb._today_pnl(missing) == "DB unavailable"
    assert hb._last_tick(missing) in ("DB unavailable", "no data")


# ---------------------------------------------------------------------------
# Test 5: PnL and positions computed correctly from DB
# ---------------------------------------------------------------------------

def test_db_queries_with_data(tmp_path):
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO trades (pnl, is_backtest, status, timestamp) VALUES (?, ?, ?, ?)",
        [
            (10.0, 0, "closed", f"{today}T10:00:00"),     # today closed +10
            (-5.0, 0, "closed", f"{today}T11:00:00"),     # today closed -5  → net +5
            (20.0, 0, "open",   f"{today}T11:30:00"),     # open position (1)
            (50.0, 0, "closed", f"{yesterday}T10:00:00"), # yesterday — excluded from PnL
            (30.0, 1, "closed", f"{today}T09:00:00"),     # backtest — excluded
        ],
    )
    conn.commit()
    conn.close()

    assert hb._open_positions(db) == "1"
    pnl = hb._today_pnl(db)
    assert pnl == "$+5.00"


# ---------------------------------------------------------------------------
# Test 6: main() calls _send_telegram and returns 0 on success
# ---------------------------------------------------------------------------

def test_main_sends_telegram_and_returns_0(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    sent = {}

    def _fake_send(token, chat_id, text):
        sent["token"] = token
        sent["chat_id"] = chat_id
        sent["text"] = text

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "365546917")
    monkeypatch.setenv("TRADE_JOURNAL_DB", db)
    monkeypatch.setenv("NEWS_ENABLED", "false")

    with (
        patch.object(hb, "_load_env"),
        patch.object(hb, "_send_telegram", side_effect=_fake_send),
        patch.object(hb, "HALT_FLAG", str(tmp_path / "no_flag")),
    ):
        rc = hb.main()

    assert rc == 0
    assert "fake_token" == sent.get("token")
    assert "Daily heartbeat" in sent.get("text", "")
    assert "RUNNING" in sent.get("text", "")
    assert "disabled" in sent.get("text", "")


# ---------------------------------------------------------------------------
# Test 7: main() returns 1 when TELEGRAM_BOT_TOKEN is absent
# ---------------------------------------------------------------------------

def test_main_returns_1_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with patch.object(hb, "_load_env"):
        rc = hb.main()

    assert rc == 1
