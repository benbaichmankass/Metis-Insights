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
import threading
import time
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


_OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


_CLIENT_CACHE: Dict[Any, Any] = {}
_CLIENT_CACHE_LOCK = threading.Lock()


def _client_cache_key(settings: Dict[str, Any]) -> Optional[tuple]:
    """Identity of the connector a given ``settings`` would build.

    Returns ``None`` for any exchange whose client is NOT safe to share, in
    which case the caller builds a fresh one exactly as before.

    **IB is deliberately excluded.** An ``IBMarketData`` holds a live socket on
    a specific clientId; handing one instance to concurrent callers is the
    documented multi-client collision that BL-20260706-IBACCTUPDATES-COLLISION
    is about. IB already has its own connection reuse + circuit breaker in
    ``IBClient``; this cache must not second-guess it.
    """
    name = str(
        settings.get("EXCHANGE", settings.get("exchange", "bybit"))
    ).strip().lower()
    if name == "bybit":
        testnet_raw = str(os.environ.get("BYBIT_TESTNET", "true")).strip().lower()
        return (
            "bybit",
            _connector_class_id("src.exchange.bybit_connector", "BybitConnector"),
            testnet_raw not in {"false", "0", "no"},
            settings.get("BYBIT_API_KEY"),
            settings.get("BYBIT_API_SECRET"),
        )
    if name == "alpaca":
        return (
            "alpaca",
            _connector_class_id("src.exchange.alpaca_connector", "AlpacaMarketData"),
            settings.get("ALPACA_API_KEY_ID"),
            settings.get("ALPACA_API_SECRET_KEY"),
        )
    if name == "oanda":
        return (
            "oanda",
            _connector_class_id("src.exchange.oanda_connector", "OandaMarketData"),
            settings.get("OANDA_API_TOKEN"),
        )
    return None


def _connector_class_id(module_path: str, attr: str):
    """Identity of the class ``_build_exchange_client_uncached`` WOULD construct.

    Part of the cache key so the cache is keyed on *what would actually be
    built*, not merely on the credentials. Without this the memo returns a
    stale client after the connector class is swapped — which is exactly how
    it broke ``test_default_is_bybit``: an earlier test in the full suite
    warmed the cache with a real ``BybitConnector``, so a later
    ``monkeypatch.setattr(... BybitConnector, _FakeBybit)`` had NO effect and
    the caller silently got the pre-patch object.

    That is a real defect, not merely a test artifact: any caller that swaps
    the connector class at runtime would likewise be handed the old one with
    no signal. Resolving the attribute here (a cached module lookup, not an
    import cost) makes such a swap MISS the cache and rebuild, which is the
    correct behaviour in both production and tests.

    Returns ``None`` when the module cannot be imported, which propagates to a
    ``None`` cache key and disables caching for that exchange — fail-safe:
    never serve a client we cannot identify.
    """
    try:
        import importlib
        return id(getattr(importlib.import_module(module_path), attr))
    except Exception:  # noqa: BLE001
        return None


def _build_exchange_client(settings: Dict[str, Any]):
    """Return a connector instance for the configured exchange.

    Logic preserved verbatim from the legacy
    ``pipeline._build_killzone_exchange``, with ONE addition (2026-08-10):
    the constructed client is MEMOIZED per credential identity.

    **Why (BL-20260810-TICK-CHAIN-260S-PER-TICK).** Every strategy builder
    called this, so a 52-strategy tick built 52 ccxt clients. ccxt loads the
    exchange's full market catalogue LAZILY on the first ``fetch_ohlcv`` of
    each instance, so each fresh client paid a full instrument-list download
    before its ~200ms kline call. Measured from journalctl: ~3.2s per builder,
    ~166s of a 251s tick. Reusing the client makes ``load_markets()`` happen
    once per process instead of once per strategy per tick.

    This changes NO data semantics — the same symbol/timeframe/limit request
    returns the same bytes; we simply stop re-downloading the catalogue that
    describes them. It is not a cache of market data (see ``fetch_candles``
    for that, which is separately bounded and separately disable-able).
    """
    key = _client_cache_key(settings)
    if key is not None:
        with _CLIENT_CACHE_LOCK:
            cached = _CLIENT_CACHE.get(key)
        if cached is not None:
            return cached

    client = _build_exchange_client_uncached(settings)

    # Only cache a client that constructed cleanly. A failed build raises
    # before reaching here, so a broken client is never memoized.
    if key is not None and client is not None:
        with _CLIENT_CACHE_LOCK:
            _CLIENT_CACHE.setdefault(key, client)
            return _CLIENT_CACHE[key]
    return client


def reset_exchange_client_cache() -> None:
    """Drop every memoized connector. For tests and for a forced reconnect."""
    with _CLIENT_CACHE_LOCK:
        _CLIENT_CACHE.clear()


def _build_exchange_client_uncached(settings: Dict[str, Any]):
    """The original construction path, unchanged."""
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


_CANDLE_CACHE: Dict[Any, tuple] = {}
_CANDLE_CACHE_LOCK = threading.Lock()

# Seconds in each timeframe, used to bound the cache TTL by the bar's own
# period. Unknown timeframes get no cache at all (fail-safe: serve fresh).
_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "1w": 604800,
}


def _candle_cache_ttl(timeframe: str) -> float:
    """TTL for a cached frame — a FRACTION of the bar's own period.

    A cached frame's only staleness risk is its LAST (still-forming) bar, so
    the tolerable age scales with the bar. ``CANDLE_CACHE_TTL_FRACTION``
    (default 0.10) is deliberately a *_FRACTION cadence knob, not a default-off
    ``*_ENABLED`` gate (Prime Directive: no required capability behind an
    enable flag), and an unparseable value falls back to the default rather
    than disabling — a typo must not silently switch caching off OR on.

    Set the fraction to 0 to serve every request fresh (the rollback path,
    one env flip + restart, no redeploy).
    """
    base = _TF_SECONDS.get(str(timeframe).strip().lower())
    if not base:
        return 0.0
    try:
        frac = float(os.environ.get("CANDLE_CACHE_TTL_FRACTION", "0.10"))
    except (TypeError, ValueError):
        frac = 0.10
    if frac <= 0:
        return 0.0
    # Cap so a 1d bar cannot serve an hours-old frame: at most 60s of staleness.
    return min(base * frac, 60.0)


def _candle_cache_key(client: Any, symbol: str, timeframe: str,
                      limit: int, since: Optional[int]) -> Optional[tuple]:
    """Cache identity. ``since`` requests are NEVER cached.

    A ``since=`` read is a historical-range reconstruction (the M30 exit
    panel); it is rare, large, and its correctness matters more than its
    speed, so it always goes to the venue.
    """
    if since is not None:
        return None
    if _candle_cache_ttl(timeframe) <= 0:
        return None
    # Key on the CLIENT INSTANCE, not the symbol alone: two connectors may be
    # different venues (or testnet vs mainnet) serving the same symbol string,
    # and their candles are not interchangeable.
    return (id(client), str(symbol), str(timeframe), int(limit))


def _candle_cache_get(key: Optional[tuple]):
    if key is None:
        return None
    with _CANDLE_CACHE_LOCK:
        hit = _CANDLE_CACHE.get(key)
    if not hit:
        return None
    stored_at, df, ttl = hit
    if (time.monotonic() - stored_at) > ttl:
        return None
    return df


def _candle_cache_put(key: Optional[tuple], df) -> None:
    if key is None or df is None:
        return
    ttl = _candle_cache_ttl(key[2])
    if ttl <= 0:
        return
    with _CANDLE_CACHE_LOCK:
        # Bound the map so a long-lived process can't accumulate entries for
        # every (symbol, timeframe, limit) ever requested.
        if len(_CANDLE_CACHE) > 512:
            _CANDLE_CACHE.clear()
        _CANDLE_CACHE[key] = (time.monotonic(), df.copy(), ttl)


def reset_candle_cache() -> None:
    """Drop every cached frame. For tests and for a forced refresh."""
    with _CANDLE_CACHE_LOCK:
        _CANDLE_CACHE.clear()


def _fetch_phase(name: str):
    """Time ONE venue candle fetch into the per-tick cost record. NO-OP fallback.

    WHY. The 2026-08-12 warm read (96 ticks, one process) put `pipeline.signal_build`
    at **43.3% of the whole tick** — ~46.7s of a 107.9s tick, over 23 symbols. That
    localises the dominant cost but does NOT say whether it is FETCH-bound (venue
    round-trips, which parallelise) or COMPUTE-bound (indicator math on the frames,
    which on a 2-core box does not). Those two have near-opposite fixes, so shipping
    either without this split would be a guess.

    Keyed on TIMEFRAME because that also sizes a second, cheaper hypothesis derived
    from the code alone: `_candle_cache_ttl` is `min(bar_seconds * frac, 60.0)`, and
    consecutive ticks are >= 108s apart, so **the cache cannot hit across ticks for
    ANY timeframe** — its only value today is within-tick sharing between strategies
    on the same (symbol, timeframe, limit). The 60s cap binds for every bar >= 10m
    (a 1h frame wants 360s, a 4h frame 1440s), which are exactly the frames where a
    ~108s-old copy is safest — a 4h bar is 0.75% formed at that age. If the miss
    counts here are dominated by >= 15m frames, raising the cap is a large win for a
    one-line change; if they are dominated by 5m (30s TTL, legitimately fresh), it
    buys nothing. The counts decide it, not the argument.

    ⚠️ **CROSS-CUTTING — do NOT sum these into a parent's subtree.** Unlike
    `monitor.*` (all under `order_monitor`) and `pipeline.*` (all under
    `run_one_tick`), `fetch_candles` is called from BOTH halves of the tick: by the
    signal builders under `run_one_tick`, and by the monitor's ohlcv fetcher under
    `order_monitor`. The names are dotted so `tick_cost.snapshot()` counts them as
    children and keeps them out of `attributed_pct` (no double-count), and each
    one's own `pct_of_total` stays valid — but the `fetch.*` family does not belong
    to either parent, and a reader adding it to `pipeline.*` would over-count.

    `fetch.cache_hit` is recorded for its **`n`, not its duration** (a hit is a dict
    lookup). Hits vs per-timeframe misses is the direct empirical test of the
    never-hits-across-ticks claim above, rather than leaving it as arithmetic.
    """
    try:
        from src.runtime.tick_cost import hook
        return hook(f"fetch.{name}")
    except Exception:  # noqa: BLE001
        # An instrumentation import error must never stop a live fetch.
        import contextlib
        return contextlib.nullcontext()


def fetch_candles(
    symbol: str,
    timeframe: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    limit: int,
    exchange_client: Any = None,
    since: Optional[int] = None,
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
    since : int, optional
        Epoch MILLISECONDS (CCXT convention) to fetch candles FORWARD from —
        the historical-range read the M30 P5 exit panel uses to reconstruct
        MFE/MAE over a closed trade's holding window. ``None`` (default) fetches
        the exchange's most-recent ``limit`` candles (unchanged behaviour). Only
        connectors whose ``get_ohlcv`` accepts ``since`` honour it (Bybit/CCXT
        today); against a connector that does not, ``fetch_candles`` returns
        ``None`` rather than silently returning the wrong (recent) bars.
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

    cache_key = _candle_cache_key(exchange_client, symbol, timeframe, limit, since)
    cached = _candle_cache_get(cache_key)
    if cached is not None:
        # Counted for its `n` (see _fetch_phase): hits vs misses is the empirical
        # test of whether the cache reaches across ticks at all.
        with _fetch_phase("cache_hit"):
            pass
        # .copy() is load-bearing: a builder that mutates the frame (adds an
        # indicator column) must not corrupt the copy the next builder sees.
        return cached.copy()

    _tf_label = str(timeframe).strip().lower() or "unknown"
    with _fetch_phase(_tf_label):
        return _fetch_candles_uncached(
            exchange_client, symbol, timeframe, limit, since, cache_key
        )


def _fetch_candles_uncached(
    exchange_client: Any,
    symbol: str,
    timeframe: str,
    limit: int,
    since: Optional[int],
    cache_key: Optional[tuple],
) -> Optional[pd.DataFrame]:
    """The venue round-trip + normalisation, split out so `_fetch_phase` wraps
    exactly the uncached path and nothing else. Behaviour is unchanged."""
    try:
        if since is not None:
            candles_raw = exchange_client.get_ohlcv(
                symbol, timeframe, limit=limit, since=since
            )
        else:
            candles_raw = exchange_client.get_ohlcv(symbol, timeframe, limit=limit)
    except TypeError as exc:
        # A `since` request against a connector whose get_ohlcv predates the
        # range param — do NOT silently fall back to recent bars (that would
        # misrepresent the requested historical window); return None so the
        # caller records the window as uncovered.
        logger.warning(
            "fetch_candles: connector get_ohlcv does not accept since= "
            "(symbol=%s timeframe=%s): %s", symbol, timeframe, exc,
        )
        return None
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

    _candle_cache_put(cache_key, candles_df)
    return candles_df.copy()
