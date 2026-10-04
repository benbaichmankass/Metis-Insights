"""TRADEIFY-RISK-75 (2026-10-04): each prop account sizes AND reads its own ticket.

tradeify_1's first live ticket was refused at the executor: "ticket asks $74.85,
above the flat-mode cap $50.00". The producer was not the fault — tradeify_1's
runtime account_cfg sizes at its configured 0.5% x $10k = $50 (pinned below).
The fault was the read: ``prop_journal.list_outbound_tickets`` read the
``prop_tickets`` sidecar unfiltered and keyed it by ``order_package_id`` alone,
so when breakout_1 and tradeify_1 ticketed the SAME order package (both roster
the leg) one row won and both accounts' views returned breakout_1's $75 ticket.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db))
    return db


def _runtime_cfg(name: str) -> dict:
    """The flat account_cfg the coordinator builds (src/core/coordinator.py)."""
    from src.units.accounts import load_accounts

    acc = next(a for a in load_accounts() if a.name == name)
    return {
        "account_id": acc.name,
        "exchange": acc.exchange,
        "risk_pct": acc.risk_manager.risk_pct,
        "account_class": getattr(acc, "account_class", "real_money"),
        "backtest_ruleset": getattr(acc, "backtest_ruleset", None),
    }


@pytest.mark.parametrize("name,expected_risk_usd", [
    ("tradeify_1", 50.0),   # risk.risk_pct 0.005 x tradeify_247_1step $10,000
    ("breakout_1", 75.0),   # risk.risk_pct 0.015 x breakout.yaml $5,000
])
def test_runtime_account_cfg_sizes_at_configured_risk(name, expected_risk_usd) -> None:
    from src.prop.account_rulesets import unit_for_account
    from src.prop.breakout_ticket import BreakoutSignal
    from src.prop.multi_account_ticket import build_account_leg

    unit = unit_for_account(name, _runtime_cfg(name))
    sig = BreakoutSignal(
        strategy="trend_donchian_eth_prop", symbol="ETHUSDT", direction="long",
        entry=2400.0, sl=2385.0, tp=2450.0, timeframe="4h",
        signal_time=datetime.now(timezone.utc),
    )
    leg = build_account_leg(sig, unit)
    assert leg.ticket is not None
    assert leg.ticket.risk_usd == pytest.approx(expected_risk_usd)


def test_outbound_tickets_are_scoped_to_the_account(isolated_db: Path) -> None:
    from src.prop import prop_journal
    from src.units.db.database import Database

    db = Database()
    db.insert_order_package({
        "order_package_id": "pkg-shared",
        "strategy_name": "trend_donchian_eth_prop",  # on both prop rosters
        "symbol": "ETHUSDT", "direction": "long",
        "entry": 2400.0, "sl": 2385.0, "tp": 2450.0, "status": "emitted",
    })
    db.insert_order_package({
        "order_package_id": "pkg-breakout-only",
        "strategy_name": "trend_donchian_eth_prop",
        "symbol": "ETHUSDT", "direction": "short",
        "entry": 2400.0, "sl": 2415.0, "tp": 2350.0, "status": "emitted",
    })
    common = {"strategy": "trend_donchian_eth_prop", "symbol": "ETHUSDT",
              "direction": "long", "entry": 2400.0, "sl": 2385.0, "tp": 2450.0,
              "status": "emitted"}
    prop_journal.record_ticket({**common, "ticket_id": "tk-breakout",
                                "account_id": "breakout_1", "qty": 5.0,
                                "risk_usd": 75.0, "order_package_id": "pkg-shared"})
    prop_journal.record_ticket({**common, "ticket_id": "tk-tradeify",
                                "account_id": "tradeify_1", "qty": 3.33,
                                "risk_usd": 50.0, "order_package_id": "pkg-shared"})
    prop_journal.record_ticket({**common, "direction": "short", "ticket_id": "tk-breakout-2",
                                "account_id": "breakout_1", "qty": 5.0, "risk_usd": 75.0,
                                "order_package_id": "pkg-breakout-only"})

    tradeify = prop_journal.list_outbound_tickets(account_id="tradeify_1", status="emitted")
    assert [(r["ticket_id"], r["risk_usd"], r["account_id"]) for r in tradeify] == [
        ("tk-tradeify", 50.0, "tradeify_1")]

    breakout = prop_journal.list_outbound_tickets(account_id="breakout_1", status="emitted")
    assert sorted(r["ticket_id"] for r in breakout) == ["tk-breakout", "tk-breakout-2"]
    assert {r["account_id"] for r in breakout} == {"breakout_1"}

    # The unscoped view keeps BOTH tickets on the shared package (no collision).
    every = prop_journal.list_outbound_tickets()
    assert sorted(r["ticket_id"] for r in every
                  if r["order_package_id"] == "pkg-shared") == ["tk-breakout", "tk-tradeify"]
