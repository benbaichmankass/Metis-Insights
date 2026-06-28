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
    # True when routing to Bybit demo endpoint (demo: true in accounts.yaml).
    # Distinct from dry_run: demo accounts DO call the exchange (paper money);
    # dry_run accounts suppress all exchange calls entirely.
    demo: bool = False
    base_currency: str = "USDT"
    max_concurrent_positions: int = 1
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, account_id: str, data: dict) -> "AccountProfile":
        """Build an AccountProfile from a raw accounts.yaml entry dict.

        accounts.yaml uses ``mode: live | dry_run`` (string) for the live/dry
        toggle and an optional ``demo: true`` bool for the Bybit demo endpoint.
        This method maps both correctly.
        """
        exchange_raw = data.get("exchange", "bybit").lower()
        if exchange_raw == "bybit":
            exchange: ExchangeID = "bybit"
        elif exchange_raw in ("interactive_brokers", "ib"):
            exchange = "interactive_brokers"
        else:
            exchange = "unknown"

        # accounts.yaml uses mode: live | dry_run (not a bool dry_run field).
        # Default to "live" to match the canonical executor resolver
        # (src/units/accounts/__init__.py::_resolve_mode) — per the Prime
        # Directive, omitting `mode` must NOT strand capability to dry
        # (S-AUDIT-H H3-F5). This is a read-only typed view today (no order
        # path reads it), but aligning the default removes the footgun.
        mode = str(data.get("mode", "live")).lower()
        dry_run = mode != "live"

        # demo: true means routes to Bybit demo endpoint — real trades, paper money
        demo = bool(data.get("demo", False))

        account_type_raw = data.get("account_type", "")
        if account_type_raw:
            account_type: AccountType = account_type_raw  # type: ignore[assignment]
        elif demo:
            account_type = "bybit_demo"
        elif exchange == "bybit" and not dry_run:
            account_type = "bybit_live"
        elif exchange == "bybit":
            account_type = "bybit_demo"
        elif exchange == "interactive_brokers" and dry_run:
            account_type = "ib_paper"
        elif exchange == "interactive_brokers":
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
            demo=demo,
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
        return self.demo

    def __repr__(self) -> str:
        live_label = "LIVE" if self.is_live else "dry_run"
        demo_label = "/demo" if self.demo else ""
        return f"AccountProfile({self.account_id!r}, {self.exchange}{demo_label}, {live_label})"
