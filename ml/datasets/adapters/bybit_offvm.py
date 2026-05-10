"""Bybit-V5 off-VM adapter for `market_raw` (S-AI-WS5-B-PART-1 + PART-2).

**Off-VM only.** Refuses to run unless the operator has set
`ICT_OFFVM_BUILD_HOST=1`. The Oracle live VM must NEVER set this
env var; market_raw builds are meant to run on a separate build
host (developer laptop, HF Space, GitHub Actions runner with
operator-supplied creds).

WS9 rule: heavy market-data pulls do not belong on the live trading
VM. This adapter exists so the regime classifier (WS5-B-PART-2) can
build historical bars from Bybit V5 klines on a build host.

Sprint history:
- S-AI-WS5-B-PART-1 — class + env-gate + `NotImplementedError` on
  the actual fetch path.
- S-AI-WS5-B-PART-2 (PR 2A) — `_fetch_bars(...)` wired via ccxt's
  Bybit V5 connector. Time-range pagination (`since`, `limit`) over
  `[start, end]`; canonical row normalisation; credentials sourced
  from env (`BYBIT_API_KEY` / `BYBIT_API_SECRET`); klines endpoint
  is public so anonymous fallback is supported. Tests mock the
  exchange object via `_build_exchange` so CI never touches the
  network.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, ClassVar, Iterator, Mapping

from .base import MarketRawAdapter

OFFVM_ENV = "ICT_OFFVM_BUILD_HOST"
OFFVM_EXPECTED = "1"

# ccxt timeframe tokens we accept. Bybit-via-ccxt accepts these
# tokens directly; mapping to bar-length-in-ms is also used to
# advance the pagination cursor when a page comes back empty so we
# don't loop forever on a stale `since`.
_TIMEFRAME_MS: Mapping[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

# ccxt's bybit fetch_ohlcv default page size; 1000 is the documented
# Bybit V5 max per request.
_PAGE_LIMIT = 1000


class OffVmGuardrailViolation(RuntimeError):
    """Raised when the off-VM adapter tries to run on (or as if on) the live VM."""


def _iso_to_ms(iso_str: str) -> int:
    """Parse ISO 8601 (with optional `Z`) to ms epoch UTC.

    Accepts both `2025-01-01` (date only, treated as 00:00 UTC) and
    `2025-01-01T00:00:00Z` / `2025-01-01T00:00:00+00:00`. Naive
    datetimes are treated as UTC. Raises ValueError on parse failure.
    """
    text = iso_str.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


class BybitOffvmMarketRawAdapter(MarketRawAdapter):
    source: ClassVar[str] = "bybit_v5_offvm"

    def iter_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        testnet: bool | None = None,
        **_: Any,
    ) -> Iterator[Mapping[str, Any]]:
        self._enforce_offvm()

        if timeframe not in _TIMEFRAME_MS:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; "
                f"known: {sorted(_TIMEFRAME_MS)}"
            )
        bar_ms = _TIMEFRAME_MS[timeframe]
        start_ms = _iso_to_ms(start)
        end_ms = _iso_to_ms(end)
        if end_ms <= start_ms:
            raise ValueError(
                f"end {end!r} must be after start {start!r}"
            )

        exchange = self._build_exchange(
            api_key=api_key if api_key is not None else os.environ.get("BYBIT_API_KEY"),
            api_secret=(
                api_secret if api_secret is not None
                else os.environ.get("BYBIT_API_SECRET")
            ),
            testnet=testnet if testnet is not None else _read_testnet_flag(),
        )

        cursor = start_ms
        last_cursor = -1
        while cursor < end_ms:
            if cursor == last_cursor:
                # Defensive: stale `since` (exchange returned the same
                # boundary twice). Advance one bar so we don't spin.
                cursor += bar_ms
                continue
            last_cursor = cursor
            page = exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=cursor,
                limit=_PAGE_LIMIT,
            )
            if not page:
                break
            advanced = False
            for candle in page:
                # ccxt OHLCV row is [ts_ms, open, high, low, close, volume].
                ts_ms = int(candle[0])
                if ts_ms < cursor:
                    # Defensive: drop bars before our cursor (the API
                    # occasionally returns a pre-window prefix).
                    continue
                if ts_ms >= end_ms:
                    return
                yield {
                    "ts": _ms_to_iso(ts_ms),
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]) if candle[5] is not None else 0.0,
                    "source": self.source,
                }
                advanced = True
                cursor = ts_ms + bar_ms
            if not advanced:
                # Page came back but every bar was filtered out.
                # Step one bar to avoid an infinite loop.
                cursor += bar_ms

    @classmethod
    def _build_exchange(
        cls,
        *,
        api_key: str | None,
        api_secret: str | None,
        testnet: bool,
    ) -> Any:
        """Construct a ccxt Bybit client. Tests monkeypatch this hook.

        Lazy-imports ccxt so a build host without ccxt installed can
        still hit the env-gate guardrail without an ImportError, and
        so tests that monkeypatch this method don't need ccxt either.
        """
        try:
            import ccxt  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "ccxt is required for BybitOffvmMarketRawAdapter live fetch; "
                "install with `pip install ccxt` on the build host."
            ) from e
        exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        if testnet:
            exchange.set_sandbox_mode(True)
        return exchange

    @staticmethod
    def _enforce_offvm() -> None:
        if os.environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
            raise OffVmGuardrailViolation(
                f"BybitOffvmMarketRawAdapter requires {OFFVM_ENV}={OFFVM_EXPECTED} "
                "to run. This adapter MUST NOT run on the Oracle live VM. "
                "Set the env var only on a build host that is not the live VM."
            )


def _read_testnet_flag() -> bool:
    return os.environ.get("BYBIT_TESTNET", "false").strip().lower() == "true"
