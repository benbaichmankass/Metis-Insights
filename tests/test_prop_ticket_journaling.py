"""Regression tests for the prop ticket journaling fixes (prop-trade-pipeline-debug).

1. ``emit_prop_ticket`` must persist the ``order_package_id`` on the
   ``prop_tickets`` row. The execute_pkg breakout branch passes the id in
   ``order["meta"]["order_package_id"]`` (the order dict has no top-level key),
   so the previous ``order.get("order_package_id")`` was ALWAYS None — every
   prop_tickets row had a null order_package_id, breaking the ticket↔package
   join the dashboard + reconcile rely on.

2. The emitted ticket sizes off the account's real risk_pct (1.5% on breakout_1),
   exercising the flat-runtime-account_cfg sizing fix end-to-end through the
   executor (risk_usd ≈ $75, not the $25 the 0.5% default produced).
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _account_cfg() -> dict:
    # The FLAT shape coordinator.multi_account_execute builds (risk_pct at the
    # top level), for the $5k Breakout account at 1.5% per-trade risk.
    return {
        "account_id": "breakout_1",
        "exchange": "breakout",
        "account_class": "prop",
        "risk_pct": 0.015,
    }


def test_emit_records_order_package_id_from_meta(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    order = {
        "symbol": "ETHUSDT", "direction": "short",
        "side": "Sell", "entry": 1717.0, "sl": 1740.0, "tp": 1650.0,
        "strategy": "trend_donchian_eth",
        "meta": {"order_package_id": "op-xyz-123", "timeframe": "1h"},
    }
    # Inject a no-op emitter so no Telegram/FCM I/O happens.
    trade_id = emit_prop_ticket(order, _account_cfg(), timeframe="1h",
                                _emitter=lambda ticket: None)
    assert trade_id.startswith("prop-manual-")

    rows = prop_journal.list_tickets(limit=10)
    assert len(rows) == 1
    assert rows[0]["order_package_id"] == "op-xyz-123"   # was always None pre-fix


def test_emit_sizes_off_configured_risk_pct(isolated_env: Path) -> None:
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    order = {
        "symbol": "ETHUSDT", "direction": "short",
        "side": "Sell", "entry": 1717.0, "sl": 1740.0, "tp": 1650.0,
        "strategy": "trend_donchian_eth",
        "meta": {"order_package_id": "op-xyz-123"},
    }
    emit_prop_ticket(order, _account_cfg(), timeframe="1h",
                     _emitter=lambda ticket: None)
    row = prop_journal.list_tickets(limit=10)[0]
    # 1.5% of the $5k account = $75 risk (the fix); the 0.5% default was $25.
    assert row["risk_usd"] == pytest.approx(75.0, abs=0.01)
