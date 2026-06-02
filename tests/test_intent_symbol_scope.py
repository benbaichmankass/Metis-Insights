"""Per-strategy symbol-scope gate in the intent multiplexer (2026-06-02).

A strategy is evaluated/emits only on the symbols it declares in
config/strategies.yaml ``symbols:``. Added with the WS-A metals sleeve so
``mgc_pullback_1d`` (gold) / ``mhg_pullback_1d`` (copper) never trade MES or
each other's metal, and the crypto strategies stop trading MES — while
single-symbol accounts (bybit_2 = BTCUSDT) are unaffected.
"""
from __future__ import annotations

from src.runtime.intent_multiplexer import _collect_intents, _strategy_symbol_scope


def _buy_builder(settings):
    sym = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    return {
        "symbol": sym,
        "side": "buy",
        "price": 100.0,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 150.0,
        "meta": {"strategy_name": "test"},
    }


def _names(intents):
    return {i.strategy for i in intents}


def test_scope_map_reads_declared_symbols():
    scope = _strategy_symbol_scope()
    # The real config declares these single-instrument scopes.
    assert scope.get("mgc_pullback_1d") == {"MGC"}
    assert scope.get("mhg_pullback_1d") == {"MHG"}
    assert "BTCUSDT" in scope.get("vwap", set())
    assert scope.get("mes_trend_long_1d") == {"MES"}


def test_metal_tick_only_runs_its_owner():
    builders = {"mgc_pullback_1d": _buy_builder, "mhg_pullback_1d": _buy_builder,
                "vwap": _buy_builder}
    strategies = ["mgc_pullback_1d", "mhg_pullback_1d", "vwap"]

    on_mgc = _names(_collect_intents(
        {"SYMBOL": "MGC"}, builders=builders, strategies=strategies, target_qty_hint=1.0))
    assert on_mgc == {"mgc_pullback_1d"}  # copper + crypto skipped

    on_mhg = _names(_collect_intents(
        {"SYMBOL": "MHG"}, builders=builders, strategies=strategies, target_qty_hint=1.0))
    assert on_mhg == {"mhg_pullback_1d"}


def test_btc_tick_excludes_metals_strategies():
    builders = {"mgc_pullback_1d": _buy_builder, "vwap": _buy_builder}
    on_btc = _names(_collect_intents(
        {"SYMBOL": "BTCUSDT"}, builders=builders,
        strategies=["mgc_pullback_1d", "vwap"], target_qty_hint=1.0))
    assert "vwap" in on_btc
    assert "mgc_pullback_1d" not in on_btc


def test_unknown_or_undeclared_strategy_is_permissive():
    # A strategy absent from the config (no declared symbols) must NOT be
    # gated — falls through and emits, preserving legacy/test behaviour.
    builders = {"totally_made_up_strategy": _buy_builder}
    on_mgc = _names(_collect_intents(
        {"SYMBOL": "MGC"}, builders=builders,
        strategies=["totally_made_up_strategy"], target_qty_hint=1.0))
    assert "totally_made_up_strategy" in on_mgc
