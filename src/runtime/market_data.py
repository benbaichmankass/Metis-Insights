"""Market-data fetcher — keeps OHLCV out of the pipeline signal builders.

S-033 (architecture-audit-2026-05-02 § P1-8). Pre-PR
``turtle_soup_signal_builder`` and ``vwap_signal_builder`` in
``src/runtime/pipeline.py`` instantiated a ``BybitConnector`` /
``BinanceConnector`` and called ``get_ohlcv()`` inline. That coupled
**signal generation** (a strategy concern) to **exchange reachability**
(an infrastructure concern). Per CLAUDE.md § Architecture rules § 2 the
strategy units should be pure — given candles + config, they emit a
package; they shouldn't decide where the candles come from.

This module owns the fetch + the DataFrame normalisation so the
builders can call a single helper and stay focused on signal logic.
The two existing builders both did the same shape of work:

  1. Pick the connector based on ``settings["EXCHANGE"]``.
  2. Honour ``BYBIT_TESTNET``.
  3. Call ``get_ohlcv(symbol, timeframe, limit=N)``.
  4. Convert list-of-rows → ``pandas.DataFrame`` with the canonical
     column ordering.
  5. Cast OHLCV columns to numeric.

``fetch_candles`` does (1)–(5) in one place. The builders accept the
returned DataFrame (or ``None`` when the fetch failed) and decide how
to react; this module never inspects strategy state.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


_OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


def _build_exchange_client(settings: Dict[str, Any]):
    """Return a connector instance for the configured exchange.

    Logic preserved verbatim from the legacy
    ``pipeline._build_killzone_exchange`` so the runtime behaviour is
    bit-for-bit identical post-PR.
    """
    exchange_name = str(
        settings.get("EXCHANGE", settings.get("exchange", "bybit"))
    ).strip().lower()
    bybit_testnet_raw = str(
        os.environ.get("BYBIT_TESTNET", "true")
    ).strip().lower()
    testnet = bybit_testnet_raw not in {"false", "0", "no"}

    if exchange_name == "bybit":
        from src.exchange.bybit_connector import BybitConnector
        return BybitConnector(
            api_key=settings.get("BYBIT_API_KEY"),
            api_secret=settings.get("BYBIT_API_SECRET"),
            testnet=testnet,
        )

    if exchange_name in ("interactive_brokers", "ib"):
        return _build_ib_market_data(settings)

    if exchange_name == "alpaca":
        from src.exchange.alpaca_connector import AlpacaMarketData
        return AlpacaMarketData(
            api_key=settings.get("ALPACA_API_KEY_ID"),
            api_secret=settings.get("ALPACA_API_SECRET_KEY"),
        )

    if exchange_name == "oanda":
        from src.exchange.oanda_connector import OandaMarketData
        return OandaMarketData(api_token=settings.get("OANDA_API_TOKEN"))

    raise ValueError(f"Unsupported EXCHANGE value: {exchange_name}")


def _build_ib_market_data(settings: Dict[str, Any]):
    """Return an IBMarketData connector for the IB Gateway endpoint.

    IB has no API keys — connection identity (host/port/clientId/account)
    is resolved from the IB account entry in ``config/accounts.yaml`` (via
    the canonical loader), with ``IB_HOST`` / ``IB_PORT`` env overrides.
    The market-data ``clientId`` is offset off the execution client's id so
    the data socket and the order socket coexist on the Gateway.
    """
    from src.exchange.ib_connector import IBMarketData

    host = (
        settings.get("IB_HOST")
        or os.environ.get("IB_HOST")
        or _ib_account_field("ib_host")
        or "127.0.0.1"
    )
    port = (
        settings.get("IB_PORT")
        or os.environ.get("IB_PORT")
        or _ib_account_field("ib_port")
    )
    if not port:
        raise ValueError(
            "IB market data: no ib_port (config IB account / IB_PORT env)."
        )
    account = (
        settings.get("IB_ACCOUNT")
        or os.environ.get("IB_ACCOUNT")
        or _ib_account_field("ib_account")
    )
    exec_client_id = int(_ib_account_field("ib_client_id") or (int(port) % 1000))
    # +1 keeps the market-data socket distinct from the execution socket.
    md_client_id = int(
        settings.get("IB_MD_CLIENT_ID")
        or os.environ.get("IB_MD_CLIENT_ID")
        or (exec_client_id + 1)
    )
    # Default to delayed data (3) so MES works without a paid CME real-time
    # subscription (strategy-refinement / model-training mode). Override via
    # IB_MARKET_DATA_TYPE=1 once a live CME feed is active.
    try:
        md_type = int(
            settings.get("IB_MARKET_DATA_TYPE")
            or os.environ.get("IB_MARKET_DATA_TYPE")
            or 3
        )
    except (TypeError, ValueError):
        md_type = 3
    return IBMarketData(
        host=str(host),
        port=int(port),
        client_id=md_client_id,
        account=str(account) if account else None,
        market_data_type=md_type,
    )


def _ib_account_field(field: str):
    """Best-effort read of an ``ib_*`` field from the first IB account.

    Uses the canonical accounts-dict loader (not a hand-rolled parser).
    Returns ``None`` when no IB account is configured or on any error.
    """
    try:
        from src.config.accounts_loader import load_accounts_dict
        accounts = load_accounts_dict() or {}
    except Exception:  # noqa: BLE001
        return None
    for cfg in accounts.values():
        if not isinstance(cfg, dict):
            continue
        if str(cfg.get("exchange", "")).lower() in ("interactive_brokers", "ib"):
            val = cfg.get(field)
            if val is not None:
                return val
    return None


def connector_for_symbol(symbol: str, settings: Optional[Dict[str, Any]] = None):
    """Return the right connector for *symbol* based on its instrument profile.

    Routes candle fetches per instrument: BTCUSDT → Bybit, MES →
    Interactive Brokers (per ``config/instruments.yaml``). Falls back to the
    process ``EXCHANGE`` setting when the symbol has no instrument profile,
    so the existing single-symbol/single-exchange path is unchanged.
    """
    settings = settings or {}
    exchange = None
    try:
        from src.core.profile_loader import load_instrument_profiles
        profiles = load_instrument_profiles() or {}
        prof = profiles.get(symbol)
        if prof is None:
            # Contract-month symbols (BL-20260617-MHGN6-CANDLEROUTE): an
            # adopted/broker-specific futures contract like ``MHGN6`` has no
            # instrument profile of its own — resolve its base root (``MHG``)
            # so the fetch routes to the exchange that actually trades it
            # (IBKR) instead of falling through to the process EXCHANGE
            # default (Bybit). Same month-code grammar as
            # ``order_monitor._base_futures_symbol``.
            import re
            m = re.match(r"^([A-Z]{2,})([FGHJKMNQUVXZ]\d{1,2})$",
                         str(symbol or "").strip().upper())
            if m:
                prof = profiles.get(m.group(1))
        if prof is not None:
            exchange = getattr(prof, "exchange", None)
    except Exception:  # noqa: BLE001
        exchange = None
    if exchange:
        routed = dict(settings)
        routed["EXCHANGE"] = exchange
        return _build_exchange_client(routed)
    return _build_exchange_client(settings)


def fetch_candles(
    symbol: str,
    timeframe: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    limit: int,
    exchange_client: Any = None,
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV candles for *symbol* / *timeframe* and return a DataFrame.

    Parameters
    ----------
    symbol : str
        Exchange-native symbol (e.g. ``"BTCUSDT"`` for Bybit).
    timeframe : str
        Exchange-native timeframe (e.g. ``"5m"``, ``"15m"``).
    settings : dict, optional
        Pipeline settings — used to pick the connector + read API
        creds when ``exchange_client`` is not provided. Fields
        consulted: ``EXCHANGE`` / ``exchange`` (default bybit),
        ``BYBIT_API_KEY``, ``BYBIT_API_SECRET``.
    limit : int
        Number of candles to fetch.
    exchange_client : object, optional
        Pre-built connector. When provided, ``fetch_candles`` skips
        the construction step and uses this client directly. The
        pipeline builders use this to keep the existing
        ``monkeypatch.setattr(pipeline, "_build_killzone_exchange",
        …)`` test fixtures working — the builder constructs the
        client (through the shim the tests patch) and passes it in.

    Returns
    -------
    pandas.DataFrame | None
        DataFrame with columns
        ``["timestamp", "open", "high", "low", "close", "volume"]``
        (numeric where applicable), or ``None`` when the exchange
        returned no rows. Never raises; on a configuration or network
        error logs and returns ``None`` so the caller can decide how
        to react. The legacy builders raised ``RuntimeError`` on a
        missing fetch — they keep that behaviour by checking the
        return value and raising themselves.
    """
    if exchange_client is None:
        try:
            exchange_client = _build_exchange_client(settings or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_candles: connector init failed (%s)", exc)
            return None

    try:
        candles_raw = exchange_client.get_ohlcv(symbol, timeframe, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_candles: get_ohlcv failed for symbol=%s timeframe=%s (%s)",
            symbol, timeframe, exc,
        )
        return None

    if candles_raw is None:
        return None
    if hasattr(candles_raw, "__len__") and len(candles_raw) == 0:
        return None

    if isinstance(candles_raw, pd.DataFrame):
        candles_df = candles_raw.copy()
    else:
        candles_df = pd.DataFrame(candles_raw, columns=list(_OHLCV_COLUMNS))

    for col in ("open", "high", "low", "close", "volume"):
        if col in candles_df.columns:
            candles_df[col] = pd.to_numeric(candles_df[col], errors="coerce")

    return candles_df
