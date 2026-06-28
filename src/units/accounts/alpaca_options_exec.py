"""Alpaca options ORDER execution — multi-leg (mleg) + single-leg.

Phase-1 Slice-2 of the Alpaca L3 options build (docs/research/alpaca-options-PHASE1-spec.md).

Kept in its own module (NOT folded into ``AlpacaClient``) so the live equity
bracket-order path is untouched — same reasoning as the separate read-only
``AlpacaOptionsData`` client. Hand-rolled raw ``requests`` + retCode envelopes, in
the established ``AlpacaClient`` style (no ``alpaca-py`` dependency).

Order shapes (verified against Alpaca docs):
  - **Defined-risk spread (2-4 legs)** → ``order_class="mleg"`` in one atomic order;
    per-leg ``position_intent``; ``ratio_qty`` in simplest form; NO equity leg;
    ``type`` market|limit, ``time_in_force=day``, whole-contract ``qty``.
  - **Single long option** (the degenerate smoke-test case) → a plain single-leg
    order on the OCC option symbol (NO ``order_class``). mleg requires >=2 legs, so a
    lone long is not an mleg order.

WIRED (paper only). Reached via ``execute.execute_pkg`` →
``options_overlay.place_options_expression`` → ``place_spread`` for the
``alpaca_options_paper`` account (``mode: live``, ``account_class: paper``,
``options.express_as: debit_vertical``). It places real **paper-money** option
orders; there is no real-money options account. Defaults to the paper host.
(Field beats comment: the earlier "DORMANT — not imported by any executor"
note predated the options-overlay wiring.)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_HOSTS = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}

_SIDES = ("buy", "sell")
_INTENTS = ("buy_to_open", "sell_to_open", "buy_to_close", "sell_to_close")
_TYPES = ("market", "limit")

# Option-lifecycle activity types for /v2/account/activities (expiry/assignment/
# exercise). Sourced from the pure lifecycle module so the two never drift.
from src.units.accounts.options_lifecycle import (  # noqa: E402
    OPTION_LIFECYCLE_ACTIVITY_TYPES as _OPTION_LIFECYCLE_TYPES,
)


class MissingCredentialsError(RuntimeError):
    """Raised when an action needs the Alpaca key pair. Names env vars, never values."""


class OptionsOrderError(ValueError):
    """Raised for a structurally-invalid options order (bad legs/side/intent/qty)."""


@dataclass(frozen=True)
class OptionLeg:
    """One leg of an options order.

    ``symbol`` is the OCC contract symbol; ``side`` ∈ {buy,sell}; ``position_intent``
    ∈ {buy_to_open,sell_to_open,buy_to_close,sell_to_close}; ``ratio_qty`` is the
    integer leg ratio (simplest form across all legs, GCD == 1).
    """

    symbol: str
    side: str
    position_intent: str
    ratio_qty: int = 1


def _validate_leg(leg: OptionLeg) -> None:
    if not leg.symbol:
        raise OptionsOrderError("leg symbol is empty")
    if leg.side not in _SIDES:
        raise OptionsOrderError(f"invalid leg side {leg.side!r} (expected buy/sell)")
    if leg.position_intent not in _INTENTS:
        raise OptionsOrderError(f"invalid position_intent {leg.position_intent!r}")
    if int(leg.ratio_qty) < 1:
        raise OptionsOrderError(f"ratio_qty must be >= 1, got {leg.ratio_qty}")


def build_mleg_body(
    legs: List[OptionLeg],
    *,
    qty: int,
    order_type: str = "limit",
    limit_price: Optional[float] = None,
    time_in_force: str = "day",
) -> Dict[str, Any]:
    """Build the ``order_class="mleg"`` request body for a 2-4 leg defined-risk order.

    Pure + total-validating (raises ``OptionsOrderError`` on a bad shape). No I/O —
    unit-tested directly. Enforces: 2-4 legs, no duplicate leg symbols, whole-contract
    qty >= 1, ``limit`` requires a positive ``limit_price``, ``time_in_force=day``.
    """
    if not (2 <= len(legs) <= 4):
        raise OptionsOrderError(f"mleg requires 2-4 legs, got {len(legs)}")
    if order_type not in _TYPES:
        raise OptionsOrderError(f"invalid order type {order_type!r}")
    q = int(qty)
    if q < 1:
        raise OptionsOrderError(f"qty must be a whole number >= 1, got {qty}")
    seen = set()
    for leg in legs:
        _validate_leg(leg)
        if leg.symbol in seen:
            raise OptionsOrderError(f"duplicate leg symbol {leg.symbol}")
        seen.add(leg.symbol)
    body: Dict[str, Any] = {
        "order_class": "mleg",
        "qty": str(q),
        "type": order_type,
        "time_in_force": time_in_force,
        "legs": [
            {
                "symbol": leg.symbol,
                "ratio_qty": str(int(leg.ratio_qty)),
                "side": leg.side,
                "position_intent": leg.position_intent,
            }
            for leg in legs
        ],
    }
    if order_type == "limit":
        if limit_price is None or float(limit_price) <= 0:
            raise OptionsOrderError("limit order requires a positive limit_price")
        body["limit_price"] = f"{float(limit_price):.2f}"
    return body


def build_single_option_body(
    symbol: str,
    *,
    side: str,
    qty: int,
    position_intent: str,
    order_type: str = "limit",
    limit_price: Optional[float] = None,
    time_in_force: str = "day",
) -> Dict[str, Any]:
    """Build a plain single-leg option order body (no ``order_class``).

    The degenerate long-option smoke case. Pure + validating.
    """
    if not symbol:
        raise OptionsOrderError("symbol is empty")
    if side not in _SIDES:
        raise OptionsOrderError(f"invalid side {side!r}")
    if position_intent not in _INTENTS:
        raise OptionsOrderError(f"invalid position_intent {position_intent!r}")
    if order_type not in _TYPES:
        raise OptionsOrderError(f"invalid order type {order_type!r}")
    q = int(qty)
    if q < 1:
        raise OptionsOrderError(f"qty must be a whole number >= 1, got {qty}")
    body: Dict[str, Any] = {
        "symbol": symbol,
        "qty": str(q),
        "side": side,
        "position_intent": position_intent,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if order_type == "limit":
        if limit_price is None or float(limit_price) <= 0:
            raise OptionsOrderError("limit order requires a positive limit_price")
        body["limit_price"] = f"{float(limit_price):.2f}"
    return body


class AlpacaOptionsExecutor:
    """Thin options order client (key-pair auth, paper host default)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        env: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY_ID", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_API_SECRET_KEY", "")
        env_name = (env or os.environ.get("ALPACA_ENV", "paper")).strip().lower()
        self.env = env_name if env_name in _HOSTS else "paper"
        self.base_url = _HOSTS[self.env]
        self.timeout = timeout

    def _require_creds(self, action: str) -> None:
        if not self.api_key or not self.api_secret:
            raise MissingCredentialsError(
                f"alpaca options {action}: ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY unset."
            )

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.request(
                method, f"{self.base_url}{path}", headers=headers,
                json=json_body, timeout=self.timeout,
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
    def place_spread(
        self,
        legs: List[OptionLeg],
        *,
        qty: int,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Dict[str, Any]:
        """Submit a 2-4 leg defined-risk spread as one atomic mleg order."""
        self._require_creds("place_spread")
        body = build_mleg_body(
            legs, qty=qty, order_type=order_type,
            limit_price=limit_price, time_in_force=time_in_force,
        )
        env = self._request("POST", "/v2/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        return {"retCode": 0, "result": {"orderId": str(result.get("id") or "")}}

    def place_single_option(
        self,
        symbol: str,
        *,
        side: str,
        qty: int,
        position_intent: str,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Dict[str, Any]:
        """Submit a plain single-leg option order (the long-option smoke case)."""
        self._require_creds("place_single_option")
        body = build_single_option_body(
            symbol, side=side, qty=qty, position_intent=position_intent,
            order_type=order_type, limit_price=limit_price, time_in_force=time_in_force,
        )
        env = self._request("POST", "/v2/orders", body)
        if env.get("retCode") != 0:
            return env
        result = env.get("result") or {}
        return {"retCode": 0, "result": {"orderId": str(result.get("id") or "")}}

    def option_positions(self) -> Optional[list]:
        """Open OPTION positions only (asset_class == 'us_option').

        Returns ``None`` on a read failure (distinguishes "could not read" from
        genuinely-flat ``[]``), mirroring ``AlpacaClient.positions``.
        """
        try:
            self._require_creds("option_positions")
        except MissingCredentialsError as exc:
            logger.warning("%s", exc)
            return None
        env = self._request("GET", "/v2/positions")
        if env.get("retCode") != 0:
            logger.warning("alpaca option_positions: %s", env.get("retMsg"))
            return None
        out = []
        for pos in env.get("result") or []:
            if str(pos.get("asset_class") or "").lower() != "us_option":
                continue
            out.append(pos)
        return out

    def close_position(self, occ_symbol: str) -> Dict[str, Any]:
        """Close (liquidate) a single option leg's position by OCC symbol.

        A 404 (no open position) maps to retCode 0 — idempotent. For a 2-4 leg
        spread, close each leg (or submit the reversing mleg via ``place_spread``
        with the to_close intents); this single-symbol close is the per-leg /
        single-option path.
        """
        self._require_creds("close_position")
        env = self._request("DELETE", f"/v2/positions/{str(occ_symbol).upper()}")
        if env.get("retCode") == 404:
            return {"retCode": 0, "result": {"note": "no open position"}}
        return env

    def close_structure(self, occ_symbols: List[str]) -> Dict[str, Any]:
        """Liquidate every leg of a structure (the Slice-4 options close path).

        Closes each OCC leg via :meth:`close_position` (idempotent — a 404/no-position
        leg is a success). Returns ``{retCode:0, result:{closed:[...], failed:[...]}}``;
        ``retCode`` is non-zero only if a leg's liquidation failed. Used for an active
        (pre-expiry) close; expiry/assignment needs no close call (the broker concludes
        the position itself) — the monitor only journals the conclusion.
        """
        self._require_creds("close_structure")
        closed: List[str] = []
        failed: List[Dict[str, Any]] = []
        for sym in occ_symbols or []:
            env = self.close_position(sym)
            if env.get("retCode") == 0:
                closed.append(str(sym).upper())
            else:
                failed.append({"symbol": str(sym).upper(), "retMsg": env.get("retMsg")})
        ret = 0 if not failed else 1
        return {"retCode": ret, "result": {"closed": closed, "failed": failed}}

    def account_activities(
        self,
        *,
        activity_types: Optional[List[str]] = None,
        after: Optional[str] = None,
        page_size: int = 100,
    ) -> Dict[str, Any]:
        """Fetch ``/v2/account/activities`` filtered to option-lifecycle event types.

        *activity_types* defaults to expiration/assignment/exercise
        (``options_lifecycle.OPTION_LIFECYCLE_ACTIVITY_TYPES``); *after* is an
        ISO-8601 lower bound (the lookback). Returns the raw retCode envelope; on
        success ``result`` is the JSON list of activity records. The endpoint is on
        the trading API (key-pair auth), not the market-data feed.
        """
        self._require_creds("account_activities")
        types = activity_types or list(_OPTION_LIFECYCLE_TYPES)
        params = [f"activity_types={','.join(types)}", f"page_size={int(page_size)}"]
        if after:
            params.append(f"after={after}")
        path = "/v2/account/activities?" + "&".join(params)
        return self._request("GET", path)
