"""Prop-terminal platform adapter boundary.

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.

Everything that knows a URL, a selector or a page layout lives BELOW this
interface, in one adapter per terminal. Everything platform-neutral (ticket
intake, reconciliation, the local rule guards, the ``ingest_report``
write-back, the kill switch) lives ABOVE it and never imports an adapter
module directly — it calls :func:`src.prop.platform.get_adapter`.

Slice 1 (read-only) implements ``login`` and the three reads. The four
order-control methods are on the interface so the shape is fixed, and they
raise :class:`NotImplementedError` on every adapter: there is no code path
in slice 1 that clicks an order control.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


class FeasibilityError(RuntimeError):
    """The terminal refused to be driven in a way we will not work around.

    ``reason`` is a short machine-readable token (``challenge``, ``captcha``,
    ``2fa``, ``login_rejected``, ``password_expired``, ``no_credentials``,
    ``timeout``, ``unknown_page``). A feasibility finding is reported, never
    retried in a loop and never evaded.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"feasibility: {reason}" + (f" ({detail})" if detail else ""))


@dataclass
class AccountSnapshot:
    """Account metrics as read off the terminal.

    A field that could not be read is ``None`` — never ``0`` — and its label
    is listed in ``unparsed`` so "we could not look" stays distinguishable
    from "we looked and it is zero".
    """

    balance: Optional[float] = None
    equity: Optional[float] = None
    unrealized: Optional[float] = None
    realized_today: Optional[float] = None
    margin_used: Optional[float] = None
    available: Optional[float] = None
    currency: Optional[str] = None
    unparsed: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    side: Optional[str] = None          # "long" | "short"
    quantity: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    raw: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkingOrder:
    symbol: str
    side: Optional[str] = None
    order_type: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    raw: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InstrumentSpec:
    """Per-symbol instrument specification as read off the terminal.

    ``symbol`` is the name this read searched for (the terminal's own
    symbol, e.g. ``ADAUSD``, not the bot's canonical ``ADAUSDT``). A field
    that could not be read is ``None`` — never ``0`` or ``1`` — and its
    label is listed in ``unparsed`` so "we could not look" stays
    distinguishable from "we looked and it is zero/one". ``raw_snippet`` is
    a short excerpt of the panel text near the symbol, kept only to help
    fix selectors when nothing else parsed. It is REDACTED before it is
    ever sliced out of the page text (redact the whole text first, then
    take the excerpt — never the other way round, or a slice boundary
    landing inside a secret/URL/e-mail can leak a fragment the redaction
    pattern no longer matches); see ``dxtrade.redact_text``.

    ``search_ok``/``panel_ok`` record whether this symbol's own search and
    info-panel-open attempt reported success, so a caller can tell "we
    looked and found nothing" from "we never actually looked" and print
    the outcome even when every field below is ``unparsed``.
    """

    symbol: str
    digits: Optional[int] = None
    contract_size: Optional[float] = None
    min_qty: Optional[float] = None
    qty_step: Optional[float] = None
    unparsed: List[str] = field(default_factory=list)
    raw_snippet: Optional[str] = None
    search_ok: Optional[bool] = None
    panel_ok: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PropPlatformAdapter:
    """One terminal. ``page`` is a Playwright ``Page`` (sync API)."""

    platform: str = ""

    # ── slice 1: read path ──────────────────────────────────────────────
    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        raise NotImplementedError

    def read_account(self, page: Any) -> AccountSnapshot:
        raise NotImplementedError

    def read_positions(self, page: Any) -> List[Position]:
        raise NotImplementedError

    def read_orders(self, page: Any) -> List[WorkingOrder]:
        raise NotImplementedError

    def read_instrument_specs(self, page: Any, symbols: List[str],
                              secrets: List[str] = ()) -> List["InstrumentSpec"]:
        """One :class:`InstrumentSpec` per requested symbol, same order.

        Read-only: looks up each symbol (e.g. via a search box) and reads
        whatever specification panel that surfaces, if any. Never clicks an
        order control. A symbol the terminal does not expose specs for
        returns an ``InstrumentSpec`` with every field ``None`` and all of
        them listed in ``unparsed`` — not a raised error — so a caller can
        report per-symbol coverage rather than failing the whole read.
        ``secrets`` (e.g. username, password) are passed through so an
        implementation redacts the whole page text before slicing any
        excerpt out of it, never after.
        """
        raise NotImplementedError

    # ── slice 2: order controls — deliberately NOT implemented ─────────
    def place_bracket(self, page: Any, ticket: Dict[str, Any]) -> Any:
        raise NotImplementedError("order placement is slice 2 (a separate held Tier-2 PR)")

    def modify_bracket(self, page: Any, position: Position,
                       stop_loss: Optional[float], take_profit: Optional[float]) -> Any:
        raise NotImplementedError("order modification is slice 2 (a separate held Tier-2 PR)")

    def cancel_order(self, page: Any, order: WorkingOrder) -> Any:
        raise NotImplementedError("order cancellation is slice 2 (a separate held Tier-2 PR)")

    def flatten(self, page: Any, symbol: Optional[str] = None) -> Any:
        raise NotImplementedError("flatten is slice 2 (a separate held Tier-2 PR)")
