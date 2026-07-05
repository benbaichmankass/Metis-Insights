"""Unit tests for the Breakout manual-bridge executor (PB-20260616-004 step 4).

Pure — the emitter is injected, so no FCM/Telegram/network. Verifies the
manual-fill contract: a ticket is built + emitted and a ``prop-manual-<uuid>``
marker is returned (no live position).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.prop.breakout_executor import (
    MANUAL_FILL_PREFIX,
    emit_prop_ticket,
    is_manual_fill_id,
)


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Each emit journals a prop_tickets row, and the ONE-TICKET-PER-TRADE guard
    # reads that journal back — without per-test isolation the first test's
    # 'emitted' SOLUSDT-long ticket suppresses every later emit for the same
    # key (CI failure on PR #5622), and pre-guard the emits silently polluted
    # a shared repo-root DB. Same isolation as test_prop_ticket_journaling.
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))


def _order(**over):
    o = {
        "symbol": "SOLUSDT", "direction": "long", "side": "Buy",
        "entry": 150.0, "sl": 145.0, "tp": 162.0, "strategy": "trend_donchian_sol",
    }
    o.update(over)
    return o


def _acct(**over):
    # a prop account_cfg shape: unit_for_account resolves it to the breakout
    # ruleset (account_size $5k) and converts risk 0.015 → 1.5%.
    a = {"account_id": "breakout_1", "exchange": "breakout",
         "backtest_ruleset": "prop_rulesets/breakout.yaml",
         "risk": {"risk_pct": 0.015}}
    a.update(over)
    return a


def test_emits_ticket_and_returns_manual_marker():
    captured = {}

    def fake_emit(ticket):
        captured["ticket"] = ticket
        return {"push": True, "telegram": True}

    tid = emit_prop_ticket(_order(), _acct(), _emitter=fake_emit)
    assert tid.startswith(MANUAL_FILL_PREFIX)
    assert is_manual_fill_id(tid)
    t = captured["ticket"]
    assert t.signal.symbol == "SOLUSDT"
    assert t.signal.direction == "long"
    assert t.side == "Buy"
    assert t.risk_usd > 0
    assert t.qty_units > 0


def test_sizing_from_account_ruleset():
    # 1.5% of the breakout $5k ruleset = $75 risk (canonical account→ruleset path)
    seen = {}
    tid = emit_prop_ticket(_order(), _acct(), _emitter=lambda t: seen.setdefault("t", t))
    assert abs(seen["t"].risk_usd - 75.0) < 1e-6
    assert is_manual_fill_id(tid)


def test_short_maps_to_sell():
    seen = {}
    emit_prop_ticket(
        _order(direction="short", side="Sell", entry=150.0, sl=155.0, tp=138.0),
        _acct(), _emitter=lambda t: seen.setdefault("t", t))
    assert seen["t"].side == "Sell"
    assert seen["t"].signal.direction == "short"


def test_invalid_levels_raise():
    with pytest.raises(ValueError):
        emit_prop_ticket(_order(sl=0.0), _acct(), _emitter=lambda t: None)


def test_emit_failure_is_swallowed_journal_row_survives():
    def boom(_ticket):
        raise RuntimeError("telegram down")

    # a delivery failure must NOT prevent the manual-fill id (the journal row)
    tid = emit_prop_ticket(_order(), _acct(), _emitter=boom)
    assert is_manual_fill_id(tid)


def test_is_manual_fill_id_negative():
    assert not is_manual_fill_id("dry-abc123")
    assert not is_manual_fill_id(None)
    assert not is_manual_fill_id("12345")
