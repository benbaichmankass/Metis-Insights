"""Alpaca execution client — M15 Phase 2b (S-M15-PHASE2B-ALPACA).

Real REST integration for Alpaca's Trading API (paper host by default),
the migration's second platform per the Phase-0 verdict: the daily ETF
futures-replacements (trend1d QQQ/SPY ≈ ``mes_trend_long_1d``,
pullback1d GLD ≈ ``mgc_pullback_1d``) and the SPY intraday candidates
(docs/research/m15-phase0-results-2026-06-10.md).

Mirrors the OANDA contract: ``place()`` returns a
retCode-style envelope, missing creds raise
:class:`MissingCredentialsError` naming the env vars (never values),
and the factory (`clients.alpaca_client_for`) returns ``None`` when
creds are absent so the account loads ``configured: False``.

Orders are **bracket** market orders (entry + ``take_profit`` limit +
``stop_loss`` stop in one atomic request) so SL/TP protection is
broker-side from the first fill — surviving RTH closes, weekends, and
trader restarts. Bracket orders require whole-share quantities and
``time_in_force: day`` legs (Alpaca constraint); qty is floored at 1.

Auth: key id + secret from ``ALPACA_API_KEY_ID`` /
``ALPACA_API_SECRET_KEY`` (free paper keys). ``ALPACA_ENV`` picks the
host: ``paper`` (default) → paper-api.alpaca.markets, ``live`` →
api.alpaca.markets (an explicit flip, like ``OANDA_ENV``).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_HOSTS = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}


class MissingCredentialsError(RuntimeError):
    """Raised when an action requires the Alpaca key pair.

    Message carries env-var *names* only, never values (no-secrets rule).
    """


class AlpacaClient:
    """Thin Trading-API REST client (key-pair auth, paper host default)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        env_name = (env or os.environ.get("ALPACA_ENV", "paper")).strip().lower()
        self.env = env_name if env_name in _HOSTS else "paper"
        self.base_url = (base_url or _HOSTS[self.env]).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------ utils
    def _require_creds(self, action: str) -> None:
        if not self.api_key or not self.api_secret:
            raise MissingCredentialsError(
                f"alpaca {action}: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY unset."
            )

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        """One HTTP round-trip → retCode envelope (never raises on HTTP)."""
        url = f"{self.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=self.timeout
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

    # ------------------------------------------------------------ orders
    def place(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Place a bracket MARKET order; retCode envelope.

        Expects the executor's order dict: ``symbol``, ``side``
        (``Buy``/``Sell``, case-insensitive), ``qty`` (shares, floored
        at 1 whole share — bracket orders disallow fractionals),
        optional ``sl`` / ``tp`` prices (both present → bracket; one →
        OTO; none → plain market). Equity prices are 2dp.
        """
        self._require_creds("place")
        side = str(order.get("side", "")).strip().lower()
        if side not in ("buy", "sell"):
            return {"retCode": -2, "retMsg": f"invalid side {order.get('side')!r}"}
        qty = max(1, int(round(float(order["qty"]))))
        body: Dict[str, Any] = {
            "symbol": str(order["symbol"]).upper(),
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": "day",
        }
        tp, sl = order.get("tp"), order.get("sl")
        if tp is not None and sl is not None:
            body["order_class"] = "bracket"
            body["take_profit"] = {"limit_price": f"{float(tp):.2f}"}
            body["stop_loss"] = {"stop_price": f"{float(sl):.2f}"}
        elif tp is not None or sl is not None:
            body["order_class"] = "oto"
            if tp is not None:
                body["take_profit"] = {"limit_price": f"{float(tp):.2f}"}
            if sl is not None:
                body["stop_loss"] = {"stop_price": f"{float(sl):.2f}"}
        env = self._request("POST", "/v2/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        return {"retCode": 0, "result": {"orderId": str(result.get("id") or "")}}

    # ------------------------------------------------------------ account
    def balance(self) -> Optional[float]:
        """Account equity in USD, or ``None`` on any failure."""
        try:
            self._require_creds("balance")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/account")
        if env.get("retCode") != 0:
            logger.warning("alpaca balance: %s", env.get("retMsg"))
            return None
        acct = env.get("result") or {}
        try:
            return float(acct.get("equity") or acct.get("cash"))
        except (TypeError, ValueError):
            return None

    def positions(self) -> Optional[list]:
        """Open positions as ``[{symbol, side, qty, avg_price, unrealized_pnl}]``.

        Returns ``None`` on a READ FAILURE (missing creds, network error,
        non-2xx) so callers can distinguish "could not read" from "genuinely
        flat" (``[]``) — mirroring the IB read path. Collapsing a failed read
        into ``[]`` is what let a transient Alpaca outage read as "flat" and
        the position-snapshot reconciler false-close a live position
        (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE).
        """
        try:
            self._require_creds("positions")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/positions")
        if env.get("retCode") != 0:
            logger.warning("alpaca positions: %s", env.get("retMsg"))
            return None
        out = []
        for pos in env.get("result") or []:
            try:
                qty = abs(float(pos.get("qty") or 0))
            except (TypeError, ValueError):
                continue
            if qty == 0:
                continue
            out.append(
                {
                    "symbol": pos.get("symbol"),
                    "side": "buy" if str(pos.get("side", "")).lower() == "long" else "sell",
                    "qty": qty,
                    "avg_price": float(pos.get("avg_entry_price") or 0) or None,
                    "unrealized_pnl": float(pos.get("unrealized_pl") or 0),
                }
            )
        return out

    def close(self, symbol: str) -> Dict[str, Any]:
        """Close the full position on *symbol*; retCode envelope.

        A 404 (no open position) maps to retCode 0 — idempotent close,
        matching the reconciler's expectations.
        """
        self._require_creds("close")
        env = self._request("DELETE", f"/v2/positions/{str(symbol).upper()}")
        if env.get("retCode") == 404:
            return {"retCode": 0, "result": {"note": "no open position"}}
        return env

    def _open_orders_for_symbol(self, symbol: str) -> Optional[list]:
        """Open (working) orders on *symbol*, including bracket child legs.

        Returns the flattened order list (parents + nested ``legs``) filtered
        to *symbol*, or ``None`` on a read failure (so the caller can refuse
        to act rather than assume "no legs"). ``nested=true`` so an
        un-triggered bracket's children come back attached to the parent;
        once the entry fills the legs surface as top-level open orders too.
        """
        sym = str(symbol).upper()
        env = self._request(
            "GET", f"/v2/orders?status=open&nested=true&symbols={sym}"
        )
        if env.get("retCode") != 0:
            return None
        out: list = []
        for o in env.get("result") or []:
            out.append(o)
            for leg in o.get("legs") or []:
                out.append(leg)
        return [o for o in out if str(o.get("symbol") or "").upper() == sym]

    def modify_protective(
        self,
        symbol: str,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Replace the resting SL/TP legs of the open bracket on *symbol*.

        S2 of the live-trade management contract (BL-20260616-LTMGMT-MODIFY).
        Alpaca bracket legs are independent working orders, so a SL/TP modify
        is a leg **replace** (``PATCH /v2/orders/{id}``): the stop leg's
        ``stop_price`` (when *sl* is given) and the limit leg's ``limit_price``
        (when *tp* is given). Only the leg(s) the verdict changed are touched,
        so the other leg's protection is never dropped — no naked re-arm
        window, unlike the IB cancel-and-replace path.

        retCode envelope: ``{"retCode": 0, "result": {"orderId": <last>,
        "patched": [...]}}`` when at least one leg was patched;
        ``{"retCode": <non-zero>, "retMsg": ...}`` on a read failure, a PATCH
        failure, or no matching protective leg found.
        """
        self._require_creds("modify")
        if sl is None and tp is None:
            return {"retCode": -2, "retMsg": "no sl/tp provided — nothing to modify"}
        legs = self._open_orders_for_symbol(symbol)
        if legs is None:
            return {"retCode": -1, "retMsg": "could not read open orders"}
        patched: list = []
        errors: list = []
        for o in legs:
            otype = str(o.get("type") or o.get("order_type") or "").lower()
            oid = o.get("id")
            if not oid:
                continue
            if sl is not None and "stop" in otype:
                env = self._request(
                    "PATCH", f"/v2/orders/{oid}",
                    {"stop_price": f"{float(sl):.2f}"},
                )
                (patched if env.get("retCode") == 0 else errors).append(str(oid))
            elif tp is not None and otype == "limit":
                env = self._request(
                    "PATCH", f"/v2/orders/{oid}",
                    {"limit_price": f"{float(tp):.2f}"},
                )
                (patched if env.get("retCode") == 0 else errors).append(str(oid))
        if errors:
            return {"retCode": 1,
                    "retMsg": f"leg replace failed for orders {errors}"}
        if not patched:
            return {"retCode": 1,
                    "retMsg": "no matching protective leg found for "
                              f"{str(symbol).upper()}"}
        return {"retCode": 0,
                "result": {"orderId": patched[-1], "patched": patched}}
