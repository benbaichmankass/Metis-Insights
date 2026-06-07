"""aggregate_intents hard-gate enforcement (PERF-20260601-006 phase 3).

Verifies the phase-3 hard-gate path that:

  * **Drops** OFF-cell candidate intents from the aggregator's set BEFORE
    the reinforcement / conflict-resolution logic runs.
  * Emits a ``regime_hard_gate`` audit row with ``enforced: true`` so a
    later analysis can partition phase-2 "would have gated" history from
    phase-3 "did gate" history by event name.
  * Stays gated behind ``REGIME_ROUTER_ENABLED`` — the default-off
    behaviour is byte-identical to phase 2 (shadow log-only, intents
    not dropped).
  * Never raises if the policy is empty / missing — fail-permissive
    keeps the intent on any verdict-load exception.
  * When the flag is on, ``regime_shadow_gate`` rows are NOT also
    emitted (we don't want duplicate audit events for the same intent).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

from src.runtime.intents import (
    StrategyIntent,
    aggregate_intents,
)


def _capture_audit_rows() -> tuple:
    captured: List[Dict[str, Any]] = []

    def _spy(payload, *args, **kwargs):
        captured.append(dict(payload))

    return captured, _spy


def _make_intent(
    strategy: str, side: str, regime: str | None,
    target_qty: float = 1.0, adx_14: float | None = 15.0,
    vol_regime: str | None = None,
) -> StrategyIntent:
    return StrategyIntent(
        strategy=strategy,
        symbol="BTCUSDT",
        side=side,
        target_qty=target_qty,
        regime=regime,
        adx_14=adx_14,
        vol_regime=vol_regime,
        entry=70000.0,
        sl=69000.0,
        tp=72000.0,
    )


# Policy: vwap OFF in every regime; htf_pullback OFF in chop. Nesting
# is policy[regime][strategy][side] (matches src.runtime.regime.policy
# and the live config/regime_policy.yaml shape).
_POLICY = {
    "chop": {
        "vwap": {"long": "off", "short": "off"},
        "htf_pullback_trend_2h": {"long": "off", "short": "off"},
    },
    "transitional": {
        "vwap": {"long": "off", "short": "off"},
    },
    "trending": {
        "vwap": {"long": "off", "short": "off"},
    },
}


# --- env-flag toggle -------------------------------------------------------


def test_default_off_keeps_phase_2_behaviour(monkeypatch):
    """No REGIME_ROUTER_ENABLED → phase 2: shadow log-only, intent KEPT,
    aggregator returns it as winning intent."""
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"  # NOT gated — flag off
    assert result.winning_intent is intent
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(shadow) == 1
    assert shadow[0]["enforced"] is False
    assert hard == []  # phase-3 row NOT emitted under default-off


def test_enabled_off_cell_intent_is_dropped(monkeypatch):
    """REGIME_ROUTER_ENABLED=1 + OFF-cell intent → intent dropped,
    regime_hard_gate emitted with enforced:true, aggregator returns
    flat because no live candidates survived."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "flat"  # nothing survived the gate
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(hard) == 1
    assert hard[0]["enforced"] is True
    assert hard[0]["strategy"] == "vwap"
    assert hard[0]["cell"] == "off"
    assert hard[0]["gated"] is True
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert shadow == []  # phase-2 row not emitted when phase-3 is active


def test_enabled_on_cell_intent_is_kept(monkeypatch):
    """REGIME_ROUTER_ENABLED=1 + ON-cell intent → intent unchanged, no
    regime_hard_gate row emitted."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    # trend_donchian not in the policy → permissive → ON in every regime.
    intent = _make_intent("trend_donchian", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"
    assert result.winning_intent is intent
    assert [r for r in captured if r.get("event") == "regime_hard_gate"] == []


def test_enabled_mixed_off_dropped_on_survives(monkeypatch):
    """REGIME_ROUTER_ENABLED=1 with one OFF-cell (vwap) and one
    ON-cell (trend_donchian) intent → OFF dropped, ON drives the result."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    off_intent = _make_intent("vwap", "long", "trending", target_qty=2.0)
    on_intent = _make_intent("trend_donchian", "long", "trending", target_qty=1.0)
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([off_intent, on_intent], symbol="BTCUSDT")
    # vwap dropped → on_intent is the only candidate; reinforcement isn't
    # triggered (only one intent) — on_intent wins outright.
    assert result.side == "long"
    assert result.winning_intent is on_intent
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(hard) == 1
    assert hard[0]["strategy"] == "vwap"


def test_enabled_all_off_returns_flat(monkeypatch):
    """REGIME_ROUTER_ENABLED=1 with every intent in an OFF cell → all
    candidates dropped → flat result with the standard 'no candidates'
    reason."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    intents = [
        _make_intent("vwap", "long", "chop"),
        _make_intent("vwap", "short", "trending"),
        _make_intent("htf_pullback_trend_2h", "long", "chop"),
    ]
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents(intents, symbol="BTCUSDT")
    assert result.side == "flat"
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(hard) == 3


def test_enabled_conflict_resolution_after_gate(monkeypatch):
    """Phase 3 must run BEFORE conflict resolution: if the gate drops
    the loser, the winner gets the position uncontested."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    # vwap-short OFF in trending → dropped. trend_donchian-long ON →
    # wins the symbol (no conflict to resolve anymore).
    off_short = _make_intent("vwap", "short", "trending")
    on_long = _make_intent("trend_donchian", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([off_short, on_long], symbol="BTCUSDT")
    assert result.side == "long"
    assert result.winning_intent is on_long


# --- robustness ------------------------------------------------------------


def test_enabled_empty_policy_is_fail_permissive(monkeypatch):
    """If the policy table is missing/empty, the hard gate must NOT drop
    anything — fail-permissive so a policy-loader bug never silently
    strands a live signal."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value={}), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"  # intent KEPT despite would-be OFF cell
    assert result.winning_intent is intent
    assert captured == []  # no gate rows emitted at all


def test_enabled_policy_load_exception_is_fail_permissive(monkeypatch):
    """An exception in _load_regime_policy must NOT drop the intent.
    Same bias as the empty-policy case."""
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "1")
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("policy.yaml unreadable")

    with patch("src.runtime.intents._load_regime_policy", side_effect=_raise), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"
    assert result.winning_intent is intent


def test_kill_switch_values_recognized(monkeypatch):
    """Any of 1/true/yes/on (case-insensitive) enables; anything else
    leaves phase 2 in place."""
    from src.runtime.intents import _regime_router_enabled

    for truthy in ("1", "true", "yes", "on", "True", "TRUE", "Yes"):
        monkeypatch.setenv("REGIME_ROUTER_ENABLED", truthy)
        assert _regime_router_enabled() is True, truthy
    for falsy in ("0", "false", "no", "off", "", "anything-else"):
        monkeypatch.setenv("REGIME_ROUTER_ENABLED", falsy)
        assert _regime_router_enabled() is False, falsy
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)
    assert _regime_router_enabled() is False  # absent → default off
