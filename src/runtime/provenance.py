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
    "MEASURED", "ESTIMATED", "FABRICATED", "UNVERIFIED", "UNTRUSTED_BUCKETS",
    "UNMEASURED_MARKER",
    "PROVENANCE_KEYS", "MEASURED_SOURCES", "ESTIMATED_SOURCES",
    "FABRICATED_SOURCES",
    "classify", "classify_row", "is_measured", "split_counts", "coverage",
    "require_measured", "ProvenanceError",
]

# --- Buckets -----------------------------------------------------------------
MEASURED = "measured"
"""The value came from the venue or an actual recorded fill."""

ESTIMATED = "estimated"
"""The value was DERIVED from a defensible anchor — e.g. the OHLC bar covering
the recorded ``closed_at``. Much closer to truth than a stale mark, but still
not a fill: the bar says where the market was, not where THIS order filled.

Kept distinct from :data:`FABRICATED` deliberately (operator decision,
2026-07-30). Collapsing the two would lose the only signal that separates "we
reconstructed this responsibly" from "we substituted an unrelated price hours
later". It is NOT measured, and :func:`is_measured` is False for it — the
binary trust gate is unchanged."""

FABRICATED = "fabricated"
"""The value was synthesised with no defensible anchor to the close — a
mark-to-market read at an arbitrary later time, or a proration assumption. A
model output, not an observation. Never aggregate silently."""

UNTRUSTED_BUCKETS: Tuple[str, ...]
"""Every bucket that is NOT a fill — defined below once the names exist. Use
this rather than re-listing buckets at a call site."""

UNVERIFIED = "unverified"
"""No provenance was recorded, or the source is unrecognised. Deliberately NOT
``MEASURED``: absence of a provenance record is not evidence of measurement.
This is the bucket that the 247 legacy rows fall into.

Also the bucket for :data:`UNMEASURED_MARKER` — a value that was *explicitly*
declared unmeasurable. For TRUST the two are identical (neither is a
measurement), which is why they share a bucket. They differ only in
ACCOUNTABILITY, and that distinction is read from the raw source string, not the
bucket: see :data:`UNMEASURED_MARKER`."""

UNMEASURED_MARKER = "unmeasured"
"""The canonical ``pnl_source`` value meaning **"this close is real, its PnL
could not be measured, and we are saying so on the record."**

The honest terminal state the schema previously had no way to express — and its
absence is what turned ``check_db_integrity`` INV-2 into a forcing function
pointed the wrong way. INV-2 demanded a number for every closed row past the
sweep grace and never asked what KIND of number, so the only way to clear it was
to put *something* in ``pnl``; ``_sweep_local_pnl_for_unpriced`` obliged with a
mark price taken hours after the close, and the check went green on
+$247,683.78 of manufactured money while a correct, honest NULL would have
stayed red forever.

With this marker, "we don't know" becomes a *declarable* answer. INV-2 now
alerts only on SILENCE (an undeclared NULL); a declaration clears it and is
counted separately by INV-2b, so the unmeasured population stays visible and the
marker can never be used to quietly mute the check.

One spelling, defined here. Do not inline the string at a call site — a second
spelling would split the population and hide half of it."""

UNTRUSTED_BUCKETS = (ESTIMATED, FABRICATED, UNVERIFIED)

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

ESTIMATED_SOURCES = frozenset({
    # OHLC bar covering the recorded closed_at — the sanctioned reconstruction
    # for a confirmed close whose real fill was never recovered. Anchored to
    # the close TIME, unlike `local_markprice` which is simply "now".
    "candle_at_close",
    # The exit REASON (sl/tp) derived by comparing the recovered exit price to
    # the order package's bracket. A defensible derivation, but the venue never
    # said "this was a stop-out" — so it is not a measurement of the reason.
    # Its sibling value `unresolved` is deliberately absent here: it falls to
    # UNVERIFIED, which is exactly what it means.
    "price_vs_pkg_bracket",
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


#: Per-key bucket overrides — the SAME source string can mean different things
#: depending on what it is the provenance OF.
#:
#: The motivating case is a mark price. Substituting a live mark for the exit of
#: a trade that CLOSED hours earlier is fabrication: there is a true value (the
#: fill) and the mark is not it. But marking an OPEN position to the current
#: market is not fabrication at all — it is the standard, correct valuation, and
#: no truer number exists while the position is still open. Filing both under
#: :data:`FABRICATED` because they share a string would cry wolf on every open
#: position and devalue the signal for the case that actually matters.
#:
#: This changes REPORTING only, never trust: everything here is still outside
#: :data:`MEASURED`, so :func:`is_measured` stays False, :func:`require_measured`
#: still rejects it, and :func:`coverage` still counts only real measurements.
_KEY_BUCKET_OVERRIDES: Dict[str, Dict[str, str]] = {
    "unrealizedPnlSource": {
        # An open position marked to the live market — the correct valuation,
        # anchored to the current price. Not a fill, so not MEASURED.
        "markprice_local": ESTIMATED,
        "local_markprice": ESTIMATED,
        # Prop has no broker feed at all, so its uPnL is a dashboard-side mark
        # estimate (and assumes 1:1 contract value) — weaker than a broker mark,
        # but still anchored to a current price rather than invented.
        "prop_estimate": ESTIMATED,
        # Broker-reported unrealised PnL — the venue's own number.
        "broker": MEASURED,
        # The honest "we could not measure this leg" value. NOT zero, and never
        # summed as zero (see the uPnL aggregation rule in CLAUDE.md).
        "unavailable": UNVERIFIED,
    },
}


class ProvenanceError(RuntimeError):
    """Raised by :func:`require_measured` when untrusted values would be used."""


def classify(source: Any, key: Optional[str] = None) -> str:
    """Bucket a raw provenance string. Unknown/empty -> :data:`UNVERIFIED`.

    *key* selects the provenance key the string belongs to, so a value whose
    meaning depends on context resolves correctly (see
    :data:`_KEY_BUCKET_OVERRIDES` — a mark on an OPEN position is an estimate,
    the same mark stamped on a CLOSED trade's exit is a fabrication). Omitting
    *key* keeps the strict, closed-trade reading, which is the safe default: it
    can over-report fabrication, never under-report it.

    Deliberately total (never raises, never returns None) so a caller can't
    accidentally skip the check via an exception path.
    """
    s = str(source or "").strip()
    if key:
        override = _KEY_BUCKET_OVERRIDES.get(key, {}).get(s)
        if override is not None:
            return override
    if s in FABRICATED_SOURCES:
        return FABRICATED
    if s in ESTIMATED_SOURCES:
        return ESTIMATED
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
    return classify(raw, key), (raw or "(none)")


def is_measured(row: Any, key: str = "exit_price_source") -> bool:
    """True only for :data:`MEASURED`. ``UNVERIFIED`` is False — by design."""
    return classify_row(row, key)[0] == MEASURED


def split_counts(
    rows: Iterable[Any], key: str = "exit_price_source",
) -> Dict[str, int]:
    """``{measured, estimated, fabricated, unverified, total}`` over *rows*."""
    out = {MEASURED: 0, ESTIMATED: 0, FABRICATED: 0, UNVERIFIED: 0, "total": 0}
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
    bad = (counts[FABRICATED] + counts[ESTIMATED]
           + (0 if allow_unverified else counts[UNVERIFIED]))
    if bad:
        raise ProvenanceError(
            f"{context}: refusing to use {bad} untrusted value(s) for {key!r} "
            f"(measured={counts[MEASURED]} estimated={counts[ESTIMATED]} "
            f"fabricated={counts[FABRICATED]} "
            f"unverified={counts[UNVERIFIED]} of {counts['total']}). "
            f"Filter with provenance.is_measured() or report the split via "
            f"provenance.coverage() instead of aggregating blind."
        )
