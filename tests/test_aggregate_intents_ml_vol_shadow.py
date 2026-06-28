"""aggregate_intents ML-vol shadow audit — Design A, Phase 1 (default-off).

Verifies the ``REGIME_ML_VERDICT_MODE`` gate added to ``_shadow_regime_gate`` /
``_hard_regime_gate``:

  * mode ``off`` (default) — NO ``regime_ml_vol_shadow`` row + the candidate
    set / decision is identical to before (zero added behaviour).
  * mode ``shadow`` — a ``regime_ml_vol_shadow`` row is emitted per candidate
    carrying both labels + ``agree``, and the decision STILL uses the frozen
    label (unchanged result).
  * Fail-permissive — when the ML verdict raises, the gate proceeds (no crash,
    decision unchanged).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

import src.runtime.intents as intents_mod
from src.runtime.intents import StrategyIntent, aggregate_intents


def _capture():
    captured: List[Dict[str, Any]] = []

    def _spy(payload, *args, **kwargs):
        captured.append(dict(payload))

    return captured, _spy


def _make_intent(strategy="vwap", side="long", regime="chop",
                 vol_regime="calm") -> StrategyIntent:
    return StrategyIntent(
        strategy=strategy, symbol="BTCUSDT", side=side, target_qty=0.0,
        regime=regime, adx_14=15.0, vol_regime=vol_regime,
        entry=70000.0, sl=69000.0, tp=72000.0,
    )


@pytest.fixture(autouse=True)
def _reset_policy(monkeypatch):
    # Empty trend policy so only the ML-vol axis is under test.
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", {})
    monkeypatch.delenv("REGIME_ML_VERDICT_MODE", raising=False)
    # The router is baseline-ON; this file tests the SHADOW path by default, so
    # disable the router (the one hard-gate test below clears DISABLED to enforce).
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)
    monkeypatch.setenv("REGIME_ROUTER_DISABLED", "1")
    yield


# --- mode off (default): zero added behaviour ------------------------------

def test_mode_off_emits_no_ml_vol_row():
    captured, spy = _capture()
    intent = _make_intent()
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])
    ml_rows = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"]
    assert not ml_rows
    # Decision unchanged: single intent wins.
    assert result.side == "long"
    assert result.winning_intent is intent


def test_mode_off_does_not_call_ml_verdict(monkeypatch):
    """The off path must short-circuit BEFORE any ML work — ml_vol_regime is
    never imported/called."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("ml_vol_regime must not run when mode=off")

    monkeypatch.setattr("src.runtime.regime.ml_vol_regime_for_symbol", _boom)
    captured, spy = _capture()
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_make_intent()])
    assert called["n"] == 0


# --- mode shadow: emits row, decision unchanged ----------------------------

def test_mode_shadow_emits_ml_vol_row(monkeypatch):
    monkeypatch.setenv("REGIME_ML_VERDICT_MODE", "shadow")
    monkeypatch.setattr(
        "src.runtime.regime.ml_vol_regime_for_symbol",
        lambda symbol, *a, **k: {
            "vol_regime": "volatile", "p_volatile": 0.81,
            "source": "ml-advisory:btc-regime-1h-v2", "model_id": "btc-regime-1h-v2",
        },
    )
    monkeypatch.setattr(
        "src.runtime.strategy_monocle._strategy_timeframe_label",
        lambda name: "1h",
    )
    captured, spy = _capture()
    intent = _make_intent(vol_regime="calm")  # frozen=calm, ml=volatile → disagree
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])
    rows = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"]
    assert len(rows) == 1
    row = rows[0]
    assert row["vol_regime_frozen"] == "calm"
    assert row["vol_regime_ml"] == "volatile"
    assert row["p_volatile"] == 0.81
    assert row["agree"] is False
    assert row["ml_source"] == "ml-advisory:btc-regime-1h-v2"
    assert row["model_id"] == "btc-regime-1h-v2"
    assert row["enforced"] is False
    assert row["strategy"] == "vwap"
    # Decision STILL uses the frozen label / is unchanged.
    assert result.side == "long"
    assert result.winning_intent is intent


def test_mode_shadow_agreement_true(monkeypatch):
    monkeypatch.setenv("REGIME_ML_VERDICT_MODE", "shadow")
    monkeypatch.setattr(
        "src.runtime.regime.ml_vol_regime_for_symbol",
        lambda symbol, *a, **k: {
            "vol_regime": "calm", "p_volatile": 0.2,
            "source": "ml-advisory:m", "model_id": "m",
        },
    )
    monkeypatch.setattr(
        "src.runtime.strategy_monocle._strategy_timeframe_label", lambda name: "1h",
    )
    captured, spy = _capture()
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_make_intent(vol_regime="calm")])
    row = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"][0]
    assert row["agree"] is True


def test_mode_shadow_agreement_none_when_unknown(monkeypatch):
    """ml=unknown → agree is None (not a false mismatch)."""
    monkeypatch.setenv("REGIME_ML_VERDICT_MODE", "shadow")
    monkeypatch.setattr(
        "src.runtime.regime.ml_vol_regime_for_symbol",
        lambda symbol, *a, **k: {
            "vol_regime": "unknown", "p_volatile": None,
            "source": "unavailable", "model_id": None,
        },
    )
    monkeypatch.setattr(
        "src.runtime.strategy_monocle._strategy_timeframe_label", lambda name: "1h",
    )
    captured, spy = _capture()
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_make_intent(vol_regime="calm")])
    row = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"][0]
    assert row["agree"] is None


# --- fail-permissive: ML path raises → gate proceeds -----------------------

def test_ml_verdict_raises_gate_proceeds(monkeypatch):
    monkeypatch.setenv("REGIME_ML_VERDICT_MODE", "shadow")

    def _boom(symbol, *a, **k):
        raise RuntimeError("ml exploded")

    monkeypatch.setattr("src.runtime.regime.ml_vol_regime_for_symbol", _boom)
    monkeypatch.setattr(
        "src.runtime.strategy_monocle._strategy_timeframe_label", lambda name: "1h",
    )
    captured, spy = _capture()
    intent = _make_intent()
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])  # must NOT raise
    # No ML row (the per-candidate emit swallowed the error) but the decision
    # is intact.
    ml_rows = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"]
    assert not ml_rows
    assert result.side == "long"
    assert result.winning_intent is intent


# --- enforcement path (hard gate) also emits, never alters the kept set ----

def test_hard_gate_emits_ml_row_and_keeps_intent(monkeypatch):
    monkeypatch.setenv("REGIME_ML_VERDICT_MODE", "shadow")
    # Router is baseline-on — clear the file-fixture's DISABLED to enforce.
    monkeypatch.delenv("REGIME_ROUTER_DISABLED", raising=False)
    # Non-empty policy so the hard gate runs its (empty-cell) loop, but no cell
    # gates this intent, so it survives.
    monkeypatch.setattr(
        intents_mod, "_REGIME_POLICY_CACHE",
        {"trend_vol": {"trending": {"volatile": {"other": {"long": "off"}}}}},
    )
    monkeypatch.setattr(
        "src.runtime.regime.ml_vol_regime_for_symbol",
        lambda symbol, *a, **k: {
            "vol_regime": "volatile", "p_volatile": 0.9,
            "source": "ml-advisory:m", "model_id": "m",
        },
    )
    monkeypatch.setattr(
        "src.runtime.strategy_monocle._strategy_timeframe_label", lambda name: "1h",
    )
    captured, spy = _capture()
    intent = _make_intent(strategy="trend_donchian", regime="trending",
                          vol_regime="calm")
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])
    rows = [r for r in captured if r.get("event") == "regime_ml_vol_shadow"]
    assert len(rows) == 1  # the hard-gate path emitted it
    # The ML axis never drops the intent — it survives (frozen label drives it).
    assert result.side == "long"
    assert result.winning_intent is intent
