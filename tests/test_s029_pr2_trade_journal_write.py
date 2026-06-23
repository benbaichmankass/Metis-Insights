"""Regression: ``execute_pkg`` must write a row to ``trade_journal.db``
after a successful exchange submission.

Pre-fix (architecture-audit-2026-05-02 § P0-2): only smoke tests wrote
to the journal (via ``Coordinator._log_smoke_to_journal``). Live
``execute_pkg`` returned the trade_id and exited — leaving the journal
empty for real trades, which made ``/last5``, ``/strategies``, and the
hourly report's "Strategies (today)" view silently incomplete.

Post-fix: ``_log_trade_to_journal`` runs after a successful
``_submit_order`` call. Status starts ``open``; the close path (S-030
monitor loop) will update via ``Database.update_trade``.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.execute import execute_pkg, _log_trade_to_journal


@pytest.fixture()
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    return db_path


def _stub_bybit_client():
    class _StubBybit:
        def place_order(self, **kwargs):
            return {"retCode": 0, "result": {"orderId": "BYBIT-FAKE-12345"}}

        def get_wallet_balance(self, **kwargs):
            return {"result": {"list": [{"coin": [{"usdValue": "10000"}]}]}}

    return _StubBybit()


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


def _read_trades(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM trades ORDER BY id"))
    conn.close()
    return rows


class TestLiveSubmissionWritesTradeRow:
    def test_live_trade_lands_one_row_with_correct_fields(self, tmp_journal):
        execute_pkg(
            _pkg(),
            _account_cfg(),
            exchange_client=_stub_bybit_client(),
            balance_usdt=10_000.0,
            dry_run=False,
        )

        rows = _read_trades(tmp_journal)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "BTCUSDT"
        assert row["direction"] == "short"
        assert row["entry_price"] == 50_000.0
        assert row["stop_loss"] == 50_500.0
        assert row["take_profit_1"] == 49_000.0
        assert row["status"] == "open"
        assert row["is_backtest"] == 0
        assert row["strategy_name"] == "vwap"
        assert row["account_id"] == "bybit_2"
        assert row["setup_type"] == "vwap"
        assert row["entry_reason"] == "vwap mean-revert short"
        # notes is a JSON blob with trade_id + confidence + signal_logic
        import json
        notes = json.loads(row["notes"])
        assert notes["trade_id"] == "BYBIT-FAKE-12345"
        assert notes["is_dry"] is False
        assert notes["confidence"] == pytest.approx(0.72)

    def test_per_account_attribution_correct(self, tmp_journal):
        execute_pkg(
            _pkg(strategy="turtle_soup"),
            _account_cfg(name="bybit_1"),
            exchange_client=_stub_bybit_client(),
            balance_usdt=10_000.0,
            dry_run=False,
        )
        execute_pkg(
            _pkg(strategy="vwap"),
            _account_cfg(name="bybit_2"),
            exchange_client=_stub_bybit_client(),
            balance_usdt=10_000.0,
            dry_run=False,
        )
        rows = _read_trades(tmp_journal)
        assert len(rows) == 2
        attribution = {(r["account_id"], r["strategy_name"]) for r in rows}
        assert attribution == {("bybit_1", "turtle_soup"), ("bybit_2", "vwap")}


class TestJournalFailureDoesNotCrashOrder:
    """A journal write failure must NEVER unwind the order. The trade
    has already been placed at the exchange — losing the journal row
    is a reporting glitch, not an order-cancel signal."""

    def test_db_unwritable_does_not_raise(self, tmp_journal, monkeypatch):
        # Point at a path that can't be created (a non-existent parent
        # under a file rather than a dir) — Database.create_tables
        # should fail.
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_journal / "not_a_dir" / "x.db"))

        trade_id = execute_pkg(
            _pkg(),
            _account_cfg(),
            exchange_client=_stub_bybit_client(),
            balance_usdt=10_000.0,
            dry_run=False,
        )
        # Order returned its trade_id even though journal write blew up.
        assert trade_id == "BYBIT-FAKE-12345"

    def test_helper_returns_false_on_error(self, tmp_journal, monkeypatch):
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_journal / "missing" / "x.db"))
        result = _log_trade_to_journal(
            _pkg(),
            _account_cfg(),
            {"qty": 0.1, "symbol": "BTCUSDT"},
            trade_id="TID-1",
            is_dry=False,
        )
        assert result is False


class TestNoWriteOnAlternatePaths:
    def test_dry_run_writes_a_labeled_rejection_row(self, tmp_journal):
        """BUG-049 fix (2026-06-23): a dry/shadow dispatch now writes exactly
        ONE non-live 'rejected' journal row (reason=dry_run_no_order_placed)
        instead of nothing. The old behaviour (no write) left the order package
        open/unlinked, so the reconciler mis-stamped it 'orphaned — never
        executed' at 5 min. The rejection row carries the order_package_id so
        the package is relabelled 'rejected', not orphaned; status='rejected'
        keeps it out of the operator's per-account PnL view (same as any other
        rejection), so it does not pollute PnL."""
        execute_pkg(
            _pkg(),
            _account_cfg(),
            exchange_client=_stub_bybit_client(),
            balance_usdt=10_000.0,
            dry_run=True,
        )
        rows = _read_trades(tmp_journal) if tmp_journal.exists() else []
        assert len(rows) == 1
        assert rows[0]["status"] == "rejected"

    def test_smoke_test_does_not_write_via_this_path(self, tmp_journal):
        """Smoke-test orders use ``_submit_test_order`` and are journaled
        by ``Coordinator._log_smoke_to_journal`` (separate writer).
        ``execute_pkg``'s journal write must skip them so they don't
        appear twice."""
        smoke_pkg = _pkg()
        smoke_pkg.meta = {"is_test": True, "smoke_id": "smoke-123"}

        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
        ) as journal_stub:
            try:
                execute_pkg(
                    smoke_pkg,
                    _account_cfg(),
                    exchange_client=_stub_bybit_client(),
                    balance_usdt=10_000.0,
                    dry_run=False,
                )
            except Exception:
                pass
            journal_stub.assert_not_called()


# ---------------------------------------------------------------------------
# WC-2: the smoke-journal insert must stamp account_class / is_demo so a smoke
# row on a PAPER account is not mis-classified as real_money (column defaults
# is_demo=0, account_class=NULL→real_money would otherwise leak it into
# real-money stats). No order package → no order_package_id.
# ---------------------------------------------------------------------------


def _smoke_trade_row(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM trades WHERE strategy_name = 'smoke_test' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r is not None else None
    finally:
        conn.close()


def test_smoke_journal_stamps_paper_account_class(tmp_path):
    """ib_paper is account_class:paper in config/accounts.yaml → the smoke row
    is stamped paper / is_demo=1, with no order_package_id."""
    from src.core.coordinator import _log_smoke_to_journal
    from tests.fixtures.real_schema_db import make_canonical_db

    db_path = tmp_path / "trade_journal.db"
    make_canonical_db(db_path)

    pkg = OrderPackage(
        strategy="smoke", symbol="MHG", direction="long",
        entry=6.40, sl=6.05, tp=7.03, confidence=0.5,
        meta={"test_qty": 1.0, "smoke_id": "smoke-paper"},
    )
    result = {"account_id": "ib_paper", "trade_id": "tx-p",
              "status": "dry_run", "reason": "ok"}
    assert _log_smoke_to_journal(pkg, result, db_path=str(db_path)) is True

    row = _smoke_trade_row(db_path)
    assert row is not None
    assert row["account_class"] == "paper"
    assert int(row["is_demo"]) == 1
    assert row["order_package_id"] is None


def test_smoke_journal_stamps_real_money_account_class(tmp_path):
    """bybit_2 is account_class:real_money → real_money / is_demo=0."""
    from src.core.coordinator import _log_smoke_to_journal
    from tests.fixtures.real_schema_db import make_canonical_db

    db_path = tmp_path / "trade_journal.db"
    make_canonical_db(db_path)

    pkg = OrderPackage(
        strategy="smoke", symbol="BTCUSDT", direction="long",
        entry=80_000.0, sl=79_000.0, tp=82_000.0, confidence=0.5,
        meta={"test_qty": 0.001, "smoke_id": "smoke-real"},
    )
    result = {"account_id": "bybit_2", "trade_id": "tx-r",
              "status": "dry_run", "reason": "ok"}
    assert _log_smoke_to_journal(pkg, result, db_path=str(db_path)) is True

    row = _smoke_trade_row(db_path)
    assert row is not None
    assert row["account_class"] == "real_money"
    assert int(row["is_demo"]) == 0
    assert row["order_package_id"] is None
