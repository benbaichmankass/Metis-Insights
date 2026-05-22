"""Yahoo Finance off-VM adapter for `market_raw` (MES / index-futures intake).

**Off-VM only.** Reuses the same `ICT_OFFVM_BUILD_HOST=1` guard as the
Bybit adapter — heavy market-data pulls do not belong on the live
trading VM (WS9 rule). Runs on the trainer VM / a build host.

Why this exists: the bot now trades **MES** (Micro E-mini S&P 500,
IBKR) alongside BTCUSDT, but the ML pipeline previously had no
historical-market-data source for MES (the only network adapter,
`bybit_v5_offvm`, is crypto-only). This adapter pulls OHLCV bars for
MES — and any other yfinance-addressable instrument — so the regime
classifier and other market-data baselines can train on MES history.

Because Micro (`MES`) and full-size (`ES`) E-mini S&P futures track the
identical underlying index level, the default ticker for `symbol=MES`
is the full-size continuous front-month `ES=F`, which carries far more
yfinance history than the micro contract. Override with `ticker=...`
(e.g. `MES=F`, `^GSPC`, `SPY`) when a different series is wanted.

Implements the multi-source intake directive (2026-05-10): "we should
have a running list of various sources to choose from." Tests monkeypatch
`_download` so CI never touches the network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter
# Reuse the exact same off-VM guard contract as the Bybit adapter so a
# build host only has to set one env var for every network adapter.
from .bybit_offvm import (
    OFFVM_ENV,
    OFFVM_EXPECTED,
    OffVmGuardrailViolation,
)

# Canonical timeframe token -> yfinance `interval` token. yfinance caps
# intraday history (≈730 d for 60m, ≈7 d for 1m); `1d` reaches back
# decades, which is what a "pull as much history as possible" MES build
# wants. Daily is therefore the default the orchestrator uses.
_TIMEFRAME_TO_YF: Mapping[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "60m",
    "1d": "1d",
}

# Default yfinance ticker per bot symbol. `ES=F` (continuous front-month
# E-mini S&P) shares MES's price level and has the deepest yfinance
# history. Callers can override per build via the `ticker=` kwarg.
_DEFAULT_TICKER_MAP: Mapping[str, str] = {
    "MES": "ES=F",
    "MESUSD": "ES=F",
    "ES": "ES=F",
}


def _to_iso_utc(value: Any) -> str:
    """Normalise a pandas Timestamp / datetime to ISO-8601 UTC with `Z`."""
    ts = value
    # pandas Timestamp has tz_localize/tz_convert; fall back for plain datetime.
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if not isinstance(ts, datetime):
        ts = datetime.fromisoformat(str(value))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class YFinanceOffvmMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "yfinance_offvm"

    def iter_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str | None = None,
        ticker: str | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        self._enforce_offvm()

        if timeframe not in _TIMEFRAME_TO_YF:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; "
                f"known: {sorted(_TIMEFRAME_TO_YF)}"
            )
        yf_interval = _TIMEFRAME_TO_YF[timeframe]
        yf_ticker = ticker or _DEFAULT_TICKER_MAP.get(symbol, symbol)

        frame = self._download(
            ticker=yf_ticker,
            interval=yf_interval,
            start=start,
            end=end,
        )
        if frame is None or len(frame) == 0:
            return

        # yfinance returns a single-level OHLCV frame, but when a single
        # ticker is requested it sometimes hands back a MultiIndex with
        # the ticker as the outer column level. Flatten to the field name.
        columns = frame.columns
        if hasattr(columns, "nlevels") and columns.nlevels > 1:
            frame = frame.copy()
            frame.columns = columns.get_level_values(0)

        col = {c.lower(): c for c in frame.columns}
        for required in ("open", "high", "low", "close"):
            if required not in col:
                raise ValueError(
                    f"yfinance frame for {yf_ticker!r} missing {required!r} "
                    f"column; got {list(frame.columns)}"
                )
        vol_col = col.get("volume")

        for index_value, row in frame.iterrows():
            close = row[col["close"]]
            # Skip holiday / pre-listing gaps that yfinance pads with NaN.
            if close is None or close != close:  # noqa: PLR0124 (NaN check)
                continue
            yield {
                "ts": _to_iso_utc(index_value),
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(row[col["open"]]),
                "high": float(row[col["high"]]),
                "low": float(row[col["low"]]),
                "close": float(close),
                "volume": (
                    float(row[vol_col])
                    if vol_col is not None and row[vol_col] == row[vol_col]
                    else 0.0
                ),
                "source": self.source,
            }

    @classmethod
    def _download(
        cls,
        *,
        ticker: str,
        interval: str,
        start: str,
        end: str | None,
    ) -> Any:
        """Fetch an OHLCV DataFrame from yfinance. Tests monkeypatch this.

        Lazy-imports yfinance so a build host without it installed still
        hits the env-gate guardrail (and the unit tests, which patch this
        hook) without needing the dependency.
        """
        try:
            import yfinance as yf  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "yfinance is required for YFinanceOffvmMarketRawAdapter; "
                "install with `pip install yfinance` on the build host."
            ) from e
        return yf.download(
            tickers=ticker,
            interval=interval,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=False,
        )

    @staticmethod
    def _enforce_offvm() -> None:
        if __import__("os").environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
            raise OffVmGuardrailViolation(
                f"YFinanceOffvmMarketRawAdapter requires {OFFVM_ENV}={OFFVM_EXPECTED} "
                "to run. This adapter MUST NOT run on the Oracle live VM. "
                "Set the env var only on a build host that is not the live VM."
            )
