"""Tests for S-007 #117-118: /strategies → registry summary."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# Stub heavy deps required by telegram_query_bot before any src import.
for _mod in (
    "telegram", "telegram.ext", "dotenv", "requests",
    "pybit", "pybit.unified_trading",
    "src.runtime.signal_notifications",
):
    sys.modules.setdefault(_mod, MagicMock())
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}
_tg = sys.modules["telegram"]
_tg.Update = MagicMock
_tg.BotCommand = MagicMock
_tg.InlineKeyboardButton = MagicMock
_tg.InlineKeyboardMarkup = MagicMock
_tg_ext = sys.modules["telegram.ext"]
_tg_ext.Application = MagicMock
_tg_ext.CommandHandler = MagicMock
_tg_ext.CallbackQueryHandler = MagicMock


# ---------------------------------------------------------------------------
# strategy_dashboard_data — registry enrichment
# ---------------------------------------------------------------------------

def test_dashboard_rows_include_service_and_model():
    """/strategies rows must include service and model from the registry."""
    from src.bot import data_loaders as dl
    rows = dl.strategy_dashboard_data()
    assert rows, "Expected at least one row from registry"
    for r in rows:
        assert "service" in r, f"'{r['strategy']}' row missing 'service'"
        assert "model" in r, f"'{r['strategy']}' row missing 'model'"


def test_dashboard_rows_service_matches_registry():
    """Service field in each row must match registry.service_name()."""
    from src.bot import data_loaders as dl
    from src.strategy_registry import service_name
    for r in dl.strategy_dashboard_data():
        expected = service_name(r["strategy"])
        assert r["service"] == expected, (
            f"Row service '{r['service']}' != registry '{expected}' "
            f"for strategy '{r['strategy']}'"
        )


def test_dashboard_rows_no_hardcoded_fallback(monkeypatch):
    """strategy_dashboard_data() uses the registry, not a hardcoded list."""
    import types
    from src.bot import data_loaders as dl

    fake_reg = types.ModuleType("src.strategy_registry")
    fake_reg.load_strategies = lambda: [
        {"name": "alpha", "service": "ict-trader-alpha",
         "model": None, "signal_prefixes": []},
    ]
    # list_live_strategies() also reads registry; fake it via monkeypatch.
    monkeypatch.setattr(dl, "list_live_strategies", lambda: ["alpha"])
    monkeypatch.setitem(sys.modules, "src.strategy_registry", fake_reg)

    rows = dl.strategy_dashboard_data()
    assert len(rows) == 1
    assert rows[0]["strategy"] == "alpha"
    assert rows[0]["service"] == "ict-trader-alpha"


def test_dashboard_rows_breakout_has_model():
    """breakout_confirmation row must carry the model filename from registry."""
    from src.bot import data_loaders as dl
    rows = {r["strategy"]: r for r in dl.strategy_dashboard_data()}
    bc = rows.get("breakout_confirmation")
    assert bc is not None
    assert bc["model"] is not None
    assert bc["model"].endswith(".joblib")


def test_dashboard_rows_vwap_model_is_none():
    from src.bot import data_loaders as dl
    rows = {r["strategy"]: r for r in dl.strategy_dashboard_data()}
    assert rows["vwap"]["model"] is None


# ---------------------------------------------------------------------------
# _format_strategies_dashboard — shows service + model
# ---------------------------------------------------------------------------

def _make_row(strategy, service, model=None, signals=0, pnl=0.0, open_pos=0):
    return {"strategy": strategy, "service": service, "model": model,
            "signals_today": signals, "pnl": pnl, "open_pos": open_pos,
            "status": "active"}


def test_formatter_shows_service():
    import src.bot.telegram_query_bot as bot
    row = _make_row("ict", "ict-trader-ict")
    text = bot._format_strategies_dashboard([row])
    assert "ict-trader-ict" in text


def test_formatter_shows_model_when_set():
    import src.bot.telegram_query_bot as bot
    row = _make_row("breakout_confirmation", "ict-trader-breakout",
                    model="btc_v1.joblib")
    text = bot._format_strategies_dashboard([row])
    assert "btc_v1.joblib" in text


def test_formatter_omits_model_line_when_none():
    import src.bot.telegram_query_bot as bot
    row = _make_row("vwap", "ict-trader-vwap", model=None)
    text = bot._format_strategies_dashboard([row])
    assert "🧠" not in text


def test_formatter_missing_service_defaults_to_question_mark():
    import src.bot.telegram_query_bot as bot
    row = {"strategy": "alpha", "signals_today": 0, "pnl": 0.0,
           "open_pos": 0, "status": "active"}  # no service key
    text = bot._format_strategies_dashboard([row])
    assert "?" in text
