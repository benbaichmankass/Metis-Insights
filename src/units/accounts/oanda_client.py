"""OANDA v20 execution client — M15 Phase 2 (S-M15-PHASE2-OANDA).

Real REST integration for the OANDA practice/live API (the Phase-0
verdict's first wire: XAU/USD; docs/research/m15-phase0-results-2026-06-10.md).
Mirrors the contract the executor/coordinator already speak (the
bybit retCode-style shape): ``place()`` returns a retCode-style envelope,
missing creds raise :class:`MissingCredentialsError` naming the env var
(never the value), and the factory (`clients.oanda_client_for`) returns
``None`` when creds are absent so the account loads as
``configured: False``.

Auth: a single bearer token (``OANDA_API_TOKEN``) + account id
(``OANDA_ACCOUNT_ID``) — OANDA has no key+secret pair, so this client
reads env directly (the ``ib_client_for`` pattern, not
``resolve_credentials``). ``OANDA_ENV`` picks the host: ``practice``
(default) → api-fxpractice.oanda.com, ``live`` → api-fxtrade.oanda.com.

Orders are MARKET with broker-side ``stopLossOnFill`` /
``takeProfitOnFill`` (the bot's SL/TP always lives at the broker —
positions stay protected through weekend closes and trader restarts).
Units are signed integers (positive=long, negative=short) of the base
instrument; the executor's qty (base-asset quantity from RiskManager)
is rounded to whole units with a floor of 1.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class MissingCredentialsError(RuntimeError):
    """Raised when an action requires the OANDA token/account id.

    Message carries env-var *names* only, never values (no-secrets rule,
    see ``src/runtime/execution_diagnostics.py``).
    """


def to_instrument(symbol: str) -> str:
    """``XAUUSD``/``XAU_USD`` → ``XAU_USD`` (compact 6-char split 3+3)."""
    s = str(symbol).strip().upper()
    if "_" in s:
        return s
    if len(s) == 6:
        return f"{s[:3]}_{s[3:]}"
    return s


def _price_decimals(instrument: str) -> int:
    """Display precision OANDA accepts per instrument family.

    Practice-grade map: metals (XAU/XAG) 3dp, JPY-quoted pairs 3dp,
    everything else 5dp. A wrong precision is rejected by OANDA with
    PRICE_PRECISION_EXCEEDED — surfaced through the retCode envelope,
    never silently mispriced.
    """
    inst = instrument.upper()
    if inst.startswith(("XAU", "XAG")) or inst.endswith("JPY"):
        return 3
    return 5


class OandaClient:
    """Thin v20 REST client (token auth, practice host by default)."""

    def __init__(
        self,
        api_token: Optional[str] = None,
        account_id: Optional[str] = None,
        env: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_token = api_token or os.environ.get("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")
        env_name = (env or os.environ.get("OANDA_ENV", "practice")).strip().lower()
        self.env = env_name if env_name in _HOSTS else "practice"
        self.base_url = (base_url or _HOSTS[self.env]).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------ utils
    def _require_creds(self, action: str) -> None:
        if not self.api_token:
            raise MissingCredentialsError(
                f"oanda {action}: OANDA_API_TOKEN is unset."
            )
        if not self.account_id:
            raise MissingCredentialsError(
                f"oanda {action}: OANDA_ACCOUNT_ID is unset."
            )

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        """One HTTP round-trip → retCode-style envelope (never raises on HTTP).

        ``{"retCode": 0, "result": <json>}`` on 2xx;
        ``{"retCode": <status>, "retMsg": <errorMessage>}`` otherwise.
        Network-level failures return retCode -1 with the exception text.
        """
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
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
        msg = (
            payload.get("errorMessage")
            or payload.get("rejectReason")
            or f"HTTP {resp.status_code}"
        )
        return {"retCode": resp.status_code, "retMsg": str(msg)}

    # ------------------------------------------------------------ orders
    def place(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Place a MARKET order with attached SL/TP; retCode envelope.

        Expects the executor's order dict: ``symbol``, ``side``
        (``Buy``/``Sell``, case-insensitive), ``qty`` (base units,
        rounded to int, floor 1), optional ``sl`` / ``tp`` prices.
        On success ``result.orderId`` carries the fill (preferred) or
        create transaction id.
        """
        self._require_creds("place")
        instrument = to_instrument(order["symbol"])
        qty = max(1, int(round(float(order["qty"]))))
        side = str(order.get("side", "")).strip().lower()
        if side not in ("buy", "sell"):
            return {"retCode": -2, "retMsg": f"invalid side {order.get('side')!r}"}
        units = qty if side == "buy" else -qty
        dp = _price_decimals(instrument)
        body: Dict[str, Any] = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        if order.get("sl") is not None:
            body["order"]["stopLossOnFill"] = {"price": f"{float(order['sl']):.{dp}f}"}
        if order.get("tp") is not None:
            body["order"]["takeProfitOnFill"] = {"price": f"{float(order['tp']):.{dp}f}"}
        env = self._request("POST", f"/v3/accounts/{self.account_id}/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        fill = result.get("orderFillTransaction") or {}
        create = result.get("orderCreateTransaction") or {}
        cancel = result.get("orderCancelTransaction") or {}
        if cancel and not fill:
            # FOK orders can be created-then-cancelled (e.g. market halted,
            # insufficient margin) while the HTTP call still returns 201.
            return {
                "retCode": -3,
                "retMsg": f"order cancelled: {cancel.get('reason', 'unknown')}",
            }
        order_id = fill.get("id") or create.get("id")
        return {"retCode": 0, "result": {"orderId": str(order_id or "")}}

    # ------------------------------------------------------------ account
    def balance(self) -> Optional[float]:
        """Account NAV in account currency, or ``None`` on any failure."""
        try:
            self._require_creds("balance")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", f"/v3/accounts/{self.account_id}/summary")
        if env.get("retCode") != 0:
            logger.warning("oanda balance: %s", env.get("retMsg"))
            return None
        acct = (env.get("result") or {}).get("account") or {}
        try:
            return float(acct.get("NAV") or acct.get("balance"))
        except (TypeError, ValueError):
            return None

    def positions(self) -> Optional[list]:
        """Open positions as ``[{symbol, side, qty, avg_price, unrealized_pnl}]``.

        Returns ``None`` on a READ FAILURE (missing creds, network error,
        non-2xx) so callers distinguish "could not read" from "genuinely flat"
        (``[]``) — same contract as the IB/Alpaca read paths
        (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE). OANDA is dry_run today, so this
        is forward protection for when ``oanda_practice`` is promoted to live and
        starts hitting the position-snapshot reconciler.
        """
        try:
            self._require_creds("positions")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", f"/v3/accounts/{self.account_id}/openPositions")
        if env.get("retCode") != 0:
            logger.warning("oanda positions: %s", env.get("retMsg"))
            return None
        out = []
        for pos in (env.get("result") or {}).get("positions") or []:
            for side_key, side in (("long", "buy"), ("short", "sell")):
                leg = pos.get(side_key) or {}
                units = float(leg.get("units") or 0)
                if units == 0:
                    continue
                out.append(
                    {
                        "symbol": pos.get("instrument"),
                        "side": side,
                        "qty": abs(units),
                        "avg_price": float(leg.get("averagePrice") or 0) or None,
                        "unrealized_pnl": float(leg.get("unrealizedPL") or 0),
                    }
                )
        return out

    def close(self, symbol: str) -> Dict[str, Any]:
        """Close the open leg(s) on *symbol*; retCode envelope.

        OANDA rejects a closeout request for a side with no units, so
        only the legs actually open are named. No open position →
        retCode 0 (idempotent close, matching the reconciler's
        expectations).
        """
        self._require_creds("close")
        instrument = to_instrument(symbol)
        open_positions = self.positions()
        if open_positions is None:
            # Read failure — can't confirm whether a leg is open. Surface it
            # rather than mis-reporting an idempotent "no open position" close.
            return {"retCode": -1, "retMsg": "could not read open positions"}
        legs = [p for p in open_positions if p.get("symbol") == instrument]
        if not legs:
            return {"retCode": 0, "result": {"note": "no open position"}}
        body: Dict[str, Any] = {}
        for leg in legs:
            body["longUnits" if leg["side"] == "buy" else "shortUnits"] = "ALL"
        return self._request(
            "PUT",
            f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            body,
        )
