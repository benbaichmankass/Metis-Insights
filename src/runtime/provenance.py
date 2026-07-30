"""Canonical provenance vocabulary for journal numbers — is this value MEASURED
or MANUFACTURED?

WHY THIS MODULE EXISTS (read before changing anything here)
-----------------------------------------------------------
On 2026-07-30 a "Bybit scalp exit leak" of −$6,358 turned out to be almost
entirely a measurement artifact. The chain:

  1. ``clients.account_closed_pnl_for_trade`` returns None for demo accounts
     (#4503, a correct fix for demo closed-pnl records mis-mapping).
  2. So ``order_monitor._close_trade_from_order_status`` never recovers the real
     exit fill, leaves ``exit_price`` NULL, and pins ``exit_reason`` to
     ``reconciler_filled`` — ``_classify_broker_exit`` is downstream of a price
     the code deliberately refuses to fetch, so ``sl``/``tp`` became structurally
     unreachable on demo.
  3. Six hours later ``_sweep_local_pnl_for_unpriced`` substitutes
     ``last_mark_price()`` — the market price at sweep time — as ``exit_price``
     and books ``pnl`` from it.

Every one of those steps is individually correct and individually justified by a
real prior incident. There is no wrong line of code. That is precisely why
repeated line-by-line audits returned clean while the defect kept producing
wrong decisions: it lives at the seams, not in any component.

The blast radius when it was finally measured: 226 closed rows carrying
+$247,683.78 of ``local_markprice`` PnL (the bulk of it ``ib_paper``, where a
stale mark is multiplied by a futures contract value), 247 more rows with no
provenance recorded at all, and a fabricated share of closed trades running
0.0% (May) → 30.5% (June) → **64.9% (July)**.

THE ACTUAL ROOT CAUSE, AND WHAT THIS MODULE FIXES
-------------------------------------------------
The journal already recorded provenance — ``notes.exit_price_source`` and
``notes.pnl_source`` — and **nothing read it**. ``exit_price_source`` was
written in 12 files and branched on in exactly one, for an unrelated value; the
entire ``ml/`` tree referenced it zero times. A signal that is written and never
read is indistinguishable from one that does not exist, except that it is worse:
reviewers see the field and assume something acts on it.

Meanwhile the codebase already knew the principle. ``/performance`` reports
``rCoverage``/``rTradeCount`` for exactly this reason —
*"transparency, never a raw-pnl fallback"* (``performance.py:404``). The derived
R-metric was correctly protected from fabrication while the base ``pnl`` it is
computed from was silently fabricated.

So this module exists to make provenance **impossible to ignore**:

  * ONE vocabulary, defined once. Do not re-derive it, do not inline a string
    literal, do not write another per-incident ``exclude_*`` predicate. Four of
    those already exist (``exclude_reduce_leg`` / ``reconciler`` / ``superseded``
    / ``reset_flat``), each a patch for a different flavour of "this number
    isn't real", and collectively they still missed the general case.
  * An explicit ``UNVERIFIED`` bucket that is NEVER folded into ``MEASURED``.
    Not recording provenance is not evidence of measurement.
  * :func:`coverage` so every aggregate can report its honest denominator, the
    way ``rCoverage`` already does.
  * :func:`require_measured`, which raises rather than quietly averaging
    manufactured numbers, for callers that must not guess.

Enforcement lives in ``scripts/check_provenance_consumers.py`` (CI job
``provenance-consumer-guard``), which fails the build when a provenance key
gains a writer but no consumer — the same shape as the existing
``canonical-db-resolver`` / ``env-gate-guard`` / ``silent-empty-guard`` guards.
Documentation did not prevent this bug; a guard can.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "MEASURED", "FABRICATED", "UNVERIFIED",
    "PROVENANCE_KEYS", "MEASURED_SOURCES", "FABRICATED_SOURCES",
    "classify", "classify_row", "is_measured", "split_counts", "coverage",
    "require_measured", "ProvenanceError",
]

# --- Buckets -----------------------------------------------------------------
MEASURED = "measured"
"""The value came from the venue or an actual recorded fill."""

FABRICATED = "fabricated"
"""The value was synthesised by the bot (mark-to-market, proration, estimate).
It is a model output, not an observation. Never aggregate silently."""

UNVERIFIED = "unverified"
"""No provenance was recorded, or the source is unrecognised. Deliberately NOT
``MEASURED``: absence of a provenance record is not evidence of measurement.
This is the bucket that the 247 legacy rows fall into."""

# --- The keys this module governs -------------------------------------------
# Adding a key here without adding a consumer will FAIL the provenance-consumer
# guard. That is intentional: it is the mechanism that stops the write-only
# pattern from being recreated.
PROVENANCE_KEYS: Tuple[str, ...] = (
    "exit_price_source",
    "pnl_source",
    "exit_reason_source",
    "close_exec_type",
    "unrealizedPnlSource",
)

MEASURED_SOURCES = frozenset({
    # Broker truth.
    "bybit_closed_pnl", "bybit_closed_pnl_rebuild", "bybit_closed_pnl_backfill",
    "exchange", "broker_truth",
    # A real fill the bot itself recorded at close time.
    "recorded_exit_price", "operator_flatten_fill", "verdict",
})

FABRICATED_SOURCES = frozenset({
    # entry x mark x qty, where the mark is `last_mark_price()` at SWEEP time —
    # for a CONFIRMED CLOSE this is the market hours after the exit, not the
    # exit. This single source accounts for the +$247,683.78 above.
    "local_markprice",
    "markprice_local",
    # A netted record's economics split across rows by qty share — a modelling
    # assumption about attribution, not an observed per-row fill.
    "netted_prorated",
    # Dashboard-side mark estimate for prop (no broker feed exists at all).
    "prop_estimate",
})


class ProvenanceError(RuntimeError):
    """Raised by :func:`require_measured` when untrusted values would be used."""


def classify(source: Any) -> str:
    """Bucket a raw provenance string. Unknown/empty -> :data:`UNVERIFIED`.

    Deliberately total (never raises, never returns None) so a caller can't
    accidentally skip the check via an exception path.
    """
    s = str(source or "").strip()
    if s in FABRICATED_SOURCES:
        return FABRICATED
    if s in MEASURED_SOURCES:
        return MEASURED
    return UNVERIFIED


def _decode_notes(notes: Any) -> Dict[str, Any]:
    if isinstance(notes, Mapping):
        return dict(notes)
    if isinstance(notes, (str, bytes)) and notes:
        try:
            decoded = json.loads(notes)
            if isinstance(decoded, dict):
                return decoded
        except (ValueError, TypeError):
            return {}
    return {}


def classify_row(row: Any, key: str = "exit_price_source") -> Tuple[str, str]:
    """Return ``(bucket, raw_source)`` for a trade row (dict / sqlite3.Row).

    Reads ``row['notes']`` JSON, falling back to a top-level column of the same
    name so a future typed column works without touching callers.
    """
    if key not in PROVENANCE_KEYS:
        raise ValueError(
            f"{key!r} is not a declared provenance key; add it to "
            f"PROVENANCE_KEYS (and give it a consumer) rather than passing an "
            f"ad-hoc string."
        )
    raw = ""
    try:
        raw = str(row[key] or "") if row[key] is not None else ""
    except (KeyError, IndexError, TypeError):
        raw = ""
    if not raw:
        try:
            notes = row["notes"]
        except (KeyError, IndexError, TypeError):
            notes = None
        raw = str(_decode_notes(notes).get(key) or "")
    return classify(raw), (raw or "(none)")


def is_measured(row: Any, key: str = "exit_price_source") -> bool:
    """True only for :data:`MEASURED`. ``UNVERIFIED`` is False — by design."""
    return classify_row(row, key)[0] == MEASURED


def split_counts(
    rows: Iterable[Any], key: str = "exit_price_source",
) -> Dict[str, int]:
    """``{measured, fabricated, unverified, total}`` over *rows*."""
    out = {MEASURED: 0, FABRICATED: 0, UNVERIFIED: 0, "total": 0}
    for row in rows:
        out[classify_row(row, key)[0]] += 1
        out["total"] += 1
    return out


def coverage(counts: Mapping[str, int]) -> Optional[float]:
    """Measured fraction in ``[0,1]``, or None when there is nothing to cover.

    The PnL analogue of ``/performance``'s ``rCoverage``. None (not 0.0) on an
    empty window, so "no trades" stays distinguishable from "no trade was
    measured" — the exact distinction whose absence made this bug invisible.
    """
    total = int(counts.get("total") or 0)
    if total <= 0:
        return None
    return round(int(counts.get(MEASURED) or 0) / total, 4)


def require_measured(
    rows: Iterable[Any],
    *,
    key: str = "exit_price_source",
    context: str = "aggregate",
    allow_unverified: bool = False,
) -> None:
    """Raise :class:`ProvenanceError` if *rows* contain untrusted values.

    For callers that must not silently average manufactured numbers — a
    promotion gate, a risk decision, an ML label set. Fails LOUD rather than
    returning a plausible wrong number, which is the failure mode that let a
    −$6,358 "leak" and a +$247k paper "profit" both go unquestioned.

    ``allow_unverified=True`` tolerates legacy rows with no provenance record
    but still rejects known-fabricated ones.
    """
    counts = split_counts(rows, key)
    bad = counts[FABRICATED] + (0 if allow_unverified else counts[UNVERIFIED])
    if bad:
        raise ProvenanceError(
            f"{context}: refusing to use {bad} untrusted value(s) for {key!r} "
            f"(measured={counts[MEASURED]} fabricated={counts[FABRICATED]} "
            f"unverified={counts[UNVERIFIED]} of {counts['total']}). "
            f"Filter with provenance.is_measured() or report the split via "
            f"provenance.coverage() instead of aggregating blind."
        )
