"""Tests for the S-022 PR4 silent-except sweep.

Each test simulates the underlying error condition and asserts that
``outcomes.report()`` is called with the right action/level. Where
possible we intercept at the outcomes layer rather than mocking
internals — that way the test is pinned to the operator-visible
behavior (a Telegram-eligible alert), not the implementation path.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    sys.modules.setdefault(_mod, MagicMock())


@pytest.fixture
def captured_reports():
    """Patch outcomes.report and capture every call across the codebase."""
    captured = []

    def fake_report(action, status, *, level, reason=None, **ctx):
        captured.append({
            "action": action,
            "status": status,
            "level": getattr(level, "value", level),
            "reason": reason,
            "ctx": ctx,
        })
        return {"action": action, "status": status}

    with patch("src.runtime.outcomes.report", side_effect=fake_report):
        yield captured


# ---------------------------------------------------------------------------
# risk_counters: positions fetch + daily-loss DB failure
# ---------------------------------------------------------------------------


def test_positions_fetch_failure_reports_warn(captured_reports):
    from src.runtime.risk_counters import inject_runtime_counters

    bad_client = MagicMock()
    bad_client.get_positions.side_effect = RuntimeError("exchange 503")

    inject_runtime_counters({}, bad_client)

    matches = [r for r in captured_reports
               if r["action"] == "risk_counters" and r["status"] == "positions_fetch_failed"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"
    assert "exchange 503" in (matches[0]["reason"] or "")


def test_daily_loss_db_failure_reports_warn(captured_reports, tmp_path):
    from src.runtime.risk_counters import inject_runtime_counters

    # Point at a bad path so sqlite3.connect raises
    bogus_db = tmp_path / "does-not-exist-dir" / "tj.db"
    inject_runtime_counters({"TRADE_JOURNAL_DB": str(bogus_db)}, exchange_client=None)

    matches = [r for r in captured_reports
               if r["status"] == "daily_loss_fetch_failed"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"


def test_per_strategy_db_failure_reports_warn(captured_reports, tmp_path):
    from src.runtime.risk_counters import inject_per_strategy_counters

    bogus_db = tmp_path / "does-not-exist-dir" / "tj.db"
    inject_per_strategy_counters({}, "vwap", db_path=str(bogus_db))

    matches = [r for r in captured_reports
               if r["status"] == "per_strategy_fetch_failed"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"
    assert matches[0]["ctx"].get("strategy") == "vwap"


# ---------------------------------------------------------------------------
# pipeline.py: audit log_signal failure
# ---------------------------------------------------------------------------


def test_audit_log_failure_reports_warn():
    """When log_signal raises mid-run_pipeline, an audit_log:write_failed
    outcome is emitted at WARN.
    """
    captured = []

    def fake_report(action, status, *, level, reason=None, **ctx):
        captured.append({"action": action, "status": status,
                         "level": getattr(level, "value", level),
                         "reason": reason})
        return {}

    # Stub heavy deps so pipeline imports cleanly
    for mod in ("pandas", "matplotlib", "matplotlib.pyplot", "numpy", "scipy", "sklearn"):
        sys.modules.setdefault(mod, MagicMock())

    from src.runtime import pipeline as pl

    settings = {"DRY_RUN": "true", "ALLOW_LIVE_TRADING": "false"}
    actionable = {"symbol": "BTCUSDT", "side": "buy", "qty": 1.0, "price": 50_000.0}

    # pipeline.py does `from src.runtime.outcomes import ... report`
    # at import time, so the bound name is pipeline.report. Patch the
    # binding inside pipeline (not the source module) for this test.
    with patch.object(pl, "log_signal", side_effect=RuntimeError("disk full")), \
            patch("src.runtime.pipeline.os.path.exists", return_value=False), \
            patch("src.runtime.pipeline.write_status"), \
            patch("src.runtime.pipeline.send_via_alert_manager"), \
            patch("src.runtime.pipeline.notify_operator"), \
            patch("src.runtime.pipeline.report", side_effect=fake_report):
        pl.run_pipeline(settings=settings, signal_builder=lambda _s: actionable)

    matches = [c for c in captured if c["status"] == "write_failed" and c["action"] == "audit_log"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"
    assert "disk full" in (matches[0]["reason"] or "")


# ---------------------------------------------------------------------------
# dashboards/stats.py: balance/positions/last_trade failures
# ---------------------------------------------------------------------------


def test_dashboard_strategy_data_failure_reports(captured_reports):
    """When strategy_dashboard_data raises, build_stats reports + still returns."""
    from src.units.dashboards import stats as stats_mod

    fake_loaders = MagicMock()
    fake_loaders.strategy_dashboard_data.side_effect = RuntimeError("DB locked")
    fake_loaders.account_open_positions = lambda *_: None
    fake_loaders.account_balance = lambda *_: None
    fake_loaders.account_last_trade = lambda *_: None
    sys.modules["src.bot.data_loaders"] = fake_loaders

    try:
        out = stats_mod.build_stats(
            accounts=[],
            paused_account_ids=set(),
            paused_strategy_names=set(),
            alert_snapshot=[],
        )
    finally:
        sys.modules.pop("src.bot.data_loaders", None)

    assert out["strategies"] == []  # graceful degrade
    matches = [r for r in captured_reports
               if r["status"] == "strategy_data_failed"]
    assert len(matches) == 1


def test_dashboard_balance_failure_reports(captured_reports):
    from src.units.dashboards import stats as stats_mod

    fake_loaders = MagicMock()
    fake_loaders.strategy_dashboard_data = lambda: []
    fake_loaders.account_balance.side_effect = RuntimeError("API down")
    fake_loaders.account_open_positions = lambda *_: None
    fake_loaders.account_last_trade = lambda *_: None
    sys.modules["src.bot.data_loaders"] = fake_loaders

    try:
        stats_mod.build_stats(
            accounts=[{"account_id": "main", "exchange": "bybit"}],
            paused_account_ids=set(),
            paused_strategy_names=set(),
            exchange_clients={"main": object()},
            alert_snapshot=[],
        )
    finally:
        sys.modules.pop("src.bot.data_loaders", None)

    matches = [r for r in captured_reports
               if r["status"] == "balance_failed"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# coordinator: _log_smoke_to_journal failure
# ---------------------------------------------------------------------------


def test_smoke_journal_write_failure_reports(captured_reports):
    from src.core import coordinator as coord_mod

    pkg = coord_mod.OrderPackage(
        strategy="smoke", symbol="BTCUSDT", direction="long",
        entry=50_000, sl=49_000, tp=51_000, confidence=0.5,
        meta={"test_qty": 0.001},
    )
    smoke_result = {"account_id": "main", "trade_id": "tx1",
                    "status": "rejected_too_small", "reason": "ok"}

    # Force the DB call to blow up by passing a bogus db path
    ok = coord_mod._log_smoke_to_journal(
        pkg, smoke_result, db_path="/nonexistent_dir/tj.db",
    )
    assert ok is False
    matches = [r for r in captured_reports
               if r["status"] == "journal_write_failed"]
    assert len(matches) == 1
    assert matches[0]["level"] == "warn"
