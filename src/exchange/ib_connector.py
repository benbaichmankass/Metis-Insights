"""Interactive Brokers market-data connector (MES candles via ib_insync).

Companion to ``src/units/accounts/ib_client.py`` (which owns *execution*).
This module owns *market data*: it exposes the same ``get_ohlcv(symbol,
timeframe, limit)`` surface as ``BybitConnector`` / ``BinanceConnector``
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
        """
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
