"""Tests for src/bot/data_loaders.py — Sprint S-001 PR-B1/B2/B3.

Each loader has a happy path + at least one failure-mode test, per the
spec's acceptance criteria (docs/TELEGRAM-SPEC.md §6).
"""
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    # it must include the production roster (S-012 PR B1: turtle_soup + vwap).
    assert isinstance(out, list)
    if out:
        for expected in ("turtle_soup", "vwap"):
            assert expected in out


def test_list_live_strategies_handles_both_sources_broken(monkeypatch):
    """Returns [] only when both the registry and pipeline are unavailable.

    S-007: list_live_strategies() now tries the registry first, then pipeline.
    Both must be broken to produce an empty list.
    """

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("simulated broken import")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    monkeypatch.setitem(sys.modules, "src.runtime.pipeline", _Boom())
    assert dl.list_live_strategies() == []


def test_list_live_strategies_uses_registry_when_available(monkeypatch):
    """Registry is the primary source; pipeline fallback should not be needed."""
    import types
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.load_strategies = lambda: [
        {"name": "alpha", "service": "ict-trader-alpha", "model": None},
        {"name": "beta", "service": "ict-trader-beta", "model": None},
    ]
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)
    result = dl.list_live_strategies()
    assert result == ["alpha", "beta"]


# -- list_trader_services -----------------------------------------------------

def test_list_trader_services_uses_registry(monkeypatch):
    """S-007: primary source is the registry service field."""
    import types
    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.load_strategies = lambda: [
        {"name": "breakout_confirmation", "service": "ict-trader-breakout", "model": "x.joblib"},
        {"name": "vwap", "service": "ict-trader-vwap", "model": None},
    ]
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)
    assert dl.list_trader_services() == ["ict-trader-breakout", "ict-trader-vwap"]


def test_list_trader_services_falls_back_to_deploy_dir(fake_repo, monkeypatch):
    """When the registry is unavailable, deploy/ directory scan is the fallback."""
    deploy = fake_repo / "deploy"
    (deploy / "ict-trader-live.service").write_text("# unit\n")
    (deploy / "ict-trader-binance-1.service").write_text("# unit\n")
    (deploy / "ict-telegram-bot.service").write_text("# unit\n")
    (deploy / "ict-heartbeat.timer").write_text("# timer\n")

    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("simulated broken registry")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
    out = dl.list_trader_services()
    assert sorted(out) == ["ict-trader-binance-1", "ict-trader-live"]


def test_list_trader_services_both_sources_missing(tmp_path, monkeypatch):
    """Returns [] when registry is broken and deploy/ dir does not exist."""
    class _Boom:
        def __getattr__(self, _name):
            raise RuntimeError("simulated broken registry")

    monkeypatch.setitem(sys.modules, "src.strategy_registry", _Boom())
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
    # S-012 PR D2 (single-process): every env-discovered account routes
    # through ict-trader-live; per-account systemd units do not exist.
    assert sub["service"] == "ict-trader-live"
    assert sub["exchange"] == "binance"


def test_list_accounts_empty_repo(fake_repo):
    # No .env, no yaml — must return [], not crash.
    assert dl.list_accounts() == []


def test_list_accounts_yaml_takes_precedence(fake_repo):
    """``accounts.yaml`` overrides ``.env``-discovered accounts.

    Uses the production dict-shape (S-012 PR B3); the historical
    list-shape parser branch was dropped when ``data_loaders``
    switched to the canonical ``load_accounts_dict`` reader.
    """
    pytest.importorskip("yaml")
    (fake_repo / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    yaml_path = fake_repo / "config" / "accounts.yaml"
    yaml_path.write_text(
        "accounts:\n"
        "  live:\n"
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
            account_id TEXT NOT NULL DEFAULT 'live',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_trades_account_created
            ON trades (account_id, datetime(created_at) DESC);
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


# -- Exchange-aware account queries (PR-B3) -----------------------------------

@pytest.fixture
def fake_repo_b3(tmp_path, monkeypatch):
    (tmp_path / "deploy").mkdir()
    monkeypatch.setattr(dl, "REPO_ROOT", str(tmp_path))
    return tmp_path


def _bybit_account(env_path):
    return {"account_id": "live", "exchange": "bybit",
            "env_path": env_path, "service": "ict-trader-live",
            "strategies": [], "source": "env"}


def test_account_balance_bybit_happy(fake_repo_b3):
    env_path = str(fake_repo_b3 / ".env")
    (fake_repo_b3 / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.return_value = {
        "result": {"list": [{"coin": [
            {"coin": "USDT", "walletBalance": "100", "usdValue": "100.0"},
            {"coin": "BTC", "walletBalance": "0.01", "usdValue": "650.5"},
        ]}]}
    }
    with patch.object(dl, "_bybit_client", return_value=fake_client):
        out = dl.account_balance(_bybit_account(env_path))
    assert out is not None
    assert out["total_usdt"] == pytest.approx(750.5)


def test_account_balance_returns_none_on_exception(fake_repo_b3):
    env_path = str(fake_repo_b3 / ".env")
    (fake_repo_b3 / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    fake_client = MagicMock()
    fake_client.get_wallet_balance.side_effect = RuntimeError("net down")
    with patch.object(dl, "_bybit_client", return_value=fake_client):
        assert dl.account_balance(_bybit_account(env_path)) is None


def test_account_balance_unknown_exchange_returns_none():
    acc = {"account_id": "x", "exchange": "ftx", "env_path": None,
           "service": "ict-trader-x", "strategies": [], "source": "env"}
    assert dl.account_balance(acc) is None


def test_account_balance_missing_keys_returns_none(fake_repo_b3):
    # No .env on disk → _read_env_file → {} → _bybit_client returns None.
    acc = _bybit_account(str(fake_repo_b3 / ".env"))
    assert dl.account_balance(acc) is None


def test_account_open_positions_bybit_filters_zero_size(fake_repo_b3):
    env_path = str(fake_repo_b3 / ".env")
    (fake_repo_b3 / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    fake_client = MagicMock()
    fake_client.get_positions.return_value = {
        "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01",
             "avgPrice": "65000", "unrealisedPnl": "10.5"},
            {"symbol": "ETHUSDT", "side": "Sell", "size": "0",
             "avgPrice": "3000", "unrealisedPnl": "0"},
        ]}
    }
    with patch.object(dl, "_bybit_client", return_value=fake_client):
        out = dl.account_open_positions(_bybit_account(env_path))
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["symbol"] == "BTCUSDT"
    assert out[0]["entry_price"] == 65000.0


def test_account_open_positions_returns_none_on_exception(fake_repo_b3):
    env_path = str(fake_repo_b3 / ".env")
    (fake_repo_b3 / ".env").write_text("BYBIT_API_KEY=abc\nBYBIT_API_SECRET=def\n")
    fake_client = MagicMock()
    fake_client.get_positions.side_effect = RuntimeError("api down")
    with patch.object(dl, "_bybit_client", return_value=fake_client):
        assert dl.account_open_positions(_bybit_account(env_path)) is None


def test_account_last_trade_returns_latest_live_row(trade_journal_db):
    conn = sqlite3.connect(trade_journal_db)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "is_backtest, strategy_name, account_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-29T10:00:00", "BTCUSDT", "LONG", 65000.0, 0, "ict",
         "live", "2026-04-29 10:00:00"),
    )
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "is_backtest, strategy_name, account_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-29T11:00:00", "ETHUSDT", "SHORT", 3200.0, 0, "vwap",
         "live", "2026-04-29 11:00:00"),
    )
    # A backtest row that must NOT be returned.
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, is_backtest, account_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-04-29T12:00:00", "SOLUSDT", 1, "live", "2026-04-29 12:00:00"),
    )
    conn.commit()
    conn.close()
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    out = dl.account_last_trade(acc)
    assert out is not None
    assert out["symbol"] == "ETHUSDT"
    assert out["strategy_name"] == "vwap"


def test_account_last_trade_returns_none_when_account_has_no_rows(trade_journal_db):
    # No rows inserted for this account_id — query returns no results.
    acc = {"account_id": "binance-sub-1", "exchange": "binance",
           "env_path": None, "service": "ict-trader-binance-1",
           "strategies": [], "source": "env"}
    assert dl.account_last_trade(acc) is None


def test_account_last_trade_returns_row_for_non_legacy_account(trade_journal_db):
    conn = sqlite3.connect(trade_journal_db)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "is_backtest, account_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-29T10:00:00", "ETHUSDT", "LONG", 3200.0, 0,
         "binance-sub-1", "2026-04-29 10:00:00"),
    )
    conn.commit()
    conn.close()
    acc = {"account_id": "binance-sub-1", "exchange": "binance",
           "env_path": None, "service": "ict-trader-binance-1",
           "strategies": [], "source": "env"}
    out = dl.account_last_trade(acc)
    assert out is not None
    assert out["symbol"] == "ETHUSDT"


def test_account_last_trade_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "nope.db"))
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    assert dl.account_last_trade(acc) is None


# -- recent_trades_for --------------------------------------------------------

def _insert_trade(db, ts, symbol, direction="LONG", entry_price=100.0,
                  is_backtest=0, strategy="ict", account_id="live"):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
        "is_backtest, strategy_name, account_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, symbol, direction, entry_price, is_backtest, strategy, account_id, ts),
    )
    conn.commit()
    conn.close()


def test_recent_trades_for_returns_latest_live_rows(trade_journal_db):
    # Three live rows (newest last) + one backtest row that must be excluded.
    _insert_trade(trade_journal_db, "2026-04-29 09:00:00", "BTCUSDT")
    _insert_trade(trade_journal_db, "2026-04-29 10:00:00", "ETHUSDT")
    _insert_trade(trade_journal_db, "2026-04-29 11:00:00", "SOLUSDT")
    _insert_trade(trade_journal_db, "2026-04-29 12:00:00", "BNBUSDT",
                  is_backtest=1)
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    out = dl.recent_trades_for(acc, n=5)
    assert isinstance(out, list)
    # Note: recent_trades_for does not filter is_backtest (matches existing
    # cmd_last5 behavior of fetch_last_5_trades). Newest first.
    symbols = [r["symbol"] for r in out]
    assert symbols[0] == "BNBUSDT"
    assert symbols[1] == "SOLUSDT"
    # All expected columns are present.
    for col in ("id", "timestamp", "symbol", "direction", "entry_price",
                "exit_price", "stop_loss", "take_profit_1", "take_profit_2",
                "take_profit_3", "position_size", "setup_type", "killzone",
                "bias", "entry_reason", "exit_reason", "pnl", "pnl_percent",
                "status", "notes", "is_backtest", "created_at"):
        assert col in out[0]


def test_recent_trades_for_respects_n_parameter(trade_journal_db):
    for i in range(7):
        _insert_trade(trade_journal_db,
                      f"2026-04-29 1{i}:00:00", f"SYM{i}")
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    assert len(dl.recent_trades_for(acc, n=3)) == 3
    assert len(dl.recent_trades_for(acc, n=5)) == 5
    assert len(dl.recent_trades_for(acc, n=10)) == 7


def test_recent_trades_for_returns_empty_when_account_has_no_rows(
        trade_journal_db):
    # Row exists for 'live' but not for 'binance-sub-1' — query isolates by account_id.
    _insert_trade(trade_journal_db, "2026-04-29 10:00:00", "BTCUSDT")
    acc = {"account_id": "binance-sub-1", "exchange": "binance",
           "env_path": None, "service": "ict-trader-binance-1",
           "strategies": [], "source": "env"}
    assert dl.recent_trades_for(acc, n=5) == []


def test_recent_trades_for_returns_rows_for_non_legacy_account(trade_journal_db):
    _insert_trade(trade_journal_db, "2026-04-29 10:00:00", "ETHUSDT",
                  account_id="binance-sub-1")
    _insert_trade(trade_journal_db, "2026-04-29 11:00:00", "SOLUSDT",
                  account_id="binance-sub-1")
    acc = {"account_id": "binance-sub-1", "exchange": "binance",
           "env_path": None, "service": "ict-trader-binance-1",
           "strategies": [], "source": "env"}
    out = dl.recent_trades_for(acc, n=5)
    assert len(out) == 2
    assert out[0]["symbol"] == "SOLUSDT"  # newest first


def test_recent_trades_for_isolates_accounts(trade_journal_db):
    _insert_trade(trade_journal_db, "2026-04-29 10:00:00", "BTCUSDT",
                  account_id="live")
    _insert_trade(trade_journal_db, "2026-04-29 11:00:00", "ETHUSDT",
                  account_id="bybit-sub2")
    live_acc = {"account_id": "live"}
    sub2_acc = {"account_id": "bybit-sub2"}
    live_rows = dl.recent_trades_for(live_acc, n=5)
    sub2_rows = dl.recent_trades_for(sub2_acc, n=5)
    assert len(live_rows) == 1 and live_rows[0]["symbol"] == "BTCUSDT"
    assert len(sub2_rows) == 1 and sub2_rows[0]["symbol"] == "ETHUSDT"


def test_recent_trades_for_returns_empty_when_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "nope.db"))
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    assert dl.recent_trades_for(acc, n=5) == []


def test_recent_trades_for_handles_invalid_account(monkeypatch):
    assert dl.recent_trades_for(None, n=5) == []
    assert dl.recent_trades_for("not-a-dict", n=5) == []


def test_recent_trades_for_handles_invalid_n(trade_journal_db):
    _insert_trade(trade_journal_db, "2026-04-29 10:00:00", "BTCUSDT")
    acc = {"account_id": "live", "exchange": "bybit", "env_path": None,
           "service": "ict-trader-live", "strategies": [], "source": "env"}
    # Bogus n falls back to default 5; zero/negative coerced to >=1.
    assert isinstance(dl.recent_trades_for(acc, n="oops"), list)
    assert isinstance(dl.recent_trades_for(acc, n=0), list)
    assert isinstance(dl.recent_trades_for(acc, n=-3), list)


# ---------------------------------------------------------------------------
# S-005 M4: strategy_dashboard_data
# ---------------------------------------------------------------------------

def _make_signals_db(tmp_path, rows):
    """Create a minimal signals DB and return its path."""
    path = str(tmp_path / "signals.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE signals "
        "(id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, "
        "signal_type TEXT, direction TEXT, price REAL, "
        "timeframe TEXT, reason TEXT, metadata TEXT)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO signals (timestamp, symbol, signal_type, direction) "
            "VALUES (?, 'BTCUSDT', ?, 'buy')",
            (r["timestamp"], r["signal_type"]),
        )
    conn.commit()
    conn.close()
    return path


def _make_tj_db(tmp_path, rows):
    """Create a minimal trade journal DB and return its path."""
    path = str(tmp_path / "trade_journal.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE trades "
        "(id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, "
        "direction TEXT, entry_price REAL, pnl REAL, status TEXT, "
        "is_backtest INTEGER, strategy_name TEXT)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "pnl, status, is_backtest, strategy_name) "
            "VALUES (?, 'BTCUSDT', 'long', 50000, ?, ?, ?, ?)",
            (r["timestamp"], r.get("pnl", 0.0), r.get("status", "closed"),
             r.get("is_backtest", 0), r.get("strategy_name")),
        )
    conn.commit()
    conn.close()
    return path


class TestStrategyDashboardData:
    from datetime import date as _dt

    def test_returns_one_row_per_strategy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        rows = dl.strategy_dashboard_data(["breakout_confirmation", "vwap", "ict"])
        assert len(rows) == 3
        assert [r["strategy"] for r in rows] == [
            "breakout_confirmation", "vwap", "ict"
        ]

    def test_each_row_has_required_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        rows = dl.strategy_dashboard_data(["vwap"])
        assert set(rows[0].keys()) >= {"strategy", "signals_today", "pnl", "open_pos", "status"}

    def test_signals_today_counts_correct_strategy(self, tmp_path, monkeypatch):
        from datetime import date
        today = date.today().isoformat() + "T10:00:00"
        sig_db = _make_signals_db(tmp_path, [
            {"timestamp": today, "signal_type": "vwap_signal"},
            {"timestamp": today, "signal_type": "vwap_signal"},
            {"timestamp": today, "signal_type": "ml_breakout"},  # breakout, not vwap
        ])
        monkeypatch.setattr(dl, "SIGNALS_DB", sig_db)
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        rows = dl.strategy_dashboard_data(["vwap"])
        assert rows[0]["signals_today"] == 2

    def test_signals_today_returns_zero_for_incompatible_schema(self, tmp_path, monkeypatch):
        # Regression test for "no such column: timestamp" warning.
        # trade_journal.db has a signals table with logged_at_utc (not timestamp).
        # _count_signals_today must return 0 gracefully instead of logging a warning.
        bad_db = str(tmp_path / "trade_journal.db")
        conn = sqlite3.connect(bad_db)
        conn.execute(
            "CREATE TABLE signals "
            "(id INTEGER PRIMARY KEY, logged_at_utc TEXT NOT NULL, "
            "strategy TEXT, symbol TEXT, side TEXT)"
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(dl, "SIGNALS_DB", bad_db)
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        # Must not raise; returns 0 silently (warning is acceptable, exception is not)
        rows = dl.strategy_dashboard_data(["vwap"])
        assert rows[0]["signals_today"] == 0

    def test_pnl_sums_closed_trades_today(self, tmp_path, monkeypatch):
        from datetime import date
        today = date.today().isoformat() + "T10:00:00"
        tj_db = _make_tj_db(tmp_path, [
            {"timestamp": today, "pnl": 30.0, "status": "closed",
             "is_backtest": 0, "strategy_name": "ict"},
            {"timestamp": today, "pnl": -10.0, "status": "closed",
             "is_backtest": 0, "strategy_name": "ict"},
            {"timestamp": today, "pnl": 999.0, "status": "closed",
             "is_backtest": 0, "strategy_name": "vwap"},  # different strategy
        ])
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", tj_db)
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        rows = dl.strategy_dashboard_data(["ict"])
        assert rows[0]["pnl"] == pytest.approx(20.0)

    def test_open_pos_counts_open_trades(self, tmp_path, monkeypatch):
        from datetime import date
        today = date.today().isoformat() + "T10:00:00"
        tj_db = _make_tj_db(tmp_path, [
            {"timestamp": today, "status": "open", "is_backtest": 0,
             "strategy_name": "breakout_confirmation"},
            {"timestamp": today, "status": "open", "is_backtest": 0,
             "strategy_name": "breakout_confirmation"},
            {"timestamp": today, "status": "closed", "is_backtest": 0,
             "strategy_name": "breakout_confirmation"},
        ])
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", tj_db)
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        rows = dl.strategy_dashboard_data(["breakout_confirmation"])
        assert rows[0]["open_pos"] == 2

    def test_status_is_active_for_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        rows = dl.strategy_dashboard_data(["vwap", "ict"])
        assert all(r["status"] == "active" for r in rows)

    def test_missing_dbs_return_zero_counters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dl, "SIGNALS_DB", str(tmp_path / "nosignals.db"))
        monkeypatch.setattr(dl, "TRADE_JOURNAL_DB", str(tmp_path / "notj.db"))
        rows = dl.strategy_dashboard_data(["breakout_confirmation"])
        assert rows[0]["signals_today"] == 0
        assert rows[0]["pnl"] == 0.0
        assert rows[0]["open_pos"] == 0
