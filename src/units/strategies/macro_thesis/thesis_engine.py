"""M28 P3 — the S1 rule-based thesis former (pure, observe-only).

The first, deterministic rung of the LLM ladder (design §5, decision (b)): turn
the data-layer reads into structured :class:`~.thesis.TradeThesis` objects by
explicit rules, before any LLM proposes one. Given a value read (P1) for an
instrument — plus optional linked signals (P2/§3) and watched events (P2/§2) —
form a ``draft`` thesis whose direction, rationale, and conviction are all
mechanically derived from the read, so every field is traceable and replayable.

Deliberately **pure**: no clock, no randomness, no I/O — ``thesis_id`` /
``created_at`` are passed in (replayable at backtest time). It returns the
*would-be* thesis; persisting it (``thesis_store``) and ever placing it (the
gated P3+ executor) are separate. A fairly-valued / unknown read yields **no**
thesis (``None``) — the sleeve only speaks when it has a view. Nothing here
touches an order path.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .thesis import TradeThesis, new_thesis_id
from .valuation import ValueRead, value_to_direction

# direction of the *signal* → the thesis's position side.
_SIDE = {"bullish": "long", "bearish": "short"}


def value_conviction(read: ValueRead) -> Optional[float]:
    """Map a value read's extremity to a ``[0,1]`` conviction.

    ``cheap_score`` is orientation-normalized (1 = cheapest, 0 = richest); a read
    near the center (``0.5``) is no-view (conviction ~0), an extreme read is
    high-conviction. ``conviction = |cheap_score − 0.5| × 2`` clamped to
    ``[0,1]``. ``None`` when the read has no score (honest-null — never a
    fabricated conviction)."""
    cs = read.cheap_score
    if cs is None:
        return None
    conv = abs(cs - 0.5) * 2.0
    return 0.0 if conv < 0 else 1.0 if conv > 1 else conv


def form_value_thesis(
    symbol: str,
    read: ValueRead,
    *,
    thesis_id: str,
    created_at: str,
    express_as: str = "debit_vertical",
    venue: str = "alpaca",
    account: Optional[str] = "alpaca_options_paper",
    signal_ids: Optional[Sequence[str]] = None,
    watched_events: Optional[Sequence[Mapping[str, Any]]] = None,
    world_view: Optional[Mapping[str, Any]] = None,
    macro_context: Optional[Mapping[str, Any]] = None,
    min_conviction: float = 0.0,
) -> Optional[TradeThesis]:
    """Form a ``draft`` :class:`TradeThesis` from a value read, or ``None``.

    Returns ``None`` when the read is fairly-valued/unknown (``value_to_direction``
    → ``neutral``) or when the derived conviction is below ``min_conviction`` —
    the sleeve only forms a thesis when it has a view strong enough to act on.
    Direction, rationale, valuation block, and ``thesis_conviction`` are all
    mechanically derived from ``read`` (fully traceable). ``signal_ids`` /
    ``watched_events`` link the evidence + the non-price decision rules."""
    sig_dir = value_to_direction(read)
    side = _SIDE.get(sig_dir)
    if side is None:  # neutral / unknown → no view, no thesis
        return None
    conviction = value_conviction(read)
    if conviction is not None and conviction < min_conviction:
        return None

    valuation_block = {
        "metric": read.metric,
        "value": read.value,
        "label": read.label,
        "cheap_score": read.cheap_score,
        "z_score": read.z_score,
        "percentile": read.percentile,
        "n": read.n,
    }
    rationale = (
        f"{symbol}: {read.metric} reads {read.label} "
        f"(cheap_score={read.cheap_score}); {side} value thesis"
    )
    return TradeThesis(
        thesis_id=thesis_id,
        created_at=created_at,
        updated_at=created_at,
        status="draft",
        rationale=rationale,
        world_view=dict(world_view) if world_view else {},
        signals=list(signal_ids) if signal_ids else [],
        valuation=valuation_block,
        macro_context=dict(macro_context) if macro_context else {},
        instrument={"symbol": symbol, "venue": venue, "express_as": express_as},
        direction=side,
        watched_events=[dict(e) for e in watched_events] if watched_events else [],
        thesis_conviction=conviction,
        conviction_provenance={
            "source": "value_read", "metric": read.metric,
            "cheap_score": read.cheap_score, "signal_direction": sig_dir,
        },
        account=account,
    )


def form_theses_from_reads(
    reads_by_symbol: Mapping[str, ValueRead],
    *,
    id_prefix: str,
    created_at: str,
    min_conviction: float = 0.0,
    watched_events_by_symbol: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    signal_ids_by_symbol: Optional[Mapping[str, Sequence[str]]] = None,
    **kw: Any,
) -> list[TradeThesis]:
    """Batch former: one draft thesis per symbol with a directional value read.

    ``thesis_id`` is derived deterministically (``mth-<id_prefix>-<symbol>``) so a
    scan replays identically. Symbols with a neutral/unknown read (or below
    ``min_conviction``) are silently skipped — the result holds only the theses
    the sleeve would actually form this scan. Sorted by descending conviction so
    the strongest views lead."""
    watched = watched_events_by_symbol or {}
    sigs = signal_ids_by_symbol or {}
    out: list[TradeThesis] = []
    for symbol in sorted(reads_by_symbol):
        read = reads_by_symbol[symbol]
        t = form_value_thesis(
            symbol, read,
            thesis_id=new_thesis_id(f"{id_prefix}-{symbol}"),
            created_at=created_at,
            min_conviction=min_conviction,
            watched_events=watched.get(symbol),
            signal_ids=sigs.get(symbol),
            **kw,
        )
        if t is not None:
            out.append(t)
    out.sort(key=lambda t: (t.thesis_conviction if t.thesis_conviction is not None else -1.0),
             reverse=True)
    return out
