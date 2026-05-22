"""Interactive Brokers historical adapter for `market_raw` (MES intraday).

Pulls historical intraday bars for MES (Micro E-mini S&P 500) — and any
other IB-addressable instrument — via `ib_insync`, for the regime
classifier and other market-data baselines that need the strategies'
real timeframes (turtle_soup=15m, vwap/ict_scalp_5m=5m).

WHY THIS RUNS ON THE LIVE VM (not the trainer): IB Gateway is a
loopback-only socket on the live VM (`127.0.0.1:4002` for the paper
gateway, via the gnzsnz socat relay; see config/accounts.yaml). IBKR
permits exactly one logged-in session per username, so we CANNOT spin up
a second gateway on the trainer — doing so would disconnect the live MES
trader. Historical pulls therefore share the live gateway on a DISTINCT
clientId and must be paced gently to avoid IBKR pacing violations that
could disturb the live trading connection.

Guard: unlike the Bybit/yfinance off-VM adapters (which refuse to run ON
the live VM), this one MUST run on the live VM, so it does the opposite —
it refuses unless `ICT_IB_HISTORICAL_OK=1` is set, a deliberate opt-in so
a stray import never opens an IB socket.

Pacing (IBKR limits ~60 historical requests / 10 min / contract): chunk
`endDateTime` backwards by a per-barSize window and sleep `pause_s`
(default 12 s) between requests — "slow and steady".

Tests monkeypatch `_historical_bars` so CI never imports ib_insync or
opens a socket.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

IB_HIST_ENV = "ICT_IB_HISTORICAL_OK"
IB_HIST_EXPECTED = "1"

# Canonical timeframe token -> (IB barSizeSetting, per-request chunk in days).
# Chunk sizes stay well under IBKR's per-barSize duration ceiling so each
# reqHistoricalData call is accepted.
_TIMEFRAME_TO_IB: Mapping[str, tuple[str, int]] = {
    "1m":  ("1 min",   1),
    "5m":  ("5 mins",  20),
    "15m": ("15 mins", 30),
    "1h":  ("1 hour",  60),
    "1d":  ("1 day",   365),
}


class IBHistoricalGuardViolation(RuntimeError):
    """Raised when the IB adapter is invoked without the explicit opt-in."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class IBKRHistoricalMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "ibkr_offvm"

    def iter_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 450,
        exchange: str = "CME",
        currency: str = "USD",
        what_to_show: str = "TRADES",
        use_rth: bool = False,
        pause_s: float = 12.0,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        self._enforce_opt_in()
        if timeframe not in _TIMEFRAME_TO_IB:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; known: {sorted(_TIMEFRAME_TO_IB)}"
            )
        bar_size, chunk_days = _TIMEFRAME_TO_IB[timeframe]
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end) if end else datetime.now(timezone.utc)
        if end_dt <= start_dt:
            raise ValueError(f"end {end!r} must be after start {start!r}")

        bars = self._historical_bars(
            symbol=symbol, exchange=exchange, currency=currency,
            bar_size=bar_size, chunk_days=chunk_days,
            start_dt=start_dt, end_dt=end_dt,
            host=host, port=int(port), client_id=int(client_id),
            what_to_show=what_to_show, use_rth=bool(use_rth),
            pause_s=float(pause_s),
        )
        seen: set[str] = set()
        for b in sorted(bars, key=lambda r: r["ts"]):
            ts = b["ts"]
            if ts in seen:  # chunk overlaps can repeat boundary bars
                continue
            seen.add(ts)
            close = b.get("close")
            if close is None or close != close:  # noqa: PLR0124
                continue
            yield {
                "ts": ts,
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(close),
                "volume": float(b.get("volume") or 0.0),
                "source": self.source,
            }

    @classmethod
    def _historical_bars(
        cls,
        *,
        symbol: str,
        exchange: str,
        currency: str,
        bar_size: str,
        chunk_days: int,
        start_dt: datetime,
        end_dt: datetime,
        host: str,
        port: int,
        client_id: int,
        what_to_show: str,
        use_rth: bool,
        pause_s: float,
    ) -> list[dict[str, Any]]:
        """Connect to the live IB gateway and pull chunked history.

        Tests monkeypatch this. Lazy-imports ib_insync so the guard fires
        (and tests run) without the dependency or a live socket.
        """
        import time
        from datetime import timedelta

        try:
            from ib_insync import IB, ContFuture  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "ib_insync is required for IBKRHistoricalMarketRawAdapter; "
                "install with `pip install ib_insync` on the live VM."
            ) from e

        ib = IB()
        ib.connect(host, port, clientId=client_id, timeout=30)
        out: list[dict[str, Any]] = []
        try:
            try:
                ib.reqMarketDataType(3)  # delayed-OK; harmless for historical
            except Exception:  # noqa: BLE001
                pass
            contract = ContFuture(symbol, exchange, currency=currency)
            ib.qualifyContracts(contract)
            cursor_end = end_dt
            while cursor_end > start_dt:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime=cursor_end,
                    durationStr=f"{chunk_days} D",
                    barSizeSetting=bar_size,
                    whatToShow=what_to_show,
                    useRTH=use_rth,
                    formatDate=2,  # epoch seconds, UTC
                )
                if not bars:
                    break
                for b in bars:
                    ts = b.date
                    if isinstance(ts, (int, float)):
                        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    else:
                        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                    out.append({
                        "ts": _iso(dt),
                        "open": b.open, "high": b.high, "low": b.low,
                        "close": b.close, "volume": b.volume,
                    })
                earliest = min(b.date for b in bars)
                earliest_dt = (
                    datetime.fromtimestamp(int(earliest), tz=timezone.utc)
                    if isinstance(earliest, (int, float))
                    else (earliest if earliest.tzinfo else earliest.replace(tzinfo=timezone.utc))
                )
                # Step the window back; stop if the gateway stops giving us
                # earlier data (earliest no longer advancing).
                next_end = earliest_dt - timedelta(seconds=1)
                if next_end >= cursor_end:
                    break
                cursor_end = next_end
                time.sleep(pause_s)  # pace to protect the live trading session
        finally:
            ib.disconnect()
        return out

    @staticmethod
    def _enforce_opt_in() -> None:
        if os.environ.get(IB_HIST_ENV, "") != IB_HIST_EXPECTED:
            raise IBHistoricalGuardViolation(
                f"IBKRHistoricalMarketRawAdapter requires {IB_HIST_ENV}={IB_HIST_EXPECTED}. "
                "This adapter shares the live IB gateway; set the flag only when a "
                "paced historical pull is intended (distinct clientId, slow pacing)."
            )


def _parse_dt(raw: str) -> datetime:
    text = raw.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
