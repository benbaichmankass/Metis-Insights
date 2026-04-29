"""
S-005 M5: Full multiplex dry-run integration tests.

Exercises the complete pipeline stack end-to-end:
  signal builder → STRATEGY_RISK_PCT scaling → per-strategy caps
  → safe_place_order → dry_run

All exchange I/O is stubbed; no network calls are made.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import src.runtime.pipeline as _pipeline_mod
from src.runtime.pipeline import (
    STRATEGIES,
    STRATEGY_RISK_PCT,
    multiplexed_signal_builder,
    run_pipeline,
)
from src.runtime.orders import safe_place_order


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat(symbol="BTCUSDT"):
    return {"symbol": symbol, "side": "none", "qty": 0}


def _signal(side="buy", qty=1.0, strategy="breakout_confirmation"):
    return {"symbol": "BTCUSDT", "side": side, "qty": qty,
            "meta": {"strategy_name": strategy}}


def _base_settings(**extra):
    return {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10", **extra}


class _DummyTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, msg):
        self.messages.append(msg)


# ---------------------------------------------------------------------------
# 1. First-wins with STRATEGY_RISK_PCT scaling
# ---------------------------------------------------------------------------

def test_multiplex_breakout_wins_with_risk_scaling(monkeypatch):
    """breakout fires first; qty is scaled by STRATEGY_RISK_PCT['breakout_confirmation']."""
    for name in STRATEGIES:
        if name == "breakout_confirmation":
            monkeypatch.setitem(
                _pipeline_mod._STRATEGY_BUILDERS, name,
                lambda s: _signal(qty=1.0, strategy="breakout_confirmation"),
            )
        else:
            monkeypatch.setitem(
                _pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat(),
            )

    signal = multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "breakout_confirmation"
    expected_qty = 1.0 * STRATEGY_RISK_PCT["breakout_confirmation"]
    assert abs(signal["qty"] - expected_qty) < 1e-9


def test_multiplex_vwap_wins_when_breakout_flat(monkeypatch):
    """vwap fires when breakout is flat; qty scaled by 0.3."""
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation",
                        lambda s: _flat())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "vwap",
                        lambda s: _signal(qty=1.0, strategy="vwap"))
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "killzone", lambda s: _flat())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "ict", lambda s: _flat())

    signal = multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})

    assert signal["meta"]["strategy_name"] == "vwap"
    assert abs(signal["qty"] - STRATEGY_RISK_PCT["vwap"]) < 1e-9


def test_multiplex_ict_fires_last(monkeypatch):
    """ICT fires only when all three prior strategies return flat."""
    for name in ["breakout_confirmation", "vwap", "killzone"]:
        monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "ict",
                        lambda s: _signal(qty=1.0, strategy="ict"))

    signal = multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})

    assert signal["meta"]["strategy_name"] == "ict"
    assert abs(signal["qty"] - STRATEGY_RISK_PCT["ict"]) < 1e-9


def test_multiplex_all_flat_returns_no_signal(monkeypatch):
    """All strategies flat → side=none, qty=0."""
    for name in STRATEGIES:
        monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat())

    signal = multiplexed_signal_builder({"SYMBOL": "BTCUSDT"})

    assert signal["side"] == "none"
    assert float(signal["qty"]) == 0.0


# ---------------------------------------------------------------------------
# 2. Per-strategy risk cap → refusal end-to-end through run_pipeline
# ---------------------------------------------------------------------------

def test_pipeline_per_strategy_cap_refuses_order(monkeypatch):
    """Per-strategy cap blocks the order; run_pipeline returns status=refused."""
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation",
        lambda s: _signal(qty=0.4, strategy="breakout_confirmation"),
    )
    for name in ["vwap", "killzone", "ict"]:
        monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat())

    # Inject per-strategy counters directly so DB is not needed
    monkeypatch.setattr(_pipeline_mod, "inject_runtime_counters",
                        lambda s, _c: s)
    monkeypatch.setattr(_pipeline_mod, "inject_per_strategy_counters",
                        lambda s, _n: {**s,
                                       "STRATEGY_OPEN_POSITIONS": "5",
                                       "MAX_POS_PER_STRATEGY": "2"})

    settings = _base_settings(MAX_POS_PER_STRATEGY="2")
    tg = _DummyTelegram()

    result = run_pipeline(settings, telegram_client=tg)

    assert result["order_result"]["status"] == "refused"
    assert "MAX_POS_PER_STRATEGY" in result["order_result"]["reason"]


def test_pipeline_below_cap_reaches_dry_run(monkeypatch):
    """When strategy counter is below cap, order reaches dry_run status."""
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation",
        lambda s: _signal(qty=0.4, strategy="breakout_confirmation"),
    )
    for name in ["vwap", "killzone", "ict"]:
        monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat())

    monkeypatch.setattr(_pipeline_mod, "inject_runtime_counters",
                        lambda s, _c: s)
    monkeypatch.setattr(_pipeline_mod, "inject_per_strategy_counters",
                        lambda s, _n: {**s,
                                       "STRATEGY_OPEN_POSITIONS": "1",
                                       "MAX_POS_PER_STRATEGY": "5"})

    settings = _base_settings(MAX_POS_PER_STRATEGY="5")
    tg = _DummyTelegram()

    result = run_pipeline(settings, telegram_client=tg)

    assert result["order_result"]["status"] == "dry_run"


# ---------------------------------------------------------------------------
# 3. Halt flag integration
# ---------------------------------------------------------------------------

def test_pipeline_halt_flag_blocks_multiplexed_order(monkeypatch, tmp_path):
    """Halt flag present → status=halted regardless of strategy signal."""
    flag = tmp_path / "halt.flag"
    flag.write_text("halted")
    monkeypatch.setattr(_pipeline_mod, "HALT_FLAG_PATH", str(flag))
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation",
        lambda s: _signal(qty=0.4, strategy="breakout_confirmation"),
    )
    for name in ["vwap", "killzone", "ict"]:
        monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, name, lambda s: _flat())

    settings = _base_settings()
    tg = _DummyTelegram()

    result = run_pipeline(settings, telegram_client=tg)

    assert result["order_result"]["status"] == "halted"


# ---------------------------------------------------------------------------
# 4. STRATEGY_RISK_PCT integrity
# ---------------------------------------------------------------------------

def test_strategy_risk_pct_covers_all_core_strategies():
    """breakout + vwap + ict are in STRATEGY_RISK_PCT and sum to 1.0."""
    for key in ("breakout_confirmation", "vwap", "ict"):
        assert key in STRATEGY_RISK_PCT, f"{key} missing from STRATEGY_RISK_PCT"
    total = sum(STRATEGY_RISK_PCT.values())
    assert abs(total - 1.0) < 1e-9


def test_strategies_list_order_ict_last():
    """ICT must be last in STRATEGIES (newest, most gated)."""
    assert STRATEGIES[-1] == "ict"
    assert len(STRATEGIES) >= 4


# ---------------------------------------------------------------------------
# 5. safe_place_order per-strategy daily-loss cap integration
# ---------------------------------------------------------------------------

def test_daily_loss_per_strategy_cap_end_to_end():
    """End-to-end: daily loss cap refuses order via safe_place_order."""
    order = {
        "symbol": "BTCUSDT", "side": "buy", "qty": 0.3,
        "meta": {"strategy_name": "vwap"},
    }
    settings = {
        "DRY_RUN": "true",
        "MAX_QTY": "10",
        "MAX_DAILY_LOSS_PER_STRATEGY_USD": "50",
        "STRATEGY_DAILY_PNL": "-75.0",  # loss > cap
    }

    result = safe_place_order(order, settings, client=None)

    assert result["status"] == "refused"
    assert "MAX_DAILY_LOSS_PER_STRATEGY_USD" in result["reason"]
    assert "vwap" in result["reason"]
