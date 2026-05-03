"""
Offline tests for the VWAP strategy runtime path.

All tests use fake candle data — no exchange calls, no secrets, no network.

Dependency note: requires pandas (listed in requirements.txt).
If pandas is absent the entire module is skipped via pytest.importorskip.
matplotlib is mocked so the pipeline import chain works without it installed.
"""
import sys
import types
from unittest import mock

# Provide a minimal matplotlib stub so pipeline.py can be imported without
# matplotlib installed (matplotlib is a transitive dep of signal_notifications).
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

import pytest

pd = pytest.importorskip("pandas")

# S-012 PR C5: VWAP helpers moved from strategies/vwap_signal_builder.py
# (deleted) into src/units/strategies/vwap.py.
from src.units.strategies.vwap import (
    MIN_CANDLES,
    ENTRY_STD_THRESHOLD,
    build_vwap_signal,
    compute_vwap,
)
from src.runtime.orders import safe_place_order
from src.runtime.pipeline import run_pipeline
from src.runtime.validation import validate_startup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candles(*close_prices, volume=1000.0):
    """Build a minimal OHLCV DataFrame from a list of close prices."""
    rows = []
    for i, close in enumerate(close_prices):
        rows.append({
            "timestamp": i,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": volume,
        })
    return pd.DataFrame(rows)


def _candles_below_vwap():
    """Candles where the last price is well below VWAP (triggers buy)."""
    # High prices dominate the window, then a sharp dip at the end.
    prices = [100, 102, 101, 103, 102, 60]
    return _candles(*prices)


def _candles_above_vwap():
    """Candles where the last price is well above VWAP (triggers sell)."""
    prices = [100, 98, 99, 97, 98, 140]
    return _candles(*prices)


def _candles_near_vwap():
    """Candles where the last price is within 1 std-dev of VWAP (no signal)."""
    prices = [100, 100, 100, 100, 100, 100]
    return _candles(*prices)


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


# ---------------------------------------------------------------------------
# Unit: compute_vwap
# ---------------------------------------------------------------------------

class TestComputeVwap:
    def test_basic_vwap_calculation(self):
        df = _candles(100, 102, 101)
        vwap = compute_vwap(df)
        # typical_price = (high + low + close) / 3
        # high = close + 2, low = close - 2 → typical = close
        # VWAP = mean of close prices (equal volume)
        expected = (100 + 102 + 101) / 3
        assert abs(vwap - expected) < 0.01

    def test_too_few_candles_raises(self):
        df = _candles(100)  # only 1 row
        with pytest.raises(ValueError, match="at least"):
            compute_vwap(df)

    def test_empty_dataframe_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ValueError, match="non-empty"):
            compute_vwap(df)

    def test_zero_volume_raises(self):
        df = _candles(100, 102, volume=0)
        with pytest.raises(ValueError, match="volume"):
            compute_vwap(df)

    def test_missing_columns_raises(self):
        df = pd.DataFrame({"close": [100, 102], "volume": [1, 1]})
        with pytest.raises(ValueError, match="missing columns"):
            compute_vwap(df)

    def test_vwap_weighted_by_volume(self):
        """Higher-volume candles should pull VWAP toward their typical price."""
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100, "volume": 1},
            {"timestamp": 1, "open": 199, "high": 202, "low": 198, "close": 200, "volume": 100},
        ])
        vwap = compute_vwap(df)
        assert vwap > 190, "VWAP should be pulled toward the high-volume candle"


# ---------------------------------------------------------------------------
# Unit: build_vwap_signal
# ---------------------------------------------------------------------------

class TestBuildVwapSignal:
    def test_buy_signal_when_price_below_vwap(self):
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert signal["symbol"] == "BTCUSDT"
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_sell_signal_when_price_above_vwap(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        assert signal["meta"]["current_price"] > signal["meta"]["vwap"]

    def test_no_signal_when_price_near_vwap(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"

    def test_signal_includes_vwap_meta(self):
        df = _candles(100, 102, 101, 103, 100)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert "vwap" in signal["meta"]
        assert "current_price" in signal["meta"]
        assert "std_dev" in signal["meta"]
        assert "deviation_std" in signal["meta"]
        assert "reason" in signal["meta"]

    def test_signal_does_not_carry_qty(self):
        """S-026 G1: strategies emit the trade idea, not the order.
        Sizing is decided per-account by the RiskManager, so the
        strategy package must never carry a top-level ``qty`` field."""
        for df in (_candles_below_vwap(), _candles_above_vwap(), _candles_near_vwap()):
            signal = build_vwap_signal(df, symbol="BTCUSDT")
            assert "qty" not in signal, (
                f"S-026 G1: build_vwap_signal must not emit qty (got {signal!r})"
            )

    def test_insufficient_candles_raises(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT")

    # ----- G5 (CP-2026-05-02-12, option a) — VWAP must populate sl/tp -----

    def test_buy_signal_carries_entry_sl_tp_at_top_level(self):
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        for k in ("entry_price", "stop_loss", "take_profit"):
            assert k in signal, (
                f"BUY signal missing top-level {k}; multi-account dispatch "
                f"requires it (signal_carries_full_sltp gate)"
            )
        # Mean-reversion: TP = VWAP, entry below VWAP, SL further below entry.
        assert signal["take_profit"] == signal["meta"]["vwap"]
        assert signal["entry_price"] < signal["take_profit"]
        assert signal["stop_loss"] < signal["entry_price"]

    def test_sell_signal_carries_entry_sl_tp_at_top_level(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        for k in ("entry_price", "stop_loss", "take_profit"):
            assert k in signal
        # Mean-reversion: TP = VWAP, entry above VWAP, SL further above entry.
        assert signal["take_profit"] == signal["meta"]["vwap"]
        assert signal["entry_price"] > signal["take_profit"]
        assert signal["stop_loss"] > signal["entry_price"]

    def test_no_signal_does_not_emit_sl_tp(self):
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        # SL/TP keys must be absent for no-trade signals; the multi-account
        # dispatch fast-path uses .get() and falls through correctly when
        # they're missing.
        assert "entry_price" not in signal
        assert "stop_loss" not in signal
        assert "take_profit" not in signal

    def test_sl_distance_uses_sl_std_mult(self):
        """The stop-loss distance from entry equals sl_std_mult * std_dev."""
        df = _candles_below_vwap()
        s_default = build_vwap_signal(df, symbol="BTCUSDT")
        s_wide = build_vwap_signal(df, symbol="BTCUSDT", sl_std_mult=2.0)

        std_dev = s_default["meta"]["std_dev"]
        # default = 1.0
        d_default = s_default["entry_price"] - s_default["stop_loss"]
        d_wide = s_wide["entry_price"] - s_wide["stop_loss"]
        assert d_default == pytest.approx(1.0 * std_dev, rel=1e-6)
        assert d_wide == pytest.approx(2.0 * std_dev, rel=1e-6)

    def test_signal_is_packageable_after_g5_fix(self):
        """The signal returned by build_vwap_signal must satisfy the
        pipeline's _signal_carries_full_sltp predicate so the
        multi-account dispatch fast-path accepts it instead of falling
        into the legacy ALLOW_LIVE_TRADING gate."""
        from src.runtime.pipeline import _signal_carries_full_sltp

        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert _signal_carries_full_sltp(signal), (
            "Post-G5: VWAP signals must be packageable "
            "(this was the BUG)"
        )

    # ----- BUG-043: confidence must thread through to the journal -----

    def test_actionable_buy_signal_carries_nonzero_confidence(self):
        """BUG-043: pre-fix every VWAP order package logged as
        confidence=0.0 because build_vwap_signal never emitted the
        field. The pipeline's _signal_to_order_package then read
        meta.get("confidence") or 0.0 and silently zeroed every row.
        Pin a non-zero value at both top-level and meta so consumers
        (pipeline, renderer, journal) see a real conviction."""
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert "confidence" in signal, (
            "BUG-043: top-level confidence must be present so "
            "_extract_order_package_fields renders it"
        )
        assert signal["confidence"] > 0.0, (
            f"BUG-043: actionable buy signal must report non-zero "
            f"confidence (got {signal['confidence']!r})"
        )
        assert signal["meta"]["confidence"] == signal["confidence"], (
            "BUG-043: meta.confidence must mirror top-level so "
            "_signal_to_order_package threads it to OrderPackage"
        )
        assert signal["confidence"] <= 1.0, (
            "VWAP confidence formula caps at 1.0 — anything above is "
            "a regression in the rounding path"
        )

    def test_actionable_sell_signal_carries_nonzero_confidence(self):
        df = _candles_above_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "sell"
        assert signal["confidence"] > 0.0
        assert signal["meta"]["confidence"] == signal["confidence"]

    def test_no_signal_still_emits_confidence_field(self):
        """Even when the signal is non-actionable, meta.confidence
        must be present (and may be 0.0). This keeps the meta shape
        stable for downstream renderers."""
        df = _candles_near_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "confidence" in signal["meta"]

    def test_confidence_threads_through_to_journal_row(
        self, tmp_path, monkeypatch,
    ):
        """End-to-end pin: signal → _signal_to_order_package →
        _log_new_order_package → SELECT confidence from order_packages.
        Pre-fix this read 0.0 for every VWAP signal (BUG-043)."""
        monkeypatch.setenv(
            "TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"),
        )
        from src.runtime.pipeline import _signal_to_order_package
        from src.core.coordinator import _log_new_order_package
        from src.units.db.database import Database

        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        pkg = _signal_to_order_package(signal, settings={"SYMBOL": "BTCUSDT"})
        assert pkg.confidence > 0.0, (
            "OrderPackage must carry the strategy's confidence — "
            "regression in _signal_to_order_package's meta extraction"
        )

        order_package_id = _log_new_order_package(pkg)
        assert order_package_id and order_package_id.startswith("pkg-")

        db = Database(db_path=str(tmp_path / "trade_journal.db"))
        rows = db.get_order_packages_by_strategy(pkg.strategy)
        assert len(rows) == 1
        assert rows[0]["confidence"] == pytest.approx(pkg.confidence)
        assert rows[0]["confidence"] > 0.0, (
            "BUG-043 regression: order_packages.confidence must be "
            "non-zero for an actionable VWAP signal"
        )


# ---------------------------------------------------------------------------
# Integration: STRATEGY=vwap routes to VWAP logic via run_pipeline
# ---------------------------------------------------------------------------

class TestVwapPipelineRouting:
    def _vwap_no_signal_builder(self, settings):
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "none",
            "meta": {"strategy_name": "vwap"},
        }

    def _vwap_buy_signal_builder(self, settings):
        # S-026 G1: signals carry no qty — pipeline injects a placeholder
        # for safe_place_order until G2 moves sizing into the
        # per-account RiskManager.
        return {
            "symbol": settings.get("SYMBOL", "BTCUSDT"),
            "side": "buy",
            "meta": {"strategy_name": "vwap", "vwap": 100.0, "current_price": 90.0},
        }

    def test_vwap_strategy_routes_correctly(self, monkeypatch):
        """STRATEGY=vwap should invoke the vwap signal builder."""
        called_with = {}

        def fake_vwap_builder(settings):
            called_with["settings"] = settings
            return {"symbol": "BTCUSDT", "side": "none"}

        monkeypatch.setattr("src.runtime.pipeline.vwap_signal_builder", fake_vwap_builder)
        monkeypatch.setenv("STRATEGY", "vwap")

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "10"}
        run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
        )

        assert called_with, "vwap_signal_builder was not called"

    def test_vwap_dry_run_does_not_call_exchange_place_order(self):
        """DRY_RUN=true must never touch the exchange order path."""
        exchange = DummyExchangeClient()
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert exchange.calls == [], "Exchange order method must not be called in DRY_RUN mode"

    def test_vwap_dry_run_returns_dry_run_status(self):
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_buy_signal_builder,
        )
        assert result["order_result"]["status"] == "dry_run"

    def test_vwap_no_signal_returns_skipped(self):
        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "true",
            "MAX_QTY": "10",
        }
        result = run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=self._vwap_no_signal_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert result["order_result"]["reason"] == "no_signal"


# ---------------------------------------------------------------------------
# S-026 G1: signals without top-level qty are still routed to multi-account
# dispatch (sizing happens per-account; the strategy emits the trade idea).
# ---------------------------------------------------------------------------


class TestQtylessSignalRoutesToMultiAccountDispatch:
    """Strategy signal that satisfies _signal_carries_full_sltp and has no
    qty must still be routed through the multi-account dispatch fast-path.
    Quantity is the per-account RiskManager's job (G2)."""

    def test_qtyless_packageable_signal_dispatches_per_account(self, monkeypatch):
        from src.runtime import pipeline as pl

        # Strategy emits the trade idea — symbol/side/entry/sl/tp + meta —
        # explicitly NO qty.
        signal = {
            "symbol": "BTCUSDT",
            "side": "buy",
            "entry_price": 50_000.0,
            "stop_loss": 49_500.0,
            "take_profit": 51_000.0,
            "meta": {"strategy_name": "vwap"},
        }
        assert "qty" not in signal

        # Capture the OrderPackage that reaches multi_account_execute and
        # short-circuit the actual fan-out so no exchange/file I/O runs.
        captured = {}

        class _StubCoord:
            def multi_account_execute(self, pkg, dry_run=False):
                captured["pkg"] = pkg
                return [{"name": "fake", "trade_id": "dry-1", "error": None}]

        monkeypatch.setattr("src.core.coordinator.Coordinator", lambda: _StubCoord())

        settings = {
            "SYMBOL": "BTCUSDT",
            "DRY_RUN": "false",
            "ALLOW_LIVE_TRADING": "true",
            "MULTI_ACCOUNT_DISPATCH": "true",
            "MAX_QTY": "1",
        }
        result = pl.run_pipeline(
            settings,
            exchange_client=DummyExchangeClient(),
            telegram_client=DummyTelegramClient(),
            signal_builder=lambda _s: signal,
        )

        assert result["order_result"]["status"] == "multi_account_dispatched", (
            f"S-026 G1: qty-less actionable signal must reach the "
            f"multi-account dispatch fast-path; got "
            f"{result['order_result']!r}"
        )
        assert "pkg" in captured, "multi_account_execute was never called"
        # OrderPackage carries the trade idea — no qty field.
        pkg = captured["pkg"]
        assert pkg.symbol == "BTCUSDT"
        assert pkg.direction == "long"
        assert pkg.entry == 50_000.0
        assert not hasattr(pkg, "qty"), (
            "OrderPackage must not carry qty (sizing is per-account)"
        )


# ---------------------------------------------------------------------------
# Safety: live mode without explicit gate fails closed
# ---------------------------------------------------------------------------

class TestLiveSafetyGate:
    def test_live_without_allow_live_trading_submits_by_default(self):
        """BUG-031: DRY_RUN=false with ALLOW_LIVE_TRADING absent submits.
        Live is the default — the safety rails are the risk manager and
        /halt, not an extra opt-in env var."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_live_with_explicit_gate_is_submitted(self):
        """DRY_RUN=false + ALLOW_LIVE_TRADING=true → order reaches exchange."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "false", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "submitted"
        assert len(client.calls) == 1

    def test_dry_run_true_blocks_submission_regardless_of_allow_live(self):
        """DRY_RUN=true blocks real submission even if ALLOW_LIVE_TRADING=true."""
        client = DummyExchangeClient()
        settings = {"DRY_RUN": "true", "ALLOW_LIVE_TRADING": "true", "MAX_QTY": "10"}
        result = safe_place_order(
            {"symbol": "BTCUSDT", "side": "buy", "qty": 1},
            settings,
            client,
        )
        assert result["status"] == "dry_run"
        assert client.calls == []

    def test_mode_live_with_dry_run_truthy_is_contradiction(self, monkeypatch):
        """BUG-031: MODE=LIVE with DRY_RUN truthy is contradictory and
        must be refused. (The legacy test required ALLOW_LIVE_TRADING=true
        as an explicit opt-in; under the BUG-031 default-live contract,
        the contradiction is the operative failure mode.)"""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "LIVE",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="contradictory"):
            validate_startup()

    def test_mode_paper_is_rejected_by_validate_startup(self, monkeypatch):
        """MODE=PAPER must be rejected outright — paper trading is not supported."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "PAPER",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="MODE"):
            validate_startup()

    def test_mode_paper_lowercase_is_rejected(self, monkeypatch):
        """MODE=paper (lowercase) must also be rejected after .upper() normalisation."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "paper",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="MODE"):
            validate_startup()

    def test_mode_live_lowercase_with_dry_run_truthy_is_contradiction(self, monkeypatch):
        """BUG-031: MODE=live (lowercase) + DRY_RUN truthy → contradictory.
        The .upper() normalisation still applies."""
        env = {
            "EXCHANGE": "bybit",
            "BYBIT_API_KEY": "fake_key",
            "BYBIT_API_SECRET": "fake_secret",
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "123",
            "MODE": "live",
            "SYMBOL": "BTCUSDT",
            "TIMEFRAME": "5m",
            "RISK_PER_TRADE": "0.01",
            "MAX_QTY": "1",
            "DRY_RUN": "true",
            "ALLOW_LIVE_TRADING": "false",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        with pytest.raises(EnvironmentError, match="contradictory"):
            validate_startup()


# ---------------------------------------------------------------------------
# Edge cases: missing / malformed candle data
# ---------------------------------------------------------------------------

class TestVwapEdgeCases:
    def test_single_candle_insufficient(self):
        df = _candles(100)
        with pytest.raises(ValueError, match="at least"):
            build_vwap_signal(df, symbol="BTCUSDT")

    def test_exactly_min_candles_is_accepted(self):
        df = _candles(*([100] * MIN_CANDLES))
        # All-same prices → std_dev = 0 → deviation = 0 → no signal, but no error
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"

    def test_vwap_meta_never_contains_api_key(self):
        """Ensure VWAP signal meta cannot leak credentials."""
        df = _candles(100, 102, 101)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        meta_str = str(signal)
        for suspicious in ("api_key", "api_secret", "token", "password", "secret"):
            assert suspicious not in meta_str.lower(), (
                f"Signal output contains suspicious key: {suspicious}"
            )


# ---------------------------------------------------------------------------
# Invalid candle data — must return no-trade, never raise
# ---------------------------------------------------------------------------

class TestVwapInvalidDataNoTrade:
    """Bad market data must yield a no-trade signal; the tick must not crash."""

    def test_zero_volume_returns_no_trade(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal
        assert signal["meta"]["strategy_name"] == "vwap"

    def test_zero_volume_reason_text(self):
        df = _candles(100, 102, 101, volume=0)
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        reason = signal["meta"]["reason"]
        assert "zero" in reason.lower() or "negative" in reason.lower()

    def test_zero_volume_does_not_raise(self):
        df = _candles(100, 102, 101, volume=0)
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_missing_volume_column_returns_no_trade(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal

    def test_missing_volume_column_does_not_raise(self):
        df = pd.DataFrame([
            {"timestamp": 0, "open": 99, "high": 102, "low": 98, "close": 100},
            {"timestamp": 1, "open": 100, "high": 103, "low": 99, "close": 101},
        ])
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_empty_dataframe_returns_no_trade(self):
        df = pd.DataFrame()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "none"
        assert "qty" not in signal

    def test_empty_dataframe_does_not_raise(self):
        df = pd.DataFrame()
        build_vwap_signal(df, symbol="BTCUSDT")  # must not raise

    def test_normal_candles_still_produce_signal(self):
        """Valid candle data must continue to generate actionable signals."""
        df = _candles_below_vwap()
        signal = build_vwap_signal(df, symbol="BTCUSDT")
        assert signal["side"] == "buy"
        assert "qty" not in signal
        assert signal["meta"]["strategy_name"] == "vwap"
        assert signal["meta"]["current_price"] < signal["meta"]["vwap"]

    def test_pipeline_zero_volume_skips_order_placement(self):
        """Zero-volume candles routed through pipeline must not reach order placement."""
        exchange = DummyExchangeClient()

        def zero_volume_builder(settings):
            df = _candles(100, 102, 101, volume=0)
            return build_vwap_signal(df, symbol=settings.get("SYMBOL", "BTCUSDT"))

        settings = {"SYMBOL": "BTCUSDT", "DRY_RUN": "true", "MAX_QTY": "1"}
        result = run_pipeline(
            settings,
            exchange_client=exchange,
            telegram_client=DummyTelegramClient(),
            signal_builder=zero_volume_builder,
        )
        assert result["order_result"]["status"] == "skipped"
        assert exchange.calls == []
