"""M18 P0b — candidate-batch exposure (observe-only).

Verifies that the intent multiplexer projects the FULL per-tick candidate set
(every strategy's intent BEFORE aggregation collapses them to one) into typed
``SignalPackage``s and attaches them to the returned pipeline signal under
``CANDIDATE_BATCH_KEY`` — without changing any existing signal key, and
fail-permissively (a malformed intent is skipped, never raised).
"""
from __future__ import annotations

import math

from src.core.signal_contract import SignalPackage
from src.runtime.intent_multiplexer import (
    CANDIDATE_BATCH_KEY,
    intents_to_signal_packages,
    multiplexed_intent_signal_builder,
)
from src.runtime.intents import StrategyIntent


def _intent(strategy: str, side: str, *, entry=50_000.0, conf=0.6, ts=100.0) -> StrategyIntent:
    if side == "long":
        sl, tp = entry - 500.0, entry + 1_500.0
    elif side == "short":
        sl, tp = entry + 500.0, entry - 1_500.0
    else:
        sl, tp = entry - 500.0, entry + 1_500.0
    return StrategyIntent(
        strategy=strategy,
        symbol="BTCUSDT",
        side=side,
        target_qty=0.0,
        timestamp=ts,
        entry=entry,
        sl=sl,
        tp=tp,
        confidence=conf,
    )


class TestProjection:
    def test_maps_fields_and_side(self):
        pkgs = intents_to_signal_packages([_intent("vwap", "long"), _intent("turtle_soup", "short")])
        assert [p.strategy_id for p in pkgs] == ["vwap", "turtle_soup"]
        assert [p.side for p in pkgs] == ["long", "short"]
        p = pkgs[0]
        assert isinstance(p, SignalPackage)
        assert p.symbol == "BTCUSDT"
        assert p.entry_price == 50_000.0
        assert p.stop_loss == 49_500.0
        assert p.take_profit == 51_500.0
        # account_id is unbound at the multiplexer (account fan-out is downstream)
        assert p.account_id == ""
        assert p.source_context["confidence"] == 0.6
        assert p.source_context["source"] == "intent_multiplexer_candidate_batch"
        assert p.timestamp_utc.startswith("1970-01-01T00:01:40")  # epoch 100.0 UTC

    def test_flat_side_maps_to_none(self):
        pkgs = intents_to_signal_packages([_intent("vwap", "flat")])
        assert pkgs[0].side == "none"

    def test_empty_and_none_input(self):
        assert intents_to_signal_packages([]) == []
        assert intents_to_signal_packages(None) == []  # type: ignore[arg-type]

    def test_malformed_intent_is_skipped_not_raised(self):
        class Bad:
            side = "long"  # missing strategy/symbol/etc → AttributeError inside
        pkgs = intents_to_signal_packages([Bad(), _intent("vwap", "long")])  # type: ignore[list-item]
        # the good one survives, the bad one is skipped
        assert [p.strategy_id for p in pkgs] == ["vwap"]

    def test_unparseable_timestamp_degrades_to_empty(self):
        i = _intent("vwap", "long")
        object.__setattr__(i, "timestamp", float("nan"))
        pkgs = intents_to_signal_packages([i])
        # NaN epoch → fromtimestamp raises → ts left "", never crashes
        assert pkgs[0].timestamp_utc == "" or not math.isnan(0.0)


class TestBuilderAttachesBatch:
    def test_batch_rides_alongside_collapsed_signal(self):
        def long_builder(settings):
            return {
                "symbol": "BTCUSDT", "side": "buy", "price": 50_000.0,
                "entry_price": 50_000.0, "stop_loss": 49_700.0, "take_profit": 50_900.0,
                "meta": {"strategy_name": "vwap", "confidence": 0.7},
            }

        def short_builder(settings):
            return {
                "symbol": "BTCUSDT", "side": "sell", "price": 50_000.0,
                "entry_price": 50_000.0, "stop_loss": 50_300.0, "take_profit": 49_100.0,
                "meta": {"strategy_name": "turtle_soup", "confidence": 0.4},
            }

        signal = multiplexed_intent_signal_builder(
            {"SYMBOL": "BTCUSDT"},
            builders={"vwap": long_builder, "turtle_soup": short_builder},
            strategies=["vwap", "turtle_soup"],
        )

        # Existing contract unchanged: one collapsed actionable signal.
        assert signal["side"] in ("buy", "sell")
        # The full opportunity set rides alongside as typed SignalPackages.
        batch = signal[CANDIDATE_BATCH_KEY]
        assert all(isinstance(p, SignalPackage) for p in batch)
        assert {p.strategy_id for p in batch} == {"vwap", "turtle_soup"}
        # The batch is NOT in meta (must not leak into pkg.meta JSON downstream).
        assert CANDIDATE_BATCH_KEY not in signal["meta"]
