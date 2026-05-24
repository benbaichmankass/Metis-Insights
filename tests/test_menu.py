"""Tests for src/bot/menu.py — the trader-bot menu (pure builders).

Keyboard structure + the three collapsible snapshot renderers. The
renderers take already-fetched dicts (no I/O) so they run offline. The
``telegram`` package is stubbed by conftest when absent; keyboard
structure assertions that need real InlineKeyboard objects are guarded.
"""
from __future__ import annotations

import pytest

from src.bot import menu


def _kb_callbacks(markup) -> list[str]:
    """Flatten an InlineKeyboardMarkup to its callback_data values.

    Returns [] when telegram is stubbed (no real .inline_keyboard), so
    structure tests self-skip rather than fail on the mock.
    """
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return []
    out: list[str] = []
    for row in rows:
        for btn in row:
            cb = getattr(btn, "callback_data", None)
            if isinstance(cb, str):
                out.append(cb)
    return out


# ── constants ──────────────────────────────────────────────────────────

def test_callback_namespace_is_menu_prefixed():
    for cb in (menu.CB_HOME, menu.CB_KILL, menu.CB_KILL_ACCOUNTS,
               menu.CB_KILL_STRATS, menu.CB_SYSTEM, menu.CB_ACCOUNTS,
               menu.CB_STRATEGIES, menu.CB_CLOSEALL, menu.CB_CLOSEALL_CONFIRM):
        assert cb.startswith("menu:")


# ── keyboards (guarded on real telegram) ────────────────────────────────

def test_main_menu_has_all_five_actions():
    cbs = _kb_callbacks(menu.main_menu_keyboard())
    if not cbs:
        pytest.skip("telegram stubbed — keyboard structure not introspectable")
    for expected in (menu.CB_KILL, menu.CB_SYSTEM, menu.CB_ACCOUNTS,
                     menu.CB_STRATEGIES, menu.CB_CLOSEALL):
        assert expected in cbs


def test_account_kill_keyboard_toggles_target_mode():
    accounts = [
        {"account_id": "bybit_1", "mode": "live"},
        {"account_id": "bybit_2", "mode": "dry_run"},
    ]
    cbs = _kb_callbacks(menu.account_kill_keyboard(accounts))
    if not cbs:
        pytest.skip("telegram stubbed")
    # live account offers a flip TO dry_run; dry account offers flip TO live.
    assert "killacct:bybit_1:dry_run" in cbs
    assert "killacct:bybit_2:live" in cbs
    assert menu.CB_KILL in cbs  # back button


def test_strategy_kill_keyboard_toggles_execution():
    strats = [
        {"name": "vwap", "execution": "live"},
        {"name": "ict_scalp", "execution": "shadow"},
    ]
    cbs = _kb_callbacks(menu.strategy_kill_keyboard(strats))
    if not cbs:
        pytest.skip("telegram stubbed")
    assert "killstrat:vwap:shadow" in cbs
    assert "killstrat:ict_scalp:live" in cbs


def test_closeall_confirm_has_confirm_and_cancel():
    cbs = _kb_callbacks(menu.closeall_confirm_keyboard())
    if not cbs:
        pytest.skip("telegram stubbed")
    assert menu.CB_CLOSEALL_CONFIRM in cbs
    assert menu.CB_HOME in cbs


# ── renderers (always run; pure strings) ────────────────────────────────

def test_main_menu_text_mentions_kill_and_closeall():
    text, _kb = menu.render_main_menu()
    assert "Kill switch" in text
    assert "Close all" in text


def test_system_view_flags_holding_when_all_active():
    out = menu.render_system_view(
        services={"ict-trader-live": "active", "ict-web-api": "active"},
        heartbeat={"label": "running", "age_seconds": 12, "last_tick": "x"},
        kill_summary={"accounts_live": 2, "accounts_dry": 0,
                      "strats_live": 3, "strats_shadow": 1},
        vm={"uptime": "3d", "load": "0.2", "mem": "48%", "disk": "21%"},
    )
    assert "System update" in out
    assert "🟢 Holding" in out
    assert "ict-trader-live" in out


def test_system_view_flags_check_when_service_down():
    out = menu.render_system_view(
        services={"ict-trader-live": "inactive"},
        heartbeat={"label": "stopped"},
    )
    assert "🟡 Check" in out


def test_accounts_view_renders_mode_balance_pnl_and_trades():
    out = menu.render_accounts_view([
        {"account_id": "bybit_1", "exchange": "bybit", "mode": "live",
         "balance": 1234.5, "pnl_24h": 45.0, "open_positions": 1,
         "trades": ["BTCUSDT long +$45"]},
    ])
    assert "bybit_1" in out
    assert "live" in out
    assert "$1,234.50" in out
    assert "+$45.00" in out
    assert "BTCUSDT long" in out


def test_accounts_view_balance_unavailable_when_none():
    out = menu.render_accounts_view([{"account_id": "x", "balance": None}])
    assert "unavailable" in out


def test_accounts_view_empty():
    out = menu.render_accounts_view([])
    assert "No accounts" in out


def test_strategies_view_renders_execution_and_running():
    out = menu.render_strategies_view([
        {"name": "vwap", "label": "VWAP", "execution": "shadow",
         "running": True, "pnl_24h": -10.0, "open_positions": 0,
         "trade_count": 12, "accounts": ["bybit_1"]},
    ])
    assert "VWAP" in out
    assert "shadow" in out
    assert "running" in out
    assert "-$10.00" in out


def test_strategies_view_empty():
    out = menu.render_strategies_view([])
    assert "No strategies" in out
