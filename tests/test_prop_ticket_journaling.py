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


def test_default_emit_passes_ticket_id_for_buttons(
    isolated_env: Path, monkeypatch) -> None:
    """The default (no _emitter) emit path passes ticket_id + account_id to
    emit_prop_signal — that's what makes the Yes/No place-decision buttons attach.

    Regression: send-prop-test-ping injected an _emitter(ticket) that dropped the
    ticket_id, so the test ping showed no buttons even though real tickets do.
    """
    from src.prop import breakout_notify
    from src.prop.breakout_executor import emit_prop_ticket

    captured = {}

    def _fake_emit(ticket, **kwargs):
        captured.update(kwargs)
        return {"push": True, "telegram": True}

    monkeypatch.setattr(breakout_notify, "emit_prop_signal", _fake_emit)

    order = {
        "symbol": "SOLUSDT", "direction": "long", "side": "Buy",
        "entry": 150.0, "sl": 145.5, "tp": 175.5,
        "strategy": "trend_donchian_sol", "meta": {"order_package_id": "op-1"},
    }
    trade_id = emit_prop_ticket(order, _account_cfg(), timeframe="1h")
    assert captured.get("ticket_id") == trade_id   # → buttons attach
    assert captured.get("account_id") == "breakout_1"


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


# ── ONE TICKET PER TRADE — reticket suppression (BL-20260705-PROP-RETICKET-WHILE-OPEN) ──

def _order(direction: str = "long", **over) -> dict:
    side = "Buy" if direction == "long" else "Sell"
    base = {
        "symbol": "ETHUSDT", "direction": direction, "side": side,
        "entry": 1770.0, "sl": 1732.0, "tp": 1945.0,
        "strategy": "eth_pullback_2h",
        "meta": {"order_package_id": "op-guard-1", "timeframe": "2h"},
    }
    base.update(over)
    return base


def test_reticket_suppressed_while_ticket_live(isolated_env: Path) -> None:
    """A second signal for the same (account, symbol, direction) while the
    first ticket is still within its validity window must NOT page the
    operator again — one ticket per trade (operator directive 2026-07-05)."""
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    pushes: list = []
    emit_prop_ticket(_order(), _account_cfg(), timeframe="2h",
                     _emitter=lambda t: pushes.append(t))
    assert len(pushes) == 1

    emit_prop_ticket(_order(), _account_cfg(), timeframe="2h",
                     _emitter=lambda t: pushes.append(t))
    assert len(pushes) == 1  # no second push

    rows = prop_journal.list_tickets(limit=10)
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["emitted", "suppressed"]
    sup = next(r for r in rows if r["status"] == "suppressed")
    assert "outstanding_ticket:emitted" in (sup.get("message") or "")


def test_reticket_suppressed_while_position_open(isolated_env: Path) -> None:
    """An OPEN prop position (newest fill open/filled) suppresses new tickets
    for its (account, symbol, direction) key."""
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    prop_journal.insert_fill({
        "account_id": "breakout_1", "symbol": "ETHUSDT", "direction": "long",
        "qty": 1.9, "entry_price": 1767.71, "status": "filled",
    })
    pushes: list = []
    emit_prop_ticket(_order(), _account_cfg(), timeframe="2h",
                     _emitter=lambda t: pushes.append(t))
    assert pushes == []
    rows = prop_journal.list_tickets(limit=10)
    assert [r["status"] for r in rows] == ["suppressed"]
    assert "open_position" in (rows[0].get("message") or "")


def test_reticket_allowed_after_expiry_and_after_close(isolated_env: Path) -> None:
    """An EXPIRED unacted ticket and a CLOSED position must NOT block a fresh
    signal — a new setup after the old one went stale is a new trade."""
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    prop_journal.record_ticket({
        "ticket_id": "prop-manual-old", "account_id": "breakout_1",
        "symbol": "ETHUSDT", "direction": "long", "entry": 1700.0,
        "status": "emitted", "valid_until": "2026-07-01T00:00:00+00:00",
    })
    prop_journal.insert_fill({
        "account_id": "breakout_1", "symbol": "ETHUSDT", "direction": "long",
        "qty": 1.9, "entry_price": 1700.0, "exit_price": 1720.0,
        "status": "closed",
    })
    pushes: list = []
    emit_prop_ticket(_order(), _account_cfg(), timeframe="2h",
                     _emitter=lambda t: pushes.append(t))
    assert len(pushes) == 1  # emitted normally


def test_reticket_opposite_direction_not_suppressed(isolated_env: Path) -> None:
    """The guard is per (account, symbol, DIRECTION) — a short setup is not
    blocked by an open long."""
    from src.prop import prop_journal
    from src.prop.breakout_executor import emit_prop_ticket

    prop_journal.insert_fill({
        "account_id": "breakout_1", "symbol": "ETHUSDT", "direction": "long",
        "qty": 1.9, "entry_price": 1767.71, "status": "filled",
    })
    pushes: list = []
    emit_prop_ticket(_order("short", entry=1770.0, sl=1800.0, tp=1650.0),
                     _account_cfg(), timeframe="2h",
                     _emitter=lambda t: pushes.append(t))
    assert len(pushes) == 1
