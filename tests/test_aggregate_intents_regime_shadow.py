"""aggregate_intents shadow-gate emission (PERF-20260601-002 phase 2).

Verifies that the aggregator:

  * Evaluates the regime policy for every candidate intent and emits a
    ``regime_shadow_gate`` audit row for any OFF cell that WOULD have
    suppressed the intent.
  * Does NOT change the aggregation decision in phase 2 — the would-gate
    intent still feeds the existing flat/reinforcement/conflict logic
    exactly as before. (Phase 3 will turn this on; phase 2 is log-only.)
  * Never raises if the policy is empty / missing / malformed.
  * Carries the intent's regime + adx_14 fields through from
    ``intent_from_signal`` (which reads them from ``signal.meta``).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from src.runtime.intents import (
    StrategyIntent,
    aggregate_intents,
    intent_from_signal,
)


@pytest.fixture(autouse=True)
def _shadow_router(monkeypatch):
    """This file tests the SHADOW (would-gate) path. The regime router is
    baseline-on since the Design-A vol-gate go-live, so the shadow path is now
    opt-out — disable the router for every test here."""
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)
    monkeypatch.setenv("REGIME_ROUTER_DISABLED", "1")
    yield


def _capture_audit_rows() -> List[Dict[str, Any]]:
    """Return a list that gets appended every time the in-process
    aggregator's ``log_signal`` is called from ``_shadow_regime_gate``."""
    captured: List[Dict[str, Any]] = []

    def _spy(payload, *args, **kwargs):
        captured.append(dict(payload))

    return captured, _spy


def _make_intent(strategy: str, side: str, regime: str | None,
                 adx_14: float | None = None,
                 vol_regime: str | None = None) -> StrategyIntent:
    return StrategyIntent(
        strategy=strategy,
        symbol="BTCUSDT",
        side=side,
        target_qty=0.0,
        regime=regime,
        adx_14=adx_14,
        vol_regime=vol_regime,
        entry=70000.0,
        sl=69000.0,
        tp=72000.0,
    )


# --- StrategyIntent contract ----------------------------------------------

def test_strategy_intent_accepts_regime_fields():
    intent = _make_intent("vwap", "long", "chop", adx_14=15.5)
    assert intent.regime == "chop"
    assert intent.adx_14 == 15.5


def test_strategy_intent_defaults_regime_fields_to_none():
    """Backwards compatibility — older code paths that don't pass regime
    fields must still produce a valid intent."""
    intent = StrategyIntent(
        strategy="vwap", symbol="BTCUSDT", side="long", target_qty=0.0,
    )
    assert intent.regime is None
    assert intent.adx_14 is None


# --- intent_from_signal pulls regime from signal.meta ----------------------

def test_intent_from_signal_pulls_regime_from_meta():
    signal = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "entry_price": 70000.0,
        "stop_loss": 69000.0,
        "take_profit": 72000.0,
        "meta": {
            "strategy_name": "vwap",
            "regime": "chop",
            "adx_14": 15.5,
        },
    }
    intent = intent_from_signal(signal)
    assert intent is not None
    assert intent.regime == "chop"
    assert intent.adx_14 == 15.5


def test_intent_from_signal_handles_missing_regime_meta():
    """A builder that hasn't yet been wired to stamp regime (or a
    legacy/test path) must still produce a valid intent with None
    regime fields."""
    signal = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "entry_price": 70000.0,
        "stop_loss": 69000.0,
        "take_profit": 72000.0,
        "meta": {"strategy_name": "vwap"},
    }
    intent = intent_from_signal(signal)
    assert intent is not None
    assert intent.regime is None
    assert intent.adx_14 is None


# --- aggregate_intents: shadow row fires for off cells --------------------

def test_aggregator_emits_shadow_gate_for_vwap_in_chop():
    """vwap is off in every regime per the matrix — a vwap long intent
    in chop must produce a regime_shadow_gate row. The aggregator's
    decision is unchanged (the intent still feeds the vote)."""
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "chop", adx_14=15.5)
    with patch("src.runtime.intents.log_signal" if False else "src.utils.signal_audit_logger.log_signal", side_effect=spy):
        _ = aggregate_intents([intent])
    shadow_rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert shadow_rows, f"expected a shadow row, got events={[r.get('event') for r in captured]}"
    row = shadow_rows[0]
    assert row["gated"] is True
    assert row["enforced"] is False
    assert row["strategy"] == "vwap"
    assert row["side"] == "long"
    assert row["regime"] == "chop"
    assert row["cell"] == "off"
    assert row["reason"] == "regime_gated_chop"


def test_aggregator_does_NOT_emit_shadow_for_on_cell():
    """trend_donchian long in trending is ON per the matrix; no shadow
    row should fire."""
    captured, spy = _capture_audit_rows()
    intent = _make_intent("trend_donchian", "long", "trending", adx_14=30.0)
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        _ = aggregate_intents([intent])
    shadow_rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert not shadow_rows, f"unexpected shadow row: {shadow_rows}"


def test_aggregator_does_NOT_emit_shadow_for_unknown_regime():
    """ADX warmup / detector failure → intent.regime=None. Permissive
    default applies; no shadow row."""
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", regime=None)
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        _ = aggregate_intents([intent])
    shadow_rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert not shadow_rows


# --- aggregate_intents: phase 2 does NOT change decisions -----------------

def test_phase_2_does_not_change_aggregator_decision():
    """An OFF-cell intent (vwap long in chop) is logged as would-gate
    but still wins the aggregation when it's the only candidate.
    Phase 3 will change this; phase 2 must NOT."""
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "chop", adx_14=15.0)
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])
    # Aggregator picks the intent unchanged; same-direction reinforcement
    # path with one intent ⇒ that intent wins.
    assert result.side == "long"
    assert result.winning_intent is intent
    # AND a shadow row was emitted alongside.
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert len(shadow) == 1


# === S-MLOPT-S15b — vol axis on the shadow gate ============================

def test_strategy_intent_accepts_vol_regime_field():
    intent = _make_intent("vwap", "long", "chop", adx_14=15.0, vol_regime="volatile")
    assert intent.vol_regime == "volatile"


def test_strategy_intent_defaults_vol_regime_to_none():
    intent = StrategyIntent(
        strategy="vwap", symbol="BTCUSDT", side="long", target_qty=0.0,
    )
    assert intent.vol_regime is None


def test_intent_from_signal_pulls_vol_regime_from_meta():
    signal = {
        "symbol": "BTCUSDT", "side": "buy", "entry_price": 70000.0,
        "meta": {"strategy_name": "vwap", "regime": "chop", "vol_regime": "calm"},
    }
    intent = intent_from_signal(signal)
    assert intent is not None
    assert intent.vol_regime == "calm"


def test_trend_gated_row_carries_vol_axis_fields():
    """A 1-D trend-gated intent (vwap long in chop) now also logs the vol
    axis on the same row — observe-only, enforced:false."""
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "chop", adx_14=15.0, vol_regime="volatile")
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        _ = aggregate_intents([intent])
    rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert len(rows) == 1
    row = rows[0]
    assert row["gated"] is True            # trend axis
    assert row["vol_regime"] == "volatile"
    assert row["vol_gated"] is False       # empty trend_vol → permissive
    assert row["vol_cell"] == "default-on"
    assert row["enforced"] is False


def test_vol_off_cell_fires_row_even_when_trend_is_on(monkeypatch):
    """A 2-D off cell must emit a regime_shadow_gate row even when the 1-D
    trend cell is permissive (gated=False) — the vol axis is independent."""
    import src.runtime.intents as intents_mod
    monkeypatch.setattr(
        intents_mod, "_REGIME_POLICY_CACHE",
        {"trend_vol": {"trending": {"volatile": {"trend_donchian": {"long": "off"}}}}},
    )
    captured, spy = _capture_audit_rows()
    # trend_donchian long in trending is ON in the real matrix; here the
    # in-memory policy has no 1-D cell so trend is permissive, but the 2-D
    # cell is off → the row fires on the vol axis alone.
    intent = _make_intent("trend_donchian", "long", "trending", adx_14=30.0,
                          vol_regime="volatile")
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])
    rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert len(rows) == 1
    assert rows[0]["gated"] is False       # 1-D permissive
    assert rows[0]["vol_gated"] is True     # 2-D off
    assert rows[0]["vol_cell"] == "off"
    # Phase-2 invariant: the aggregator decision is unchanged.
    assert result.side == "long"
    assert result.winning_intent is intent


def test_no_row_when_both_axes_permissive(monkeypatch):
    import src.runtime.intents as intents_mod
    monkeypatch.setattr(
        intents_mod, "_REGIME_POLICY_CACHE",
        {"trend_vol": {"trending": {"volatile": {"trend_donchian": {"long": "off"}}}}},
    )
    captured, spy = _capture_audit_rows()
    # calm (not volatile) → 2-D cell absent → permissive; trend also permissive.
    intent = _make_intent("trend_donchian", "long", "trending", adx_14=30.0,
                          vol_regime="calm")
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        _ = aggregate_intents([intent])
    rows = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert not rows


def test_aggregator_survives_empty_policy(tmp_path, monkeypatch):
    """Empty/missing policy must collapse to permissive everywhere and
    emit no shadow rows."""
    # Force the cache to an empty dict for this test.
    import src.runtime.intents as intents_mod
    monkeypatch.setattr(intents_mod, "_REGIME_POLICY_CACHE", {})

    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "chop", adx_14=15.0)
    with patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent])

    assert result.side == "long"
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert not shadow, "empty policy must produce no shadow rows"
