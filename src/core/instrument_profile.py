"""Typed, immutable instrument specification.

Carries per-instrument contract details needed by allocator and
execution layers without embedding them in strategy code.
Pre-built factory methods cover the two initial instruments:
  - BTCUSDT perpetual on Bybit (current)
  - MES (Micro E-mini S&P 500) on CME via IB (future S7)

No live runtime dependency — pure data type, safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

InstrumentCategory = Literal["linear", "inverse", "spot", "futures", "unknown"]
SettlementCurrency = Literal["USDT", "USD", "BTC", "ETH", "unknown"]


@dataclass(frozen=True)
class InstrumentProfile:
    """Immutable specification for a tradeable instrument."""

    symbol: str
    exchange: str
    category: InstrumentCategory
    base_asset: str
    quote_currency: str
    settlement_currency: SettlementCurrency
    tick_size: float
    min_qty: float
    qty_step: float
    # For futures: USD notional value per contract (e.g. MES = $5 per index point)
    contract_value_usd: float = 1.0
    # Leverage cap; 0 = no cap / spot
    max_leverage: int = 0
    # Friendly label for UI / logs
    display_name: str = ""
    # The venue's per-order CEILING, or None for "this profile does not state
    # one" (2026-09-02, BL-20260902-AVAX-VENUE-MAX-CLAMP-INERT-WHEN-THE-LIVE-LOOKUP-MISSES).
    #
    # ⚠️ None here is "THIS SOURCE CANNOT SPEAK TO A CEILING", never "the venue
    # has no ceiling". Reading the first as the second is precisely what made
    # the venue-max clamp a silent no-op three times; `qty_legalize` grades it
    # as `could_not_look`, not as `absent`.
    #
    # Only populate a value that a VENUE actually published (Bybit's own
    # rejection text, or instruments-info). A guessed ceiling is a fabricated
    # constraint on the live order path. It is a FALLBACK for when the live
    # lot-rule lookup misses -- `prefer_live=True` keeps the venue
    # authoritative.
    #
    # ⚠️ A STALE VALUE IS NOT HARMLESS, and an earlier draft of this comment
    # claimed it was ("can only ever under-size ... never place an illegal
    # order"). That is FALSE and is corrected here rather than left standing,
    # because an over-confident comment on the order path is the same failure
    # mode as the collapsed state below it. Both directions are reachable:
    #   * venue RAISES its cap  -> we clamp low  -> under-size (benign)
    #   * venue LOWERS its cap  -> we clamp to a ceiling the venue no longer
    #                              honours -> THE ORDER IS STILL REJECTED
    # The second is no worse than placing unclamped (today's behaviour), but it
    # is not "never illegal". A stale ceiling narrows the bug; it does not
    # close it. Re-measure from the venue rather than trusting age.
    #
    # ⚠️ THIS FIELD IS SYMBOL-SCOPED, NOT ACCOUNT-SCOPED. One value serves every
    # account that trades the symbol, and Bybit's demo and production venues
    # need not publish the same ceiling. See the AVAXUSDT note in
    # `config/instruments.yaml` for the concrete case.
    max_qty: Optional[float] = None

    # ------------------------------------------------------------------
    # Pre-built factory methods
    # ------------------------------------------------------------------

    @classmethod
    def btcusdt_bybit_linear(cls) -> "InstrumentProfile":
        """BTCUSDT perpetual linear contract on Bybit — current live instrument."""
        return cls(
            symbol="BTCUSDT",
            exchange="bybit",
            category="linear",
            base_asset="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
            tick_size=0.1,
            min_qty=0.001,
            qty_step=0.001,
            contract_value_usd=1.0,
            max_leverage=100,
            display_name="BTC/USDT Perp (Bybit)",
        )

    @classmethod
    def mes_cme(cls) -> "InstrumentProfile":
        """Micro E-mini S&P 500 futures on CME via Interactive Brokers (future S7)."""
        return cls(
            symbol="MES",
            exchange="interactive_brokers",
            category="futures",
            base_asset="ES",
            quote_currency="USD",
            settlement_currency="USD",
            tick_size=0.25,
            min_qty=1.0,
            qty_step=1.0,
            contract_value_usd=5.0,  # $5 per index point
            max_leverage=0,
            display_name="Micro E-mini S&P 500 (CME/IB)",
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_crypto(self) -> bool:
        return self.exchange == "bybit"

    @property
    def is_futures(self) -> bool:
        return self.category == "futures"

    def round_qty(self, qty: float) -> float:
        """Round qty down to the nearest valid step."""
        if self.qty_step <= 0:
            return qty
        steps = int(qty / self.qty_step)
        return round(steps * self.qty_step, 8)

    def __repr__(self) -> str:
        return f"InstrumentProfile({self.symbol!r}, {self.exchange})"
