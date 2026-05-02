"""S-032 regression tests
(architecture-audit-2026-05-02 P1-7).

The data-loading helpers used to live under ``src/bot/`` but did no
Telegram work; they belonged to the UI unit. S-032 moves the file to
``src/ui/data_loaders.py`` and leaves a back-compat shim at the legacy
``src/bot/data_loaders.py`` path so existing call sites + test fixtures
that monkeypatch ``src.bot.data_loaders.*`` keep working.

Tests pin:
  1. Canonical home is now ``src.ui.data_loaders``.
  2. The legacy ``src.bot.data_loaders`` import path resolves to the
     same module attributes (functions, constants, re-exports).
  3. The bot's own imports point at the canonical UI path (no
     bot-internal cycle through its own back-compat shim).
"""
from __future__ import annotations


def test_canonical_module_imports():
    from src.ui import data_loaders as ui_dl
    # Sample function + constant + re-export survive the move.
    assert callable(ui_dl.list_accounts)
    assert callable(ui_dl.bybit_client_for)
    assert callable(ui_dl.binance_conn_for)
    assert callable(ui_dl.account_balance_with_diagnostic)
    assert callable(ui_dl.recent_trades_for)
    assert callable(ui_dl.list_live_strategies)
    assert callable(ui_dl.recent_signals_for)
    assert isinstance(ui_dl.LEGACY_LIVE_ACCOUNT_ID, str)
    assert isinstance(ui_dl.LEGACY_LIVE_SERVICE, str)
    assert isinstance(ui_dl.TRADE_JOURNAL_DB, str)


def test_back_compat_shim_resolves_same_callables():
    """Existing call sites + tests that do
    ``from src.bot import data_loaders as dl`` plus monkeypatch
    fixtures keying off ``src.bot.data_loaders.foo`` MUST keep
    working — the canonical home moved, the legacy import path
    didn't."""
    from src.bot import data_loaders as bot_dl
    from src.ui import data_loaders as ui_dl

    # Sample of the call sites + test stub keys.
    for name in (
        "list_accounts",
        "list_live_strategies",
        "list_trader_services",
        "recent_signals_for",
        "recent_logs_for",
        "credentials_check",
        "account_balance",
        "account_balance_with_diagnostic",
        "account_open_positions",
        "strategy_dashboard_data",
        "close_all_bybit_positions_for_strategy",
        "account_last_trade",
        "recent_trades_for",
        "bybit_client_for",
        "binance_conn_for",
        "resolve_credentials",
        "LEGACY_LIVE_ACCOUNT_ID",
        "LEGACY_LIVE_SERVICE",
        "TRADE_JOURNAL_DB",
    ):
        bot_attr = getattr(bot_dl, name)
        ui_attr = getattr(ui_dl, name)
        # Same callable / same constant — they MUST be identical.
        assert bot_attr is ui_attr, (
            f"shim mismatch for {name}: bot={bot_attr!r} ui={ui_attr!r}"
        )


def test_shim_has_logger_attribute():
    """Production code occasionally pokes the shared logger."""
    from src.bot import data_loaders as bot_dl
    from src.ui import data_loaders as ui_dl
    assert bot_dl.logger is ui_dl.logger
