"""aggregate_intents hard-gate enforcement (regime router — BASELINE ON).

Verifies the hard-gate path that:

  * **Drops** OFF-cell candidate intents from the aggregator's set BEFORE
    the reinforcement / conflict-resolution logic runs.
  * Emits a ``regime_hard_gate`` audit row with ``enforced: true`` so a
    later analysis can partition shadow "would have gated" history from
    enforce "did gate" history by event name.
  * Is **baseline-on** since the Design-A vol-gate go-live (2026-06-28): a
    *required* live capability must not sit behind a default-off flag
    (Prime Directive), so an unset env enforces. The kill-switch /
    rollback is ``REGIME_ROUTER_DISABLED`` (and a leftover legacy explicit
    ``REGIME_ROUTER_ENABLED=0`` still disables, for a VM mid-migration).
  * Never raises if the policy is empty / missing — fail-permissive
    keeps the intent on any verdict-load exception.
  * When enforcing, ``regime_shadow_gate`` rows are NOT also emitted (we
    don't want duplicate audit events for the same intent).
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


def _clean_router_env(monkeypatch):
    """Default (unset) env = baseline-on. Clear both flags so a test that wants
    the baseline enforce path doesn't inherit a stray DISABLED/ENABLED."""
    monkeypatch.delenv("REGIME_ROUTER_DISABLED", raising=False)
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)


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


# --- baseline-on / kill-switch ---------------------------------------------


def test_baseline_on_off_cell_intent_is_dropped(monkeypatch):
    """Unset env = baseline-on → an OFF-cell intent is dropped,
    regime_hard_gate emitted with enforced:true, aggregator returns flat
    because no live candidates survived."""
    _clean_router_env(monkeypatch)
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "flat"  # nothing survived the baseline gate
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(hard) == 1
    assert hard[0]["enforced"] is True
    assert hard[0]["strategy"] == "vwap"
    assert hard[0]["cell"] == "off"
    assert hard[0]["gated"] is True
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    assert shadow == []  # shadow row not emitted when enforcing


def test_disabled_kill_switch_keeps_shadow_behaviour(monkeypatch):
    """REGIME_ROUTER_DISABLED=1 → shadow log-only, intent KEPT, aggregator
    returns it as winning intent (the rollback / observe path)."""
    _clean_router_env(monkeypatch)
    monkeypatch.setenv("REGIME_ROUTER_DISABLED", "1")
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"  # NOT gated — router disabled
    assert result.winning_intent is intent
    shadow = [r for r in captured if r.get("event") == "regime_shadow_gate"]
    hard = [r for r in captured if r.get("event") == "regime_hard_gate"]
    assert len(shadow) == 1
    assert shadow[0]["enforced"] is False
    assert hard == []  # enforce row NOT emitted when disabled


def test_legacy_explicit_enabled_zero_disables(monkeypatch):
    """A leftover explicit REGIME_ROUTER_ENABLED=0 (a VM mid-migration with the
    old var still set) keeps the shadow path — the legacy rollback is honoured."""
    _clean_router_env(monkeypatch)
    monkeypatch.setenv("REGIME_ROUTER_ENABLED", "0")
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")
    with patch("src.runtime.intents._load_regime_policy", return_value=_POLICY), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"  # legacy explicit-off → not gated
    assert [r for r in captured if r.get("event") == "regime_hard_gate"] == []


def test_enabled_on_cell_intent_is_kept(monkeypatch):
    """Baseline-on + ON-cell intent → intent unchanged, no regime_hard_gate
    row emitted."""
    _clean_router_env(monkeypatch)
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
    """Baseline-on with one OFF-cell (vwap) and one ON-cell (trend_donchian)
    intent → OFF dropped, ON drives the result."""
    _clean_router_env(monkeypatch)
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
    """Baseline-on with every intent in an OFF cell → all candidates dropped →
    flat result with the standard 'no candidates' reason."""
    _clean_router_env(monkeypatch)
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
    """The gate must run BEFORE conflict resolution: if it drops the loser,
    the winner gets the position uncontested."""
    _clean_router_env(monkeypatch)
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
    _clean_router_env(monkeypatch)
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
    _clean_router_env(monkeypatch)
    captured, spy = _capture_audit_rows()
    intent = _make_intent("vwap", "long", "trending")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("policy.yaml unreadable")

    with patch("src.runtime.intents._load_regime_policy", side_effect=_raise), \
         patch("src.utils.signal_audit_logger.log_signal", side_effect=spy):
        result = aggregate_intents([intent], symbol="BTCUSDT")
    assert result.side == "long"
    assert result.winning_intent is intent


def test_router_active_resolution(monkeypatch):
    """``_regime_router_active`` — baseline ON; DISABLED truthy or a legacy
    explicit ENABLED-falsy disables; unset / ENABLED-truthy stays active."""
    from src.runtime.intents import _regime_router_active

    # Baseline: both unset → active.
    _clean_router_env(monkeypatch)
    assert _regime_router_active() is True

    # Kill-switch wins.
    for truthy in ("1", "true", "yes", "on", "True", "TRUE", "Yes"):
        monkeypatch.setenv("REGIME_ROUTER_DISABLED", truthy)
        assert _regime_router_active() is False, truthy
    monkeypatch.delenv("REGIME_ROUTER_DISABLED", raising=False)

    # A non-truthy DISABLED does NOT disable (only explicit truthy kills).
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("REGIME_ROUTER_DISABLED", falsy)
        assert _regime_router_active() is True, falsy
    monkeypatch.delenv("REGIME_ROUTER_DISABLED", raising=False)

    # Legacy explicit-off rollback.
    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("REGIME_ROUTER_ENABLED", falsy)
        assert _regime_router_active() is False, falsy
    # Legacy explicit-on / empty / unset → active.
    for onish in ("1", "true", "on", ""):
        monkeypatch.setenv("REGIME_ROUTER_ENABLED", onish)
        assert _regime_router_active() is True, onish
    monkeypatch.delenv("REGIME_ROUTER_ENABLED", raising=False)
    assert _regime_router_active() is True  # absent → baseline on
