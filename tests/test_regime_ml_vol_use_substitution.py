"""Design-A Phase 2 — ``REGIME_ML_VERDICT_MODE=use`` substitutes the advisory
head's ML vol label into the gate DECISION (was a documented placeholder), plus
the per-symbol vol-head resolution + the ML-only-enforce guard that make it
production-safe.

- ``use`` now flips the vol-axis decision via ``_decision_vol_regime`` (the cells
  were authored under the ML label and LOSE money under the frozen one —
  ``docs/research/A-vol-gating-OFFcell-design-2026-06-27.md``).
- Resolution is per-SYMBOL (``ml_vol_regime_for_symbol``): the validated A/B used
  the single BTC 15m advisory head for every BTC cell, so a 1h/4h strategy gets
  the symbol's advisory vol label, not ``unknown`` from a per-timeframe lookup.
- The hard gate only DROPS on a vol cell when the label was ML-sourced
  (``vol_enforced``) — a frozen fallback never enforces a money-losing vol cell.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import src.runtime.intents as intents_mod
from src.runtime.intents import StrategyIntent, aggregate_intents
from src.runtime.regime.ml_vol_verdict import _advisory_entry_for_symbol


def _capture():
    rows: List[Dict[str, Any]] = []
    return rows, (lambda payload, *a, **k: rows.append(dict(payload)))


def _intent(vol_regime: str | None) -> StrategyIntent:
    return StrategyIntent(
        strategy="trend_donchian", symbol="BTCUSDT", side="long",
        target_qty=1.0, regime="trending", adx_14=30.0, vol_regime=vol_regime,
        entry=70000.0, sl=69000.0, tp=72000.0,
    )


# OFF only in (trending, volatile) — the load-bearing Design-A cell.
_VOL_POLICY = {"trend_vol": {"trending": {"volatile": {"trend_donchian": {"long": "off"}}}}}


def _ml(vol: str, model="btc-regime-15m-lgbm-v2"):
    return {"vol_regime": vol, "source": f"ml-advisory:{model}", "model_id": model}


# --- A1/A2: use substitutes the per-symbol ML label into the shadow gate -----

def test_use_substitutes_ml_label_and_flips_the_vol_gate(monkeypatch):
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    # Router is baseline-on; this asserts the SHADOW would-gate row, so disable.
    monkeypatch.setenv("REGIME_ROUTER_DISABLED", "1")
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime_for_symbol", return_value=_ml("volatile")), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("calm")])  # frozen=calm would NOT gate
    gate_rows = [r for r in rows if r.get("event") == "regime_shadow_gate"]
    assert len(gate_rows) == 1
    row = gate_rows[0]
    assert row["vol_gated"] is True and row["vol_regime"] == "volatile"
    assert row["vol_regime_frozen"] == "calm" and row["vol_regime_ml"] == "volatile"
    assert row["vol_label_source"] == "ml"


def test_off_mode_keeps_frozen_label_no_gate(monkeypatch):
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="off"), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("calm")])
    assert [r for r in rows if r.get("event") == "regime_shadow_gate"] == []


def test_use_ml_unknown_falls_back_to_frozen(monkeypatch):
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    # Router is baseline-on; this asserts the SHADOW would-gate row, so disable.
    monkeypatch.setenv("REGIME_ROUTER_DISABLED", "1")
    rows, spy = _capture()
    with patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime_for_symbol",
               return_value={"vol_regime": "unknown", "source": "unavailable"}), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        aggregate_intents([_intent("volatile")])  # frozen=volatile still gates
    gate_rows = [r for r in rows if r.get("event") == "regime_shadow_gate"]
    assert len(gate_rows) == 1
    assert gate_rows[0]["vol_label_source"] == "frozen"
    assert gate_rows[0]["vol_regime_ml"] == "unknown"


# --- A2: per-symbol resolution picks the symbol's advisory head --------------

def test_advisory_entry_for_symbol_prefers_15m_non_yz():
    specs = {
        ("BTCUSDT", "15M"): {"symbol": "BTCUSDT", "timeframe": "15m",
                             "model_id": "btc-regime-15m-lgbm-v2", "is_yz": False},
        ("BTCUSDT", "1H"): {"symbol": "BTCUSDT", "timeframe": "1h",
                            "model_id": "btc-regime-1h-yz", "is_yz": True},
    }
    # A 1h strategy still resolves the symbol's 15m non-yz head.
    entry = _advisory_entry_for_symbol("BTCUSDT", specs)
    assert entry is not None and entry["model_id"] == "btc-regime-15m-lgbm-v2"
    # A symbol with no advisory head → None (→ unknown → frozen, permissive).
    assert _advisory_entry_for_symbol("ETHUSDT", specs) is None


# --- A3: the hard gate only enforces a vol cell when the label is ML-sourced --

def test_hard_gate_drops_on_ml_vol_but_not_on_frozen_fallback(monkeypatch):
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", _VOL_POLICY)
    # ML-sourced volatile → the off cell enforces → intent dropped → flat.
    with patch.object(intents_mod, "_regime_router_active", return_value=True), \
         patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime_for_symbol", return_value=_ml("volatile")), \
         patch("src.utils.signal_audit_logger.log_signal"):
        result = aggregate_intents([_intent("calm")])
    assert result.side == "flat"  # the ML vol gate dropped the only intent

    # Frozen-fallback (ML unknown) on a frozen-volatile intent → the vol cell
    # would match, but the guard refuses to enforce a non-ML vol label → KEPT.
    with patch.object(intents_mod, "_regime_router_active", return_value=True), \
         patch.object(intents_mod, "_regime_ml_verdict_mode", return_value="use"), \
         patch("src.runtime.regime.ml_vol_regime_for_symbol",
               return_value={"vol_regime": "unknown", "source": "unavailable"}), \
         patch("src.utils.signal_audit_logger.log_signal"):
        result = aggregate_intents([_intent("volatile")])
    assert result.side == "long"  # frozen-only vol cell does NOT enforce
