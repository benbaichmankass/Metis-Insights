"""S-064 — GET /api/bot/liquidity.

Tier-1 read endpoint backing the dashboard's Liquidity Maps tab.
Surfaces the per-symbol liquidity zones (equal highs / equal lows /
recent sweeps) the pipeline writes to
``runtime_logs/liquidity_state.json`` via
``src/runtime/liquidity_state.py`` (S-064 prereq PR).

The web API and the pipeline are separate processes; the file is
the only shared surface. Empty / missing file is a 200 with empty
arrays — the pipeline may not have written a snapshot yet, the
operator should see "no zones detected" rather than a 503.

See ``docs/api-tier-policy.md`` — Tier 1 (no session).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from src.runtime.liquidity_state import read_state

router = APIRouter(prefix="/api/bot", tags=["bot"])

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
DEFAULT_SWEEP_LIMIT = 25


def _empty_payload(symbol: str) -> Dict[str, Any]:
    """Shape the dashboard expects when no zones are detected for the
    requested symbol — keeps the client code branching simple."""
    return {
        "symbol": symbol,
        "as_of": None,
        "equal_highs": [],
        "equal_lows": [],
        "recent_sweeps": [],
    }


def _slice_zones(zones: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if not isinstance(zones, list):
        return []
    return zones[: max(0, limit)]


def build_liquidity(
    symbol: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    sweeps_limit: int = DEFAULT_SWEEP_LIMIT,
    state: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the response for *symbol*. Pure-ish (state I/O isolated).

    When *symbol* is ``None`` the endpoint defaults to the first
    symbol in the state file (alphabetical) so the dashboard's
    "first paint with no symbol selected" works without an extra
    round-trip.
    """
    snapshot = state if state is not None else read_state()

    if not snapshot:
        return _empty_payload(symbol or "")

    if symbol is None:
        symbol = sorted(snapshot.keys())[0]

    sym_state = snapshot.get(symbol)
    if not isinstance(sym_state, dict):
        return _empty_payload(symbol)

    return {
        "symbol": symbol,
        "as_of": sym_state.get("as_of"),
        "equal_highs": _slice_zones(sym_state.get("equal_highs") or [], limit),
        "equal_lows": _slice_zones(sym_state.get("equal_lows") or [], limit),
        "recent_sweeps": _slice_zones(
            sym_state.get("recent_sweeps") or [], sweeps_limit
        ),
        "available_symbols": sorted(snapshot.keys()),
    }


@router.get("/liquidity")
def get_liquidity(
    symbol: Optional[str] = Query(None, min_length=1, max_length=32),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    sweeps_limit: int = Query(DEFAULT_SWEEP_LIMIT, ge=1, le=MAX_LIMIT),
) -> Dict[str, Any]:
    return build_liquidity(symbol=symbol, limit=limit, sweeps_limit=sweeps_limit)
