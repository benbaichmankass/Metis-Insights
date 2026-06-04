"""Bybit-V5 funding-rate + open-interest fetcher (S-MLOPT-S11, M14 Phase 2.3).

A side-stream fetcher that produces the funding/OI series `market_features`
joins (as-of, past-only) to compute the S-MLOPT-S11 funding/OI feature columns.
Kept SEPARATE from `market_raw` (which stays canonical OHLCV-only) — same
architectural split S9 / WS5-B used.

**Off-VM only.** Refuses to run unless the operator has set
`ICT_OFFVM_BUILD_HOST=1` (the trainer VM sets it; the Oracle live VM must NOT).
The funding/OI endpoints are public, so anonymous fetch works; credentials are
read from env when present.

Output rows are a merged, ts-sorted union of:
  - funding-rate settlements (Bybit settles every 8h)      → `funding_rate`
  - open-interest snapshots (chosen `oi_interval`, e.g. 1h) → `open_interest`
Each row carries whichever field the source provided at that ts (the other is
``None``); `market_features` carries each column forward independently on its
as-of join, so the two different cadences compose cleanly.

Tests mock `_build_exchange` so CI never touches the network (same hook as
`bybit_offvm.BybitOffvmMarketRawAdapter`).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

OFFVM_ENV = "ICT_OFFVM_BUILD_HOST"
OFFVM_EXPECTED = "1"

# Bybit V5 open-interest history intervals (ccxt timeframe tokens it accepts).
_OI_INTERVALS = {"5m", "15m", "30m", "1h", "4h", "1d"}
_PAGE_LIMIT = 200  # Bybit V5 max per request for funding + OI history.


class OffVmGuardrailViolation(RuntimeError):
    """Raised when the off-VM fetcher tries to run on (or as if on) the live VM."""


def _iso_to_ms(iso_str: str) -> int:
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


def _enforce_offvm() -> None:
    if os.environ.get(OFFVM_ENV, "") != OFFVM_EXPECTED:
        raise OffVmGuardrailViolation(
            f"bybit_funding_oi requires {OFFVM_ENV}={OFFVM_EXPECTED} to run. "
            "It MUST NOT run on the Oracle live VM. Set the env var only on a "
            "build host that is not the live VM."
        )


def _read_testnet_flag() -> bool:
    return os.environ.get("BYBIT_TESTNET", "false").strip().lower() == "true"


def _build_exchange(
    *, api_key: str | None, api_secret: str | None, testnet: bool
) -> Any:
    """Construct a ccxt Bybit client. Tests monkeypatch this hook."""
    try:
        import ccxt  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - exercised only on a real host
        raise RuntimeError(
            "ccxt is required for bybit_funding_oi live fetch; "
            "install with `pip install ccxt` on the build host."
        ) from e
    exchange = ccxt.bybit(
        {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
    )
    if testnet:
        exchange.set_sandbox_mode(True)
    return exchange


def _paginate(fetch_page, *, start_ms: int, end_ms: int, bar_ms: int) -> list[dict]:
    """Generic forward pager over a [start, end) window with retry/backoff.

    ``fetch_page(since_ms)`` returns a list of ccxt records each carrying a
    ``timestamp`` (ms). Advances the cursor past the newest record of each page;
    steps one ``bar_ms`` on an empty/stale page so it never spins.
    """
    out: list[dict] = []
    cursor = start_ms
    last_cursor = -1
    while cursor < end_ms:
        if cursor == last_cursor:
            cursor += bar_ms
            continue
        last_cursor = cursor
        page = None
        for attempt in range(7):
            try:
                page = fetch_page(cursor)
                break
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                if any(
                    k in name
                    for k in ("RateLimit", "DDoS", "Network", "Timeout", "ExchangeNotAvailable")
                ):
                    import time

                    time.sleep(min(2**attempt, 60))
                    continue
                raise
        if not page:
            break
        advanced = False
        for rec in page:
            ts = rec.get("timestamp")
            if ts is None:
                continue
            ts = int(ts)
            if ts < cursor:
                continue
            if ts >= end_ms:
                return out
            out.append(rec)
            advanced = True
            cursor = ts + bar_ms
        if not advanced:
            cursor += bar_ms
    return out


def fetch_funding_oi_rows(
    *,
    symbol: str,
    start: str,
    end: str,
    oi_interval: str = "1h",
    api_key: str | None = None,
    api_secret: str | None = None,
    testnet: bool | None = None,
    exchange: Any | None = None,
) -> list[Mapping[str, Any]]:
    """Merged, ts-sorted funding + open-interest rows over ``[start, end)``.

    Each output row is ``{ts, symbol, funding_rate, open_interest}`` with one of
    the two value fields populated (the other ``None``). ``exchange`` is injected
    by tests; production builds it from env via ``_build_exchange``.
    """
    _enforce_offvm()
    if oi_interval not in _OI_INTERVALS:
        raise ValueError(
            f"unsupported oi_interval {oi_interval!r}; known: {sorted(_OI_INTERVALS)}"
        )
    start_ms, end_ms = _iso_to_ms(start), _iso_to_ms(end)
    if end_ms <= start_ms:
        raise ValueError(f"end {end!r} must be after start {start!r}")

    ex = exchange or _build_exchange(
        api_key=api_key if api_key is not None else os.environ.get("BYBIT_API_KEY"),
        api_secret=(
            api_secret if api_secret is not None
            else os.environ.get("BYBIT_API_SECRET")
        ),
        testnet=testnet if testnet is not None else _read_testnet_flag(),
    )

    # Funding settles every 8h on Bybit perps.
    funding = _paginate(
        lambda since: ex.fetch_funding_rate_history(
            symbol, since=since, limit=_PAGE_LIMIT
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        bar_ms=8 * 60 * 60_000,
    )
    _OI_MS = {
        "5m": 5 * 60_000, "15m": 15 * 60_000, "30m": 30 * 60_000,
        "1h": 60 * 60_000, "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000,
    }
    oi = _paginate(
        lambda since: ex.fetch_open_interest_history(
            symbol, oi_interval, since=since, limit=_PAGE_LIMIT
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        bar_ms=_OI_MS[oi_interval],
    )

    rows: list[dict[str, Any]] = []
    for rec in funding:
        fr = rec.get("fundingRate")
        rows.append(
            {
                "ts": _ms_to_iso(int(rec["timestamp"])),
                "symbol": symbol,
                "funding_rate": float(fr) if fr is not None else None,
                "open_interest": None,
            }
        )
    for rec in oi:
        # ccxt normalises Bybit OI to openInterestAmount (contracts) +
        # openInterestValue (USD); prefer the amount, fall back to value.
        oiv = rec.get("openInterestAmount")
        if oiv is None:
            oiv = rec.get("openInterestValue")
        if oiv is None:
            info = rec.get("info") or {}
            oiv = info.get("openInterest")
        rows.append(
            {
                "ts": _ms_to_iso(int(rec["timestamp"])),
                "symbol": symbol,
                "funding_rate": None,
                "open_interest": float(oiv) if oiv is not None else None,
            }
        )
    rows.sort(key=lambda r: r["ts"])
    return rows
