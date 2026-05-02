from __future__ import annotations
from src.runtime.signal_writer import write_signal
from src.utils.signal_audit_logger import log_signal
from src.runtime.risk_counters import inject_runtime_counters, inject_per_strategy_counters
from src.news.news_pipeline import get_news_score

import os

HALT_FLAG_PATH = "/tmp/trader_halt.flag"
from dotenv import load_dotenv
if os.path.exists(".env.live"):
    load_dotenv(".env.live")

import logging
from typing import Any, Callable, Dict, Optional

import pandas as pd

from src.runtime.notify import notify_operator, send_via_alert_manager
from src.runtime.orders import safe_place_order
from src.runtime.outcomes import Level, report
from src.web.runtime_status import write_status

_OUTCOME_LEVEL_BY_STATUS: Dict[str, Level] = {
    # Happy / expected
    "submitted": Level.INFO,
    "multi_account_dispatched": Level.INFO,
    "dry_run": Level.INFO,
    "skipped": Level.INFO,
    "halted": Level.INFO,
    "news_veto": Level.INFO,
    "refused": Level.INFO,
    # Validation: bounded but a sign of upstream drift
    "failed_validation": Level.WARN,
    # Hard failures: page the operator
    "failed_exchange": Level.ERROR,
    "failed_dispatch": Level.ERROR,
    "error": Level.ERROR,
}


def _report_pipeline_outcome(result: Dict[str, Any], signal: Dict[str, Any]) -> None:
    """Translate the run_pipeline result dict into an outcomes.report() call.

    Never raises. Centralizes the status → level mapping so individual
    sites in the pipeline don't have to care about alerting.
    """
    try:
        status = str((result or {}).get("status") or "unknown")
        level = _OUTCOME_LEVEL_BY_STATUS.get(status, Level.ERROR)
        meta = (signal or {}).get("meta") or {}
        report(
            "pipeline_order",
            status,
            level=level,
            reason=(result or {}).get("reason"),
            symbol=(signal or {}).get("symbol"),
            side=(signal or {}).get("side"),
            qty=(signal or {}).get("qty"),
            strategy=meta.get("strategy_name"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("outcomes reporting failed")

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


def _signal_to_order_package(signal: Dict[str, Any], settings: dict):
    """Build an ``OrderPackage`` from a pipeline signal dict.

    The signal shape is what every builder in this module produces:
    ``{symbol, side, qty, price/entry_price, stop_loss, take_profit,
    meta: {strategy_name, ...}}``. The Coordinator's per-account
    dispatch path consumes ``OrderPackage``, which has a slightly
    different shape (``direction`` instead of ``side``, ``entry`` /
    ``sl`` / ``tp``). This helper bridges the two so we can fan a
    pipeline-generated signal out to every account in
    ``config/accounts.yaml`` without changing the strategy builders.
    """
    from src.core.coordinator import OrderPackage

    meta = dict(signal.get("meta") or {})
    side = str(signal.get("side", "")).strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError(
            f"_signal_to_order_package: side must be buy/sell, got {side!r}"
        )
    direction = "long" if side == "buy" else "short"

    entry = signal.get("entry_price") or signal.get("price") or meta.get("price")
    sl = signal.get("stop_loss") or meta.get("stop_loss") or meta.get("sl")
    tp = signal.get("take_profit") or meta.get("take_profit") or meta.get("tp")
    if entry is None or sl is None or tp is None:
        raise ValueError(
            "_signal_to_order_package: signal missing entry/sl/tp "
            f"(entry={entry!r}, sl={sl!r}, tp={tp!r}); strategy must "
            "populate price+stop_loss+take_profit before fan-out."
        )

    strategy = (
        meta.get("strategy_name")
        or signal.get("strategy")
        or settings.get("STRATEGY")
        or "unknown"
    )
    return OrderPackage(
        strategy=str(strategy),
        symbol=str(signal.get("symbol") or settings.get("SYMBOL") or "BTCUSDT"),
        direction=direction,
        entry=float(entry),
        sl=float(sl),
        tp=float(tp),
        confidence=float(meta.get("confidence") or 0.0),
        meta=meta,
    )


def _multi_account_dispatch_enabled(settings: dict) -> bool:
    """Return True when pipeline signals should fan out to every account.

    Default flipped to **true** (post-CP-2026-05-02): the architecture is
    "strategy produces signal; each account decides whether to live-trade
    that signal." The legacy single-client path applied a global
    ``ALLOW_LIVE_TRADING`` gate to every signal, which surfaced as the
    "ALLOW_LIVE_TRADING=true is required" failed-validation message even
    when accounts.yaml had per-account dry/live state set correctly.

    Operator can still pin to the legacy single-client path by exporting
    ``MULTI_ACCOUNT_DISPATCH=false`` — used for single-account smoke
    deployments that don't want to load Coordinator.
    """
    raw = settings.get("MULTI_ACCOUNT_DISPATCH") if isinstance(settings, dict) else None
    if raw is None:
        raw = os.environ.get("MULTI_ACCOUNT_DISPATCH", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def turtle_soup_signal_builder(settings: dict) -> Dict[str, Any]:
    """Sweep + reversal at 15m. S-012 PR C3 wires it into the multiplexer.

    Calls the units-layer ``src.units.strategies.turtle_soup.order_package``
    so the same signal logic exercised by tests/test_s012_turtle_soup.py
    is what runs in production. Routes through the same pipeline-level
    signal shape used by VWAP / killzone / ict so downstream consumers
    (multiplexer, RiskManager, order layer) need no changes.

    Returns
    -------
    dict
        Pipeline signal: {symbol, side, qty, price, stop_loss, take_profit,
        meta} where side ∈ {"buy", "sell", "none"}.
    """
    from src.units.strategies.turtle_soup import order_package

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    timeframe = settings.get("TURTLE_SOUP_TIMEFRAME", settings.get("timeframe", "15m"))
    qty = float(settings.get("MAX_QTY", settings.get("max_qty", 1)) or 1)

    exchange = _build_killzone_exchange(settings)
    candles_raw = exchange.get_ohlcv(symbol, timeframe, limit=200)

    if candles_raw is None or (hasattr(candles_raw, "__len__") and len(candles_raw) == 0):
        raise RuntimeError(
            f"Turtle Soup: no candle data returned for symbol={symbol} "
            f"timeframe={timeframe}. "
            "Check that the exchange connection is configured and the symbol is valid."
        )

    if isinstance(candles_raw, pd.DataFrame):
        candles_df = candles_raw.copy()
    else:
        candles_df = pd.DataFrame(
            candles_raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    for col in ("open", "high", "low", "close", "volume"):
        candles_df[col] = pd.to_numeric(candles_df[col], errors="coerce")

    cfg: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe}
    # Merge per-strategy params from config/strategies.yaml when available.
    try:
        from src.units.strategies import load_strategy_config
        params = load_strategy_config().get("turtle_soup", {})
        cfg.update(params)
    except Exception as exc:
        logger.warning("Turtle Soup: could not load strategies.yaml params (%s); using adapter defaults", exc)

    try:
        pkg = order_package(cfg, candles_df=candles_df)
    except ValueError as exc:
        # No setup on the latest bar — return a flat signal, not an error.
        logger.info("Turtle Soup: no actionable signal (%s)", exc)
        return {
            "symbol": symbol,
            "side": "none",
            "qty": 0,
            "meta": {"strategy_name": "turtle_soup", "reason": str(exc)},
        }

    side = "buy" if pkg["direction"] == "long" else "sell"
    logger.info(
        "Turtle Soup: %s signal at %s (entry=%s sl=%s tp=%s confidence=%.3f)",
        side, symbol, pkg["entry"], pkg["sl"], pkg["tp"], pkg["confidence"],
    )
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": pkg["entry"],
        "entry_price": pkg["entry"],
        "stop_loss": pkg["sl"],
        "take_profit": pkg["tp"],
        "meta": {
            **(pkg.get("meta") or {}),
            "strategy_name": "turtle_soup",
            "confidence": pkg["confidence"],
            "direction": pkg["direction"],
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

    Timeframe resolution (S-015 mid-sprint fix):

      1. Per-strategy ``timeframe`` from ``config/strategies.yaml`` —
         the operator-controlled source of truth. VWAP runs at 5m as
         of S-015; the legacy 15m setting is no longer compatible.
      2. ``settings["TIMEFRAME"]`` env var, then ``settings["timeframe"]``
         — only consulted if the strategies.yaml entry is missing.
      3. Hard default ``"5m"``.

    This ordering ensures the YAML change wins even on accounts whose
    .env file still has the legacy ``TIMEFRAME=15m`` line.
    """
    from src.units.strategies import load_strategy_config
    from src.units.strategies.vwap import build_vwap_signal

    try:
        strategies_cfg = load_strategy_config()
    except Exception:  # noqa: BLE001 — never fail-open on a config error
        strategies_cfg = {}
    vwap_cfg = strategies_cfg.get("vwap", {}) or {}

    symbol = settings.get("SYMBOL", settings.get("symbol", "BTCUSDT"))
    timeframe = (
        vwap_cfg.get("timeframe")
        or settings.get("TIMEFRAME")
        or settings.get("timeframe")
        or "5m"
    )
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
# Source of truth is config/strategies.yaml (S-007). Order in the YAML determines
# multiplexer priority. Falls back to the original hardcoded list if the registry
# cannot be loaded (e.g. missing pyyaml in a minimal deploy environment).
def _strategies_from_registry() -> list:
    try:
        from src.strategy_registry import load_strategies
        return [s["name"] for s in load_strategies()]
    except Exception as exc:
        logger.warning("pipeline: registry unavailable, using hardcoded STRATEGIES list: %s", exc)
        # S-012 PR C3: hardcoded fallback matches the production roster
        # in config/strategies.yaml after PR B1.
        return ["turtle_soup", "vwap"]


STRATEGIES = _strategies_from_registry()

# Per-strategy risk allocation fractions applied inside the multiplexer.
# S-012 PR C5: roster reduced to turtle_soup + vwap (50 / 50 split). The
# legacy breakout / killzone / ict builders and entries are deleted.
STRATEGY_RISK_PCT: Dict[str, float] = {
    "turtle_soup": 0.5,
    "vwap": 0.5,
}

_STRATEGY_BUILDERS: Dict[str, Callable[[dict], Dict[str, Any]]] = {
    "turtle_soup": turtle_soup_signal_builder,
    "vwap": vwap_signal_builder,
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
            report(
                "strategy_builder",
                "exception",
                level=Level.ERROR,
                reason=f"{type(exc).__name__}: {exc}",
                strategy=strategy_name,
            )
            continue

        if signal.get("side") in ("buy", "sell") and float(signal.get("qty", 0)) > 0:
            risk_scale = STRATEGY_RISK_PCT.get(strategy_name, 1.0)
            signal = dict(signal)
            signal["qty"] = float(signal["qty"]) * risk_scale
            logger.info(
                "Multiplexer: '%s' produced actionable signal (risk_scale=%.2f)",
                strategy_name, risk_scale,
            )
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
    """Pipeline adapter. Chooses strategy from STRATEGY env var.

    S-012 PR C5: roster is turtle_soup + vwap. Default is the multiplexer
    so unset / unknown values still iterate the active strategies.
    """
    logger.info("Pipeline start")

    strategy_name = str(os.environ.get("STRATEGY", "multiplexed")).strip().lower()

    if signal_builder is not None:
        builder = signal_builder
    elif strategy_name in ("turtle_soup", "turtlesoup"):
        builder = turtle_soup_signal_builder
    elif strategy_name == "vwap":
        builder = vwap_signal_builder
    else:
        # "multiplexed" or anything unknown → multiplexer.
        builder = multiplexed_signal_builder

    logger.info("Using strategy builder: %s", strategy_name)
    signal = builder(settings)
    _write_ict_signals_from_meta(signal, settings)

    if signal.get("side") in ("buy", "sell"):
        meta = signal.get("meta", {}) or {}
        price = meta.get("price", meta.get("entry_price", signal.get("price")))

        _strat_key = (meta.get("strategy_name") or strategy_name or "").lower()
        try:
            from src.strategy_registry import signal_prefixes as _sp
            _prefixes = _sp(_strat_key)
            _sig_type = _prefixes[0] if _prefixes else "trade_signal"
        except Exception:
            # Pre-S-007 fallback: preserves exact historical behaviour.
            _sig_type = (
                "ml_breakout" if _strat_key == "breakout_confirmation"
                else ("fvg" if meta.get("fvg") else "trade_signal")
            )

        write_signal(
            symbol=signal.get("symbol", "UNKNOWN"),
            signal_type=_sig_type,
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
        settings = inject_runtime_counters(settings, exchange_client)
        _strat_name = (signal.get("meta") or {}).get("strategy_name")
        if _strat_name:
            settings = inject_per_strategy_counters(settings, _strat_name)
        _sym = signal.get("symbol", settings.get("SYMBOL", "BTCUSDT"))
        _base = _sym.upper().split("/")[0]
        if _base.endswith("USDT"):
            _base = _base[:-4]
        _tags = list(dict.fromkeys(t for t in [_base, _sym] if t))
        news_result = get_news_score(settings, symbol_tags=_tags)
        if news_result.veto:
            logger.warning("news veto: %s", news_result.reason)
            result = {"status": "news_veto", "reason": news_result.reason, "signal": signal}
            _veto_msg = (
                f"\U0001f6ab News veto: {news_result.reason}\n"
                f"Symbol: {signal.get('symbol', '?')} | Side: {signal.get('side', '?')}"
                f" | Qty: {signal.get('qty', '?')}\n"
                f"Adj: {news_result.adjustment:.4f} | Items: {news_result.item_count}"
            )[:200]
            try:
                if telegram_client is not None:
                    notify_operator(telegram_client, _veto_msg)
                else:
                    send_via_alert_manager(_veto_msg)
            except Exception:
                logger.exception("news veto notify failed")
        else:
            logger.info(
                "news: decision=%s adj=%.4f items=%d reason=%s",
                news_result.decision,
                news_result.adjustment,
                news_result.item_count,
                news_result.reason[:80],
            )

            multi = _multi_account_dispatch_enabled(settings)
            global_dry = str(
                (settings.get("DRY_RUN") if isinstance(settings, dict) else None)
                or os.environ.get("DRY_RUN", "false")
            ).strip().lower() in {"true", "1", "yes", "on"}

            def _signal_packageable(_sig: Dict[str, Any]) -> bool:
                """True only when the signal carries everything the
                Coordinator needs to fan out (entry, sl, tp). Stub /
                synthetic signals from smoke tests routinely omit sl/tp;
                routing those through the multi-account path would crash
                in ``_signal_to_order_package``. Falling back to the
                legacy single-client path preserves their behaviour."""
                _meta = _sig.get("meta") or {}
                return (
                    (_sig.get("entry_price") or _sig.get("price") or _meta.get("price")) is not None
                    and (_sig.get("stop_loss") or _meta.get("stop_loss") or _meta.get("sl")) is not None
                    and (_sig.get("take_profit") or _meta.get("take_profit") or _meta.get("tp")) is not None
                )

            if multi and not global_dry and _signal_packageable(signal):
                # CP-2026-05-02: when the system is live AND the signal
                # is fully populated, fan-out to every account so each
                # one can apply its own dry/live state. Validation runs
                # in forced-dry mode first (halt flag, MAX_QTY, etc.).
                val_settings = {
                    **(settings if isinstance(settings, dict) else {}),
                    "DRY_RUN": "true",
                    "ALLOW_LIVE_TRADING": "false",
                }
                result = safe_place_order(signal, val_settings, exchange_client)
                if result.get("status") == "dry_run":
                    try:
                        from src.core.coordinator import Coordinator
                        pkg = _signal_to_order_package(signal, settings)
                        coord = Coordinator()
                        multi_results = coord.multi_account_execute(
                            pkg, dry_run=False,
                        )
                        result = {
                            "status": "multi_account_dispatched",
                            "validation": result,
                            "multi_account_results": multi_results,
                            "order": signal,
                        }
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "multi-account dispatch failed: %s", exc,
                        )
                        result = {
                            "status": "failed_dispatch",
                            "reason": f"multi_account_execute: {exc}",
                            "order": signal,
                        }
            else:
                # Legacy single-client path. Reached when:
                #   * MULTI_ACCOUNT_DISPATCH is pinned off by the operator,
                #   * the global mode is dry (no need to fan out), or
                #   * signal is missing entry/sl/tp (smoke/synthetic).
                result = safe_place_order(signal, settings, exchange_client)

    _report_pipeline_outcome(result, signal)

    try:
        # S-012 PR E4: include strategy attribution so the audit log
        # answers "which strategy fired this tick" for every line.
        # Source priority: signal.meta.strategy_name (set by every
        # builder in src/runtime/pipeline.py) → top-level signal["strategy"]
        # → settings["STRATEGY"]/env → "unknown".
        _meta = signal.get("meta") or {}
        _strategy = (
            _meta.get("strategy_name")
            or signal.get("strategy")
            or settings.get("STRATEGY")
            or os.environ.get("STRATEGY")
            or "unknown"
        )
        log_signal(
            {
                "event": "pipeline_result",
                "strategy": _strategy,
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "qty": signal.get("qty"),
                "status": result.get("status"),
                "reason": result.get("reason"),
            }
        )
    except Exception as _audit_exc:  # noqa: BLE001
        logger.exception("pipeline audit log_signal failed")
        # Audit data loss is operationally relevant — surface it.
        # WARN, not ERROR, because this is a write to a JSONL we own;
        # if it fails repeatedly the disk-free health check or hourly
        # report will escalate.
        report(
            "audit_log",
            "write_failed",
            level=Level.WARN,
            reason=f"{type(_audit_exc).__name__}: {_audit_exc}",
        )

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

    write_status()

    return {
        "signal": signal,
        "order_result": result,
    }
