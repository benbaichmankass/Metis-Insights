"""Tests for ``src.main._build_monitor_ohlcv_fetcher``.

The helper exists to wire fresh candles into the order_monitor loop
so each strategy's ``monitor()`` hook can produce TP / SL / VWAP-cross
/ time-decay close verdicts. Without it, every monitor tick was
called with ``candles_df=None`` and the strategies short-circuited
silently — see PR #566 RCA for the field evidence.

These tests pin:

1. The closure delegates to ``fetch_candles`` with the right kwargs
   (the captured exchange client + ``limit=200`` + the call's
   symbol/timeframe).
2. Init-failure short-circuits to ``None`` rather than raising, so
   ``run_monitor_tick(ohlcv_fetcher=None)`` falls back to its prior
   no-change behaviour.
3. Missing symbol / timeframe inputs short-circuit to ``None`` —
   ``order_monitor`` reads ``meta.timeframe`` from the open package
   and packages predating timeframe wiring may not have it.
4. ``fetch_candles`` returning ``None`` (network error, empty
   response) propagates as ``None``.
"""

from __future__ import annotations

import pandas as pd
import pytest

import src.main as main_module


@pytest.fixture
def fake_exchange():
    """Sentinel exchange client; the closure should pass this through
    to ``fetch_candles`` rather than building a fresh one."""
    class _FakeExchange:
        pass

    return _FakeExchange()


def test_fetcher_passes_captured_exchange_and_args(monkeypatch, fake_exchange):
    """The returned closure invokes ``fetch_candles`` with the
    exchange built at construction time + ``limit=200`` + the
    caller's ``(symbol, timeframe)`` pair.

    Pinning this contract keeps the monitor's market-data path in
    sync with ``pipeline._build_vwap_signal`` /
    ``_build_turtle_soup_signal`` (both call ``fetch_candles`` with
    the same shape) so a config change in one place doesn't quietly
    skew the other.
    """
    monkeypatch.setattr(
        "src.runtime.pipeline._build_killzone_exchange",
        lambda settings: fake_exchange,
    )

    seen = {}
    expected = pd.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.5, 100.5, 101.5],
            "close": [100.5, 101.5, 102.5],
            "volume": [10.0, 11.0, 12.0],
        }
    )

    def _spy(symbol, timeframe, *, settings=None, exchange_client=None, limit):
        seen["symbol"] = symbol
        seen["timeframe"] = timeframe
        seen["settings"] = settings
        seen["exchange_client"] = exchange_client
        seen["limit"] = limit
        return expected

    monkeypatch.setattr("src.runtime.market_data.fetch_candles", _spy)

    settings = {"EXCHANGE": "bybit", "SYMBOL": "BTCUSDT"}
    fetcher = main_module._build_monitor_ohlcv_fetcher(settings)
    assert callable(fetcher)

    out = fetcher("BTCUSDT", "5m")

    assert out is expected
    assert seen["symbol"] == "BTCUSDT"
    assert seen["timeframe"] == "5m"
    assert seen["exchange_client"] is fake_exchange
    assert seen["limit"] == 200
    # settings is forwarded so fetch_candles' fallback connector path
    # has the env it needs if the captured client is later evicted.
    assert seen["settings"] is settings


def test_fetcher_returns_none_when_exchange_init_raises(monkeypatch):
    """A connector init failure must not raise out of
    ``_build_monitor_ohlcv_fetcher`` — the caller passes the result
    straight to ``run_monitor_tick(ohlcv_fetcher=...)`` and a None
    fetcher is the documented fallback (every strategy's
    ``monitor()`` short-circuits on ``candles_df is None``)."""
    def _raise(_settings):
        raise RuntimeError("connector boom")

    monkeypatch.setattr("src.runtime.pipeline._build_killzone_exchange", _raise)

    fetcher = main_module._build_monitor_ohlcv_fetcher({"EXCHANGE": "bybit"})

    assert fetcher is None


@pytest.mark.parametrize(
    "symbol,timeframe",
    [
        (None, "5m"),
        ("", "5m"),
        ("BTCUSDT", None),
        ("BTCUSDT", ""),
    ],
)
def test_fetcher_short_circuits_on_missing_inputs(
    monkeypatch, fake_exchange, symbol, timeframe,
):
    """Without a strategy_name, a falsy symbol/timeframe pair has no
    fallback — the closure short-circuits to ``None`` rather than
    calling ``fetch_candles`` with falsy args. The strategy_name
    fallback path is tested separately below."""
    monkeypatch.setattr(
        "src.runtime.pipeline._build_killzone_exchange",
        lambda settings: fake_exchange,
    )

    called = []

    def _spy(*args, **kwargs):
        called.append((args, kwargs))
        return pd.DataFrame()

    monkeypatch.setattr("src.runtime.market_data.fetch_candles", _spy)

    fetcher = main_module._build_monitor_ohlcv_fetcher({})
    assert fetcher is not None

    assert fetcher(symbol, timeframe) is None
    assert called == []


def test_fetcher_falls_back_to_strategy_yaml_timeframe(
    monkeypatch, fake_exchange,
):
    """When ``meta.timeframe`` is missing (legacy package rows pre-
    2026-05-09), the fetcher must fall back to the per-strategy
    timeframe from ``config/strategies.yaml``. Without this fallback
    the closure short-circuits to ``None``, ``monitor()`` never
    receives candles, and the position sits open until the watchdog
    cascades it (or operator intervention)."""
    monkeypatch.setattr(
        "src.runtime.pipeline._build_killzone_exchange",
        lambda settings: fake_exchange,
    )
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"vwap": {"timeframe": "5m"}, "turtle_soup": {"timeframe": "15m"}},
    )

    seen = {}

    def _spy(symbol, timeframe, *, settings=None, exchange_client=None, limit):
        seen["symbol"] = symbol
        seen["timeframe"] = timeframe
        return pd.DataFrame()

    monkeypatch.setattr("src.runtime.market_data.fetch_candles", _spy)

    fetcher = main_module._build_monitor_ohlcv_fetcher({})
    assert fetcher is not None

    # vwap legacy row → falls back to 5m
    out = fetcher("BTCUSDT", None, "vwap")
    assert out is not None
    assert seen == {"symbol": "BTCUSDT", "timeframe": "5m"}

    # turtle_soup legacy row → falls back to 15m
    seen.clear()
    fetcher("BTCUSDT", None, "turtle_soup")
    assert seen == {"symbol": "BTCUSDT", "timeframe": "15m"}

    # Unknown strategy → no fallback → short-circuits to None
    seen.clear()
    assert fetcher("BTCUSDT", None, "unknown_strategy") is None
    assert seen == {}


def test_fetcher_propagates_none_from_fetch_candles(monkeypatch, fake_exchange):
    """``fetch_candles`` returns ``None`` on network/connector
    failure. The closure should propagate that — never fabricate an
    empty DataFrame, because order_monitor distinguishes
    ``candles_df is None`` from ``len(candles_df) == 0`` only by
    way of the strategy's first guard."""
    monkeypatch.setattr(
        "src.runtime.pipeline._build_killzone_exchange",
        lambda settings: fake_exchange,
    )
    monkeypatch.setattr(
        "src.runtime.market_data.fetch_candles",
        lambda *a, **k: None,
    )

    fetcher = main_module._build_monitor_ohlcv_fetcher({})
    assert fetcher("BTCUSDT", "5m") is None


def test_main_loop_call_site_passes_ohlcv_fetcher_kwarg():
    """Pin the wiring at the call site itself.

    Prior to this fix (PR follow-up to #566) the loop in
    ``src.main`` called ``run_monitor_tick()`` with no arguments,
    leaving ``ohlcv_fetcher=None`` and silencing every
    strategy's ``monitor()`` hook. AST-walk ``src/main.py`` and
    assert that at least one ``run_monitor_tick(...)`` call passes
    ``ohlcv_fetcher`` as a keyword. If anyone re-introduces the
    bare call this test fails before the trader silently regresses
    in production.
    """
    import ast
    import inspect

    source = inspect.getsource(main_module)
    tree = ast.parse(source)

    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )
        if name != "run_monitor_tick":
            continue
        if any(kw.arg == "ohlcv_fetcher" for kw in node.keywords):
            matches.append(node)

    assert matches, (
        "src/main.py must call run_monitor_tick(ohlcv_fetcher=...). "
        "A bare call leaves the monitor loop blind to fresh candles "
        "and silently disables every strategy's close-verdict path."
    )
