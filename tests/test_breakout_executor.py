"""Unit tests for the Breakout manual-bridge executor (PB-20260616-004 step 4).

Pure — the emitter is injected, so no FCM/Telegram/network. Verifies the
manual-fill contract: a ticket is built + emitted and a ``prop-manual-<uuid>``
marker is returned (no live position).
"""
from __future__ import annotations

import pytest

from src.prop.breakout_executor import (
    MANUAL_FILL_PREFIX,
    emit_prop_ticket,
    is_manual_fill_id,
)


def _order(**over):
    o = {
        "symbol": "SOLUSDT", "direction": "long", "side": "Buy",
        "entry": 150.0, "sl": 145.0, "tp": 162.0, "strategy": "trend_donchian_sol",
    }
    o.update(over)
    return o


def test_emits_ticket_and_returns_manual_marker():
    captured = {}

    def fake_emit(ticket):
        captured["ticket"] = ticket
        return {"push": True, "telegram": True}

    tid = emit_prop_ticket(_order(), {"account_id": "breakout_1"}, _emitter=fake_emit)
    assert tid.startswith(MANUAL_FILL_PREFIX)
    assert is_manual_fill_id(tid)
    t = captured["ticket"]
    assert t.signal.symbol == "SOLUSDT"
    assert t.signal.direction == "long"
    assert t.side == "Buy"
    # risk_usd = risk_pct% of account size (routing default 0.6% of 5000 = 30,
    # unless account_cfg overrides) — positive sizing computed
    assert t.risk_usd > 0
    assert t.qty_units > 0


def test_account_cfg_overrides_risk_and_size():
    seen = {}
    tid = emit_prop_ticket(
        _order(), {"account_id": "breakout_1", "risk_pct": 1.5, "account_size_usd": 5000.0},
        _emitter=lambda t: seen.setdefault("t", t))
    # 1.5% of 5000 = $75 risk
    assert abs(seen["t"].risk_usd - 75.0) < 1e-6
    assert is_manual_fill_id(tid)


def test_short_maps_to_sell():
    seen = {}
    emit_prop_ticket(
        _order(direction="short", side="Sell", entry=150.0, sl=155.0, tp=138.0),
        {"account_id": "breakout_1"}, _emitter=lambda t: seen.setdefault("t", t))
    assert seen["t"].side == "Sell"
    assert seen["t"].signal.direction == "short"


def test_invalid_levels_raise():
    with pytest.raises(ValueError):
        emit_prop_ticket(_order(sl=0.0), {"account_id": "x"}, _emitter=lambda t: None)


def test_emit_failure_is_swallowed_journal_row_survives():
    def boom(_ticket):
        raise RuntimeError("telegram down")

    # a delivery failure must NOT prevent the manual-fill id (the journal row)
    tid = emit_prop_ticket(_order(), {"account_id": "breakout_1"}, _emitter=boom)
    assert is_manual_fill_id(tid)


def test_is_manual_fill_id_negative():
    assert not is_manual_fill_id("dry-abc123")
    assert not is_manual_fill_id(None)
    assert not is_manual_fill_id("12345")
