"""/packages bot command — end-to-end thin-shell coverage.

CP-2026-05-03-15. Three contracts under test:

1. ``data_loaders.recent_rejections`` returns ONLY rejection rows from
   ``trade_journal.db::trades`` (status='rejected' or 'exchange_rejected').
   This is the inverse of the rejection filters CP-2026-05-03-14
   added to the success-path surfaces.
2. ``data_loaders.open_order_packages`` returns ONLY packages still in
   status='open' with no ``linked_trade_id`` — the "stuck" set.
3. ``processor.render_packages_collapsable`` produces a single HTML
   message that names the rejection reason token prominently and
   handles the empty / refusal-only / open-only / both shapes.
4. ``cmd_packages`` is a thin shell — parses N, calls the two loaders,
   passes their output to the renderer, sends one Telegram message.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.execute import (
    _log_trade_to_journal,
    log_rejection_to_journal,
)


# ---------------------------------------------------------------------------
# Fixtures — the rejection-row seeders mirror test_execute_journal_rejections.
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _pkg(strategy="vwap", **overrides):
    base = dict(
        strategy=strategy,
        symbol="BTCUSDT",
        direction="short",
        entry=50_000.0,
        sl=50_500.0,
        tp=49_000.0,
        confidence=0.72,
        meta={"strategy_name": strategy, "entry_reason": "vwap mean-revert short"},
    )
    base.update(overrides)
    return OrderPackage(**base)


def _account_cfg(name="bybit_2"):
    return {
        "account_id": name,
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_2",
        "risk_pct": 0.01,
        "min_balance_usd": 50.0,
        "min_qty": 0.001,
        "qty_precision": 3,
    }


def _seed_one_open_two_rejections(db_path):
    """Seed one open trade + one rejected + one exchange_rejected.

    The order matters for ``recent_rejections`` ordering assertions —
    creation order = id order = newest-first descending.
    """
    log_rejection_to_journal(
        _pkg(strategy="vwap"),
        _account_cfg("bybit_2"),
        reason="DAILY_LOSS_CAP",
        status="rejected",
        sized_qty=0.04,
    )
    log_rejection_to_journal(
        _pkg(strategy="vwap"),
        _account_cfg("bybit_2"),
        reason="RuntimeError: Order submission failed: retCode=110007",
        status="exchange_rejected",
        sized_qty=0.04,
    )
    _log_trade_to_journal(
        _pkg(strategy="vwap"),
        _account_cfg("bybit_2"),
        {"qty": 0.04, "symbol": "BTCUSDT"},
        trade_id="EXCH-OK-1",
        is_dry=False,
    )


def _seed_rejected_too_small(db_path):
    """Insert a single ``status='rejected_too_small'`` row.

    Mirrors the shape produced by ``scripts/smoke_test_trade.py`` when
    Bybit returns ``ErrCode: 10001`` (qty below minimum). Pre-PR these
    rows polluted ``/last5`` because the rejection-row filter only
    excluded ``'rejected'`` / ``'exchange_rejected'``.
    """
    # Make sure the trades table exists before raw INSERT (Database()
    # __init__ runs create_tables() — same pattern as
    # _seed_open_packages above).
    from src.units.db.database import Database

    Database(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price,"
            " stop_loss, take_profit_1, position_size, setup_type,"
            " entry_reason, status, strategy_name, account_id, is_backtest,"
            " created_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-01T22:00:00",
                "BTCUSDT", "long",
                70000.0, 68600.0, 71400.0, 0.0001,
                "smoke_test", "live-plumbing smoke",
                "rejected_too_small",
                "smoke_test", "bybit_2", 0,
                "2026-05-01T22:00:00",
                '{"smoke_id": "test", "trade_id": "rejected_too_small:foo"}',
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_open_packages(db_path, *, n_open=2, n_closed=1, n_open_with_link=1):
    """Seed N open / N closed / N open-but-already-linked packages.

    ``open_order_packages`` should return only the bare-open subset
    (status='open' AND linked_trade_id IS NULL).
    """
    from src.units.db.database import Database

    # Database.__init__ runs create_tables() so the schema is ready.
    db = Database(str(db_path))

    for i in range(n_open):
        db.insert_order_package({
            "order_package_id": f"pkg-open-{i:02d}",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "long" if i % 2 == 0 else "short",
            "entry": 50_000.0 + i,
            "sl": 49_900.0,
            "tp": 50_100.0,
            "confidence": 0.5 + i * 0.1,
            "status": "open",
        })
    for i in range(n_closed):
        db.insert_order_package({
            "order_package_id": f"pkg-closed-{i:02d}",
            "strategy_name": "turtle_soup",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 51_000.0,
            "sl": 51_500.0,
            "tp": 50_000.0,
            "status": "closed",
            "close_reason": "tp_hit",
        })
    for i in range(n_open_with_link):
        db.insert_order_package({
            "order_package_id": f"pkg-linked-{i:02d}",
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry": 50_500.0,
            "sl": 50_000.0,
            "tp": 51_000.0,
            "status": "open",
            "linked_trade_id": 1,  # already routed to a real trade
        })


# ---------------------------------------------------------------------------
# data_loaders.recent_rejections — surfaces what /last5 hides
# ---------------------------------------------------------------------------


class TestRecentRejections:
    def test_returns_only_rejection_rows(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_one_open_two_rejections(tmp_journal)
            rows = data_loaders.recent_rejections(n=10)

        assert len(rows) == 2
        statuses = {r["status"] for r in rows}
        assert statuses == {"rejected", "exchange_rejected"}

    def test_newest_first_ordering(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_one_open_two_rejections(tmp_journal)
            rows = data_loaders.recent_rejections(n=10)

        # Seed wrote 'rejected' first then 'exchange_rejected' second; the
        # second has the higher id so it comes out first when sorted DESC.
        assert rows[0]["status"] == "exchange_rejected"
        assert rows[1]["status"] == "rejected"

    def test_respects_limit(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_one_open_two_rejections(tmp_journal)
            rows = data_loaders.recent_rejections(n=1)

        assert len(rows) == 1

    def test_returns_empty_when_db_missing(self, tmp_path):
        from src.units.ui import data_loaders

        missing = tmp_path / "no" / "such" / "x.db"
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(missing)):
            assert data_loaders.recent_rejections(n=5) == []

    def test_clamps_invalid_n_to_default(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_one_open_two_rejections(tmp_journal)
            # Bad input must not crash; default is 10 and seed has 2 rows.
            assert len(data_loaders.recent_rejections(n="not-an-int")) == 2  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# rejected_too_small — added to REFUSAL_STATUSES post-CP-16 follow-up.
# Symmetry: excluded from /last5 (recent_trades_for) AND included in
# /packages (recent_rejections), same as the other two refusal tokens.
# ---------------------------------------------------------------------------


class TestRejectedTooSmallStatus:
    def test_recent_trades_for_excludes_rejected_too_small(self, tmp_journal):
        """Operator hit: smoke_test rows with status='rejected_too_small'
        were polluting /last5 because the pre-PR filter only matched
        'rejected' / 'exchange_rejected'."""
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_rejected_too_small(tmp_journal)
            rows = data_loaders.recent_trades_for(
                {"account_id": "bybit_2"}, n=10
            )

        assert rows == [], (
            "rejected_too_small must be filtered from /last5 — operator's "
            "smoke-test rows from 2026-05-01 were leaking through."
        )

    def test_recent_rejections_includes_rejected_too_small(self, tmp_journal):
        """The inverse filter (/packages) must SHOW rejected_too_small —
        these are real rejections, just from the smoke-test plumbing
        rather than a strategy signal."""
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_rejected_too_small(tmp_journal)
            rows = data_loaders.recent_rejections(n=10)

        assert len(rows) == 1
        assert rows[0]["status"] == "rejected_too_small"

    def test_account_last_trade_excludes_rejected_too_small(self, tmp_journal):
        """Mirror of recent_trades_for at the single-row helper."""
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_rejected_too_small(tmp_journal)
            row = data_loaders.account_last_trade({"account_id": "bybit_2"})

        assert row is None, (
            "account_last_trade must skip rejected_too_small rows. The "
            "smoke-test row should not surface as 'last live trade'."
        )

    def test_refusal_statuses_constant_lists_all_four(self):
        """The published constant should match the SQL predicates so
        any future symmetric aggregator can opt in via REFUSAL_STATUSES
        without duplicating the literal list. Four sibling tokens after
        the CP-17 ghost-trade cleanup: rejected, exchange_rejected,
        rejected_too_small, orphaned."""
        from src.units.ui.data_loaders import REFUSAL_STATUSES

        assert set(REFUSAL_STATUSES) == {
            "rejected", "exchange_rejected",
            "rejected_too_small", "orphaned",
        }


def _seed_orphaned_row(db_path):
    """Insert a single ``status='orphaned'`` row.

    Mirrors the pre-#357 ghost-trade shape that the operator backfills
    via ``notebooks/operator/cleanup_ghost_trades.ipynb`` — a row that
    was logged ``status='open'`` BEFORE the exchange call returned,
    then orphaned when the exchange refused the order.
    """
    from src.units.db.database import Database

    Database(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price,"
            " stop_loss, take_profit_1, position_size, setup_type,"
            " entry_reason, status, strategy_name, account_id, is_backtest,"
            " created_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-02T22:56:50",
                "BTCUSDT", "short",
                78800.0, 78923.27, 78652.54, 0.007,
                None, "vwap signal",
                "orphaned",
                "vwap", "bybit_2", 0,
                "2026-05-02T22:56:50",
                '{"trade_id": "1df8286b-0525-4fe7-ace9-e9f884db9726",'
                ' "is_dry": false, "confidence": 0.0,'
                ' "orphaned_at": "2026-05-03T20:00:00"}',
            ),
        )
        conn.commit()
    finally:
        conn.close()


class TestOrphanedStatus:
    def test_recent_trades_for_excludes_orphaned(self, tmp_journal):
        """The ghost-trade backfill (CP-17) marks pre-#357 open rows
        as ``orphaned`` so they stop polluting /last5."""
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_orphaned_row(tmp_journal)
            rows = data_loaders.recent_trades_for(
                {"account_id": "bybit_2"}, n=10
            )

        assert rows == [], (
            "Orphaned ghost-trade rows must be filtered from /last5 — "
            "they don't correspond to actual exchange positions."
        )

    def test_recent_rejections_includes_orphaned(self, tmp_journal):
        """The inverse filter (/packages) must show orphaned rows so
        the operator can audit what got backfilled."""
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_orphaned_row(tmp_journal)
            rows = data_loaders.recent_rejections(n=10)

        assert len(rows) == 1
        assert rows[0]["status"] == "orphaned"


# ---------------------------------------------------------------------------
# data_loaders.open_order_packages — surfaces "stuck" packages
# ---------------------------------------------------------------------------


class TestOpenOrderPackages:
    def test_returns_only_open_with_no_link(self, tmp_journal):
        from src.units.ui import data_loaders

        _seed_open_packages(tmp_journal, n_open=3, n_closed=2, n_open_with_link=2)
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            rows = data_loaders.open_order_packages(n=10)

        # 3 bare-open, 2 closed, 2 open-but-already-linked.
        # Only the 3 bare-open rows survive.
        assert len(rows) == 3
        for r in rows:
            assert r["status"] == "open"
            assert r["linked_trade_id"] is None

    def test_respects_limit(self, tmp_journal):
        from src.units.ui import data_loaders

        _seed_open_packages(tmp_journal, n_open=5, n_closed=0, n_open_with_link=0)
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            rows = data_loaders.open_order_packages(n=2)

        assert len(rows) == 2

    def test_returns_empty_when_db_missing(self, tmp_path):
        from src.units.ui import data_loaders

        missing = tmp_path / "no.db"
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(missing)):
            assert data_loaders.open_order_packages(n=5) == []


# ---------------------------------------------------------------------------
# processor.render_packages_collapsable — single-message HTML envelope
# ---------------------------------------------------------------------------


class TestRenderPackagesCollapsable:
    def test_empty_returns_friendly_message(self):
        from src.units.ui.processor import render_packages_collapsable

        out = render_packages_collapsable([], [])
        assert "No refusals" in out
        assert "No refusals + no stuck packages" in out

    def test_refusal_only_surfaces_reason_token(self):
        from src.units.ui.processor import render_packages_collapsable

        out = render_packages_collapsable(
            [{
                "id": 1,
                "timestamp": "2026-05-03T16:00:00",
                "symbol": "BTCUSDT",
                "direction": "short",
                "entry_price": 50_000,
                "stop_loss": 50_500,
                "take_profit_1": 49_000,
                "position_size": 0.04,
                "entry_reason": "REJECTED: DAILY_LOSS_CAP — vwap mean-revert short",
                "status": "rejected",
                "strategy_name": "vwap",
                "account_id": "bybit_2",
                "created_at": "2026-05-03T16:00:00",
            }],
            [],
        )
        # The bare token (post-prefix-strip) appears in the summary line.
        assert "DAILY_LOSS_CAP" in out
        # The status badge comes from the rejection branch.
        assert "🛑" in out
        # No "stuck packages" sub-header when open list is empty.
        assert "stuck packages" not in out.lower() or "No refusals" in out

    def test_exchange_rejected_uses_different_badge(self):
        from src.units.ui.processor import render_packages_collapsable

        out = render_packages_collapsable(
            [{
                "id": 2,
                "symbol": "BTCUSDT",
                "direction": "short",
                "entry_price": 50_000,
                "stop_loss": 50_500,
                "take_profit_1": 49_000,
                "position_size": 0.04,
                "entry_reason": "EXCHANGE_REJECTED: RuntimeError: retCode=110007",
                "status": "exchange_rejected",
                "strategy_name": "vwap",
                "account_id": "bybit_2",
                "created_at": "2026-05-03T16:00:00",
                "timestamp": "2026-05-03T16:00:00",
            }],
            [],
        )
        # Different badge for exchange-side errors so the operator
        # distinguishes "RiskManager said no" from "exchange said no".
        assert "💥" in out
        assert "retCode=110007" in out

    def test_open_packages_only_shows_stuck_section(self):
        from src.units.ui.processor import render_packages_collapsable

        out = render_packages_collapsable(
            [],
            [{
                "order_package_id": "pkg-abc12345def",
                "strategy_name": "vwap",
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry": 50_000.0,
                "sl": 49_900.0,
                "tp": 50_100.0,
                "confidence": 0.85,
                "status": "open",
                "linked_trade_id": None,
                "updated_at": "2026-05-03T16:00:00",
                "created_at": "2026-05-03T15:55:00",
            }],
        )
        # Stuck-package section header.
        assert "stuck" in out.lower() or "open package" in out.lower()
        assert "vwap" in out
        # Short-id (last 12 chars) appears in the summary line.
        assert "abc12345def" in out

    def test_both_lists_render_in_one_message(self):
        from src.units.ui.processor import render_packages_collapsable

        out = render_packages_collapsable(
            [{
                "id": 1,
                "symbol": "BTCUSDT",
                "direction": "short",
                "entry_price": 50_000,
                "stop_loss": 50_500,
                "take_profit_1": 49_000,
                "position_size": 0.04,
                "entry_reason": "REJECTED: POSITION_SIZE_CAP",
                "status": "rejected",
                "strategy_name": "vwap",
                "account_id": "bybit_2",
                "created_at": "2026-05-03T16:00:00",
                "timestamp": "2026-05-03T16:00:00",
            }],
            [{
                "order_package_id": "pkg-001",
                "strategy_name": "vwap",
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry": 50_000.0,
                "sl": 49_900.0,
                "tp": 50_100.0,
                "confidence": 0.5,
                "status": "open",
                "linked_trade_id": None,
                "updated_at": "2026-05-03T16:00:00",
                "created_at": "2026-05-03T16:00:00",
            }],
        )
        # Both reasons + the package id are present.
        assert "POSITION_SIZE_CAP" in out
        assert "pkg-001" in out
        # Header summary names both counts.
        assert "1 refusal" in out
        assert "1 open package" in out


# ---------------------------------------------------------------------------
# cmd_packages — thin-shell handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmd_packages_calls_loaders_and_renders():
    from src.bot.telegram_query_bot import cmd_packages

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = []

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.recent_rejections",
               return_value=[]) as mock_rej, \
         patch("src.bot.telegram_query_bot.dl.open_order_packages",
               return_value=[]) as mock_pkg:
        await cmd_packages(fake_update, fake_context)

    mock_rej.assert_called_once_with(n=10)
    mock_pkg.assert_called_once_with(n=10)
    # Exactly one Telegram reply with HTML mode.
    fake_update.message.reply_text.assert_called_once()
    _, kwargs = fake_update.message.reply_text.call_args
    assert kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_cmd_packages_parses_n_argument():
    from src.bot.telegram_query_bot import cmd_packages

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["25"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.recent_rejections",
               return_value=[]) as mock_rej, \
         patch("src.bot.telegram_query_bot.dl.open_order_packages",
               return_value=[]) as mock_pkg:
        await cmd_packages(fake_update, fake_context)

    mock_rej.assert_called_once_with(n=25)
    mock_pkg.assert_called_once_with(n=25)


@pytest.mark.asyncio
async def test_cmd_packages_clamps_n_to_50():
    from src.bot.telegram_query_bot import cmd_packages

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["999"]  # absurdly high; clamp to 50.

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.recent_rejections",
               return_value=[]) as mock_rej, \
         patch("src.bot.telegram_query_bot.dl.open_order_packages",
               return_value=[]):
        await cmd_packages(fake_update, fake_context)

    mock_rej.assert_called_once_with(n=50)


@pytest.mark.asyncio
async def test_cmd_packages_rejects_non_integer_arg():
    from src.bot.telegram_query_bot import cmd_packages

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = ["abc"]

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=True), \
         patch("src.bot.telegram_query_bot.dl.recent_rejections") as mock_rej:
        await cmd_packages(fake_update, fake_context)

    # Loaders must not be called when arg parsing fails.
    mock_rej.assert_not_called()
    # Operator gets a usage message.
    args, _ = fake_update.message.reply_text.call_args
    assert "Usage" in args[0]


@pytest.mark.asyncio
async def test_cmd_packages_skips_when_unauthorised():
    from src.bot.telegram_query_bot import cmd_packages

    fake_update = MagicMock()
    fake_update.message = MagicMock()
    fake_update.message.reply_text = AsyncMock()
    fake_context = MagicMock()
    fake_context.args = []

    with patch("src.bot.telegram_query_bot.is_authorised", return_value=False), \
         patch("src.bot.telegram_query_bot.dl.recent_rejections") as mock_rej:
        await cmd_packages(fake_update, fake_context)

    mock_rej.assert_not_called()
    fake_update.message.reply_text.assert_not_called()
