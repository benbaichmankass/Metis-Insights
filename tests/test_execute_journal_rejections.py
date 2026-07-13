"""Rejection-path observability for the executor.

CP-2026-05-03-14 follow-up to BUG-039: every refusal must land a row in
``trade_journal.db::trades`` so ``/last5``, hourly reports, and the
upcoming ``/packages`` command can attribute "0 trades placed" to a
specific cause (dry-mode, daily-loss cap, exchange retCode, etc.).

Three contracts under test:
1. ``_log_trade_to_journal`` accepts ``status`` and ``reason`` kwargs and
   produces a uniquely-keyed row regardless of trade_id presence.
2. ``log_rejection_to_journal`` is a defensive public wrapper used by
   the coordinator from its except blocks — it never raises.
3. The aggregator queries (``recent_trades_for``, ``account_last_trade``,
   ``get_today_pnl``) filter out ``rejected`` / ``exchange_rejected``
   rows so PnL views stay focused on real exchange submissions.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from src.core.coordinator import OrderPackage
from src.units.accounts.execute import (
    _log_trade_to_journal,
    log_rejection_to_journal,
)


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


def _read_trades(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM trades ORDER BY id"))
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# _log_trade_to_journal — refactored to accept status + reason
# ---------------------------------------------------------------------------


class TestLogTradeToJournalStatus:
    def test_default_status_is_open_for_back_compat(self, tmp_journal):
        # The pre-CP-13 contract: defaults preserve `status='open'` so
        # the success path keeps writing the same row shape.
        ok = _log_trade_to_journal(
            _pkg(),
            _account_cfg(),
            {"qty": 0.123, "symbol": "BTCUSDT"},
            trade_id="EXCH-OK-1",
            is_dry=False,
        )
        assert ok is True
        rows = _read_trades(tmp_journal)
        assert len(rows) == 1
        assert rows[0]["status"] == "open"
        # entry_reason untouched (no rejection prefix).
        assert rows[0]["entry_reason"] == "vwap mean-revert short"

    def test_rejected_status_writes_with_reason_and_synthesised_trade_id(
        self, tmp_journal
    ):
        ok = _log_trade_to_journal(
            _pkg(),
            _account_cfg(),
            {"qty": 0.0, "symbol": "BTCUSDT"},
            trade_id=None,
            is_dry=False,
            status="rejected",
            reason="account_mode_dry_run",
        )
        assert ok is True
        rows = _read_trades(tmp_journal)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "rejected"
        # entry_reason carries the structured token so plain renderers
        # surface the cause without parsing JSON.
        assert "REJECTED: account_mode_dry_run" in row["entry_reason"]
        assert "vwap mean-revert short" in row["entry_reason"]
        # notes JSON has the un-mangled token for structured aggregations.
        notes = json.loads(row["notes"])
        assert notes["reason"] == "account_mode_dry_run"
        # Synthesised trade_id is present and uniquely keyed.
        assert notes["trade_id"].startswith("rejected-")

    def test_exchange_rejected_status_writes_correctly(self, tmp_journal):
        ok = _log_trade_to_journal(
            _pkg(),
            _account_cfg(),
            {"qty": 0.025, "symbol": "BTCUSDT"},
            trade_id=None,
            is_dry=False,
            status="exchange_rejected",
            reason="RuntimeError: Order submission failed: retCode=110007 qty exceeds max",
        )
        assert ok is True
        rows = _read_trades(tmp_journal)
        assert rows[0]["status"] == "exchange_rejected"
        assert "EXCHANGE_REJECTED:" in rows[0]["entry_reason"]
        assert "retCode=110007" in rows[0]["entry_reason"]
        notes = json.loads(rows[0]["notes"])
        assert "retCode=110007" in notes["reason"]

    def test_failure_returns_false_and_does_not_raise(
        self, tmp_journal, monkeypatch
    ):
        # Best-effort contract: a journal failure during a rejection
        # log must not crash the failure-handling path.
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_journal / "no" / "such" / "x.db"))
        ok = _log_trade_to_journal(
            _pkg(),
            _account_cfg(),
            {"qty": 0.0, "symbol": "BTCUSDT"},
            trade_id=None,
            is_dry=False,
            status="rejected",
            reason="DAILY_LOSS_CAP",
        )
        assert ok is False


# ---------------------------------------------------------------------------
# log_rejection_to_journal — public wrapper for the coordinator
# ---------------------------------------------------------------------------


class TestLogRejectionToJournal:
    def test_writes_rejected_row_with_sized_qty(self, tmp_journal):
        ok = log_rejection_to_journal(
            _pkg(),
            _account_cfg(),
            reason="DAILY_LOSS_CAP",
            status="rejected",
            sized_qty=0.045,
        )
        assert ok is True
        rows = _read_trades(tmp_journal)
        assert len(rows) == 1
        assert rows[0]["status"] == "rejected"
        assert rows[0]["position_size"] == pytest.approx(0.045)
        notes = json.loads(rows[0]["notes"])
        assert notes["reason"] == "DAILY_LOSS_CAP"

    def test_writes_exchange_rejected_row_without_qty(self, tmp_journal):
        ok = log_rejection_to_journal(
            _pkg(),
            _account_cfg(),
            reason="MissingCredentialsError: api_key_env=BYBIT_KEY_X unset",
            status="exchange_rejected",
            sized_qty=None,
        )
        assert ok is True
        rows = _read_trades(tmp_journal)
        assert rows[0]["status"] == "exchange_rejected"
        # sized_qty=None resolves to 0.0 in the journal row.
        assert rows[0]["position_size"] == pytest.approx(0.0)

    def test_is_dry_defaults_false_in_notes(self, tmp_journal):
        # Back-compat: callers that don't pass is_dry get notes.is_dry=False.
        log_rejection_to_journal(
            _pkg(), _account_cfg(),
            reason="DAILY_LOSS_CAP", status="rejected", sized_qty=0.01,
        )
        rows = _read_trades(tmp_journal)
        notes = json.loads(rows[0]["notes"])
        assert notes["is_dry"] is False

    def test_is_dry_true_flows_to_notes_without_flipping_is_demo(self, tmp_journal):
        # BL-20260707-MGCTREND-REASON-MISMATCH: a genuine dry/shadow rejection
        # now records notes.is_dry=True so it agrees with a
        # 'dry_run_no_order_placed' reason — but the is_demo / account_class
        # paper-vs-real column is derived from account_cfg and MUST stay
        # unaffected (a real_money account stays is_demo=0).
        log_rejection_to_journal(
            _pkg(), _account_cfg(),  # no account_class → defaults real_money
            reason="dry_run_no_order_placed", status="rejected",
            sized_qty=0.01, is_dry=True,
        )
        rows = _read_trades(tmp_journal)
        notes = json.loads(rows[0]["notes"])
        assert notes["is_dry"] is True
        assert notes["reason"] == "dry_run_no_order_placed"
        # The money-classification column is NOT touched by the is_dry flag.
        assert rows[0]["is_demo"] in (0, None)
        assert (rows[0]["account_class"] or "real_money") == "real_money"

    def test_never_raises_even_when_underlying_helper_throws(self, tmp_journal):
        # Defensive contract: the wrapper catches everything so a
        # bug-in-helper can't unwind the failure-handling path.
        with patch(
            "src.units.accounts.execute._log_trade_to_journal",
            side_effect=RuntimeError("synthetic"),
        ):
            ok = log_rejection_to_journal(
                _pkg(),
                _account_cfg(),
                reason="DAILY_LOSS_CAP",
                status="rejected",
                sized_qty=0.01,
            )
        assert ok is False  # signalled-but-swallowed


# ---------------------------------------------------------------------------
# Aggregator filters — refusal rows must not pollute /last5 or PnL views
# ---------------------------------------------------------------------------


def _seed_mixed_rows(db_path):
    """Seed three rows: one open, one rejected, one exchange_rejected.

    Used by the aggregator-filter tests to assert refusal rows are
    excluded from operator-facing surfaces.
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


class TestAggregatorsExcludeRejections:
    def test_recent_trades_for_excludes_refusals(self, tmp_journal):
        from src.units.ui import data_loaders

        # The data_loaders module caches TRADE_JOURNAL_DB at import; force
        # it to honour the test env var by patching the module attribute.
        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_mixed_rows(tmp_journal)
            rows = data_loaders.recent_trades_for(
                {"account_id": "bybit_2"}, n=10
            )
        # Only the open row survives.
        assert len(rows) == 1
        assert rows[0]["status"] == "open"

    def test_account_last_trade_excludes_refusals(self, tmp_journal):
        from src.units.ui import data_loaders

        with patch.object(data_loaders, "TRADE_JOURNAL_DB", str(tmp_journal)):
            _seed_mixed_rows(tmp_journal)
            row = data_loaders.account_last_trade({"account_id": "bybit_2"})
        # The most-recent NON-rejection row is returned.
        assert row is not None
        assert row["status"] == "open"

    def test_get_today_pnl_count_excludes_refusals(self, tmp_journal):
        from src.units.ui.processor import get_today_pnl

        _seed_mixed_rows(tmp_journal)
        result = get_today_pnl(account_id="bybit_2")
        # Three rows seeded; only one is a real submission.
        assert result["trade_count"] == 1
