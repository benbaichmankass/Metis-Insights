from __future__ import annotations
from src.runtime.signal_writer import write_signal
from src.utils.signal_audit_logger import log_signal

import os

HALT_FLAG_PATH = "/tmp/trader_halt.flag"
from dotenv import load_dotenv
if os.path.exists(".env.live"):
    load_dotenv(".env.live")
elif os.path.exists(".env.paper"):
    load_dotenv(".env.paper")

import logging
from typing import Any, Callable, Dict, Optional

import pandas as pd

from src.runtime.notify import notify_operator, send_via_alert_manager
from src.runtime.orders import safe_place_order

logger = logging.getLogger(__name__)


def default_signal_builder(settings: dict) -> Dict[str, Any]:
    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": "buy",
        "qty": 1,
    }


def _build_killzone_exchange(settings: dict):
    exchange_name = str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower()
    bybit_testnet_raw = str(__import__("os").environ.get("BYBIT_TESTNET", "true")).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

    if exchange_name == "binance":
        from src.exchange.binance_connector import BinanceConnector
        return BinanceConnector(
            api_key=settings.get("BINANCE_API_KEY"),
            api_secret=settings.get("BINANCE_API_SECRET"),
            testnet=testnet,
        )

    if exchange_name == "bybit":
        from src.exchange.bybit_connector import BybitConnector
        return BybitConnector(
            api_key=settings.get("BYBIT_API_KEY"),
            api_secret=settings.get("BYBIT_API_SECRET"),
            testnet=testnet,
        )

    raise ValueError(f"Unsupported EXCHANGE value: {exchange_name}")


def _killzone_symbol(settings: dict) -> str:
    configured = settings.get("SYMBOL")
    if configured:
        return configured

    exchange_name = str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower()
    if exchange_name == "binance":
        return "BTC/USDT"

    return "BTC/USDT:USDT"


def killzone_signal_builder(settings: dict) -> Dict[str, Any]:
    """Use KillZoneScalperBot with the selected exchange connector for market data."""
    from src.core.automated_trading_loop import KillZoneScalperBot

    symbol = _killzone_symbol(settings)
    exchange = _build_killzone_exchange(settings)

    bot = KillZoneScalperBot(
        exchange=exchange,
        symbol=symbol,
    )

    signal, price, fvg_data = bot.analyze_market()

    if not signal:
        logger.info("KillZoneScalperBot returned no signal; staying flat.")
        return {
            "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
            "side": "none",
            "qty": 0,
        }

    side = "buy" if signal.lower() == "long" else "sell"

    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": side,
        "qty": float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1),
        "meta": {
            "price": price,
            "fvg": fvg_data,
            "raw_signal": signal,
            "exchange": str(settings.get("EXCHANGE", settings.get("exchange", "bybit"))).strip().lower(),
            "market_data_symbol": symbol,
            "strategy_name": "killzone",
        },
    }


def vwap_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Fetch OHLCV candles from the configured exchange and return a VWAP
    mean-reversion signal.

    Safe under DRY_RUN=true: fetches market data for signal computation but
    relies on safe_place_order to prevent any actual order submission.

    If candle data is unavailable or insufficient, raises a clear,
    non-secret error rather than silently doing nothing.
    """
    from strategies.vwap_signal_builder import build_vwap_signal

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    timeframe = settings.get("TIMEFRAME", settings.get("timeframe", "5m"))
    qty = float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1)

    exchange = _build_killzone_exchange(settings)
    candles_raw = exchange.get_ohlcv(symbol, timeframe, limit=100)

    if candles_raw is None or (hasattr(candles_raw, "__len__") and len(candles_raw) == 0):
        raise RuntimeError(
            f"VWAP strategy: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. "
            "Check that the exchange connection is configured and the symbol is valid."
        )

    if isinstance(candles_raw, pd.DataFrame):
        candles_df = candles_raw.copy()
    else:
        candles_df = pd.DataFrame(
            candles_raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    for col in ("high", "low", "close", "volume"):
        candles_df[col] = pd.to_numeric(candles_df[col], errors="coerce")

    if candles_df[["high", "low", "close", "volume"]].isnull().all().any():
        raise RuntimeError(
            f"VWAP strategy: candle data for symbol={symbol} timeframe={timeframe} "
            "contains all-NaN columns after parsing. Data may be malformed."
        )

    logger.info(
        "VWAP signal builder: symbol=%s timeframe=%s candles=%d",
        symbol, timeframe, len(candles_df),
    )

    return build_vwap_signal(candles_df, symbol=symbol, qty=qty)


def breakout_model_signal_builder(settings: dict) -> Dict[str, Any]:
    """Use the trained breakout confirmation model to generate live buy/skip decisions."""
    from src.strategies_manager import StrategyManager

    symbol = _killzone_symbol(settings)
    exchange = _build_killzone_exchange(settings)

    candles = exchange.get_ohlcv(symbol, "1m", limit=100)
    candles_df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    candles_df["datetime_utc"] = pd.to_datetime(candles_df["timestamp"], unit="ms", utc=True)

    manager = StrategyManager()
    model_signal = manager.get_signal("breakout_confirmation", candles_df)

    if model_signal.get("signal") not in ["CONFIRM", "STRONG_CONFIRM"]:
        logger.info("Breakout model returned non-actionable signal: %s", model_signal)
        return {
            "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
            "side": "none",
            "qty": 0,
            "meta": {
                "strategy_name": "breakout_confirmation",
                "model_signal": model_signal,
            },
        }

    risk_per_trade = float(
        settings.get("RISK_PER_TRADE", settings.get("risk_per_trade", 0.01)) or 0.01
    )
    fallback_qty = float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1)

    atr = float(model_signal.get("atr_14", 0) or 0)
    if atr <= 0:
        qty = fallback_qty
    else:
        qty = fallback_qty

    return {
        "symbol": settings.get("SYMBOL", settings.get("symbol", "BTCUSDT")),
        "side": "buy",
        "qty": qty,
        "meta": {
            "strategy_name": "breakout_confirmation",
            "model_signal": model_signal,
            "prob_tp": model_signal.get("prob_tp"),
            "entry_price": model_signal.get("entry_price"),
            "atr_14": model_signal.get("atr_14"),
            "risk_per_trade": risk_per_trade,
        },
    }



def ict_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Runtime adapter for the M7 ICT strategy (M7 Phase 2.5).

    Pulls OHLCV candles from the configured exchange and delegates the
    actual signal logic to the **pure** factory in
    ``src.runtime.strategies.ict.build_ict_signal``. This thin adapter
    keeps the live-data plumbing in one place (mirroring
    ``vwap_signal_builder``) and lets the strategy itself stay pure and
    unit-testable.

    Settings recognised
    -------------------
    - ``SYMBOL`` / ``symbol`` — trading pair (default: same default the
      kill-zone helper uses for the configured exchange).
    - ``TIMEFRAME`` / ``timeframe`` — candle timeframe (default ``"15m"``).
    - ``ICT_TIMEFRAME`` — overrides ``TIMEFRAME`` for the strategy frame.
    - ``ICT_HTF_TIMEFRAME`` — optional higher-timeframe used **only** for
      the trend bias gate (e.g. ``"1h"``). When unset the function
      reuses the strategy frame.
    - ``ICT_CANDLE_LIMIT`` — number of candles to fetch (default ``200``,
      enough to seed a 50-period EMA plus headroom).
    - ``ICT_HTF_CANDLE_LIMIT`` — candle count for the HTF frame (default
      ``200``).
    - all the ``ICT_*`` knobs forwarded by ``build_ict_signal`` (see
      ``src/runtime/strategies/ict.py``) pass through unchanged.

    Safe under ``DRY_RUN=true`` because it never places orders — only
    fetches market data — and ``safe_place_order`` enforces the actual
    no-trade contract downstream.
    """
    from src.runtime.strategies.ict import build_ict_signal

    symbol = _killzone_symbol(settings)
    timeframe = settings.get(
        "ICT_TIMEFRAME",
        settings.get("TIMEFRAME", settings.get("timeframe", "15m")),
    )
    htf_timeframe = settings.get("ICT_HTF_TIMEFRAME")
    candle_limit = int(settings.get("ICT_CANDLE_LIMIT", 200) or 200)
    htf_candle_limit = int(
        settings.get("ICT_HTF_CANDLE_LIMIT", 200) or 200
    )

    exchange = _build_killzone_exchange(settings)
    candles_raw = exchange.get_ohlcv(symbol, timeframe, limit=candle_limit)

    if candles_raw is None or (
        hasattr(candles_raw, "__len__") and len(candles_raw) == 0
    ):
        raise RuntimeError(
            f"ICT strategy: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. Check exchange configuration and "
            "that the symbol is valid."
        )

    candles_df = _coerce_ohlcv_with_dt_index(candles_raw)

    htf_df = None
    if htf_timeframe:
        try:
            htf_raw = exchange.get_ohlcv(
                symbol, htf_timeframe, limit=htf_candle_limit
            )
        except Exception as exc:
            logger.warning(
                "ICT strategy: HTF fetch failed (%s) — falling back to "
                "strategy frame for trend gate",
                exc,
            )
            htf_raw = None
        if htf_raw is not None and (
            not hasattr(htf_raw, "__len__") or len(htf_raw) > 0
        ):
            htf_df = _coerce_ohlcv_with_dt_index(htf_raw)

    settings_for_builder = dict(settings)
    settings_for_builder.setdefault("SYMBOL", symbol)

    logger.info(
        "ICT signal builder: symbol=%s timeframe=%s candles=%d htf=%s",
        symbol,
        timeframe,
        len(candles_df),
        htf_timeframe or "(reuse)",
    )

    return build_ict_signal(
        candles_df,
        settings=settings_for_builder,
        htf_df=htf_df,
    )


def _coerce_ohlcv_with_dt_index(raw: Any) -> pd.DataFrame:
    """
    Normalise raw exchange OHLCV into a DataFrame with a UTC
    ``DatetimeIndex``.

    The ICT analyzer requires a DatetimeIndex (kill-zones are derived
    from ``df.index.hour``). We accept either:

    - a list of ``[ts_ms, open, high, low, close, volume]`` rows
      (the ccxt / Bybit / Binance native shape), or
    - a DataFrame already containing a ``timestamp`` column in ms or a
      DatetimeIndex.
    """
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    else:
        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )

    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" not in df.columns:
            raise RuntimeError(
                "ICT strategy: candle frame is missing a 'timestamp' "
                "column and has no DatetimeIndex."
            )
        df["timestamp"] = pd.to_datetime(
            df["timestamp"], unit="ms", utc=True
        )
        df = df.set_index("timestamp")

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _write_ict_signals_from_meta(signal: dict, settings: dict) -> None:
    """Write individual ICT detections even when no trade is taken."""
    if not isinstance(signal, dict):
        return

    meta = signal.get("meta") or {}
    symbol = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
    timeframe = settings.get("TIMEFRAME", "15m")

    fvgs = meta.get("fvgs") or []
    for fvg in fvgs:
        if not isinstance(fvg, dict):
            continue
        fvg_type = fvg.get("type", "unknown")
        gap_low = fvg.get("gap_low")
        gap_high = fvg.get("gap_high")
        price = None
        if gap_low is not None and gap_high is not None:
            try:
                price = (float(gap_low) + float(gap_high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"fvg_{fvg_type}",
            direction=fvg_type,
            price=price,
            timeframe=timeframe,
            reason="ICT FVG detected",
            metadata=str(fvg),
        )

    order_blocks = meta.get("order_blocks") or meta.get("obs") or []
    for ob in order_blocks:
        if not isinstance(ob, dict):
            continue
        ob_type = ob.get("type", "unknown")
        low = ob.get("low")
        high = ob.get("high")
        price = None
        if low is not None and high is not None:
            try:
                price = (float(low) + float(high)) / 2.0
            except Exception:
                price = None
        write_signal(
            symbol=symbol,
            signal_type=f"ob_{ob_type}",
            direction=ob_type,
            price=price,
            timeframe=timeframe,
            reason="ICT order block detected",
            metadata=str(ob),
        )

# Ordered list of strategies tried in multiplexed mode; first actionable signal wins.
STRATEGIES = ["breakout_confirmation", "vwap"]

_STRATEGY_BUILDERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    "breakout_confirmation": breakout_model_signal_builder,
    "vwap": vwap_signal_builder,
    "killzone": killzone_signal_builder,
    # M7 Phase 2.5: registered for direct STRATEGY=ict selection. Adding
    # it to the multiplexer order (STRATEGIES, above) is intentionally
    # deferred to its own checkpoint — see CHECKPOINT_LOG.md.
    "ict": ict_signal_builder,
}


def multiplexed_signal_builder(settings: dict) -> Dict[str, Any]:
    """
    Loop STRATEGIES in order; return the first actionable signal.

    Each strategy is sized independently (no compounding across strategies).
    If a strategy raises an exception it is logged and skipped.
    Returns a side=none signal when no strategy fires.
    """
    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))

    for strategy_name in STRATEGIES:
        builder = _STRATEGY_BUILDERS.get(strategy_name)
        if builder is None:
            logger.warning("Multiplexer: unknown strategy '%s' — skipping", strategy_name)
            continue
        try:
            signal = builder(settings)
        except Exception as exc:
            logger.warning("Multiplexer: strategy '%s' raised %s — skipping", strategy_name, exc)
            continue

        if signal.get("side") in ("buy", "sell") and float(signal.get("qty", 0)) > 0:
            logger.info("Multiplexer: '%s' produced actionable signal", strategy_name)
            return signal

        logger.info("Multiplexer: '%s' returned no actionable signal", strategy_name)

    logger.info("Multiplexer: no strategy fired — staying flat")
    return {"symbol": symbol, "side": "none", "qty": 0,
            "meta": {"strategy_name": "multiplexed", "reason": "no_strategy_triggered"}}


def run_pipeline(
    settings: dict,
    exchange_client: Any = None,
    telegram_client: Any = None,
    signal_builder: Optional[Callable[[dict], Dict[str, Any]]] = None,
) -> dict:
    """Pipeline adapter. Chooses strategy from STRATEGY env var, defaults to killzone."""
    logger.info("Pipeline start")

    strategy_name = str(os.environ.get("STRATEGY", "killzone")).strip().lower()

    if signal_builder is not None:
        builder = signal_builder
    elif strategy_name == "multiplexed":
        builder = multiplexed_signal_builder
    elif strategy_name == "vwap":
        builder = vwap_signal_builder
    elif strategy_name == "breakout":
        builder = breakout_model_signal_builder
    elif strategy_name == "ict":
        builder = ict_signal_builder
    else:
        builder = killzone_signal_builder

    logger.info("Using strategy builder: %s", strategy_name)
    signal = builder(settings)
    _write_ict_signals_from_meta(signal, settings)

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", meta.get("entry_price", signal.get("price")))

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type="ml_breakout" if meta.get("strategy_name") == "breakout_confirmation" else ("fvg" if meta.get("fvg") else "trade_signal"),
            direction="bullish" if signal.get("side") == "buy" else "bearish",
            price=float(price) if price is not None else None,
            timeframe=settings.get("TIMEFRAME", settings.get("timeframe", "unknown")),
            reason="Actionable pipeline signal",
            metadata=str(signal),
        )

    logger.info("Generated signal: %s", signal)

    if signal.get("side") in ("none", "", None) or float(signal.get("qty", 0)) <= 0:
        logger.info("No actionable signal; skipping order placement.")
        result = {"status": "skipped", "reason": "no_signal", "signal": signal}
    elif os.path.exists(HALT_FLAG_PATH):
        logger.warning("Trader is HALTED — flag file present. Skipping order placement.")
        result = {"status": "halted", "reason": "halt_flag_active"}
    else:
        result = safe_place_order(signal, settings, exchange_client)

    try:
        log_signal(
            {
                "event": "pipeline_result",
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "qty": signal.get("qty"),
                "status": result.get("status"),
                "reason": result.get("reason"),
            }
        )
    except Exception:
        pass

    status = result.get("status", "unknown")
    reason = result.get("reason")
    symbol = signal.get("symbol", "?")
    side = signal.get("side", "?")
    qty = signal.get("qty", "?")

    message = f"Pipeline result: status={status} | symbol={symbol} | side={side} | qty={qty}"
    if reason:
        message += f" | reason={reason}"

    if telegram_client is not None:
        notify_operator(telegram_client, message)
    else:
        send_via_alert_manager(message)

    logger.info("Pipeline complete: %s", result)

    return {
        "signal": signal,
        "order_result": result,
    }
