import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from src.core.automated_trading_loop import KillZoneScalperBot
from src.runtime.pipeline import (
    STRATEGIES,
    STRATEGY_RISK_PCT,
    breakout_model_signal_builder,
    killzone_signal_builder,
    multiplexed_signal_builder,
    run_pipeline,
)
import src.runtime.pipeline as _pipeline_mod


class DummyExchangeClient:
    def __init__(self):
        self.calls = []

    def place_order(self, **order):
        self.calls.append(order)
        return {"ok": True, "order": order}


class DummyTelegramClient:
    def __init__(self):
        self.messages = []

    def send_message(self, message: str):
        self.messages.append(message)


def no_signal_builder(settings):
    return {
        "symbol": settings["SYMBOL"],
        "side": "none",
        "qty": 0,
    }


def forced_long_builder(settings):
    return {
        "symbol": settings["SYMBOL"],
        "side": "buy",
        "qty": 1,
    }


def test_pipeline_skips_when_no_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert result["order_result"]["status"] == "skipped"
    assert result["order_result"]["reason"] == "no_signal"
    assert exchange.calls == []
    assert len(telegram.messages) >= 0


def test_pipeline_places_order_for_forced_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "dry_run"
    assert len(exchange.calls) == 0


class FailingTelegramClient:
    def send_message(self, message: str):
        raise RuntimeError("telegram send failed")


def test_pipeline_does_not_crash_when_telegram_notification_fails():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = FailingTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "dry_run"


def test_pipeline_returns_skipped_status_for_no_signal():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert result["order_result"]["status"] == "skipped"
    assert result["order_result"]["reason"] == "no_signal"


def test_pipeline_telegram_message_includes_skipped_status():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=no_signal_builder,
    )

    assert len(telegram.messages) == 1
    assert "skipped" in telegram.messages[0].lower()
    assert "BTCUSDT" in telegram.messages[0]


def test_pipeline_telegram_message_includes_dry_run_status():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert len(telegram.messages) == 1
    assert "dry_run" in telegram.messages[0].lower()
    assert "BTCUSDT" in telegram.messages[0]


def test_pipeline_telegram_message_includes_failed_validation_reason():
    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "false",
        "MAX_QTY": "10",
    }
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert len(telegram.messages) == 1
    assert "failed_validation" in telegram.messages[0].lower()
    assert "ALLOW_LIVE_TRADING" in telegram.messages[0]


class StubStrategyExchange:
    def __init__(self):
        self.balance_calls = 0

    def get_balance(self):
        self.balance_calls += 1
        return {"total": {"USDT": 1000}}

    def get_ohlcv(self, symbol="BTC/USDT", timeframe="15m", limit=100):
        rows = [
            [1, 100, 110, 95, 105, 1],
            [2, 105, 115, 100, 111, 1],
            [3, 111, 120, 108, 118, 1],
        ]
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return df

    def get_price(self, symbol="BTC/USDT"):
        return 112

    def place_market_order(self, symbol, side, amount, params=None):
        return {"symbol": symbol, "side": side, "amount": amount, "params": params or {}}


def test_killzone_bot_accepts_injected_exchange():
    exchange = StubStrategyExchange()
    bot = KillZoneScalperBot(exchange=exchange, symbol="BTC/USDT")
    assert bot.exchange is exchange
    assert bot.symbol == "BTC/USDT"


def test_killzone_bot_requires_exchange():
    with pytest.raises(ValueError, match="exchange connector"):
        KillZoneScalperBot(exchange=None)


def test_killzone_signal_builder_selects_binance(monkeypatch):
    captured = {}

    class FakeBinanceConnector:
        def __init__(self, api_key=None, api_secret=None, testnet=True):
            captured["connector"] = "binance"
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet

    def fake_analyze_market(self):
        captured["bot_symbol"] = self.symbol
        captured["bot_exchange_class"] = self.exchange.__class__.__name__
        return "long", 50000.0, {"type": "bullish", "top": 50100, "bottom": 49900, "idx": 199}

    monkeypatch.setattr("src.exchange.binance_connector.BinanceConnector", FakeBinanceConnector)
    monkeypatch.setattr(KillZoneScalperBot, "analyze_market", fake_analyze_market)

    settings = {
        "EXCHANGE": "binance",
        "MODE": "testnet",
        "BINANCE_API_KEY": "binance_key",
        "BINANCE_API_SECRET": "binance_secret",
        "SYMBOL": "BTCUSDT",
        "MAX_QTY": "2",
    }

    signal = killzone_signal_builder(settings)

    assert captured["connector"] == "binance"
    assert captured["api_key"] == "binance_key"
    assert captured["api_secret"] == "binance_secret"
    assert captured["testnet"] is True
    assert captured["bot_symbol"] == "BTCUSDT"
    assert signal["side"] == "buy"
    assert signal["qty"] == 2.0
    assert signal["meta"]["exchange"] == "binance"


def test_killzone_signal_builder_selects_bybit(monkeypatch):
    captured = {}

    class FakeBybitConnector:
        def __init__(self, api_key=None, api_secret=None, testnet=True):
            captured["connector"] = "bybit"
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet

    def fake_analyze_market(self):
        captured["bot_symbol"] = self.symbol
        captured["bot_exchange_class"] = self.exchange.__class__.__name__
        return "short", 50000.0, {"type": "bearish", "top": 50100, "bottom": 49900, "idx": 199}

    monkeypatch.setattr("src.exchange.bybit_connector.BybitConnector", FakeBybitConnector)
    monkeypatch.setattr(KillZoneScalperBot, "analyze_market", fake_analyze_market)

    settings = {
        "EXCHANGE": "bybit",
        "MODE": "live",
        "BYBIT_API_KEY": "bybit_key",
        "BYBIT_API_SECRET": "bybit_secret",
        "SYMBOL": "BTC/USDT:USDT",
        "MAX_QTY": "3",
    }

    signal = killzone_signal_builder(settings)

    assert captured["connector"] == "bybit"
    assert captured["api_key"] == "bybit_key"
    assert captured["api_secret"] == "bybit_secret"
    assert captured["testnet"] is False
    assert captured["bot_symbol"] == "BTC/USDT:USDT"
    assert signal["side"] == "sell"
    assert signal["qty"] == 3.0
    assert signal["meta"]["exchange"] == "bybit"


def test_killzone_signal_builder_raises_for_unsupported_exchange():
    settings = {
        "EXCHANGE": "kraken",
        "MODE": "testnet",
        "SYMBOL": "BTCUSDT",
    }

    with pytest.raises(ValueError, match="Unsupported EXCHANGE value"):
        killzone_signal_builder(settings)


def test_pipeline_skips_when_halted(tmp_path, monkeypatch):
    flag = tmp_path / "trader_halt.flag"
    flag.write_text("halted")
    monkeypatch.setattr("src.runtime.pipeline.HALT_FLAG_PATH", str(flag))

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "halted"
    assert result["order_result"]["reason"] == "halt_flag_active"
    assert exchange.calls == []


def test_pipeline_runs_normally_when_not_halted(tmp_path, monkeypatch):
    flag = tmp_path / "trader_halt.flag"
    # flag intentionally NOT created
    monkeypatch.setattr("src.runtime.pipeline.HALT_FLAG_PATH", str(flag))

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
    exchange = DummyExchangeClient()
    telegram = DummyTelegramClient()

    result = run_pipeline(
        settings,
        exchange_client=exchange,
        telegram_client=telegram,
        signal_builder=forced_long_builder,
    )

    assert result["order_result"]["status"] == "dry_run"
    assert exchange.calls == []


# ---------------------------------------------------------------------------
# Strategy multiplexer tests
# ---------------------------------------------------------------------------

def _make_signal(side="buy", qty=1.0, strategy="test"):
    return {"symbol": "BTCUSDT", "side": side, "qty": qty,
            "meta": {"strategy_name": strategy}}


def _flat_signal(symbol="BTCUSDT"):
    return {"symbol": symbol, "side": "none", "qty": 0}


def test_multi_strategy_pipeline_strategies_list_contains_expected_strategies():
    assert "breakout_confirmation" in STRATEGIES
    assert "vwap" in STRATEGIES
    assert "killzone" in STRATEGIES, (
        "killzone was the default single-strategy path but was missing from "
        "STRATEGIES so STRATEGY=multiplexed silently skipped it (CP-2026-04-29-07)."
    )
    # M7 Phase 2.6 (CP-14): "ict" registered as the last fallback so it
    # only fires when nothing else fires.
    assert "ict" in STRATEGIES
    assert STRATEGIES[-1] == "ict", (
        "ICT must be the last fallback so it cannot pre-empt the "
        "existing breakout / VWAP strategies; existing tick outcomes "
        "must remain unchanged."
    )


def test_multiplexed_killzone_position_before_ict():
    """killzone must appear before ict in STRATEGIES so it is tried first."""
    assert STRATEGIES.index("killzone") < STRATEGIES.index("ict")


def test_multiplexed_killzone_fires_when_breakout_and_vwap_flat(monkeypatch):
    """killzone (third in STRATEGIES) fires when breakout_confirmation and vwap
    both return flat — confirms the missing-killzone gap is closed (CP-2026-04-29-07)."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "killzone",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="killzone"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "ict",
        lambda s: _make_signal(side="sell", qty=1.0, strategy="ict"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "killzone", (
        "killzone must fire before ict when breakout and vwap are flat"
    )


def test_multi_strategy_pipeline_ict_runs_only_after_others_flat(monkeypatch):
    """ICT (last in STRATEGIES) must not be invoked when an earlier
    strategy already produced an actionable signal — this is the
    behaviour-preservation guarantee for CP-14."""
    ict_called = []

    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="vwap"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "ict",
        lambda s: ict_called.append(True)
        or _make_signal(side="sell", qty=1.0, strategy="ict"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "vwap"
    assert ict_called == [], "ICT must not run when an earlier strategy already fires"


def test_multi_strategy_pipeline_ict_fires_when_others_flat(monkeypatch):
    """When breakout, VWAP, and killzone all return flat, ICT (last in the list)
    is invoked and its actionable signal is returned."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "killzone",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "ict",
        lambda s: _make_signal(side="buy", qty=2.5, strategy="ict"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "2.5"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert abs(signal["qty"] - 2.5 * STRATEGY_RISK_PCT["ict"]) < 1e-9  # 2.5 * 0.3
    assert signal["meta"]["strategy_name"] == "ict"


def test_multi_strategy_pipeline_first_wins(monkeypatch):
    """First strategy returns actionable; second builder must not be called."""
    second_called = []

    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="breakout_confirmation"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: second_called.append(True) or _make_signal(side="sell", qty=1.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "breakout_confirmation"
    assert second_called == [], "second strategy must not be invoked when first fires"


def test_multi_strategy_pipeline_fallback_to_second(monkeypatch):
    """First strategy flat; second produces the actionable signal."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="sell", qty=2.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "2"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "sell"
    assert abs(signal["qty"] - 2.0 * STRATEGY_RISK_PCT["vwap"]) < 1e-9  # 2.0 * 0.3
    assert signal["meta"]["strategy_name"] == "vwap"


def test_multi_strategy_pipeline_no_signal_when_all_flat(monkeypatch):
    """All strategies flat → side=none returned."""
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "breakout_confirmation", lambda s: _flat_signal())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "vwap", lambda s: _flat_signal())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "killzone", lambda s: _flat_signal())
    monkeypatch.setitem(_pipeline_mod._STRATEGY_BUILDERS, "ict", lambda s: _flat_signal())

    settings = {"SYMBOL": "BTCUSDT"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "none"
    assert float(signal["qty"]) == 0


def test_multi_strategy_pipeline_skips_erroring_strategy(monkeypatch):
    """Strategy that raises is skipped; next strategy wins."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: (_ for _ in ()).throw(RuntimeError("exchange down")),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "1"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == "vwap"


def test_multi_strategy_pipeline_per_strategy_sizing_no_compounding(monkeypatch):
    """Each strategy uses its own qty; quantities must not be summed."""
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=3.0, strategy="breakout_confirmation"),
    )
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=5.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "MAX_QTY": "3"}
    signal = multiplexed_signal_builder(settings)

    # Only the first-winning strategy qty is returned, no summing; scaled by risk fraction
    assert abs(signal["qty"] - 3.0 * STRATEGY_RISK_PCT["breakout_confirmation"]) < 1e-9  # 3.0 * 0.4
    assert signal["meta"]["strategy_name"] == "breakout_confirmation"


def test_multi_strategy_pipeline_via_env_var(monkeypatch):
    """STRATEGY=multiplexed env var activates the multiplexer through run_pipeline."""
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _make_signal(side="buy", qty=1.0, strategy="breakout_confirmation"),
    )

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
    telegram = DummyTelegramClient()

    result = run_pipeline(settings, telegram_client=telegram)

    assert result["signal"]["side"] == "buy"
    assert result["signal"]["meta"]["strategy_name"] == "breakout_confirmation"
    assert result["order_result"]["status"] == "dry_run"


def test_multi_strategy_pipeline_respects_max_qty_via_env_var(monkeypatch):
    """
    M6 risk-cap guarantee: when the multiplexer is active and a strategy
    returns a qty above MAX_QTY, safe_place_order must still reject the
    order. Proves the combined execution path does not bypass risk caps.
    """
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: _flat_signal(),
    )
    # vwap claims qty=10, exceeding MAX_QTY=1 — caps must still bite.
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "vwap",
        lambda s: _make_signal(side="buy", qty=10.0, strategy="vwap"),
    )

    settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
    telegram = DummyTelegramClient()

    result = run_pipeline(settings, telegram_client=telegram)

    assert result["signal"]["meta"]["strategy_name"] == "vwap"
    assert result["order_result"]["status"] == "failed_validation"
    assert "MAX_QTY" in result["order_result"]["reason"]


def test_multi_strategy_pipeline_respects_max_position_usd(monkeypatch):
    """
    M6 risk-cap guarantee: MAX_POSITION_USD must abort multiplexed orders
    just like single-strategy orders. Proves notional caps apply across
    breakout_confirmation + vwap combined execution path.
    """
    monkeypatch.setenv("STRATEGY", "multiplexed")
    monkeypatch.setitem(
        _pipeline_mod._STRATEGY_BUILDERS,
        "breakout_confirmation",
        lambda s: {
            "symbol": "BTCUSDT",
            "side": "buy",
            "qty": 1.0,
            "meta": {"strategy_name": "breakout_confirmation", "price": 100000.0},
        },
    )

    settings = {
        "SYMBOL": "BTCUSDT",
        "DRY_RUN": "true",
        "MAX_QTY": "10",
        "MAX_POSITION_USD": "5000",  # 1 * 100_000 = 100_000 USD >> 5_000
    }
    telegram = DummyTelegramClient()

    with pytest.raises(ValueError, match="MAX_POSITION_USD"):
        run_pipeline(settings, telegram_client=telegram)


# ---------------------------------------------------------------------------
# PR 6 — breakout_model_signal_builder uses fixed-qty sizing (Option B)
# Verifies qty == MAX_QTY regardless of atr_14 value in model_signal.
# ---------------------------------------------------------------------------

def _make_model_signal(signal="CONFIRM", atr_14=None):
    return {
        "signal": signal,
        "prob_tp": 0.75,
        "entry_price": 60000.0,
        "atr_14": atr_14,
    }


def _breakout_settings(max_qty="0.005"):
    return {
        "SYMBOL": "BTCUSDT",
        "EXCHANGE": "bybit",
        "BYBIT_API_KEY": "fake",
        "BYBIT_API_SECRET": "fake",
        "MAX_QTY": max_qty,
        "RISK_PER_TRADE": "0.01",
    }


@pytest.mark.parametrize("atr_14", [0, 0.0, 150.0, 9999.0, None])
def test_breakout_builder_uses_max_qty_regardless_of_atr(atr_14, monkeypatch):
    """Option B: qty must always equal MAX_QTY; atr_14 value must not affect it."""
    fake_candles = [[i, 60000, 60100, 59900, 60050, 1.0] for i in range(100)]
    fake_exchange = MagicMock()
    fake_exchange.get_ohlcv.return_value = fake_candles

    fake_manager = MagicMock()
    fake_manager.get_signal.return_value = _make_model_signal(atr_14=atr_14)

    with (
        patch("src.runtime.pipeline._build_killzone_exchange", return_value=fake_exchange),
        patch("src.strategies_manager.StrategyManager", return_value=fake_manager),
    ):
        result = breakout_model_signal_builder(_breakout_settings(max_qty="0.005"))

    assert result["qty"] == float("0.005"), (
        f"Expected qty=0.005 (MAX_QTY) for atr_14={atr_14}, got {result['qty']}"
    )
    assert result["side"] == "buy"


# ---------------------------------------------------------------------------
# S-005 M1 — per-strategy qty scaling (STRATEGY_RISK_PCT)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy,raw_qty,expected_scale", [
    ("breakout_confirmation", 1.0, 0.4),
    ("vwap",                  1.0, 0.3),
    ("ict",                   1.0, 0.3),
    ("killzone",              1.0, 1.0),  # default: no entry in STRATEGY_RISK_PCT
])
def test_runtime_pipeline_strategy_qty_scaling(strategy, raw_qty, expected_scale, monkeypatch):
    """Multiplexer applies STRATEGY_RISK_PCT to winning signal qty (S-005 M1)."""
    # Make *only* the target strategy fire; everything else flat.
    for name in STRATEGIES:
        if name == strategy:
            monkeypatch.setitem(
                _pipeline_mod._STRATEGY_BUILDERS,
                name,
                lambda s, _n=name: _make_signal(side="buy", qty=raw_qty, strategy=_n),
            )
        else:
            monkeypatch.setitem(
                _pipeline_mod._STRATEGY_BUILDERS,
                name,
                lambda s: _flat_signal(),
            )

    settings = {"SYMBOL": "BTCUSDT"}
    signal = multiplexed_signal_builder(settings)

    assert signal["side"] == "buy"
    assert signal["meta"]["strategy_name"] == strategy
    assert abs(signal["qty"] - raw_qty * expected_scale) < 1e-9, (
        f"{strategy}: expected qty={raw_qty * expected_scale}, got {signal['qty']}"
    )


def test_runtime_pipeline_strategy_risk_pct_sums_to_one():
    """breakout+vwap+ict fractions must sum to 1.0 (S-005 M1 invariant)."""
    total = sum(STRATEGY_RISK_PCT.values())
    assert abs(total - 1.0) < 1e-9, f"STRATEGY_RISK_PCT sums to {total}, expected 1.0"
