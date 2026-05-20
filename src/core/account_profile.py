"""Typed, immutable view of a single trading account configuration.

Designed to be constructed from config/accounts.yaml entries.
Supports Bybit (current) and Interactive Brokers (future S7).
No live runtime dependency — pure data type, safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AccountType = Literal["bybit_demo", "bybit_live", "ib_paper", "ib_live", "prop", "unknown"]
ExchangeID = Literal["bybit", "interactive_brokers", "unknown"]


@dataclass(frozen=True)
class AccountProfile:
    """Immutable typed view of an account entry from config/accounts.yaml."""

    account_id: str
    account_type: AccountType
    exchange: ExchangeID
    dry_run: bool
    base_currency: str = "USDT"
    max_concurrent_positions: int = 1
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, account_id: str, data: dict) -> "AccountProfile":
        """Build an AccountProfile from a raw accounts.yaml entry dict."""
        exchange_raw = data.get("exchange", "bybit").lower()
        if exchange_raw == "bybit":
            exchange: ExchangeID = "bybit"
        elif exchange_raw in ("interactive_brokers", "ib"):
            exchange = "interactive_brokers"
        else:
            exchange = "unknown"

        dry_run = bool(data.get("dry_run", True))
        account_type_raw = data.get("account_type", "")

        if account_type_raw:
            account_type: AccountType = account_type_raw  # type: ignore[assignment]
        elif exchange == "bybit" and dry_run:
            account_type = "bybit_demo"
        elif exchange == "bybit" and not dry_run:
            account_type = "bybit_live"
        elif exchange == "interactive_brokers" and dry_run:
            account_type = "ib_paper"
        elif exchange == "interactive_brokers" and not dry_run:
            account_type = "ib_live"
        else:
            account_type = "unknown"

        raw_tags = data.get("tags", [])
        tags = tuple(raw_tags) if isinstance(raw_tags, (list, tuple)) else ()

        return cls(
            account_id=account_id,
            account_type=account_type,
            exchange=exchange,
            dry_run=dry_run,
            base_currency=data.get("base_currency", "USDT"),
            max_concurrent_positions=int(data.get("max_concurrent_positions", 1)),
            tags=tags,
        )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        return not self.dry_run

    @property
    def is_bybit(self) -> bool:
        return self.exchange == "bybit"

    @property
    def is_ib(self) -> bool:
        return self.exchange == "interactive_brokers"

    @property
    def is_demo(self) -> bool:
        return self.dry_run

    def __repr__(self) -> str:
        live_label = "LIVE" if self.is_live else "demo"
        return f"AccountProfile({self.account_id!r}, {self.exchange}, {live_label})"
