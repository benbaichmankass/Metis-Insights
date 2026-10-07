"""Prop-terminal platform adapter boundary.

Spec: ``docs/research/prop-automation-options-2026-09-27.md`` § 2.

Everything that knows a URL, a selector or a page layout lives BELOW this
interface, in one adapter per terminal. Everything platform-neutral (ticket
intake, reconciliation, the local rule guards, the ``ingest_report``
write-back, the kill switch) lives ABOVE it and never imports an adapter
module directly — it calls :func:`src.prop.platform.get_adapter`.

Slice 1 (read-only) implements ``login`` and the three reads. Step 3
(PROP-EXEC, 2026-09-28) adds the order controls on the dxtrade adapter only;
every one is DISARMED by default (``arm=False`` stops before the final
click). PROP-TERM (2026-09-28) built the same controls on the
``breakout_terminal`` adapter against an UNMEASURED layout.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


class FeasibilityError(RuntimeError):
    """The terminal refused to be driven in a way we will not work around.

    ``reason`` is a short machine-readable token (``asn_blocked``,
    ``access_denied``, ``challenge``, ``captcha``,
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
    # The terminal's own order id (DXtrade "Order ID" column, MEASURED run
    # 36358563148). None when the table did not carry one.
    order_id: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BracketSpec:
    """One bracket order, fully resolved, as the adapter will type it.

    Built by the platform-neutral executor from an emitted ticket
    (``src/prop/prop_executor.py::bracket_from_ticket``); the adapter never
    sizes, maps symbols or reads a ticket itself.
    """

    ticket_id: str
    venue_symbol: str
    side: str                 # "long" | "short"
    quantity: float
    stop_loss: float
    take_profit: float
    order_type: str = "limit"  # "limit" | "market"
    limit_price: Optional[float] = None
    # The venue's price increment, when declared (config/prop_platforms.yaml
    # executor.lots.<venue>.price_step). The executor types prices ROUNDED to
    # it, and the form read-back accepts a price field within one step of the
    # typed value (the terminal rounds what it shows: dry run #13965 read
    # 119.2 back for a typed 119.2002). None = exact read-back required.
    price_step: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlaceAttempt:
    """What ``place_bracket`` did. ``submitted`` is True ONLY when the submit
    control was clicked; it NEVER means the order exists. Existence is
    established by a later re-read (``prop_executor`` § 3.2 step 3)."""

    stage: str                # form_opened | form_filled | form_verified | submitted | refused
    submitted: bool = False
    detail: str = ""
    form: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PropPlatformAdapter:
    """One terminal. ``page`` is a Playwright ``Page`` (sync API)."""

    platform: str = ""
    #: Can ``modify_bracket(page, p, None, tp)`` move a resting TP? (TP
    #: doctrine B1, read by ``prop_trail._tp_step`` and tp-doctrine-guard.)
    #: False unless an adapter has a measured TP-amend verb.
    TP_AMEND_SUPPORTED = False

    # ── slice 1: read path ──────────────────────────────────────────────
    def login(self, page: Any, login_url: str, username: str, password: str) -> None:
        raise NotImplementedError

    def read_account(self, page: Any) -> AccountSnapshot:
        raise NotImplementedError

    def read_positions(self, page: Any) -> List[Position]:
        raise NotImplementedError

    def read_orders(self, page: Any) -> List[WorkingOrder]:
        raise NotImplementedError

    # ── step 3: order controls ─────────────────────────────────────────
    # Both adapters implement them. Every one takes ``arm``: with
    # ``arm=False`` (the default) it walks up to the final control and STOPS,
    # so a dry run exercises the whole path without pressing anything that
    # changes the account. Only ``src/prop/prop_executor.py`` in ``live`` mode
    # passes ``arm=True``.
    def probe_order_ticket(self, page: Any, venue_symbol: str) -> Dict[str, Any]:
        raise NotImplementedError("no order-ticket probe on this platform")

    def place_bracket(self, page: Any, spec: BracketSpec, *, arm: bool = False) -> PlaceAttempt:
        raise NotImplementedError("order placement is not built for this platform")

    def modify_bracket(self, page: Any, position: Position,
                       stop_loss: Optional[float], take_profit: Optional[float],
                       *, arm: bool = False, rollout: Any = None) -> Any:
        raise NotImplementedError("order modification is not built for this platform")

    def cancel_order(self, page: Any, order: WorkingOrder, *, arm: bool = False) -> Any:
        raise NotImplementedError("order cancellation is not built for this platform")

    def flatten(self, page: Any, symbol: Optional[str] = None, *, arm: bool = False) -> Any:
        raise NotImplementedError("flatten is not built for this platform")
