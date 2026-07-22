"""Tests for the trade-close observer hook in Database.update_trade (M12 S1).

Verifies:

- A status='closed' update on a real (non-backtest, non-demo) trade
  fires ``publish_event("trade_closed", {...})`` with the right payload.
- Backtest trades do NOT fire the hook (we don't notify the operator
  about historical replays).
- Paper / demo trades DO fire now (the operator asked for paper
  open/close/update notifications too); the payload carries the funding
  class (``account_class`` / ``is_paper``) so the consumer tags them.
- A failed publish (any exception) must NOT propagate into the close
  path. ``update_trade`` must return the rowcount unchanged.
- Updates that don't set ``status`` (e.g. partial-fill updates) don't
  fire the hook spuriously.
- Updates that set ``status`` to something other than ``'closed'``
  (e.g. ``'cancelled'``) don't fire either.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.units.db.database import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(db_path=str(tmp_path / "trade_journal.db"))


def _insert_real_trade(db: Database) -> int:
    return db.insert_trade(
        {
            "timestamp": "2026-05-26T12:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "buy",
            "entry_price": 80000.0,
            "position_size": 0.001,
            "strategy_name": "vwap",
            "account_id": "bybit_1",
            "is_backtest": 0,
            "is_demo": 0,
        }
    )


def _insert_backtest_trade(db: Database) -> int:
    return db.insert_trade(
        {
            "timestamp": "2026-05-26T12:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "buy",
            "entry_price": 80000.0,
            "position_size": 0.001,
            "strategy_name": "vwap",
            "account_id": "backtest",
            "is_backtest": 1,
            "is_demo": 0,
        }
    )


def _insert_demo_trade(db: Database) -> int:
    return db.insert_trade(
        {
            "timestamp": "2026-05-26T12:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "buy",
            "entry_price": 80000.0,
            "position_size": 0.001,
            "strategy_name": "vwap",
            "account_id": "bybit_1",
            "is_backtest": 0,
            "is_demo": 1,
        }
    )


def test_close_on_real_trade_fires_publish_event(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A status='closed' update on a real trade should call publish_event
    with the canonical payload shape."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(kind: str, payload: dict[str, Any]) -> None:
        captured.append((kind, payload))

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _capture)

    trade_id = _insert_real_trade(db)
    captured.clear()  # drop the insert's own trade_opened event
    db.update_trade(
        trade_id,
        {
            "status": "closed",
            "exit_price": 80200.0,
            "exit_reason": "tp_hit",
            "pnl": 0.2,
            "pnl_percent": 0.25,
        },
    )

    assert len(captured) == 1
    kind, payload = captured[0]
    assert kind == "trade_closed"
    assert payload["trade_id"] == trade_id
    assert payload["symbol"] == "BTCUSDT"
    assert payload["direction"] == "buy"
    assert payload["pnl"] == 0.2
    assert payload["pnl_percent"] == 0.25
    assert payload["exit_reason"] == "tp_hit"
    assert payload["strategy"] == "vwap"
    assert payload["account"] == "bybit_1"


def test_close_on_backtest_trade_does_not_fire(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    trade_id = _insert_backtest_trade(db)
    db.update_trade(trade_id, {"status": "closed", "pnl": 0.5})

    assert captured == []


def test_close_on_demo_trade_fires_with_paper_tag(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paper/demo closes now fire too — tagged via is_paper in the payload."""
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda kind, payload: captured.append((kind, payload)),
    )

    trade_id = _insert_demo_trade(db)
    db.update_trade(trade_id, {"status": "closed", "pnl": 0.5})

    assert len(captured) == 1
    kind, payload = captured[0]
    assert kind == "trade_closed"
    assert payload["trade_id"] == trade_id
    assert payload["is_paper"] is True


def test_publish_exception_does_not_propagate(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If publish_event somehow raises (despite its own try/except),
    update_trade must still return the rowcount and not bubble the
    exception up to the trader's close path."""

    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("simulated explosion")

    monkeypatch.setattr("src.runtime.mobile_push.publish_event", _boom)

    trade_id = _insert_real_trade(db)
    # Must not raise.
    rowcount = db.update_trade(trade_id, {"status": "closed", "pnl": 1.0})
    assert rowcount == 1


def test_partial_update_without_status_does_not_fire(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    trade_id = _insert_real_trade(db)
    captured.clear()  # drop the insert's own trade_opened event
    db.update_trade(trade_id, {"notes": "edited some metadata"})

    assert captured == []


def test_status_update_to_non_closed_does_not_fire(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    trade_id = _insert_real_trade(db)
    captured.clear()  # drop the insert's own trade_opened event
    db.update_trade(trade_id, {"status": "cancelled"})

    assert captured == []


def test_update_on_nonexistent_trade_does_not_fire(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the UPDATE didn't hit any rows (e.g. bad trade_id), the hook
    must NOT fire — there's no real close event."""
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    db.update_trade(999999, {"status": "closed", "pnl": 0.0})

    assert captured == []


def test_insert_real_trade_fires_trade_opened_event(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BL-20260722-XRP-SLSPAM regression (part 3). ``_fire_trade_opened_event``
    previously SELECTed ``qty``/``sl``/``tp`` — none of which exist on
    ``trades`` (the real columns are ``position_size``/``stop_loss``/
    ``take_profit_1``) — so this raised ``OperationalError`` on every insert,
    silently swallowed by ``insert_trade``'s bare except. TRADE_OPENED has
    therefore never actually fired. This pins the fix: the event now fires
    with the correct values, read off the real columns."""
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda kind, payload: captured.append((kind, payload)),
    )

    trade_id = db.insert_trade(
        {
            "timestamp": "2026-05-26T12:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "buy",
            "entry_price": 80000.0,
            "stop_loss": 79000.0,
            "take_profit_1": 82000.0,
            "position_size": 0.001,
            "strategy_name": "vwap",
            "account_id": "bybit_1",
            "is_backtest": 0,
            "is_demo": 0,
        }
    )

    assert len(captured) == 1
    kind, payload = captured[0]
    assert kind == "trade_opened"
    assert payload["trade_id"] == trade_id
    assert payload["symbol"] == "BTCUSDT"
    assert payload["qty"] == 0.001
    assert payload["entry_price"] == 80000.0
    assert payload["sl"] == 79000.0
    assert payload["tp"] == 82000.0
    assert payload["strategy"] == "vwap"
    assert payload["account"] == "bybit_1"


def test_insert_backtest_trade_does_not_fire_opened_event(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    db.insert_trade(
        {
            "timestamp": "2026-05-26T12:00:00Z",
            "symbol": "BTCUSDT",
            "direction": "buy",
            "entry_price": 80000.0,
            "position_size": 0.001,
            "strategy_name": "vwap",
            "account_id": "backtest",
            "is_backtest": 1,
            "is_demo": 0,
        }
    )

    assert captured == []


def test_stop_loss_update_does_not_fire_trade_updated_event(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documents current, intentional behaviour: ``update_trade``'s own
    trade_updated gate checks for literal ``"sl"``/``"tp"`` dict keys, which
    don't match the real ``stop_loss``/``take_profit_1`` columns any real
    caller writes (see ``order_monitor.py``'s modify-branch sync, added by
    the same fix). This keeps ``_fire_trade_updated_event`` unreachable
    on purpose — wiring it up would double-fire alongside
    ``execution_diagnostics.enqueue_trade_update`` for the same SL move
    without a dedup design (BL-20260722-XRP-SLSPAM, logged for a follow-up).
    If this test starts failing because the gate was changed to key on
    ``stop_loss``/``take_profit_1``, the double-notification risk must be
    resolved in the same change."""
    captured: list[Any] = []
    monkeypatch.setattr(
        "src.runtime.mobile_push.publish_event",
        lambda *a, **kw: captured.append((a, kw)),
    )

    trade_id = _insert_real_trade(db)
    captured.clear()  # drop the insert's own trade_opened event
    db.update_trade(trade_id, {"stop_loss": 79500.0, "take_profit_1": 82500.0})

    assert captured == []
