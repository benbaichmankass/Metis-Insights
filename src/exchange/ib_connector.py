"""Interactive Brokers market-data connector (MES candles via ib_insync).

Companion to ``src/units/accounts/ib_client.py`` (which owns *execution*).
This module owns *market data*: it exposes the same ``get_ohlcv(symbol,
timeframe, limit)`` surface as ``BybitConnector``
so it is a drop-in connector for ``src/runtime/market_data.fetch_candles``
and the per-symbol data routing in the multi-symbol pipeline.

Why a separate connector from IBClient:
    The repo separates per-exchange *market data* (``src/exchange/``)
    from per-account *execution* (``src/units/accounts/``). IBClient is
    the execution surface (orders/positions/balance, tied to a trading
    account). IBMarketData is the read-only candle surface, tied to a
    Gateway endpoint rather than an account. They share the underlying
    ib_insync connection through the ``get_ib_client`` registry, but use
    distinct ``clientId``s so a market-data request never contends with
    an order socket.

No API keys: like IBClient, the connection is the IB Gateway / TWS login
session (host + port + clientId). See ib_client.py for the full rationale.
ib_insync is imported lazily (with ib_async fallback) so this module
imports cleanly without the package installed.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from typing import Optional

import pandas as pd

from src.units.accounts.ib_client import DEFAULT_IB_HOST, IBClient, get_ib_client

logger = logging.getLogger(__name__)

# Hard wall-clock cap (seconds) on a single IB historical-data request.
# WHY (2026-06-05 incident): a logged-out IB Gateway can ACCEPT the API
# socket (so IBClient.connect() succeeds within its own timeout) yet never
# return bars for reqHistoricalData — which, with no timeout, blocks the
# caller indefinitely. The bot's pipeline fetches market data inline on the
# single main-loop thread, so one hung IB fetch stalls the BTCUSDT/Bybit
# tick AND starves the liveness heartbeat. Bounding reqHistoricalData keeps
# a wedged/logged-out Gateway from ever blocking the live-money Bybit path:
# on timeout ib_insync returns the (empty) bars it has, get_ohlcv returns
# None, and fetch_candles degrades gracefully to its fallback. Override on
# the VM via IB_FETCH_TIMEOUT_S without a redeploy.
try:
    _IB_FETCH_TIMEOUT_S = float(os.environ.get("IB_FETCH_TIMEOUT_S", "") or 8.0)
except (TypeError, ValueError):
    _IB_FETCH_TIMEOUT_S = 8.0


# --- Single-thread pinning for every IB market-data request ----------------
#
# WHY (2026-08-14 incident, BL-20260814-IB-EVENTLOOP-CONTENTION): ib_insync
# resolves its event loop per sync call and the cached IB's socket transport
# LIVES ON ONE LOOP (see IBClient._ensure_event_loop's docstring: "dispatching
# a request on a different loop would hang instead of returning bars"). So
# every IB request in a process must run on ONE thread. The web-api already
# encodes this — src/web/api/routers/candles.py pins its fetches to a
# max_workers=1 pool and calls that "load-bearing".
#
# The trader had no such pin, and until the M20 exit-loop decouple it did not
# need one: exit evaluation ran INLINE on the tick thread, so the process had
# exactly one IB caller by construction. The decouple moved exit evaluation to
# a daemon thread, and both it and the main tick call get_ohlcv on the SAME
# cached IBClient (get_ib_client caches per host/port/client_id). IBClient's
# _usage_lock covers connect() but NOT reqHistoricalData, so the two threads
# drove one loop and produced, live:
#     IBMarketData.get_ohlcv failed for symbol=MES timeframe=5m:
#         This event loop is already running
# The same collisions time out the liveness probe, which trips the circuit
# breaker for IB_BREAKER_COOLDOWN_S (120s) — and the breaker suppresses ALL IB
# calls, so the blast radius is far wider than the racing fetch: candles go
# unavailable (MONITOR BLIND on open MGC/MES packages), strategy_builder raises
# for MGC/MES/MHG, and balance() returns None so new entries are refused with
# sizing_failed.
#
# A plain lock is NOT sufficient. Serialising still lets thread B run the loop
# that thread A created; the transport is bound to the loop, and driving it
# from a different thread is the documented hang. Pinning is the fix, which is
# why this mirrors candles._FETCH_EXECUTOR rather than widening _usage_lock.
_IB_FETCH_THREAD_PREFIX = "ib-marketdata"
_IB_FETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix=_IB_FETCH_THREAD_PREFIX
)

# How long a caller waits for the pinned thread. It covers QUEUE time (another
# thread's fetch ahead of us) plus our own bounded request, so it must exceed
# _IB_FETCH_TIMEOUT_S or a queued-but-healthy fetch would be discarded as a
# failure. Overridable on the VM without a redeploy; an unparseable value falls
# back to the derived default rather than disabling the wait.
try:
    _IB_FETCH_QUEUE_TIMEOUT_S = float(
        os.environ.get("IB_FETCH_QUEUE_TIMEOUT_S", "") or 0.0
    ) or (_IB_FETCH_TIMEOUT_S * 3.0 + 5.0)
except (TypeError, ValueError):
    _IB_FETCH_QUEUE_TIMEOUT_S = _IB_FETCH_TIMEOUT_S * 3.0 + 5.0


def _on_ib_fetch_thread() -> bool:
    """True when already running ON the pinned thread.

    Re-entrancy guard: submitting to a max_workers=1 pool from inside that
    pool's own worker would wait for a slot that cannot free until the caller
    returns — a self-deadlock. Running inline is correct there anyway, since
    being on that thread is exactly the property the pin exists to provide.
    """
    return threading.current_thread().name.startswith(_IB_FETCH_THREAD_PREFIX)


# Map the bot's timeframe vocabulary to IB ``barSizeSetting`` strings.
_BAR_SIZE = {
    "1m": "1 min",
    "2m": "2 mins",
    "3m": "3 mins",
    "5m": "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "2h": "2 hours",
    "4h": "4 hours",
    "1d": "1 day",
}

# Approximate seconds per timeframe, used to size the IB ``durationStr``.
_TF_SECONDS = {
    "1m": 60, "2m": 120, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


def _duration_str(timeframe: str, limit: int) -> str:
    """Return an IB ``durationStr`` covering at least *limit* bars.

    IB caps the seconds form at 86400 ("1 D"); above that it wants a day
    count. A small headroom factor absorbs non-trading gaps so the
    request still returns ``limit`` bars after IB drops closed-session
    periods.
    """
    secs = _TF_SECONDS.get(timeframe, 300) * max(int(limit), 1)
    secs = int(secs * 1.5) + 60  # headroom for session gaps
    if secs <= 86400:
        return f"{secs} S"
    days = (secs // 86400) + 1
    return f"{days} D"


class IBMarketData:
    """Read-only OHLCV connector for Interactive Brokers instruments.

    Parameters mirror :class:`IBClient` connection identity. Only ``MES``
    is wired (contract resolution lives in :class:`IBClient`); other
    symbols raise ``ValueError`` from the underlying contract builder.

    Parameters
    ----------
    host, port, client_id, account : see :class:`IBClient`.
        ``client_id`` should differ from the execution client's id so the
        data socket and the order socket coexist on the Gateway.
    use_rth : bool
        Regular-trading-hours only. Default False (include the full
        electronic session, matching crypto's 24/7 candle stream as
        closely as a futures session allows).
    market_data_type : int
        IB market-data mode passed to ``reqMarketDataType``: 1=live,
        2=frozen, 3=delayed, 4=delayed-frozen. **Defaults to 3 (delayed)**
        so the bot works WITHOUT a paid CME real-time subscription —
        suitable for strategy refinement + model training. Set to 1 once a
        live CME subscription is active for latency-sensitive execution.
    _client : IBClient, optional
        Test seam — inject a client with a fake ib_insync ``IB``.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_IB_HOST,
        port: int,
        client_id: int,
        account: Optional[str] = None,
        use_rth: bool = False,
        market_data_type: int = 3,
        _client: Optional[IBClient] = None,
    ) -> None:
        self.use_rth = bool(use_rth)
        self.market_data_type = int(market_data_type)
        if _client is not None:
            self._client = _client
        else:
            self._client = get_ib_client(
                host=host, port=int(port), client_id=int(client_id), account=account,
            )

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """Return a candle DataFrame for *symbol* / *timeframe*.

        Columns: ``["timestamp", "open", "high", "low", "close",
        "volume"]`` — identical to the Bybit/Binance connectors so
        ``fetch_candles`` and the strategies consume IB candles unchanged.
        Returns ``None`` on any error (never raises) so the pipeline's
        per-symbol loop degrades gracefully when the Gateway is down.

        The request is executed on the process-wide pinned IB thread (see
        ``_IB_FETCH_EXECUTOR``) regardless of which thread calls, because the
        cached IB connection's socket transport is bound to a single loop.
        Callers are unaffected: this is still a blocking call returning the
        same value, and it still never raises.
        """
        if _on_ib_fetch_thread():
            return self._get_ohlcv_pinned(symbol, timeframe, limit)
        try:
            future = _IB_FETCH_EXECUTOR.submit(
                self._get_ohlcv_pinned, symbol, timeframe, limit
            )
            return future.result(timeout=_IB_FETCH_QUEUE_TIMEOUT_S)
        except _FutureTimeout:
            # Degrade exactly like a venue timeout: the caller already handles
            # None. Distinct message so a queue starvation is never read as a
            # gateway fault — they need different fixes.
            logger.warning(
                "IBMarketData.get_ohlcv timed out waiting for the pinned IB "
                "thread (symbol=%s timeframe=%s, waited %.1fs) — another IB "
                "request is still in flight.",
                symbol, timeframe, _IB_FETCH_QUEUE_TIMEOUT_S,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — never raise into the tick
            logger.warning(
                "IBMarketData.get_ohlcv dispatch failed for symbol=%s "
                "timeframe=%s: %s", symbol, timeframe, exc,
            )
            return None

    def _get_ohlcv_pinned(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """The real fetch. MUST run on the pinned thread — see ``get_ohlcv``."""
        bar_size = _BAR_SIZE.get(timeframe)
        if bar_size is None:
            logger.warning("IBMarketData: unsupported timeframe %r", timeframe)
            return None
        try:
            ib = self._client.connect()
            # Re-assert the loop right before the data calls. connect() already
            # does this, but a Telegram alert (asyncio.run) firing between
            # connect() and here would null the current loop and ib_insync's
            # reqHistoricalData would raise "no current event loop". Re-asserting
            # the client's persistent loop (the one this IB is bound to) keeps
            # the sync request resolvable.
            self._client._ensure_event_loop()
            # Delayed mode (3) by default → no paid CME real-time feed
            # needed; IB serves free delayed futures bars. Best-effort:
            # older ib_insync builds always have reqMarketDataType.
            try:
                ib.reqMarketDataType(self.market_data_type)
            except Exception:  # noqa: BLE001
                pass
            contract = self._client._build_contract(symbol)
            # `timeout` is the hard cap that makes a logged-out/wedged
            # Gateway unable to block the (single-threaded) trading loop —
            # see _IB_FETCH_TIMEOUT_S above. On timeout ib_insync returns
            # whatever bars arrived (typically none), which we treat as a
            # graceful no-data result below.
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=_duration_str(timeframe, limit),
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=self.use_rth,
                formatDate=2,
                timeout=_IB_FETCH_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "IBMarketData.get_ohlcv failed for symbol=%s timeframe=%s: %s",
                symbol, timeframe, exc,
            )
            return None

        if not bars:
            return None
        rows = []
        for b in bars:
            ts = getattr(b, "date", None)
            rows.append({
                "timestamp": pd.to_datetime(ts) if ts is not None else pd.NaT,
                "open": getattr(b, "open", None),
                "high": getattr(b, "high", None),
                "low": getattr(b, "low", None),
                "close": getattr(b, "close", None),
                "volume": getattr(b, "volume", None),
            })
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df
