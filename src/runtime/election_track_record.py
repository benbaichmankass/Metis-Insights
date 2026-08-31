"""Recent per-strategy realised PnL — the EVIDENCE-BASED tiebreak for the
intent election.

WHY THIS EXISTS. ``intents._election_sort_key`` ranks competing intents by
``confidence``, but **50.1% of live contests are exact confidence ties**
(measured on the ``conviction_arbitration`` soak, n=371, 2026-06-17 ->
2026-08-30), so something must break them. Until 2026-08-31 that something was
the strategy NAME, which the operator rejected outright: *"the deterministic
fallback shouldn't be the fucking name. It should be some other metric that we
use that actually shows which one might be better ... what is the PNL for each
of the strategies over the past three days"*.

A stable tiebreak is not the same as a meaningful one. Ranking on the name is
stable and says nothing about which trade is better; a recent track record is
stable AND is about the merit of the competitor.

⚠️ WHY NOT THE NEWS SCORE (the other candidate the operator raised). Competing
intents in an election are on the **same symbol** by construction —
``gate_intents`` filters to one symbol before any election happens — and the
news score is per-SYMBOL. It would be identical for both contenders and could
never differentiate them. Recorded here so the option is not re-proposed.

⚠️ THIS IS A CHOSEN METRIC, NOT A PROVEN ONE. Recent realised PnL is evidence
about a strategy's track record; it is NOT evidence that the strategy is right
about THIS bar, and a 3-day window is short enough to be noisy. It is defended
only as strictly better than the alphabet, which is the bar it had to clear.
Replacing it with a better-supported signal is a live question — see
``BL-20260831-CONFIDENCE-SATURATES-AT-ONE-SO-HALF-OF-ARBITRATIONS-CANNOT-BE-DECIDED-ON-IT``.

RUNTIME COST IS LOAD-BEARING, NOT DECORATION. This is consulted from the
election, which runs per symbol per tick. This repo has two wedge incidents
whose shape was exactly "a per-tick cost nobody bounded" (MB-20260609-001
steady-state, BL-20260609-001 cold-start). So: ONE read-only SQLite query for
ALL strategies, TTL-cached process-wide, never per-strategy and never per-tick.

THREE STATES, NEVER COLLAPSED (``collapsed-state-guard`` discipline):
  * ``measured``   — the strategy has closed trades in the window.
  * ``no_trades``  — we looked; it has none. NOT a PnL of 0.0.
  * ``unreadable`` — we could not look (no DB, bad schema, error). Emphatically
    NOT ``no_trades``, and never a fabricated zero.

An UNMEASURED strategy sorts LAST within this tier: a competitor cannot win on
a track record it has not demonstrated. That is the same safe direction
``_election_confidence`` takes for an unreadable confidence.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# States. Kept as constants so a caller cannot typo one into silence.
MEASURED = "measured"
NO_TRADES = "no_trades"
UNREADABLE = "unreadable"

_DEFAULT_WINDOW_DAYS = 3.0
_DEFAULT_TTL_S = 300.0

# (fetched_at, {strategy: (pnl, state)}, roster_state)
_CACHE: Optional[Tuple[float, Dict[str, Tuple[float, str]], str]] = None


def _window_days() -> float:
    """Lookback in days. An unparseable value falls back to the DEFAULT, never
    to 0 — a typo must not silently reduce the tiebreak to "no history"."""
    try:
        val = float(os.environ.get("ELECTION_TRACK_RECORD_DAYS") or _DEFAULT_WINDOW_DAYS)
        return val if val > 0 else _DEFAULT_WINDOW_DAYS
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_DAYS


def _ttl_seconds() -> float:
    """Cache TTL. ``0`` disables caching (every call reads) — the debug escape
    hatch. An unparseable value falls back to the default rather than to 0, so
    a typo cannot put a SQLite read on every election of every tick."""
    raw = os.environ.get("ELECTION_TRACK_RECORD_TTL_S")
    if raw is None or raw.strip() == "":
        return _DEFAULT_TTL_S
    try:
        val = float(raw)
        return val if val >= 0 else _DEFAULT_TTL_S
    except (TypeError, ValueError):
        return _DEFAULT_TTL_S


def _load() -> Tuple[Dict[str, Tuple[float, str]], str]:
    """ONE query for every strategy. Returns (map, roster_state)."""
    try:
        from src.utils.paths import trade_journal_db_path
        db = trade_journal_db_path()
    except Exception:  # noqa: BLE001
        return {}, UNREADABLE
    if not db or not os.path.exists(db):
        return {}, UNREADABLE

    cutoff_days = _window_days()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        try:
            cur = conn.cursor()
            # `closed_at` is mixed-encoding across the journal's history
            # (space-separated CURRENT_TIMESTAMP, ISO T+offset, epoch-ms), so a
            # raw string comparison silently drops rows —
            # BL-20260730-TRADES-TIMESTAMP-FORMAT-MIXED. Compare through
            # julianday() on both sides so SQLite parses them.
            cur.execute(
                """
                SELECT strategy_name, SUM(pnl)
                  FROM trades
                 WHERE status = 'closed'
                   AND pnl IS NOT NULL
                   AND COALESCE(is_backtest, 0) = 0
                   AND strategy_name IS NOT NULL
                   AND julianday(closed_at) >= julianday('now', ?)
                 GROUP BY strategy_name
                """,  # ts-compare-ok: both sides wrapped in julianday()
                (f"-{cutoff_days} days",),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.debug("election_track_record: journal unreadable", exc_info=False)
        return {}, UNREADABLE
    except Exception:  # noqa: BLE001
        logger.debug("election_track_record: load failed", exc_info=False)
        return {}, UNREADABLE

    out: Dict[str, Tuple[float, str]] = {}
    for name, total in rows:
        try:
            out[str(name)] = (float(total), MEASURED)
        except (TypeError, ValueError):
            continue
    return out, MEASURED


def recent_pnl_map(force: bool = False) -> Tuple[Dict[str, Tuple[float, str]], str]:
    """TTL-cached ``{strategy: (pnl, state)}`` plus the roster state.

    Fail-permissive: any failure yields an empty map with ``UNREADABLE``, which
    makes every strategy tie in this tier and the election fall through to its
    next term. A tiebreak that cannot be read must never REORDER anything.
    """
    global _CACHE
    ttl = _ttl_seconds()
    now = time.monotonic()
    if not force and ttl > 0 and _CACHE is not None:
        fetched_at, cached, state = _CACHE
        if (now - fetched_at) < ttl:
            return cached, state
    data, state = _load()
    _CACHE = (now, data, state)
    return data, state


def track_record_rank(strategy: str) -> float:
    """Sort term for *strategy*: LOWER is better (the election key is a min()).

    Returns ``-pnl`` for a measured strategy, so a higher recent PnL sorts
    first. Returns ``+inf`` for ``no_trades``/``unreadable`` so an ungraded
    competitor sorts LAST within this tier — it cannot win on a track record it
    has not demonstrated.

    ⚠️ ``+inf`` is deliberately NOT ``0.0``. A zero would place an ungraded
    strategy ABOVE every strategy with a losing recent record, which asserts an
    observation nobody made — the fabricated-zero defect this repo already
    avoids in ``exposure_soak`` (null multiple, never 0.0) and
    ``conviction_arbitration`` (null qty, never 0.0).
    """
    data, state = recent_pnl_map()
    if state == UNREADABLE:
        # Nothing is graded, so this term must not reorder anything at all.
        return 0.0
    entry = data.get(str(strategy))
    if entry is None:
        return float("inf")
    pnl, st = entry
    return -pnl if st == MEASURED else float("inf")


def state_for(strategy: str) -> str:
    """The three-state grade for *strategy* — for soaks and diagnostics."""
    data, state = recent_pnl_map()
    if state == UNREADABLE:
        return UNREADABLE
    return MEASURED if str(strategy) in data else NO_TRADES


def reset_cache() -> None:
    """Test hook. Never called on the live path."""
    global _CACHE
    _CACHE = None


__all__ = [
    "MEASURED",
    "NO_TRADES",
    "UNREADABLE",
    "recent_pnl_map",
    "track_record_rank",
    "state_for",
    "reset_cache",
]
