"""Internal trading-domain models.

The rest of this package — and any future bot wiring — should consume
these dataclasses rather than raw Tradovate JSON. That isolates the
caller from API renames and lets us swap the wire format (e.g. to
pydantic) without rippling through callers.

The ``from_api`` classmethods accept the fields Tradovate is known to
return and tolerate extras; missing optional fields fall back to None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    MARKET = "Market"
    LIMIT = "Limit"
    STOP = "Stop"
    STOP_LIMIT = "StopLimit"


class TimeInForce(str, Enum):
    DAY = "Day"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    user_id: int
    account_type: str | None
    active: bool
    legal_status: str | None

    @classmethod
    def from_api(cls, payload: dict) -> "Account":
        return cls(
            id=int(payload["id"]),
            name=str(payload.get("name", "")),
            user_id=int(payload.get("userId", 0)),
            account_type=payload.get("accountType"),
            active=bool(payload.get("active", True)),
            legal_status=payload.get("legalStatus"),
        )


@dataclass(frozen=True)
class Contract:
    id: int
    name: str
    contract_maturity_id: int | None
    product_id: int | None
    status: str | None

    @classmethod
    def from_api(cls, payload: dict) -> "Contract":
        return cls(
            id=int(payload["id"]),
            name=str(payload.get("name", "")),
            contract_maturity_id=payload.get("contractMaturityId"),
            product_id=payload.get("productId"),
            status=payload.get("status"),
        )


@dataclass(frozen=True)
class Quote:
    contract_id: int
    bid: float | None
    ask: float | None
    last: float | None
    bid_size: int | None
    ask_size: int | None
    ts: datetime

    @classmethod
    def from_md_frame(cls, contract_id: int, body: dict) -> "Quote":
        entries = body.get("entries", body)
        return cls(
            contract_id=contract_id,
            bid=_f(entries.get("Bid", {}).get("price")) if isinstance(entries.get("Bid"), dict) else _f(entries.get("bid")),
            ask=_f(entries.get("Offer", {}).get("price")) if isinstance(entries.get("Offer"), dict) else _f(entries.get("ask")),
            last=_f(entries.get("Trade", {}).get("price")) if isinstance(entries.get("Trade"), dict) else _f(entries.get("last")),
            bid_size=_i(entries.get("Bid", {}).get("size")) if isinstance(entries.get("Bid"), dict) else _i(entries.get("bidSize")),
            ask_size=_i(entries.get("Offer", {}).get("size")) if isinstance(entries.get("Offer"), dict) else _i(entries.get("askSize")),
            ts=datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class OrderRequest:
    account_id: int
    symbol: str
    side: OrderSide
    qty: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str | None = None
    is_automated: bool = True

    def to_api(self, contract_id: int) -> dict:
        body: dict[str, Any] = {
            "accountId": self.account_id,
            "action": self.side.value,
            "symbol": self.symbol,
            "orderQty": self.qty,
            "orderType": self.order_type.value,
            "isAutomated": self.is_automated,
            "timeInForce": self.time_in_force.value,
            "contractId": contract_id,
        }
        if self.limit_price is not None:
            body["price"] = self.limit_price
        if self.stop_price is not None:
            body["stopPrice"] = self.stop_price
        if self.client_order_id:
            body["text"] = self.client_order_id
        return body


@dataclass(frozen=True)
class Order:
    id: int
    account_id: int
    status: str
    symbol: str | None
    side: OrderSide | None
    qty: int | None
    filled_qty: int | None
    avg_price: float | None
    client_order_id: str | None

    @classmethod
    def from_api(cls, payload: dict) -> "Order":
        action = payload.get("action")
        return cls(
            id=int(payload["id"]),
            account_id=int(payload.get("accountId", 0)),
            status=str(payload.get("ordStatus", payload.get("status", "Unknown"))),
            symbol=payload.get("symbol"),
            side=OrderSide(action) if action in {"Buy", "Sell"} else None,
            qty=_i(payload.get("orderQty")),
            filled_qty=_i(payload.get("cumQty")),
            avg_price=_f(payload.get("avgPx")),
            client_order_id=payload.get("text"),
        )


@dataclass(frozen=True)
class Fill:
    id: int
    order_id: int
    account_id: int
    qty: int
    price: float
    side: OrderSide | None
    ts: datetime | None

    @classmethod
    def from_api(cls, payload: dict) -> "Fill":
        action = payload.get("action")
        return cls(
            id=int(payload["id"]),
            order_id=int(payload.get("orderId", 0)),
            account_id=int(payload.get("accountId", 0)),
            qty=int(payload.get("qty", 0)),
            price=float(payload.get("price", 0.0)),
            side=OrderSide(action) if action in {"Buy", "Sell"} else None,
            ts=_parse_ts(payload.get("timestamp")),
        )


@dataclass(frozen=True)
class Position:
    account_id: int
    contract_id: int
    net_pos: int
    avg_price: float | None

    @classmethod
    def from_api(cls, payload: dict) -> "Position":
        return cls(
            account_id=int(payload.get("accountId", 0)),
            contract_id=int(payload.get("contractId", 0)),
            net_pos=int(payload.get("netPos", 0)),
            avg_price=_f(payload.get("netPrice")),
        )


@dataclass(frozen=True)
class RiskLimits:
    allowed_symbols: frozenset[str] = field(default_factory=frozenset)
    max_position_per_symbol: int = 1
    max_open_orders: int = 5
    max_notional: float | None = None
    max_daily_loss: float | None = None


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
