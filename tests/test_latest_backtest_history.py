"""/latest_backtest enhancement — historical browsing + delta indicators.

CP-2026-05-?-??. The pre-enhancement command surfaced ONLY the latest
row per strategy_version (no-arg path). This sprint adds:

  /latest_backtest <strategy>     → last 5 runs newest-first
  /latest_backtest <strategy> N   → last N runs (1..20)

with 📈 / 📉 delta indicators on the LATEST row vs the prior so the
operator can spot regressions across consecutive backtest runs at a
glance.

Three contracts under test:

1. ``data_loaders.backtest_history_for`` — ordered, limited, defensive.
2. ``data_loaders.list_backtest_strategies`` — distinct, sorted, drops
   blank/NULL rows.
3. ``processor.render_backtest_history_collapsable`` — delta direction
   logic (📈 for win-rate up vs 📉 for max-DD up), empty-state
   handling, single-run case (no deltas).
4. ``cmd_latest_backtest`` — back-compat (no-arg path unchanged) +
   new arg-parsing path.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    """Fresh trade_journal.db with the backtest_results schema seeded."""
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    # Initialize schema via the canonical Database constructor (which
    # runs create_tables() in __init__).
    from src.units.db.database import Database

    Database(str(db_path))
    return db_path


def _insert_backtest(db_path, **fields):
    """Insert one row into backtest_results.

    Defaults give a sensible "good run" so tests can override only the
    metric they care about.
    """
    defaults = {
        "run_date": "2026-05-01",
        "strategy_version": "vwap_v1",
        "start_date": "2025-05-01",
        "end_date": "2026-05-01",
        "total_trades": 100,
        "winning_trades": 55,
        "losing_trades": 45,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "expectancy": 0.05,
        "max_drawdown": 200.0,
        "max_drawdown_pct": 0.05,
        "sharpe_ratio": 1.2,
        "total_pnl": 1000.0,
        "total_pnl_pct": 0.10,
        "avg_win": 30.0,
        "avg_loss": 15.0,
        "largest_win": 200.0,
        "largest_loss": 100.0,
        "config": "{}",
        "created_at": "2026-05-01T12:00:00",
    }
    defaults.update(fields)
    conn = sqlite3.connect(str(db_path))
    try:
        cols = ", ".join(defaults.keys())
        placeholders = ", ".join(["?"] * len(defaults))
        conn.execute(
            f"INSERT INTO backtest_results ({cols}) VALUES ({placeholders})",
            list(defaults.values()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# data_loaders.backtest_history_for
# ---------------------------------------------------------------------------


class TestBacktestHistoryFor:
    def test_returns_only_matching_strategy(self, tmp_journal):
        from src.units.ui import data_loaders

        _insert_backtest(tmp_journal, strategy_version="vwap_v1",
                         created_at="2026-05-01T10:00:00")
        _insert_backtest(tmp_journal, strategy_version="vwap_v2",
                         created_at="2026-05-02T10:00:00")
        _insert_backtest(tmp_journal, strategy_version="turtle_soup_v1",
                         created_at="2026-05-03T10:00:00")

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            rows = data_loaders.backtest_history_for("vwap_v1", n=10)

        assert len(rows) == 1
        assert rows[0]["strategy_version"] == "vwap_v1"

    def test_newest_first_ordering(self, tmp_journal):
        from src.units.ui import data_loaders

        # Three runs of vwap_v2 across three days.
        for i, ts in enumerate(["2026-05-01T10:00:00",
                                "2026-05-03T10:00:00",
                                "2026-05-02T10:00:00"]):
            _insert_backtest(tmp_journal, strategy_version="vwap_v2",
                             created_at=ts, win_rate=0.40 + i * 0.05)

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            rows = data_loaders.backtest_history_for("vwap_v2", n=10)

        # Newest first.
        assert [r["created_at"] for r in rows] == [
            "2026-05-03T10:00:00",
            "2026-05-02T10:00:00",
            "2026-05-01T10:00:00",
        ]

    def test_respects_limit(self, tmp_journal):
        from src.units.ui import data_loaders

        for i in range(10):
            _insert_backtest(tmp_journal, strategy_version="vwap_v3",
                             created_at=f"2026-05-{i+1:02d}T10:00:00")

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            rows = data_loaders.backtest_history_for("vwap_v3", n=3)

        assert len(rows) == 3

    def test_returns_empty_for_unknown_strategy(self, tmp_journal):
        from src.units.ui import data_loaders

        _insert_backtest(tmp_journal, strategy_version="vwap_v1")
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            assert data_loaders.backtest_history_for("not_a_strategy") == []

    def test_returns_empty_for_blank_strategy(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            assert data_loaders.backtest_history_for("") == []

    def test_returns_empty_when_db_missing(self, tmp_path):
        from src.units.ui import data_loaders

        missing = tmp_path / "no" / "such.db"
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(missing)):
            assert data_loaders.backtest_history_for("vwap_v1") == []

    def test_clamps_invalid_n_to_default(self, tmp_journal):
        from src.units.ui import data_loaders

        for i in range(8):
            _insert_backtest(tmp_journal, strategy_version="vwap_v4",
                             created_at=f"2026-05-{i+1:02d}T10:00:00")

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            # Bad N falls back to default (5).
            assert len(data_loaders.backtest_history_for(
                "vwap_v4", n="not-an-int")  # type: ignore[arg-type]
            ) == 5


# ---------------------------------------------------------------------------
# data_loaders.list_backtest_strategies
# ---------------------------------------------------------------------------


class TestListBacktestStrategies:
    def test_returns_distinct_sorted(self, tmp_journal):
        from src.units.ui import data_loaders

        # Mix of strategies, with duplicates.
        for sv in ["vwap_v1", "vwap_v2", "vwap_v1", "turtle_v1"]:
            _insert_backtest(tmp_journal, strategy_version=sv)

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            names = data_loaders.list_backtest_strategies()

        assert names == ["turtle_v1", "vwap_v1", "vwap_v2"]

    def test_drops_null_and_blank(self, tmp_journal):
        from src.units.ui import data_loaders

        _insert_backtest(tmp_journal, strategy_version="vwap_v1")
        _insert_backtest(tmp_journal, strategy_version="")
        _insert_backtest(tmp_journal, strategy_version=None)

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            assert data_loaders.list_backtest_strategies() == ["vwap_v1"]

    def test_returns_empty_when_db_missing(self, tmp_path):
        from src.units.ui import data_loaders

        missing = tmp_path / "no.db"
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(missing)):
            assert data_loaders.list_backtest_strategies() == []


# ---------------------------------------------------------------------------
# processor.render_backtest_history_collapsable
# ---------------------------------------------------------------------------


def _row(**overrides):
    base = {
        "id": 1,
        "run_date": "2026-05-01",
        "strategy_version": "vwap_v1",
        "start_date": "2025-05-01",
        "end_date": "2026-05-01",
        "total_trades": 100,
        "winning_trades": 55,
        "losing_trades": 45,
        "win_rate": 0.55,
        "profit_factor": 1.5,
        "expectancy": 0.05,
        "max_drawdown": 200.0,
        "max_drawdown_pct": 0.05,
        "sharpe_ratio": 1.2,
        "total_pnl": 1000.0,
        "total_pnl_pct": 0.10,
        "avg_win": 30.0,
        "avg_loss": 15.0,
        "largest_win": 200.0,
        "largest_loss": 100.0,
        "created_at": "2026-05-01T12:00:00",
    }
    base.update(overrides)
    return base


class TestRenderBacktestHistory:
    def test_empty_renders_friendly_message(self):
        from src.units.ui.processor import render_backtest_history_collapsable

        out = render_backtest_history_collapsable([], "vwap_v1")
        assert "No backtest history" in out
        assert "vwap_v1" in out

    def test_single_run_no_delta_indicators(self):
        from src.units.ui.processor import render_backtest_history_collapsable

        out = render_backtest_history_collapsable([_row()], "vwap_v1")
        # The 🆕 LATEST marker is present.
        assert "🆕 LATEST" in out
        # No 📈 / 📉 indicators with metric labels (only one run).
        # Note: the body itself contains an "📈 Expectancy:" label —
        # exclude that by checking the trend-arrow tag patterns.
        assert "📈WR" not in out
        assert "📉WR" not in out

    def test_delta_up_arrow_when_latest_better(self):
        """Latest run has higher win_rate than prior → 📈WR on summary."""
        from src.units.ui.processor import render_backtest_history_collapsable

        latest = _row(created_at="2026-05-02T10:00:00", win_rate=0.65)
        prior = _row(created_at="2026-05-01T10:00:00", win_rate=0.55)
        out = render_backtest_history_collapsable([latest, prior], "vwap_v1")
        assert "📈WR" in out

    def test_delta_down_arrow_when_latest_worse(self):
        """Latest run has lower win_rate than prior → 📉WR."""
        from src.units.ui.processor import render_backtest_history_collapsable

        latest = _row(created_at="2026-05-02T10:00:00", win_rate=0.45)
        prior = _row(created_at="2026-05-01T10:00:00", win_rate=0.55)
        out = render_backtest_history_collapsable([latest, prior], "vwap_v1")
        assert "📉WR" in out

    def test_max_drawdown_pct_sign_inverted(self):
        """For max_drawdown_pct, lower is better. Latest 0.10 vs prior
        0.05 → DD% got worse → 📉DD%."""
        from src.units.ui.processor import render_backtest_history_collapsable

        latest = _row(created_at="2026-05-02T10:00:00", max_drawdown_pct=0.10)
        prior = _row(created_at="2026-05-01T10:00:00", max_drawdown_pct=0.05)
        out = render_backtest_history_collapsable([latest, prior], "vwap_v1")
        assert "📉DD%" in out

    def test_max_drawdown_pct_improved(self):
        """Latest 0.03 vs prior 0.05 → DD% improved → 📈DD%."""
        from src.units.ui.processor import render_backtest_history_collapsable

        latest = _row(created_at="2026-05-02T10:00:00", max_drawdown_pct=0.03)
        prior = _row(created_at="2026-05-01T10:00:00", max_drawdown_pct=0.05)
        out = render_backtest_history_collapsable([latest, prior], "vwap_v1")
        assert "📈DD%" in out

    def test_run_count_in_header(self):
        from src.units.ui.processor import render_backtest_history_collapsable

        rows = [_row(id=i, created_at=f"2026-05-{i+1:02d}T10:00:00")
                for i in range(3)]
        out = render_backtest_history_collapsable(rows, "vwap_v1")
        assert "3 run(s)" in out


# ---------------------------------------------------------------------------
# cmd_latest_backtest — back-compat + new arg path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_latest_backtest_no_args_unchanged():
    """Back-compat: no-args path still calls latest_backtests_per_model."""
    from src.bot.telegram_query_bot import cmd_latest_backtest, BACKTEST_STATUS

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = []

    # Force "no active backtest" branch so the handler reads from DB.
    BACKTEST_STATUS["state"] = "idle"

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.latest_backtests_per_model",
               return_value=[]) as mock_latest, \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for") as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    # No-arg path uses latest_backtests_per_model, NOT the new history loader.
    mock_latest.assert_called_once_with()
    mock_history.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_latest_backtest_with_strategy_calls_history():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["vwap_v1"]

    fake_row = _row()
    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for",
               return_value=[fake_row]) as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    mock_history.assert_called_once_with("vwap_v1", n=5)
    # HTML reply because the new path uses render_backtest_history_collapsable.
    _, kwargs = fake_update.message.reply_text.call_args
    assert kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_cmd_latest_backtest_with_strategy_and_n():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["vwap_v1", "12"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for",
               return_value=[_row()]) as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    mock_history.assert_called_once_with("vwap_v1", n=12)


@pytest.mark.asyncio
async def test_cmd_latest_backtest_clamps_n_to_20():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["vwap_v1", "999"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for",
               return_value=[_row()]) as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    mock_history.assert_called_once_with("vwap_v1", n=20)


@pytest.mark.asyncio
async def test_cmd_latest_backtest_rejects_non_integer_n():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["vwap_v1", "abc"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for") as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    mock_history.assert_not_called()
    args, _ = fake_update.message.reply_text.call_args
    assert "Usage" in args[0]


@pytest.mark.asyncio
async def test_cmd_latest_backtest_unknown_strategy_lists_available():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["does_not_exist"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for",
               return_value=[]), \
         patch("src.bot.telegram_query_bot.dl.list_backtest_strategies",
               return_value=["vwap_v1", "turtle_v1"]):
        await cmd_latest_backtest(fake_update, fake_context)

    args, _ = fake_update.message.reply_text.call_args
    assert "No backtest history" in args[0]
    assert "Available" in args[0]
    assert "vwap_v1" in args[0]


@pytest.mark.asyncio
async def test_cmd_latest_backtest_skips_when_unauthorised():
    from src.bot.telegram_query_bot import cmd_latest_backtest

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["vwap_v1"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=False), \
         patch("src.bot.telegram_query_bot.dl.backtest_history_for") as mock_history:
        await cmd_latest_backtest(fake_update, fake_context)

    mock_history.assert_not_called()
    fake_update.message.reply_text.assert_not_called()
