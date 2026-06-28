"""Alpaca options market-data + contract-discovery client (READ-ONLY).

Phase-1 of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

Hand-rolled REST in the same style as ``AlpacaClient`` (raw ``requests``, key-pair
auth, retCode-style envelopes) — deliberately NOT the ``alpaca-py`` SDK, which is not
a dependency of this repo; adding a heavy SDK to the live trader for what is a handful
of documented GET endpoints isn't warranted.

This module is **read-only** (contract discovery + snapshots) and **not wired into any
order path** — it places nothing. It exists to (a) enumerate the option chain so a
strategy can pick strikes/expiries, (b) read per-contract quote + greeks + implied
volatility, and (c) let the Phase-0 probe answer empirically whether the operator's
**free ("indicative") data tier** even returns greeks/IV (the research memo flagged this
as undocumented).

Endpoints (all GET):
  - Contract discovery: ``GET {trading_host}/v2/options/contracts`` (Trading API).
  - Snapshots (quote + greeks + IV): ``GET {data_host}/v1beta1/options/snapshots/{underlying}``.

DATA-FEED HONESTY
-----------------
The free/Basic plan serves ``feed=indicative`` — a **15-min-delayed** derivative of
OPRA that Alpaca documents as "to debug one's code and not generally to be used for live
trading." Real-time OPRA (``feed=opra``) needs the $99/mo Algo Trader Plus plan. The
default here is ``indicative`` (matches the operator's current subscription); Phase-3
real-money trading should flip to ``opra`` once the plan is upgraded. Whether the
indicative feed populates ``greeks``/``implied_volatility`` at all is verified by the
probe, not assumed.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_TRADING_HOSTS = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}
_DATA_HOST = "https://data.alpaca.markets"

# Free/Basic plan = delayed indicative; OPRA real-time needs the paid plan.
_DEFAULT_FEED = "indicative"


class MissingCredentialsError(RuntimeError):
    """Raised when an action needs the Alpaca key pair. Names env vars, never values."""


class AlpacaOptionsData:
    """Read-only options chain + snapshot client (key-pair auth)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env: Optional[str] = None,
        feed: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        env_name = (env or os.environ.get("ALPACA_ENV", "paper")).strip().lower()
        self.env = env_name if env_name in _TRADING_HOSTS else "paper"
        self.trading_host = _TRADING_HOSTS[self.env]
        self.feed = (feed or os.environ.get("ALPACA_OPTIONS_FEED", _DEFAULT_FEED)).strip().lower()
        self.timeout = timeout

    # ------------------------------------------------------------ utils
    def _require_creds(self, action: str) -> None:
        if not self.api_key or not self.api_secret:
            raise MissingCredentialsError(
                f"alpaca options {action}: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY unset."
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    def _get(self, url: str, params: Optional[dict] = None) -> dict:
        """One GET → retCode envelope (never raises on HTTP)."""
        try:
            resp = requests.get(
                url, headers=self._headers(), params=params, timeout=self.timeout
            )
        except Exception as exc:  # noqa: BLE001
            return {"retCode": -1, "retMsg": f"network error: {exc}"}
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {}
        if 200 <= resp.status_code < 300:
            return {"retCode": 0, "result": payload}
        msg = payload.get("message") or f"HTTP {resp.status_code}"
        return {"retCode": resp.status_code, "retMsg": str(msg)}

    # --------------------------------------------------- contract discovery
    def list_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: Optional[str] = None,
        expiration_date_lte: Optional[str] = None,
        contract_type: Optional[str] = None,   # "call" | "put"
        strike_price_gte: Optional[float] = None,
        strike_price_lte: Optional[float] = None,
        status: str = "active",
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Enumerate tradable option contracts for *underlying*.

        Wraps ``GET /v2/options/contracts`` with the common filters a strike/expiry
        selector needs. Date filters are ISO ``YYYY-MM-DD``. Returns the retCode
        envelope; on success ``result["option_contracts"]`` is the contract list
        (each carries ``symbol`` (OCC), ``strike_price``, ``expiration_date``, ``type``,
        ``open_interest`` when present, ...).
        """
        self._require_creds("contracts")
        params: Dict[str, Any] = {
            "underlying_symbols": str(underlying).upper(),
            "status": status,
            "limit": int(limit),
        }
        if expiration_date_gte:
            params["expiration_date_gte"] = expiration_date_gte
        if expiration_date_lte:
            params["expiration_date_lte"] = expiration_date_lte
        if contract_type:
            params["type"] = str(contract_type).lower()
        if strike_price_gte is not None:
            params["strike_price_gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price_lte"] = strike_price_lte
        return self._get(f"{self.trading_host}/v2/options/contracts", params)

    # ---------------------------------------------------------- snapshots
    def snapshots(
        self, underlying: str, *, limit: int = 100, feed: Optional[str] = None
    ) -> Dict[str, Any]:
        """Per-contract snapshot (latest quote + greeks + IV) for *underlying*'s chain.

        Wraps ``GET /v1beta1/options/snapshots/{underlying}``. ``result["snapshots"]``
        maps each OCC contract symbol → ``{latestQuote, latestTrade, greeks, impliedVolatility}``.
        ``greeks`` / ``impliedVolatility`` are **nullable** (illiquid contract, or — to
        be verified by the probe — the free indicative feed may omit them); callers must
        treat absent as "not provided" (em-dash), never 0.
        """
        self._require_creds("snapshots")
        params = {"feed": (feed or self.feed), "limit": int(limit)}
        return self._get(
            f"{_DATA_HOST}/v1beta1/options/snapshots/{str(underlying).upper()}", params
        )

    @staticmethod
    def greeks_present(snapshot_payload: Dict[str, Any]) -> Dict[str, int]:
        """Summarise how many contracts in a snapshot payload carry greeks / IV.

        Pure helper for the Phase-0 probe + tests — answers "does this feed return
        greeks/IV?" without a live call. Returns counts ``{total, with_greeks, with_iv}``.
        """
        snaps = (snapshot_payload or {}).get("snapshots") or {}
        total = len(snaps)
        with_greeks = sum(1 for s in snaps.values() if (s or {}).get("greeks"))
        with_iv = sum(
            1 for s in snaps.values() if (s or {}).get("impliedVolatility") is not None
        )
        return {"total": total, "with_greeks": with_greeks, "with_iv": with_iv}

    @staticmethod
    def quote_mid(snapshot_entry: Dict[str, Any]) -> Optional[float]:
        """Mid price of a single contract's latest quote, or None when unquotable."""
        q = (snapshot_entry or {}).get("latestQuote") or {}
        bid, ask = q.get("bp"), q.get("ap")
        try:
            bid_f, ask_f = float(bid), float(ask)
        except (TypeError, ValueError):
            return None
        if bid_f <= 0 and ask_f <= 0:
            return None
        if bid_f <= 0 or ask_f <= 0:
            return max(bid_f, ask_f) or None
        return round((bid_f + ask_f) / 2.0, 4)
