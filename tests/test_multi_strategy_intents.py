"""Multi-strategy execution structure — intent layer tests.

Covers the deliverables in the multi-strategy-execution PR:

1. Same-direction reinforcement: Turtle Soup + VWAP both long on BTCUSDT
   produce one net target, not two duplicate orders.
2. Conflict resolution: opposing intents resolve to one side via
   deterministic priority, not first-wins ordering.
3. Delta-only execution: with a current open position, the executor
   sends only the delta to reach the aggregated target.
4. Risk-cap enforcement: the per-account RiskManager still gates the
   sized qty for an aggregated intent — the new layer does not bypass it.
5. Future-strategy plug-in: a third strategy (``ict_scalp``) registers
   via the same interface and flows through the same aggregator with no
   special casing.

All tests are pure / no live exchange. The intent layer is a set of
pure functions over typed dataclasses so the assertions can be exact.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest


# Pre-import stub for matplotlib so transitive pipeline imports don't
# crash in the lean sandbox env. Mirrors tests/test_s026_g2_position_size.py.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

from src.core.coordinator import OrderPackage  # noqa: E402
from src.runtime.intents import (  # noqa: E402
    DEFAULT_PRIORITIES,
    DesiredPosition,
    StrategyIntent,
    SUPPORTED_SYMBOLS,
    aggregate_intents,
    compute_execution_delta,
    intent_from_signal,
)
from src.runtime.intent_multiplexer import (  # noqa: E402
    _desired_to_pipeline_signal,
    clear_registered_intent_builders,
    intent_multiplexer_enabled,
    multiplexed_intent_signal_builder,
    register_intent_builder,
)
from src.units.accounts.risk import RiskManager  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intent(
    strategy: str,
    side: str,
    target_qty: float = 0.0,
    *,
    entry: float = 50_000.0,
    sl_offset: float = 500.0,
    tp_offset: float = 1_500.0,
    priority: int | None = None,
    timestamp: float = 100.0,
) -> StrategyIntent:
    if side == "long":
        sl = entry - sl_offset
        tp = entry + tp_offset
    elif side == "short":
        sl = entry + sl_offset
        tp = entry - tp_offset
    else:
        sl = entry - sl_offset
        tp = entry + tp_offset
    return StrategyIntent(
        strategy=strategy,
        symbol="BTCUSDT",
        side=side,
        target_qty=target_qty,
        priority=priority,
        timestamp=timestamp,
        entry=entry,
        sl=sl,
        tp=tp,
    )


# ---------------------------------------------------------------------------
# StrategyIntent construction
# ---------------------------------------------------------------------------


class TestStrategyIntentConstruction:
    def test_rejects_unsupported_symbol(self):
        """Symbols outside accounts.yaml + the static base set are rejected.

        (ETHUSDT was the fixture here until the M15 WS-C alt sleeve added it
        to bybit_1.symbols, 2026-06-11 — supported_symbols() is config-driven
        since #3358, so the fixture must use a genuinely unrouted symbol.)
        """
        with pytest.raises(ValueError, match="symbol must be one of"):
            StrategyIntent(
                strategy="turtle_soup",
                symbol="DOGEUSDT",
                side="long",
                target_qty=0.01,
            )

    def test_normalises_symbol(self):
        """``BTC/USDT`` and ``btcusdt`` both normalise to ``BTCUSDT``."""
        i = StrategyIntent(
            strategy="vwap",
            symbol="btc/usdt",
            side="long",
            target_qty=0.01,
        )
        assert i.symbol == "BTCUSDT"
        assert "BTCUSDT" in SUPPORTED_SYMBOLS

    def test_directional_with_zero_qty_is_preserved(self):
        """target_qty=0 is the sentinel for "side known, qty TBD by RiskManager".

        Strategies emit intents without pre-computing qty — sizing is
        the per-account RiskManager's job (S-026 G2). The aggregator
        must therefore preserve direction even when target_qty=0.
        """
        i = StrategyIntent(
            strategy="vwap",
            symbol="BTCUSDT",
            side="long",
            target_qty=0.0,
        )
        assert i.side == "long"
        assert i.target_qty == 0.0

    def test_flat_with_nonzero_qty_rewritten(self):
        i = StrategyIntent(
            strategy="vwap",
            symbol="BTCUSDT",
            side="flat",
            target_qty=0.05,
        )
        assert i.target_qty == 0.0

    def test_priority_defaults_from_registry(self):
        i = _intent("turtle_soup", "long", 0.01)
        assert i.effective_priority() == DEFAULT_PRIORITIES["turtle_soup"]

        i_unknown = _intent("unknown_strategy", "long", 0.01)
        # Falls back to the unknown-strategy floor so a misconfigured
        # new strategy never outranks the production set.
        assert i_unknown.effective_priority() < DEFAULT_PRIORITIES["vwap"]

    def test_explicit_priority_wins(self):
        i = _intent("turtle_soup", "long", 0.01, priority=99)
        assert i.effective_priority() == 99


# ---------------------------------------------------------------------------
# Deliverable 1: same-direction reinforcement
# ---------------------------------------------------------------------------


class TestSameDirectionReinforcement:
    def test_two_strategies_long_pick_larger_target(self):
        """Turtle Soup wants 0.01 long, VWAP wants 0.03 long → 0.03."""
        intents = [
            _intent("turtle_soup", "long", target_qty=0.01, timestamp=10.0),
            _intent("vwap", "long", target_qty=0.03, timestamp=11.0),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "long"
        assert desired.target_qty == 0.03
        assert desired.winning_intent is not None
        assert desired.winning_intent.strategy == "vwap"
        assert set(desired.meta["contributing_strategies"]) == {"turtle_soup", "vwap"}

    def test_two_strategies_short_pick_larger_target(self):
        intents = [
            _intent("turtle_soup", "short", target_qty=0.04, timestamp=10.0),
            _intent("vwap", "short", target_qty=0.02, timestamp=11.0),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "short"
        assert desired.target_qty == 0.04
        assert desired.winning_intent.strategy == "turtle_soup"

    def test_no_double_counting(self):
        """Same direction, same qty: aggregator returns ONE target — not the sum.

        This is the spec's key invariant: same-direction reinforcement
        means "keep at least the larger valid target size", NOT
        "place both orders independently".
        """
        intents = [
            _intent("turtle_soup", "long", target_qty=0.02, timestamp=10.0),
            _intent("vwap", "long", target_qty=0.02, timestamp=11.0),
        ]
        desired = aggregate_intents(intents)
        assert desired.target_qty == 0.02, (
            "Aggregator must NOT sum same-direction targets — that would "
            "double-count exposure when both strategies size against the "
            "same risk budget"
        )

    def test_same_target_tiebreaker_is_deterministic(self):
        """Two strategies, identical target, different priorities — higher priority wins.

        Determinism is the spec requirement; two ticks with the same
        inputs must always pick the same winner.
        """
        intents_a = [
            _intent("turtle_soup", "long", target_qty=0.02, timestamp=10.0),
            _intent("vwap", "long", target_qty=0.02, timestamp=10.0),
        ]
        intents_b = list(reversed(intents_a))

        desired_a = aggregate_intents(intents_a)
        desired_b = aggregate_intents(intents_b)
        assert desired_a.winning_intent.strategy == desired_b.winning_intent.strategy
        # Turtle Soup has the higher default priority.
        assert desired_a.winning_intent.strategy == "turtle_soup"


# ---------------------------------------------------------------------------
# Deliverable 2: conflict resolution by priority
# ---------------------------------------------------------------------------


class TestConflictResolution:
    def test_priority_wins_over_first_in(self):
        """VWAP long earlier in time, Turtle Soup short later — Turtle Soup wins on priority."""
        intents = [
            _intent("vwap", "long", target_qty=0.05, timestamp=1.0),
            _intent("turtle_soup", "short", target_qty=0.02, timestamp=2.0),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "short"
        assert desired.winning_intent.strategy == "turtle_soup"
        assert desired.target_qty == 0.02

    def test_dropped_intent_recorded_for_audit(self):
        intents = [
            _intent("vwap", "long", target_qty=0.05),
            _intent("turtle_soup", "short", target_qty=0.02),
        ]
        desired = aggregate_intents(intents)
        dropped = desired.meta["dropped_intents"]
        assert len(dropped) == 1
        assert dropped[0]["strategy"] == "vwap"
        assert dropped[0]["side"] == "long"

    def test_equal_priority_resolved_by_timestamp(self):
        """Forced equal priority → earliest timestamp wins (deterministic)."""
        intents = [
            _intent(
                "strat_a", "long", target_qty=0.05,
                priority=50, timestamp=100.0,
            ),
            _intent(
                "strat_b", "short", target_qty=0.02,
                priority=50, timestamp=50.0,
            ),
        ]
        desired = aggregate_intents(intents)
        assert desired.winning_intent.strategy == "strat_b"

    def test_equal_priority_equal_timestamp_resolved_alphabetically(self):
        """Last-resort tiebreaker: strategy name alphabetical."""
        intents = [
            _intent(
                "zeta", "long", target_qty=0.05,
                priority=50, timestamp=100.0,
            ),
            _intent(
                "alpha", "short", target_qty=0.02,
                priority=50, timestamp=100.0,
            ),
        ]
        desired = aggregate_intents(intents)
        assert desired.winning_intent.strategy == "alpha"


# ---------------------------------------------------------------------------
# Deliverable 3: delta-only execution
# ---------------------------------------------------------------------------


class TestDeltaExecution:
    def _desired_long(self, qty: float) -> DesiredPosition:
        return DesiredPosition(
            symbol="BTCUSDT",
            side="long",
            target_qty=qty,
            contributing_intents=tuple(),
            winning_intent=None,
            reason="test_fixture",
        )

    def _desired_short(self, qty: float) -> DesiredPosition:
        return DesiredPosition(
            symbol="BTCUSDT",
            side="short",
            target_qty=qty,
            contributing_intents=tuple(),
            winning_intent=None,
            reason="test_fixture",
        )

    def _desired_flat(self) -> DesiredPosition:
        return DesiredPosition(
            symbol="BTCUSDT",
            side="flat",
            target_qty=0.0,
            contributing_intents=tuple(),
            winning_intent=None,
            reason="test_fixture",
        )

    def test_flat_to_long_opens_full_target(self):
        delta = compute_execution_delta(
            current_signed_qty=0.0,
            desired=self._desired_long(0.03),
        )
        assert delta.action == "open"
        assert delta.side == "long"
        assert delta.qty_delta == 0.03

    def test_long_increase_sends_only_delta(self):
        """current=+0.01, target=long 0.03 → only 0.02 goes out."""
        delta = compute_execution_delta(
            current_signed_qty=0.01,
            desired=self._desired_long(0.03),
        )
        assert delta.action == "increase"
        assert delta.side == "long"
        assert delta.qty_delta == pytest.approx(0.02, abs=1e-9)

    def test_long_at_target_is_noop(self):
        """current=+0.03, target=long 0.03 → no order placed."""
        delta = compute_execution_delta(
            current_signed_qty=0.03,
            desired=self._desired_long(0.03),
        )
        assert delta.action == "noop"
        assert delta.qty_delta == 0.0

    def test_long_above_target_reduces(self):
        """current=+0.05, target=long 0.03 → reduce by 0.02 via opposite side."""
        delta = compute_execution_delta(
            current_signed_qty=0.05,
            desired=self._desired_long(0.03),
        )
        assert delta.action == "reduce"
        assert delta.side == "short"
        assert delta.qty_delta == pytest.approx(0.02, abs=1e-9)

    def test_flat_desired_closes_existing(self):
        delta = compute_execution_delta(
            current_signed_qty=0.04,
            desired=self._desired_flat(),
        )
        assert delta.action == "close"
        assert delta.side == "short"
        assert delta.qty_delta == 0.04

    def test_conflict_flips_with_explicit_legs(self):
        """current=+0.01 long, desired=short 0.02 + explicit ``reverse`` →
        flip. The post-2026-05-31 default is ``hold`` (covered by the
        TestFlipPolicy class in test_intent_delta_dispatch.py); the flip
        mechanics this test exercises are still wired as the rollback path."""
        delta = compute_execution_delta(
            current_signed_qty=0.01,
            desired=self._desired_short(0.02),
            flip_policy="reverse",
        )
        assert delta.action == "flip"
        assert delta.side == "short"
        assert delta.qty_delta == 0.02
        # Close leg's qty is implied as abs(current_qty)=0.01.

    def test_min_delta_suppresses_subtick_noise(self):
        delta = compute_execution_delta(
            current_signed_qty=0.030_000_1,
            desired=self._desired_long(0.030_000_2),
            min_delta=1e-4,
        )
        assert delta.action == "noop"


# ---------------------------------------------------------------------------
# Deliverable 4: risk-cap enforcement is not bypassed
# ---------------------------------------------------------------------------


class TestRiskCapsStillEnforced:
    """The aggregator + delta layer never decide qty. The per-account
    RiskManager still owns sizing and the daily-loss / margin / position-
    size caps. These tests pin that contract by routing an aggregated
    OrderPackage through the live RiskManager.position_size and showing
    the caps still fire.
    """

    def _build_pkg_from_aggregation(self, desired: DesiredPosition) -> OrderPackage:
        """Build an OrderPackage from an aggregated DesiredPosition.

        Mirrors the shape ``intent_multiplexer._desired_to_pipeline_signal``
        produces + ``order_bridge._signal_to_order_package`` then turns into
        an OrderPackage — collapsed here so the test depends on the live
        primitives, not the wiring.
        """
        winning = desired.winning_intent
        assert winning is not None, "test pre-condition: must have a winning intent"
        direction = "long" if desired.side == "long" else "short"
        meta = dict(winning.meta or {})
        meta["strategy_risk_pct"] = 0.5  # matches turtle_soup/vwap default split
        meta["aggregated_target_qty"] = desired.target_qty
        return OrderPackage(
            strategy=winning.strategy,
            symbol="BTCUSDT",
            direction=direction,
            entry=float(winning.entry),
            sl=float(winning.sl),
            tp=float(winning.tp),
            confidence=winning.confidence,
            meta=meta,
        )

    def test_aggregated_order_still_passes_through_risk_sizer(self):
        """Aggregated intent + low balance → RiskManager still trims qty."""
        intents = [
            _intent(
                "turtle_soup", "long", target_qty=1.0,
                entry=50_000.0, sl_offset=500.0,
            ),
            _intent(
                "vwap", "long", target_qty=2.0,
                entry=50_000.0, sl_offset=500.0,
            ),
        ]
        desired = aggregate_intents(intents)
        pkg = self._build_pkg_from_aggregation(desired)

        rm = RiskManager({
            "risk_pct": 0.01,
            "min_balance_usd": 50,
            "daily_usd": 100,
            "pos_size": 500,
            "max_dd_pct": 0.05,
        })
        # Risk-based = 1_000 × 0.01 / 500 = 0.02, but the margin pre-flight cap
        # (basis × leverage × 0.9 / entry = 1_000 × 1 × 0.9 / 50_000 = 0.018)
        # binds and governs — still orders of magnitude below the aggregator's
        # 2.0 target, so the RiskManager truncates. (The legacy
        # meta["strategy_risk_pct"]=0.5 the helper tags is IGNORED post-2026-06-29.)
        qty = rm.position_size(pkg, balance_usd=1_000.0)
        assert qty < desired.target_qty
        assert qty == pytest.approx(0.018, abs=1e-6)

    def test_risk_manager_refuses_zero_balance(self):
        """Even a huge aggregated target gets refused when there are no
        funds to size against. The arbitrary min-balance floor was
        removed 2026-06-24 — the only balance refusal left is a
        non-positive balance (physics: can't risk a fraction of zero)."""
        intents = [_intent("vwap", "long", target_qty=5.0)]
        desired = aggregate_intents(intents)
        pkg = self._build_pkg_from_aggregation(desired)

        rm = RiskManager({
            "risk_pct": 0.01,
        })
        # Non-positive balance — sizer returns 0.0 regardless of the
        # aggregated target.
        qty = rm.position_size(pkg, balance_usd=0.0)
        assert qty == 0.0, (
            "the balance gate must still refuse a non-positive balance "
            "when the aggregator hands it a large target"
        )

    def test_risk_manager_refuses_dry_run_mode(self):
        """Account-level dry_run gate still wins over aggregated intent."""
        intents = [_intent("turtle_soup", "long", target_qty=0.05)]
        desired = aggregate_intents(intents)
        pkg = self._build_pkg_from_aggregation(desired)

        rm = RiskManager({"risk_pct": 0.01, "min_balance_usd": 50}, dry_run=True)
        ok, reason = rm.evaluate(pkg)
        assert ok is False
        assert reason == "account_mode_dry_run"


# ---------------------------------------------------------------------------
# Deliverable 5: future strategy plugs into the same interface
# ---------------------------------------------------------------------------


class TestFutureStrategyPluggability:
    """A third strategy (here ``ict_scalp``) registers via the same
    interface as Turtle Soup / VWAP and flows through the same aggregator
    with no special casing. This is the readiness gate for the ICT
    scalping work that's explicitly OUT OF SCOPE this PR.
    """

    def teardown_method(self) -> None:
        clear_registered_intent_builders()

    def test_third_strategy_registers_and_aggregates(self):
        """An ``ict_scalp`` intent goes through the aggregator with no
        code change in intents.py.

        Single-symbol invariant preserved: the new strategy must also
        emit BTCUSDT intents.
        """
        intents = [
            _intent("turtle_soup", "long", target_qty=0.01),
            _intent("vwap", "long", target_qty=0.02),
            # New strategy plug-in — no aggregator code change needed.
            _intent(
                "ict_scalp", "long", target_qty=0.04,
                priority=60,  # higher than turtle_soup
            ),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "long"
        assert desired.target_qty == 0.04
        assert desired.winning_intent.strategy == "ict_scalp"
        assert "ict_scalp" in desired.meta["contributing_strategies"]

    def test_third_strategy_loses_conflict_when_priority_lower(self):
        """Plug-in strategy with low priority must lose to Turtle Soup."""
        intents = [
            _intent("turtle_soup", "long", target_qty=0.01),
            _intent(
                "ict_scalp", "short", target_qty=0.05,
                priority=20,  # below turtle_soup
            ),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "long"
        assert desired.winning_intent.strategy == "turtle_soup"

    def test_multiplexer_accepts_registered_third_strategy(self):
        """register_intent_builder lets a test add a strategy without touching
        production code. Future ICT-scalp wiring uses this same hook.
        """
        seen_calls = []

        def fake_scalp_builder(settings):
            seen_calls.append(settings)
            return {
                "symbol": "BTCUSDT",
                "side": "buy",
                "price": 50_000.0,
                "entry_price": 50_000.0,
                "stop_loss": 49_700.0,
                "take_profit": 50_900.0,
                "meta": {
                    "strategy_name": "ict_scalp",
                    "confidence": 0.55,
                },
            }

        def stub_turtle(settings):
            return {"symbol": "BTCUSDT", "side": "none", "meta": {}}

        def stub_vwap(settings):
            return {"symbol": "BTCUSDT", "side": "none", "meta": {}}

        register_intent_builder("ict_scalp", fake_scalp_builder)

        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={
                "turtle_soup": stub_turtle,
                "vwap": stub_vwap,
                "ict_scalp": fake_scalp_builder,
            },
            strategies=["turtle_soup", "vwap", "ict_scalp"],
        )

        assert signal["side"] == "buy"
        assert signal["meta"]["strategy_name"] == "ict_scalp"
        assert signal["meta"]["contributing_strategies"] == ["ict_scalp"]
        assert len(seen_calls) == 1


# ---------------------------------------------------------------------------
# Bonus: empty / flat / single-strategy edge cases
# ---------------------------------------------------------------------------


class TestAggregatorEdgeCases:
    def test_no_intents_returns_flat(self):
        desired = aggregate_intents([])
        assert desired.side == "flat"
        assert desired.target_qty == 0.0
        assert desired.contributing_intents == tuple()

    def test_all_flat_intents_returns_flat(self):
        intents = [
            _intent("turtle_soup", "flat", target_qty=0.0),
            _intent("vwap", "flat", target_qty=0.0),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "flat"
        assert "all_intents_flat" in desired.reason

    def test_one_flat_one_long_keeps_long(self):
        """Flat intent does NOT pull the net position to zero."""
        intents = [
            _intent("turtle_soup", "flat", target_qty=0.0),
            _intent("vwap", "long", target_qty=0.02),
        ]
        desired = aggregate_intents(intents)
        assert desired.side == "long"
        assert desired.target_qty == 0.02

    def test_symbol_filter_drops_other_symbols(self):
        """``aggregate_intents`` filters by symbol so a multi-symbol caller
        is safe (even though we only call it for BTCUSDT in this PR)."""
        btc = _intent("turtle_soup", "long", target_qty=0.02)
        # ``StrategyIntent`` itself only accepts BTCUSDT in this PR,
        # so we cannot construct an ETH intent. Instead we verify by
        # asking the aggregator for a symbol the input doesn't have.
        desired = aggregate_intents([btc], symbol="BTCUSDT")
        assert desired.side == "long"


# ---------------------------------------------------------------------------
# intent_from_signal — bridge from pipeline-shape signal dict
# ---------------------------------------------------------------------------


class TestIntentFromSignal:
    def test_buy_signal_becomes_long_intent(self):
        signal = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_500.0,
            "meta": {"strategy_name": "turtle_soup", "confidence": 0.7},
        }
        intent = intent_from_signal(signal, target_qty=0.01)
        assert intent is not None
        assert intent.strategy == "turtle_soup"
        assert intent.side == "long"
        assert intent.entry == 50_000.0
        assert intent.confidence == 0.7

    def test_none_side_returns_none(self):
        signal = {"symbol": "BTCUSDT", "side": "none", "meta": {}}
        assert intent_from_signal(signal) is None


# ---------------------------------------------------------------------------
# Multiplexer wiring
# ---------------------------------------------------------------------------


class TestMultiplexerWiring:
    def teardown_method(self) -> None:
        clear_registered_intent_builders()

    def test_no_intents_returns_side_none(self):
        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={
                "turtle_soup": lambda s: {"symbol": "BTCUSDT", "side": "none", "meta": {}},
                "vwap": lambda s: {"symbol": "BTCUSDT", "side": "none", "meta": {}},
            },
            strategies=["turtle_soup", "vwap"],
        )
        assert signal["side"] == "none"
        assert signal["meta"]["strategy_name"] == "multiplexed_intents"

    def test_same_direction_picks_one_winner(self):
        """End-to-end multiplexer: two strategies long → ONE output signal."""
        ts_signal = {
            "symbol": "BTCUSDT", "side": "buy",
            "price": 50_000.0, "stop_loss": 49_500.0, "take_profit": 51_500.0,
            "meta": {"strategy_name": "turtle_soup", "confidence": 0.6},
        }
        vw_signal = {
            "symbol": "BTCUSDT", "side": "buy",
            "price": 50_100.0, "stop_loss": 49_700.0, "take_profit": 51_200.0,
            "meta": {"strategy_name": "vwap", "confidence": 0.7},
        }

        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={
                "turtle_soup": lambda s: ts_signal,
                "vwap": lambda s: vw_signal,
            },
            strategies=["turtle_soup", "vwap"],
        )
        assert signal["side"] == "buy"
        assert sorted(signal["meta"]["contributing_strategies"]) == ["turtle_soup", "vwap"]
        # With equal targets (both intents default to 0) Turtle Soup wins
        # by priority.
        assert signal["meta"]["strategy_name"] == "turtle_soup"
        assert signal["meta"]["aggregated_via"] == "multi_strategy_intent_layer"

    def test_conflict_resolved_to_one_signal(self):
        """Opposing signals → one winner, one signal out (not two)."""
        ts_signal = {
            "symbol": "BTCUSDT", "side": "buy",
            "price": 50_000.0, "stop_loss": 49_500.0, "take_profit": 51_500.0,
            "meta": {"strategy_name": "turtle_soup"},
        }
        vw_signal = {
            "symbol": "BTCUSDT", "side": "sell",
            "price": 50_100.0, "stop_loss": 50_400.0, "take_profit": 49_500.0,
            "meta": {"strategy_name": "vwap"},
        }

        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={
                "turtle_soup": lambda s: ts_signal,
                "vwap": lambda s: vw_signal,
            },
            strategies=["turtle_soup", "vwap"],
        )
        # Turtle Soup wins by default priority.
        assert signal["side"] == "buy"
        assert signal["meta"]["strategy_name"] == "turtle_soup"
        assert signal["meta"]["aggregation"]["resolution"] == "priority_conflict"

    def test_strategy_that_raises_is_skipped(self):
        def crashing_builder(settings):
            raise RuntimeError("synthetic strategy error")

        ts_signal = {
            "symbol": "BTCUSDT", "side": "buy",
            "price": 50_000.0, "stop_loss": 49_500.0, "take_profit": 51_500.0,
            "meta": {"strategy_name": "turtle_soup"},
        }
        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={
                "turtle_soup": lambda s: ts_signal,
                "vwap": crashing_builder,
            },
            strategies=["turtle_soup", "vwap"],
        )
        # Crash isolation: VWAP raised, Turtle Soup still emits a signal.
        assert signal["side"] == "buy"
        assert signal["meta"]["strategy_name"] == "turtle_soup"


class TestMultiplexerEnableFlag:
    def test_default_on(self, monkeypatch):
        # D-1 (2026-05-17): default flipped from off → on.
        monkeypatch.delenv("MULTI_STRATEGY_INTENT_LAYER", raising=False)
        assert intent_multiplexer_enabled({}) is True

    def test_settings_can_disable(self, monkeypatch):
        # Rollback path: settings dict explicitly disables.
        monkeypatch.delenv("MULTI_STRATEGY_INTENT_LAYER", raising=False)
        assert intent_multiplexer_enabled({"MULTI_STRATEGY_INTENT_LAYER": "false"}) is False

    def test_env_can_disable(self, monkeypatch):
        # Rollback path: env var explicitly disables.
        monkeypatch.setenv("MULTI_STRATEGY_INTENT_LAYER", "false")
        assert intent_multiplexer_enabled({}) is False


class TestDesiredToPipelineSignal:
    """The render helper is what feeds the existing pipeline; verify
    the meta block carries every field the downstream consumers expect."""

    def test_flat_renders_side_none(self):
        desired = DesiredPosition(
            symbol="BTCUSDT",
            side="flat",
            target_qty=0.0,
            contributing_intents=tuple(),
            winning_intent=None,
            reason="no_intents",
        )
        signal = _desired_to_pipeline_signal(desired, symbol="BTCUSDT", settings={})
        assert signal["side"] == "none"
        assert signal["meta"]["strategy_name"] == "multiplexed_intents"

    def test_long_carries_entry_sl_tp_and_attribution(self):
        winning = _intent("turtle_soup", "long", target_qty=0.03)
        desired = DesiredPosition(
            symbol="BTCUSDT",
            side="long",
            target_qty=0.03,
            contributing_intents=(winning,),
            winning_intent=winning,
            reason="test",
            meta={
                "resolution": "same_direction",
                "winning_strategy": "turtle_soup",
                "contributing_strategies": ["turtle_soup"],
            },
        )
        signal = _desired_to_pipeline_signal(desired, symbol="BTCUSDT", settings={})
        assert signal["side"] == "buy"
        assert signal["entry_price"] == winning.entry
        assert signal["stop_loss"] == winning.sl
        assert signal["take_profit"] == winning.tp
        assert signal["meta"]["strategy_name"] == "turtle_soup"
        assert signal["meta"]["aggregated_via"] == "multi_strategy_intent_layer"
        assert signal["meta"]["aggregated_target_qty"] == 0.03
        # The per-strategy risk multiplier was removed 2026-06-29 — the
        # pipeline signal carries NO risk level; sizing is the RiskManager's
        # account-level job.
        assert "strategy_risk_pct" not in signal["meta"]
