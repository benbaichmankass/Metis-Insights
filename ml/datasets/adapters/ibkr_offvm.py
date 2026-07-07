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
from datetime import date, datetime, timezone
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

IB_HIST_ENV = "ICT_IB_HISTORICAL_OK"
IB_HIST_EXPECTED = "1"

# Canonical timeframe token -> (IB barSizeSetting, per-request chunk in days).
# Chunk sizes stay well under IBKR's per-barSize duration ceiling so each
# reqHistoricalData call is accepted.
_TIMEFRAME_TO_IB: Mapping[str, tuple[str, int]] = {
    "1m":  ("1 min",   7),
    "5m":  ("5 mins",  30),
    "15m": ("15 mins", 60),
    "1h":  ("1 hour",  120),
    "1d":  ("1 day",   365),
}


# Canonical IB futures-root -> listing exchange. MIRRORS the live order path's
# contract builder (src/units/accounts/ib_client.py::_build_contract
# `ib_exchanges`): MES is CME, the metals micros MGC/MHG are COMEX. A symbol NOT
# in this map falls back to the caller-supplied `exchange` (default CME), so any
# other IB-addressable instrument keeps working. Without this, an MGC/MHG pull
# requested Future(exchange='CME') and IBKR returned Error 200 "No security
# definition has been found" — the reason the metals sleeve could never be
# backfilled on its native instrument (BL-20260707-IBKR-METALS-CME-EXCHANGE).
_SYMBOL_EXCHANGE: Mapping[str, str] = {
    "MES": "CME",
    "MGC": "COMEX",
    "MHG": "COMEX",
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
        max_contracts: int = 4,
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

        # Resolve the listing exchange PER SYMBOL (MES=CME, MGC/MHG=COMEX) so a
        # metals pull isn't sent to CME (IBKR Error 200). An unknown symbol keeps
        # the caller-supplied `exchange` for back-compat.
        exchange = _SYMBOL_EXCHANGE.get(symbol.upper(), exchange)

        bars = self._historical_bars(
            symbol=symbol, exchange=exchange, currency=currency,
            bar_size=bar_size, chunk_days=chunk_days,
            start_dt=start_dt, end_dt=end_dt,
            host=host, port=int(port), client_id=int(client_id),
            what_to_show=what_to_show, use_rth=bool(use_rth),
            pause_s=float(pause_s), max_contracts=int(max_contracts),
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

    def iter_contract_bars(
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
        max_contracts: int = 4,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        """Yield PER-CONTRACT bars tagged with their `contract` month.

        The roll-adjustment-ready sibling of `iter_bars`. `iter_bars` dedups
        ACROSS contracts (one merged stream, contract identity lost) — which is
        wrong for building a back-adjusted continuous series, where the
        cross-contract OVERLAP is exactly what measures the roll offset. This
        method keeps every contract's own bars (deduped only WITHIN a contract)
        and tags each row with `contract` (the dated `lastTradeDateOrContractMonth`,
        `YYYYMMDD`). The extra `contract` key means these rows are NOT canonical
        `market_raw` — they feed `ml/datasets/continuous.py::build_continuous`
        (via `group_bars_by_contract`), not the `market_raw` builder.

        Same guard, paging, pacing, and per-symbol exchange resolution
        (`_SYMBOL_EXCHANGE`) as `iter_bars` — the ONLY difference is the
        no-cross-contract-dedup collection + the `contract` tag.
        """
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
        exchange = _SYMBOL_EXCHANGE.get(symbol.upper(), exchange)

        bars = self._historical_bars(
            symbol=symbol, exchange=exchange, currency=currency,
            bar_size=bar_size, chunk_days=chunk_days,
            start_dt=start_dt, end_dt=end_dt,
            host=host, port=int(port), client_id=int(client_id),
            what_to_show=what_to_show, use_rth=bool(use_rth),
            pause_s=float(pause_s), max_contracts=int(max_contracts),
            per_contract=True,
        )
        for b in sorted(bars, key=lambda r: (r.get("contract", ""), r["ts"])):
            close = b.get("close")
            if close is None or close != close:  # noqa: PLR0124
                continue
            yield {
                "ts": b["ts"],
                "contract": b.get("contract", ""),
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
        max_contracts: int = 4,
        per_contract: bool = False,
    ) -> list[dict[str, Any]]:
        """Connect to the live IB gateway and pull chunked history.

        Tests monkeypatch this. Lazy-imports ib_insync so the guard fires
        (and tests run) without the dependency or a live socket.
        """
        import time
        from datetime import timedelta

        try:
            from ib_insync import IB, Future  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "ib_insync is required for IBKRHistoricalMarketRawAdapter; "
                "install with `pip install ib_insync` on the live VM."
            ) from e

        def _to_dt(v: Any) -> datetime:
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(int(v), tz=timezone.utc)
            # IB DAILY bars (formatDate=2) hand back a datetime.date, not a
            # datetime — and date.replace() rejects a tzinfo kwarg. Promote a
            # bare date to midnight UTC. (Intraday bars are datetime/epoch, so
            # this branch only fires for the 1d timeframe — which is why it was
            # never hit until the native-MES daily pull.)
            if isinstance(v, date) and not isinstance(v, datetime):
                return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
            return v if getattr(v, "tzinfo", None) else v.replace(tzinfo=timezone.utc)

        ib = IB()
        ib.connect(host, port, clientId=client_id, timeout=30)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            try:
                ib.reqMarketDataType(3)  # delayed-OK; harmless for historical
            except Exception:  # noqa: BLE001
                pass
            # IBKR forbids endDateTime on a ContFuture (Error 10339), so page
            # over DATED contracts and stitch the newest expiries for depth.
            details = ib.reqContractDetails(
                Future(symbol, exchange=exchange, currency=currency, includeExpired=True)
            )
            contracts = [d.contract for d in details]
            contracts.sort(key=lambda c: (c.lastTradeDateOrContractMonth or ""), reverse=True)
            # Keep contracts that actually carry data for [start, now]: an
            # active future expires in the FUTURE (front month expires next
            # month) and each contract has data for ~the quarter before its
            # expiry. So keep expiry in [start_month, now + ~4 months] —
            # NOT "<= now", which would drop the live front month.
            start6 = start_dt.strftime("%Y%m")
            end6 = (end_dt + timedelta(days=120)).strftime("%Y%m")
            contracts = [
                c for c in contracts
                if start6 <= (c.lastTradeDateOrContractMonth or "")[:6] <= end6
            ] or contracts

            MAX_TOTAL_CHUNKS = 800
            total_chunks = 0
            reached_start = False
            for c in contracts[:max_contracts]:
                if reached_start or total_chunks >= MAX_TOTAL_CHUNKS:
                    break
                exp = (c.lastTradeDateOrContractMonth or "")[:8]
                # per_contract keeps each contract's OWN bars (dedup only within
                # a contract, so cross-contract overlaps survive for roll-offset
                # measurement); the default path dedups globally as before.
                seen_contract: set[str] = set()
                active_seen = seen_contract if per_contract else seen
                try:
                    exp_dt = datetime.strptime(exp, "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(days=2)
                except ValueError:
                    exp_dt = end_dt
                cursor_end = min(end_dt, exp_dt)
                last_cursor: datetime | None = None
                while cursor_end > start_dt and total_chunks < MAX_TOTAL_CHUNKS:
                    try:
                        bars = ib.reqHistoricalData(
                            c, endDateTime=cursor_end, durationStr=f"{chunk_days} D",
                            barSizeSetting=bar_size, whatToShow=what_to_show,
                            useRTH=use_rth, formatDate=2,
                        )
                    except Exception:  # noqa: BLE001
                        # A dead/expired contract or a transient pacing timeout
                        # on ONE request must not abort the whole multi-contract
                        # stitch (the daily pull pages ~28 quarterly MES expiries
                        # back to 2019; the oldest can hang). Skip to the next
                        # contract and keep whatever we already collected.
                        break
                    total_chunks += 1
                    if not bars:
                        break
                    for b in bars:
                        dt = _to_dt(b.date)
                        if dt < start_dt or dt > end_dt:
                            continue
                        ts = _iso(dt)
                        if ts in active_seen:
                            continue
                        active_seen.add(ts)
                        row: dict[str, Any] = {
                            "ts": ts, "open": b.open, "high": b.high,
                            "low": b.low, "close": b.close, "volume": b.volume,
                        }
                        if per_contract:
                            row["contract"] = exp
                        out.append(row)
                    earliest_dt = min(_to_dt(b.date) for b in bars)
                    if earliest_dt <= start_dt:
                        reached_start = True
                        break
                    next_end = earliest_dt - timedelta(seconds=1)
                    if last_cursor is not None and next_end >= last_cursor:
                        break  # not advancing — exhausted this contract's retention
                    last_cursor = cursor_end
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
