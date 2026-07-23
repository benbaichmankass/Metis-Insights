"""M28 — the ``TradeThesis`` core object + lifecycle state machine (pure).

The unit of work in this sleeve is not a signal, it is a **thesis**: a fully
traceable, machine-readable *bet on the world* (design §2, schema §1). Making
the discretionary trade a structured object is what makes it auditable,
backtestable, gradable — and is the precondition for ever letting an LLM propose
one. This module is the object itself: a stdlib dataclass, its lifecycle state
machine, and JSON (de)serialization for the operational store.

Deliberately **pure and deterministic**: no ``datetime.now()``, no randomness,
no I/O. ``created_at`` / ``updated_at`` and the ``thesis_id`` token are **passed
in** by the caller, so the object is fully replayable at backtest time (the same
inputs always reconstruct the same thesis) and unit-testable with no clock. The
persistence + the P3 generation engine are separate, later bricks; nothing here
touches an order path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Mapping, Optional

# --- controlled vocabularies (schema §1 / §1a) ---
STATUSES = frozenset({"draft", "active", "invalidated", "closed", "expired"})
DIRECTIONS = frozenset({"long", "short"})
EXPRESS_AS = frozenset({"debit_vertical", "spot", "etf", "future"})
CLOSE_REASONS = frozenset(
    {"target", "invalidation", "event_outcome", "time_barrier", "manual"}
)
# Free-source stack a signal/evidence row may cite (schema §Point-in-time rule 3).
FREE_SOURCES = frozenset(
    {"fred", "sec_edgar", "bls", "bea", "treasury", "fed", "rss",
     "market_data", "llm_extractor", "manual", "bigdata"}
)

# The lifecycle state machine (schema §1a). Terminal states map to ``frozenset()``.
_TRANSITIONS: dict[str, frozenset] = {
    "draft": frozenset({"active", "expired"}),
    "active": frozenset({"invalidated", "closed"}),
    "invalidated": frozenset({"closed"}),
    "closed": frozenset(),
    "expired": frozenset(),
}


def new_thesis_id(token: str) -> str:
    """``mth-<token>`` id (schema §1). The caller supplies the token (a ULID/uuid
    in the live path) so this stays deterministic + replayable."""
    return f"mth-{token}"


def can_transition(from_status: str, to_status: str) -> bool:
    """Whether ``from_status → to_status`` is a legal lifecycle edge (§1a)."""
    return to_status in _TRANSITIONS.get(from_status, frozenset())


@dataclass
class TradeThesis:
    """One row per thesis (schema §1). JSON-column fields ride as dict/list."""

    thesis_id: str
    created_at: str
    updated_at: str
    status: str = "draft"
    # --- the claim ---
    rationale: str = ""
    world_view: dict = field(default_factory=dict)          # {regime, macro_tilt, theme}
    # --- the evidence (every input traceable) ---
    signals: list = field(default_factory=list)             # signal_id refs into macro_signals
    valuation: dict = field(default_factory=dict)           # snapshot refs + computed value read
    ta_context: dict = field(default_factory=dict)          # {symbol_candidates[], setup, levels}
    macro_context: dict = field(default_factory=dict)       # point-in-time series snapshot + z
    # --- the bet ---
    instrument: dict = field(default_factory=dict)          # {symbol, venue, express_as}
    direction: Optional[str] = None                         # long | short
    entry_plan: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)
    invalidation: dict = field(default_factory=dict)        # thesis-based condition, not a tight stop
    horizon_days: Optional[int] = None
    max_hold_until: Optional[str] = None                    # hard calendar barrier
    # --- the non-price machinery (the operator's core ask) ---
    watched_events: list = field(default_factory=list)      # [{event_id, on_outcome:[{if,action}]}]
    # --- score + provenance ---
    thesis_conviction: Optional[float] = None               # [0,1] → future c_macro
    conviction_provenance: dict = field(default_factory=dict)
    grade: dict = field(default_factory=dict)               # {llm_grade, calibration_bin}
    # --- account / execution / close ---
    account: Optional[str] = None                           # alpaca_options_paper for the soak
    linked_order_package_id: Optional[str] = None           # set when it places; null while observe-only
    close_reason: Optional[str] = None                      # one of CLOSE_REASONS
    realized_pnl: Optional[float] = None                    # filled at close

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Plain dict for the JSONL/DB store (mirrors ``order_packages`` JSON cols)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TradeThesis":
        """Reconstruct from a stored row, ignoring unknown keys (rename-resilient)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dict(row).items() if k in known})

    def is_terminal(self) -> bool:
        return not _TRANSITIONS.get(self.status, frozenset())


def transition(
    thesis: TradeThesis,
    to_status: str,
    *,
    updated_at: str,
    close_reason: Optional[str] = None,
    realized_pnl: Optional[float] = None,
) -> TradeThesis:
    """Return a NEW thesis advanced to ``to_status`` (never mutates the input).

    Raises ``ValueError`` on an illegal edge (§1a), on an unknown ``to_status``,
    or when closing without a valid ``close_reason``. A ``close`` may also carry
    the ``realized_pnl``. Immutable-by-copy so the caller keeps the prior state
    for the point-in-time soak (log the *would-be* transition, then apply)."""
    if to_status not in STATUSES:
        raise ValueError(f"unknown status {to_status!r}")
    if not can_transition(thesis.status, to_status):
        raise ValueError(f"illegal transition {thesis.status!r} -> {to_status!r}")
    if to_status == "closed":
        if close_reason not in CLOSE_REASONS:
            raise ValueError(f"close requires a valid close_reason, got {close_reason!r}")
    return replace(
        thesis,
        status=to_status,
        updated_at=updated_at,
        close_reason=close_reason if to_status == "closed" else thesis.close_reason,
        realized_pnl=realized_pnl if realized_pnl is not None else thesis.realized_pnl,
    )


def would_transition(
    thesis: TradeThesis,
    to_status: str,
    *,
    at: str,
    close_reason: Optional[str] = None,
) -> Optional[dict]:
    """Observe-only: the transition record for the soak, WITHOUT applying it.

    Returns ``{thesis_id, from, to, close_reason, at}`` when the edge is legal,
    else ``None`` (nothing to log). This is what the observe-only P2/P3 soak
    writes — the *would-be* lifecycle move — before any live executor exists."""
    if not can_transition(thesis.status, to_status):
        return None
    return {
        "thesis_id": thesis.thesis_id,
        "from": thesis.status,
        "to": to_status,
        "close_reason": close_reason if to_status == "closed" else None,
        "at": at,
    }
