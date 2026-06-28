"""Design-A Phase 2 — ``REGIME_ML_VERDICT_MODE=use`` substitutes the advisory
head's ML vol label into the gate DECISION (was a documented placeholder).

Previously ``use`` behaved like ``shadow``: the gate always evaluated
``would_gate`` against the FROZEN ``intent.vol_regime``. The 2-D ``trend_vol``
OFF cells were authored under the ML label and LOSE money under the frozen one
(``docs/research/A-vol-gating-OFFcell-design-2026-06-27.md``), so a correct
enforce must gate on the ML label. These tests pin that ``use`` now flips the
vol-axis decision via ``_decision_vol_regime``, and that it stays fail-permissive
(ML ``unknown`` — e.g. no advisory head for the strategy's ``(symbol,
timeframe)`` — keeps the frozen label, never strands a signal).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import src.runtime.intents as intents_mod
from src.runtime.intents import StrategyIntent, aggregate_intents


def _capture():
    rows: List[Dict[str, Any]] = []
    return rows, (lambda payload, *a, **k: rows.append(dict(payload)))


def _intent(vol_regime: str | None) -> StrategyIntent:
    # trend_donchian long in `trending` has NO 1-D cell here → trend-permissive;
    # the only thing that can gate is the 2-D vol cell (trending+volatile).
    return StrategyIntent(
        strategy="trend_donchian", symbol="BTCUSDT", side="long",
        target_qty=1.0, regime="trending", adx_14=30.0, vol_regime=vol_regime,
        entry=70000.0, sl=69000.0, tp=72000.0,
    )


# OFF only in (trending, volatile) — the load-bearing Design-A cell.
_VOL_POLICY = {"trend_vol": {"trending": {"volatile": {"trend_donchian": {"long": "off"}}}}}


def test_use_substitutes_ml_label_and_flips_the_vol_gate(monkeypatch):
    """Frozen label is ``calm`` (would NOT gate); the advisory head says
    ``volatile``. Under ``use`` the decision uses the ML label → the vol cell
    fires, and the audit row records ``vol_label_source='ml'``."""
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime",
               return_value={"vol_regime": "volatile",
                             "source": "ml-advisory:btc-regime-15m-lgbm-v2"}), \
         patch("src.runtime.strategy_monocle._strategy_timeframe_label",
               return_value="15m"), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("calm")])
    gate_rows = [r for r in rows if r.get("event") == "regime_shadow_gate"]
    assert len(gate_rows) == 1
    row = gate_rows[0]
    assert row["vol_gated"] is True          # the ML label drove the gate
    assert row["vol_regime"] == "volatile"   # decision label = ML
    assert row["vol_regime_frozen"] == "calm"
    assert row["vol_regime_ml"] == "volatile"
    assert row["vol_label_source"] == "ml"


def test_off_mode_keeps_frozen_label_no_gate(monkeypatch):
    """Same intent (frozen ``calm``) but mode ``off`` → the ML label is never
    consulted, the frozen ``calm`` does not match the volatile-only OFF cell,
    so NO vol gate fires. This is the contrast that proves the substitution is
    what flips the decision."""
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="off"), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("calm")])
    assert [r for r in rows if r.get("event") == "regime_shadow_gate"] == []


def test_use_ml_unknown_falls_back_to_frozen(monkeypatch):
    """The LIVE reality for the current 1h/4h cells: no advisory head →
    ``ml_vol_regime`` returns ``unknown`` → the decision keeps the frozen label.
    Frozen ``volatile`` still gates (matches the cell); ``vol_label_source`` is
    ``frozen`` — i.e. ``use`` never strands the signal on a missing head."""
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime",
               return_value={"vol_regime": "unknown", "source": "unavailable"}), \
         patch("src.runtime.strategy_monocle._strategy_timeframe_label",
               return_value="1h"), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("volatile")])
    gate_rows = [r for r in rows if r.get("event") == "regime_shadow_gate"]
    assert len(gate_rows) == 1
    assert gate_rows[0]["vol_label_source"] == "frozen"
    assert gate_rows[0]["vol_regime_ml"] == "unknown"
