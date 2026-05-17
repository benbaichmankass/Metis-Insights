"""Boot-audit observability ping (Sprint S-021, PR 2 of 3).

Three contracts under test:

1. **No open packages → log only, no Telegram.**
   Empty DB → ``report_open_packages_on_boot`` returns ``{}`` or all-zeros,
   ``send_telegram_direct`` is NOT called, log captures "0 open".
2. **Open linked packages → Telegram ping.**
   DB seeded with linked open packages across two strategies →
   ``send_telegram_direct`` called once with the counts in the body.
3. **DB unavailable → no exception, returns ``{}``.**
   Non-existent ``db_path`` → function returns ``{}``, nothing raises.
"""
from __future__ import annotations

import logging

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _insert_pkg(db, *, pkg_id, strategy="vwap", status="open",
                linked_trade_id=None):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 80_000.0,
        "sl": 79_500.0,
        "tp": 80_500.0,
        "confidence": 0.5,
        "status": status,
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    from src.units.db.database import Database
    return Database(db_path=str(db_path))


# ---------------------------------------------------------------------------
# Contract 1: no open packages → log only, no Telegram
# ---------------------------------------------------------------------------


def test_no_open_packages_logs_only(tmp_journal, monkeypatch, caplog):
    """Empty DB: function returns all-zero counts, Telegram is not called."""
    telegram_calls: list[str] = []

    def _fake_send(msg, *, parse_mode=None):
        telegram_calls.append(msg)

    monkeypatch.setattr("src.runtime.boot_audit.send_telegram_direct", _fake_send,
                        raising=False)
    monkeypatch.setattr(
        "src.runtime.boot_audit._load_strategy_names",
        lambda: ["vwap", "turtle_soup"],
    )

    from src.runtime.boot_audit import report_open_packages_on_boot

    with caplog.at_level(logging.INFO, logger="src.runtime.boot_audit"):
        result = report_open_packages_on_boot()

    total = sum(v for v in result.values() if v is not None)
    assert total == 0, f"Expected 0 open packages, got {result}"
    assert not telegram_calls, "Telegram must NOT be called when there are no open packages"
    assert "0 open package" in caplog.text.lower() or "boot_audit" in caplog.text


# ---------------------------------------------------------------------------
# Contract 2: open linked packages → Telegram ping with counts
# ---------------------------------------------------------------------------


def test_open_packages_pings_telegram(tmp_journal, monkeypatch):
    """Seeded DB with linked packages → send_telegram_direct called once."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-1", strategy="vwap",
                linked_trade_id=1)
    _insert_pkg(tmp_journal, pkg_id="pkg-turtle-1", strategy="turtle_soup",
                linked_trade_id=2)

    telegram_calls: list[str] = []

    def _fake_send(msg, *, parse_mode=None):
        assert parse_mode is None, "boot_audit must use plain-text (no parse_mode)"
        telegram_calls.append(msg)

    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap", "turtle_soup"])

    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(_ba, "_send_boot_ping",
                        lambda counts, total: _fake_send(
                            "\n".join([f"{s}: {n}" for s, n in counts.items()]),
                            parse_mode=None,
                        ))

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert result.get("vwap", 0) == 1, f"Expected vwap=1, got {result}"
    assert result.get("turtle_soup", 0) == 1, f"Expected turtle_soup=1, got {result}"
    assert len(telegram_calls) == 1, "Expected exactly one Telegram call"
    body = telegram_calls[0]
    assert "vwap" in body
    assert "turtle_soup" in body


def test_open_packages_telegram_parse_mode_is_none(tmp_journal, monkeypatch):
    """Telegram call must use parse_mode=None — CLAUDE.md § 'Always do'."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-2", strategy="vwap",
                linked_trade_id=10)

    captured_kwargs: list[dict] = []

    def _fake_send(msg, *, parse_mode="SENTINEL"):
        captured_kwargs.append({"parse_mode": parse_mode})

    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap"])
    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(_ba, "_send_boot_ping",
                        lambda counts, total: _fake_send("ping", parse_mode=None))

    from src.runtime.boot_audit import report_open_packages_on_boot
    report_open_packages_on_boot()

    assert captured_kwargs, "send_telegram_direct was not called"
    assert captured_kwargs[0]["parse_mode"] is None


# ---------------------------------------------------------------------------
# Contract 3: DB unavailable → no exception, returns {}
# ---------------------------------------------------------------------------


def test_db_unavailable_no_raise(tmp_path, monkeypatch):
    """Non-existent DB path: function returns {} and does not raise."""
    bad_path = tmp_path / "does_not_exist" / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(bad_path))
    monkeypatch.setattr("src.runtime.boot_audit._load_strategy_names",
                        lambda: ["vwap"])

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert isinstance(result, dict), "Must return a dict even on DB failure"


# ---------------------------------------------------------------------------
# Contract 4: per-strategy query failure → None sentinel + ping fires
# (S-067 follow-up item D1 — see docs/audits/silent-empty-reporting-2026-05-10.md
#  § Phase-2 #1.)
# ---------------------------------------------------------------------------


def test_query_failure_records_none(tmp_journal, monkeypatch):
    """Per-strategy query exception → counts[strategy] is None, no raise."""
    monkeypatch.setattr(
        "src.runtime.boot_audit._load_strategy_names",
        lambda: ["vwap", "turtle_soup"],
    )

    # Force the per-strategy query to raise for one strategy and succeed
    # (returning empty) for the other.
    def _raise_for_vwap(self, strategy, *, status, linked_only):
        if strategy == "vwap":
            raise RuntimeError("simulated DB query failure")
        return []

    from src.units.db.database import Database
    monkeypatch.setattr(Database, "get_order_packages_by_strategy", _raise_for_vwap)

    # Stub the ping path so we don't need the live send_telegram_direct.
    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(_ba, "_send_boot_ping", lambda counts, total: None)

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert result["vwap"] is None, (
        "Query failure must record None (not 0) so the wire shape "
        "distinguishes failure from 'no open packages'."
    )
    assert result["turtle_soup"] == 0, "Successful empty query records 0"


def test_query_failure_pings_telegram(tmp_journal, monkeypatch):
    """When total=0 but a strategy query failed, ping fires (not silent)."""
    monkeypatch.setattr(
        "src.runtime.boot_audit._load_strategy_names",
        lambda: ["vwap"],
    )

    def _always_raise(self, strategy, *, status, linked_only):
        raise RuntimeError("simulated DB query failure")

    from src.units.db.database import Database
    monkeypatch.setattr(Database, "get_order_packages_by_strategy", _always_raise)

    ping_calls: list[tuple] = []
    import src.runtime.boot_audit as _ba
    monkeypatch.setattr(
        _ba, "_send_boot_ping",
        lambda counts, total: ping_calls.append((counts, total)),
    )

    from src.runtime.boot_audit import report_open_packages_on_boot
    result = report_open_packages_on_boot()

    assert result == {"vwap": None}
    assert len(ping_calls) == 1, (
        "Query failure must trigger the ping even when total=0 — "
        "the operator otherwise sees a silent all-clear."
    )
    counts, total = ping_calls[0]
    assert total == 0
    assert counts["vwap"] is None


def test_query_failure_renders_in_ping_body(tmp_journal, monkeypatch):
    """The ping body must surface '(query failed)' for the failed strategy."""
    monkeypatch.setattr(
        "src.runtime.boot_audit._load_strategy_names",
        lambda: ["vwap", "turtle_soup"],
    )

    def _raise_for_vwap(self, strategy, *, status, linked_only):
        if strategy == "vwap":
            raise RuntimeError("simulated DB query failure")
        return []

    from src.units.db.database import Database
    monkeypatch.setattr(Database, "get_order_packages_by_strategy", _raise_for_vwap)

    sent_messages: list[str] = []

    def _fake_send(msg, *, parse_mode=None):
        sent_messages.append(msg)

    monkeypatch.setattr("src.runtime.notify.send_telegram_direct", _fake_send,
                        raising=False)

    from src.runtime.boot_audit import report_open_packages_on_boot
    report_open_packages_on_boot()

    assert sent_messages, "Telegram ping must be sent on query failure"
    body = sent_messages[0]
    assert "vwap" in body
    assert "(query failed)" in body, (
        f"Ping body must render the failed strategy as '(query failed)'; got:\n{body}"
    )
    assert "WARNING" in body, "Ping body must include a WARNING line on failure"


# ---------------------------------------------------------------------------
# Sprint A-3: reconcile_journal_vs_exchange_on_boot
# ---------------------------------------------------------------------------

import sqlite3 as _sqlite3


def _make_journal_with_open_trade(tmp_path, *, account_id="bybit_2", symbol="BTCUSDT"):
    """Create a minimal trade_journal.db with one open trade row."""
    db_path = tmp_path / "trade_journal.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            account_id TEXT,
            symbol TEXT,
            side TEXT,
            status TEXT,
            is_backtest INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT INTO trades (id, account_id, symbol, side, status, is_backtest) "
        "VALUES (?, ?, ?, ?, 'open', 0)",
        (42, account_id, symbol, "buy"),
    )
    conn.commit()
    conn.close()
    return str(db_path)


_LIVE_BYBIT_CFG = {
    "account_id": "bybit_2",
    "exchange": "bybit",
    "api_key_env": "BYBIT_API_KEY_2",
    "api_secret_env": "BYBIT_SECRET_2",
    "mode": "live",
    "market_type": "linear",
}

_DRY_BYBIT_CFG = {**_LIVE_BYBIT_CFG, "account_id": "bybit_1", "mode": "dry_run"}


class TestReconcileJournalVsExchange:
    """Sprint A-3: reconcile_journal_vs_exchange_on_boot contracts."""

    def _patch(self, monkeypatch, *, account_cfgs, positions_map, telegram_calls):
        """Patch accounts, exchange, and telegram for reconciler tests.

        account_open_positions is imported lazily inside the function body,
        so we patch it at the source module so the lazy import picks up the
        mock.
        """
        import src.runtime.boot_audit as _ba
        monkeypatch.setattr(_ba, "_load_account_cfgs", lambda: account_cfgs)
        # Patch at source so the `from ... import account_open_positions`
        # inside reconcile_journal_vs_exchange_on_boot picks up the mock.
        monkeypatch.setattr(
            "src.units.accounts.clients.account_open_positions",
            lambda cfg: positions_map.get(cfg.get("account_id")),
        )
        monkeypatch.setattr(
            _ba, "_send_ghost_alert",
            lambda ghosts: telegram_calls.extend(ghosts),
        )

    def test_clean_boot_no_alert(self, tmp_path, monkeypatch):
        """Journal open trade + matching exchange position → silent, no Telegram."""
        db_path = _make_journal_with_open_trade(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        telegram_calls: list[str] = []
        self._patch(
            monkeypatch,
            account_cfgs={"bybit_2": _LIVE_BYBIT_CFG},
            positions_map={"bybit_2": [{"symbol": "BTCUSDT", "size": 0.01}]},
            telegram_calls=telegram_calls,
        )
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert result["ghost_trades"] == 0
        assert not telegram_calls, "No alert when journal and exchange agree"

    def test_ghost_trade_triggers_alert(self, tmp_path, monkeypatch):
        """Journal open, Bybit flat → ghost detected, Telegram alert fires."""
        db_path = _make_journal_with_open_trade(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        telegram_calls: list[str] = []
        self._patch(
            monkeypatch,
            account_cfgs={"bybit_2": _LIVE_BYBIT_CFG},
            positions_map={"bybit_2": []},  # Bybit has no positions
            telegram_calls=telegram_calls,
        )
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert result["ghost_trades"] == 1
        assert len(telegram_calls) == 1
        assert "42" in telegram_calls[0]       # trade id
        assert "BTCUSDT" in telegram_calls[0]  # symbol

    def test_dry_account_skipped(self, tmp_path, monkeypatch):
        """Dry account is skipped — no exchange query, no alert."""
        db_path = _make_journal_with_open_trade(tmp_path, account_id="bybit_1")
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        positions_called: list[str] = []

        import src.runtime.boot_audit as _ba
        monkeypatch.setattr(_ba, "_load_account_cfgs",
                            lambda: {"bybit_1": _DRY_BYBIT_CFG})
        monkeypatch.setattr(
            "src.units.accounts.clients.account_open_positions",
            lambda cfg: positions_called.append(cfg) or [],
        )
        telegram_calls: list[str] = []
        monkeypatch.setattr(_ba, "_send_ghost_alert",
                            lambda ghosts: telegram_calls.extend(ghosts))

        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert result["checked_accounts"] == 0
        assert not positions_called, "Must not query exchange for dry accounts"
        assert not telegram_calls

    def test_exchange_query_none_is_error_not_ghost(self, tmp_path, monkeypatch):
        """Exchange returns None (creds failure) → error count, NO ghost alert."""
        db_path = _make_journal_with_open_trade(tmp_path)
        monkeypatch.setenv("TRADE_JOURNAL_DB", db_path)
        telegram_calls: list[str] = []
        self._patch(
            monkeypatch,
            account_cfgs={"bybit_2": _LIVE_BYBIT_CFG},
            positions_map={"bybit_2": None},  # None = creds/network failure
            telegram_calls=telegram_calls,
        )
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert result["ghost_trades"] == 0
        assert result["errors"] >= 1
        assert not telegram_calls, "Creds failure must not fire a ghost alert"

    def test_untracked_position_no_alert(self, tmp_path, monkeypatch):
        """Exchange has a position, journal has no open row → log only, no Telegram."""
        # Empty journal (no open trades).
        db_path = tmp_path / "trade_journal.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, account_id TEXT, "
            "symbol TEXT, side TEXT, status TEXT, is_backtest INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))

        telegram_calls: list[str] = []
        self._patch(
            monkeypatch,
            account_cfgs={"bybit_2": _LIVE_BYBIT_CFG},
            positions_map={"bybit_2": [{"symbol": "BTCUSDT", "size": 0.01}]},
            telegram_calls=telegram_calls,
        )
        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert result["ghost_trades"] == 0
        assert result["untracked_positions"] == 1
        assert not telegram_calls, "Untracked positions must not trigger Telegram"

    def test_no_db_no_raise(self, tmp_path, monkeypatch):
        """Missing trade_journal.db: function returns without raising."""
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "nonexistent.db")
        )
        import src.runtime.boot_audit as _ba
        monkeypatch.setattr(_ba, "_load_account_cfgs",
                            lambda: {"bybit_2": _LIVE_BYBIT_CFG})
        monkeypatch.setattr(
            "src.units.accounts.clients.account_open_positions",
            lambda cfg: [{"symbol": "BTCUSDT", "size": 0.001}],
        )
        monkeypatch.setattr(_ba, "_send_ghost_alert", lambda ghosts: None)

        from src.runtime.boot_audit import reconcile_journal_vs_exchange_on_boot
        result = reconcile_journal_vs_exchange_on_boot()
        assert isinstance(result, dict)
        assert result["ghost_trades"] == 0  # empty journal → no ghosts
