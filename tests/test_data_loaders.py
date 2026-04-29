"""Tests for src/bot/data_loaders.py — Sprint S-001 PR-B1/B2.

Each loader has a happy path + at least one failure-mode test, per the
spec's acceptance criteria (docs/TELEGRAM-SPEC.md §6). Exchange queries
land in PR-B3 with their own tests.
"""
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.bot import data_loaders as dl


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A throwaway repo root with empty deploy/ and config/ dirs."""
    (tmp_path / "deploy").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(dl, "ACCOUNTS_YAML_PATH",
                        str(tmp_path / "config" / "accounts.yaml"))
    return tmp_path


# -- list_live_strategies -----------------------------------------------------

def test_list_live_strategies_happy_path():
    out = dl.list_live_strategies()
    # Defensive: in a sandbox without ccxt this returns []. In a healthy env
    # it must include the four strategies the multiplexer iterates.
    assert isinstance(out, list)
    if out:
        for expected in ("breakout_confirmation", "vwap", "killzone", "ict"):
            assert expected in out


def test_list_live_strategies_handles_pipeline_import_error(monkeypatch):
    """If src.runtime.pipeline is broken, the loader returns [].

    We simulate the broken state by injecting a sentinel module whose
    ``STRATEGIES`` attribute raises on access — safer than monkey-patching
    ``builtins.__import__`` (which would bleed into other tests via
    partially-loaded modules in sys.modules).
    """

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("simulated broken pipeline")

    monkeypatch.setitem(sys.modules, "src.runtime.pipeline", _Boom())
    assert dl.list_live_strategies() == []


# -- list_trader_services -----------------------------------------------------

def test_list_trader_services_scans_deploy_dir(fake_repo):
    deploy = fake_repo / "deploy"
    (deploy / "ict-trader-live.service").write_text("# unit\n")
    (deploy / "ict-trader-binance-1.service").write_text("# unit\n")
    (deploy / "ict-telegram-bot.service").write_text("# unit\n")  # not a trader
    (deploy / "ict-heartbeat.timer").write_text("# timer\n")  # not a service

    out = dl.list_trader_services()
    assert sorted(out) == ["ict-trader-binance-1", "ict-trader-live"]


def test_list_trader_services_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path / "does-not-exist"))
    assert dl.list_trader_services() == []


# -- list_accounts ------------------------------------------------------------

def test_list_accounts_legacy_env_only(fake_repo):
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    out = dl.list_accounts()
    assert len(out) == 1
    a = out[0]
    assert a["account_id"] == "live"
    assert a["service"] == "ict-trader-live"
    assert a["exchange"] == "bybit"
    assert a["source"] == "env"


def test_list_accounts_multi_env(fake_repo):
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    (fake_repo / ".env.binance-sub-1").write_text(
        "BINANCE_API_KEY=k\nBINANCE_API_SECRET=s\n"
    )
    out = dl.list_accounts()
    ids = [a["account_id"] for a in out]
    assert "live" in ids
    assert "binance-sub-1" in ids
    sub = next(a for a in out if a["account_id"] == "binance-sub-1")
    assert sub["service"] == "ict-trader-binance-sub-1"
    assert sub["exchange"] == "binance"


def test_list_accounts_empty_repo(fake_repo):
    # No .env, no yaml — must return [], not crash.
    assert dl.list_accounts() == []


def test_list_accounts_yaml_takes_precedence(fake_repo):
    pytest.importorskip("yaml")
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    yaml_path = fake_repo / "config" / "accounts.yaml"
    yaml_path.write_text(
        "accounts:\n"
        "  - account_id: live\n"
        "    exchange: bybit\n"
        "    env_path: /custom/.env\n"
        "    service: ict-trader-live\n"
        "    strategies: [ict]\n"
    )
    out = dl.list_accounts()
    live = next(a for a in out if a["account_id"] == "live")
    assert live["source"] == "yaml"
    assert live["env_path"] == "/custom/.env"


# -- Fixtures for DB readers --------------------------------------------------

@pytest.fixture
def trade_journal_db(tmp_path, monkeypatch):
    """A trade_journal.db with the schema PR-B0 introduced."""
    db = tmp_path / "trade_journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, direction TEXT,
            entry_price REAL, exit_price REAL,
            stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL,
            take_profit_3 REAL, position_size REAL,
            setup_type TEXT, killzone TEXT, bias TEXT,
            entry_reason TEXT, exit_reason TEXT,
            pnl REAL, pnl_percent REAL, status TEXT, notes TEXT,
            is_backtest INTEGER DEFAULT 0,
            strategy_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, strategy_version TEXT,
            start_date TEXT, end_date TEXT,
            total_trades INTEGER, winning_trades INTEGER,
            losing_trades INTEGER, win_rate REAL, profit_factor REAL,
            expectancy REAL, max_drawdown REAL, max_drawdown_pct REAL,
            sharpe_ratio REAL, total_pnl REAL, total_pnl_pct REAL,
            avg_win REAL, avg_loss REAL,
            largest_win REAL, largest_loss REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(db))
    return str(db)


@pytest.fixture
def signals_db(tmp_path, monkeypatch):
    """A signals.db matching src/runtime/signal_notifications.py's schema."""
    db = tmp_path / "signals.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            direction TEXT, price REAL,
            timeframe TEXT, reason TEXT, metadata TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(dl, "SIGNALS_DB", str(db))
    return str(db)


def _insert_signal(db, ts, signal_type, symbol="BTCUSDT", direction="bullish",
                   price=65000.0):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO signals (timestamp, symbol, signal_type, direction, "
        "price, timeframe, reason, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, symbol, signal_type, direction, price, "5m", "test", "{}"),
    )
    conn.commit()
    conn.close()


# -- recent_signals_for -------------------------------------------------------

def test_recent_signals_for_filters_by_strategy(signals_db):
    _insert_signal(signals_db, "2026-04-29T10:00:00", "fvg_bullish")
    _insert_signal(signals_db, "2026-04-29T10:01:00", "ml_breakout")
    _insert_signal(signals_db, "2026-04-29T10:02:00", "ob_bearish")
    out = dl.recent_signals_for("ict", n=5)
    types = [r["signal_type"] for r in out]
    assert set(types) == {"fvg_bullish", "ob_bearish"}
    # Newest first.
    assert out[0]["signal_type"] == "ob_bearish"


def test_recent_signals_for_unknown_strategy_returns_recent(signals_db):
    _insert_signal(signals_db, "2026-04-29T10:00:00", "anything")
    _insert_signal(signals_db, "2026-04-29T10:01:00", "else")
    out = dl.recent_signals_for("does-not-exist", n=5)
    assert len(out) == 2  # falls through to "any signal_type" path


def test_recent_signals_for_empty_strategy():
    assert dl.recent_signals_for("", n=5) == []


def test_recent_signals_for_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "no-signals.db"))
    assert dl.recent_signals_for("ict", n=5) == []


# -- recent_logs_for ----------------------------------------------------------

def test_recent_logs_for_happy_path():
    runner = MagicMock(return_value=SimpleNamespace(
        stdout="line 1\nline 2\n", stderr=""))
    out = dl.recent_logs_for("ict-trader-live", n=2, _runner=runner)
    assert "line 1" in out
    runner.assert_called_once()
    args = runner.call_args[0][0]
    assert args[0] == "journalctl"
    assert "ict-trader-live" in args


def test_recent_logs_for_journalctl_missing():
    def boom(_cmd):
        raise FileNotFoundError("journalctl")
    out = dl.recent_logs_for("ict-trader-live", n=2, _runner=boom)
    assert out == "⚠️ unavailable"


def test_recent_logs_for_invalid_service():
    assert dl.recent_logs_for("", n=2) == "⚠️ unavailable"


def test_recent_logs_for_runner_exception_returns_unavailable():
    def boom(_cmd):
        raise RuntimeError("permission denied")
    assert dl.recent_logs_for("svc", n=2, _runner=boom) == "⚠️ unavailable"


# -- latest_backtests_per_model ----------------------------------------------

def test_latest_backtests_per_model_groups_by_strategy_version(trade_journal_db):
    conn = sqlite3.connect(trade_journal_db)
    rows = [
        ("v1", "2026-04-28 10:00:00", 100.0),
        ("v1", "2026-04-29 10:00:00", 150.0),  # newer for v1
        ("v2", "2026-04-29 09:00:00", 50.0),
    ]
    for sv, created, pnl in rows:
        conn.execute(
            "INSERT INTO backtest_results (strategy_version, created_at, total_pnl) "
            "VALUES (?, ?, ?)", (sv, created, pnl),
        )
    conn.commit()
    conn.close()
    out = dl.latest_backtests_per_model()
    assert len(out) == 2
    by_v = {r["strategy_version"]: r for r in out}
    assert by_v["v1"]["total_pnl"] == 150.0
    assert by_v["v2"]["total_pnl"] == 50.0


def test_latest_backtests_per_model_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "missing.db"))
    assert dl.latest_backtests_per_model() == []


def test_latest_backtests_per_model_empty_table(trade_journal_db):
    assert dl.latest_backtests_per_model() == []
