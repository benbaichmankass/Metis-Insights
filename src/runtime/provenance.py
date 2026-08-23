"""Canonical provenance vocabulary for journal numbers — is this value MEASURED
or MANUFACTURED?

WHY THIS MODULE EXISTS (read before changing anything here)
-----------------------------------------------------------
On 2026-07-30 a "Bybit scalp exit leak" of −$6,358 turned out to be almost
entirely a measurement artifact. The chain:

  1. ``clients.account_closed_pnl_for_trade`` returned None for demo accounts
     (#4503, a correct fix for demo closed-pnl records mis-mapping).
     ⚠️ **AS WRITTEN THIS STEP IS STALE — it describes the code as it stood on
     2026-07-30 and must not be read as current.** The branch was NARROWED the
     same day (#8111, ``BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ``): demo
     no longer returns None outright, it resolves the exit from the exchange
     FILLS store instead. The closed-pnl *endpoint* stays untrusted for demo;
     "there is no broker truth for demo" was the over-generalisation that got
     fixed. The step is kept in its original form because it is the CHAIN THAT
     PRODUCED THE INCIDENT, and rewriting history here would make the account
     unfalsifiable — but the fix below is live, so do not act on step 1.
  2. So ``order_monitor._close_trade_from_order_status`` never recovers the real
     exit fill, leaves ``exit_price`` NULL, and pins ``exit_reason`` to
     ``reconciler_filled`` — ``_classify_broker_exit`` is downstream of a price
     the code deliberately refuses to fetch, so ``sl``/``tp`` became structurally
     unreachable on demo.
     ⚠️ **THE PnL HALF WAS FIXED; THE LABEL HALF WAS NOT, AND IS STILL LIVE**
     (measured 2026-08-22, ``BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE``).
     Once a price DOES arrive — from ``_sweep_pending_pnl_from_bybit`` for a
     broker record, or from the fills path above — ``exit_price`` and ``pnl``
     are written and **``exit_reason`` is never revisited**: no writer re-runs
     ``_classify_broker_exit``. So the label stays frozen at the one moment the
     answer could not be known, on REAL-MONEY rows as well as demo. Measured on
     the rows where broker truth can adjudicate: **91 of 155 (58.7%)** closes
     labelled ``reconciler_filled`` had actually reached a declared bracket
     level, and **181 of 181** mislabelled rows carry no ``exit_reason_source``
     note key at all — the marker the classifying branch stamps — which is a
     100% signature that none of them ever reached the classifier.
  3. Six hours later ``_sweep_local_pnl_for_unpriced`` substitutes
     ``last_mark_price()`` — the market price at sweep time — as ``exit_price``
     and books ``pnl`` from it.

Every one of those steps is individually correct and individually justified by a
real prior incident. There is no wrong line of code. That is precisely why
repeated line-by-line audits returned clean while the defect kept producing
wrong decisions: it lives at the seams, not in any component.

The blast radius, **and the population it is measured over** — a rule this
module's own first measurement pass produced the hard way, because the headline
figure changes SIGN depending on which rows you count (see ``CLAUDE.md`` §
"Number provenance" for the full table):

* *closed, non-backtest,* ``pnl NOT NULL`` — the decision population any
  consumer actually aggregates: **829 rows, 206 fabricated, −$36,018.60**,
  concentrated in ``bybit_1`` (152/323) and ``bybit_portfolio`` (11/12).
* *any status, incl. backtest*: **845 rows, 222 fabricated, +$247,683.78** —
  the widely-quoted figure, dominated by **4 ``orphaned`` ``ib_paper`` rows
  carrying +$284,084.92** (a stale mark times a futures multiplier, on rows that
  appear in neither Positions nor Trades).

Both are correct. What reproduces across both is the TREND: fabricated share of
closed trades 0.0% (May) → 23.7% (June) → **65.3% (July)**. Quote the population
or don't quote the number — including when the number is ours.

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
    "FABRICATED_SOURCES", "EXIT_LABEL_REFUSED_UNMEASURED",
    "classify", "classify_row", "classify_pnl", "is_measured",
    "pnl_is_trustworthy",
    "split_counts", "coverage",
    "require_measured", "ProvenanceError",
]

# --- Buckets -----------------------------------------------------------------
MEASURED = "measured"
"""The value came from the venue or an actual recorded fill.

**SCOPE — this grades a value's SOURCE, not its OWNER.** ``MEASURED`` says the
number came from the venue; it does NOT say the number belongs to the row it is
stored on, and no bucket in this module can. Misattribution is an orthogonal
axis with no field here, and a per-row grade cannot detect it in principle:
duplication is a property of a SET of rows.

This is not hypothetical. Measured 2026-08-12 (trainer-diag #8823, n=29 of 29):
every row in the netted-duplicate population carried ``bybit_closed_pnl`` and so
classified ``MEASURED``, and :func:`pnl_is_trustworthy` admitted all 29 — because
the grade was CORRECT. Bybit really did return that realized-pnl record; the
defect was that ONE netted close's record was written to N sibling rows at full
magnitude, so each row held genuine broker provenance and the wrong owner. Every
provenance sweep over those rows passed them, rightly, for months. What surfaced
them was an ARITHMETIC impossibility — R = −99.5 on a 0.012 BTC position, implying
a ~$247,000 move — not a provenance check.

So do not read a ``MEASURED`` grade as "this value was checked for ownership".
Cross-row integrity is a separate obligation
(``BL-20260812-MEASURED-PROVENANCE-CANNOT-SEE-MISATTRIBUTION``)."""

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

#: How an ABSENT source key renders. Not a source value — the difference
#: between "nothing was recorded" and "something unrecognised was recorded" is
#: exactly the accountability distinction this module documents, so the two
#: must never share a rendering.
_ABSENT_RAW = "(none)"

UNMEASURED_MARKER = "unmeasured"
"""The canonical ``pnl_source`` value meaning **"this close is real, its PnL
could not be measured, and we are saying so on the record."**

The honest terminal state the schema previously had no way to express — and its
absence is what turned ``check_db_integrity`` INV-2 into a forcing function
pointed the wrong way. INV-2 demanded a number for every closed row past the
sweep grace and never asked what KIND of number, so the only way to clear it was
to put *something* in ``pnl``; ``_sweep_local_pnl_for_unpriced`` obliged with a
mark price taken hours after the close, and the check went green on
the all-status +$247,683.78 of manufactured money while a correct, honest NULL would have
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
    # IBKR per-execution truth: CommissionReport.realizedPNL, read back from
    # the exchange-fills store (src.runtime.exchange_fills_ib). A venue-reported
    # fill, so MEASURED — and strictly better than `candle_at_close`, which is
    # all IB could otherwise get (IBKR historical-candle coverage is 0%).
    "ib_execution",
    # Exit price built from ACTUAL exchange fills on a venue that serves no
    # per-fill realised PnL (Bybit, Alpaca equities). The arithmetic downstream
    # is still local compute — `pnl_source` says so — but the exit PRICE is a
    # recorded fill rather than a mark read at sweep time, and the price is what
    # `classify_pnl` grades. This is the source that closes the 96% of fabricated
    # rows sitting on accounts whose fills were already being collected and
    # discarded (BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ).
    "exchange_fill",
    # A real fill the bot itself recorded at close time.
    "recorded_exit_price", "operator_flatten_fill", "verdict",
})

ESTIMATED_SOURCES = frozenset({
    # OHLC bar covering the recorded closed_at — the sanctioned reconstruction
    # for a confirmed close whose real fill was never recovered. Anchored to
    # the close TIME, unlike `local_markprice` which is simply "now".
    "candle_at_close",
    # A price taken from the MIRROR account's fill (bybit_portfolio <- bybit_2,
    # alpaca_portfolio <- alpaca_live). ESTIMATED, deliberately NOT MEASURED.
    # The paper book mirrors the live book's SETUPS, so the sibling's fill is a
    # defensible anchor for where this order would have gone — but it remains an
    # inference about a DIFFERENT account's execution, not a fill of this order.
    # Capacity between the books differs (the operator's own caveat when
    # proposing it, 2026-07-31), which is exactly why it is not a measurement.
    # Same bucket as `candle_at_close`, for the same reason: responsibly
    # reconstructed, still not a fill. Promoting it to MEASURED would re-import
    # fabrication wearing a better label.
    "mirror_account_fill",
    # The exit REASON (sl/tp) derived by comparing the recovered exit price to
    # the order package's bracket. A defensible derivation, but the venue never
    # said "this was a stop-out" — so it is not a measurement of the reason.
    # Its sibling value `unresolved` is deliberately absent here: it falls to
    # UNVERIFIED, which is exactly what it means.
    "price_vs_pkg_bracket",
    # The SAME derivation, but performed against an ESTIMATED exit price
    # (`candle_at_close`) rather than a broker fill — an inference on an
    # inference. It shares this bucket rather than falling to FABRICATED
    # because the price it reads is genuinely anchored to the recorded
    # `closed_at`; what it must NOT do is read as the stronger
    # `price_vs_pkg_bracket`, which is why it is a distinct value at all.
    # Written by order_monitor._sweep_local_pnl_for_unpriced
    # (BL-20260823-EXIT-LABEL-FROZEN-ON-THE-ANCHORED-PRICE-PATH).
    "price_vs_pkg_bracket_est_price",
})

#: `exit_reason_source` value meaning: the classifier was reached, LOOKED, and
#: DECLINED to label — because the exit price it would have compared against the
#: bracket is FABRICATED (`local_markprice` is the market at SWEEP time, hours
#: after the exit; `netted_duplicate_unattributed` is one record's magnitude
#: copied onto N rows). Deriving sl/tp from either would manufacture a verdict
#: out of unrelated price action.
#:
#: It is a REFUSAL, not a bucket, so it is deliberately in none of the three
#: source sets — a refusal is not a grade of the value, it is the statement that
#: no value was produced. It exists as a named constant because the alternative
#: is a silent skip, and a silent skip is indistinguishable from the classifier
#: never having run — which is exactly the 100% signature that made this whole
#: defect class readable (see `exit_reason_source`'s absence semantics).
EXIT_LABEL_REFUSED_UNMEASURED = "refused_unmeasured_price"

FABRICATED_SOURCES = frozenset({
    # entry x mark x qty, where the mark is `last_mark_price()` at SWEEP time —
    # for a CONFIRMED CLOSE this is the market hours after the exit, not the
    # exit. This single source accounts for the fabricated totals above.
    "local_markprice",
    "markprice_local",
    # A netted record's economics split across rows by qty share — a modelling
    # assumption about attribution, not an observed per-row fill.
    "netted_prorated",
    # The UN-split case, and strictly worse than `netted_prorated`: one netted
    # broker record's FULL magnitude written onto N sibling journal rows, so the
    # same figure lands on rows whose quantities differ by orders of magnitude
    # (measured 2026-08-06: pnl -2970.99 on rows of qty 0.012 / 0.717 / 0.728,
    # the first implying a ~$247,000 BTC move). Deliberately NOT spelled with the
    # `_prorated` suffix — nothing was prorated, and calling it that would claim
    # an attribution assumption the writer never made. Applied retroactively by
    # scripts/ops/mark_netted_duplicate_pnl.py; the forward-side writer fix is
    # order_monitor._prorate_netted_broker_pnl.
    # BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS.
    "netted_duplicate_unattributed",
    # Dashboard-side mark estimate for prop (no broker feed exists at all).
    "prop_estimate",
})

#: Suffix marking a value prorated across netted sibling rows by qty share.
#: See :func:`classify` — any source carrying it is FABRICATED regardless of how
#: measured the underlying broker record was, because the SPLIT is an assumption.
_PRORATED_SUFFIX = "_prorated"


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
    # A `_prorated` suffix means a netted record's economics were split across
    # sibling rows by qty share. That is a modelling assumption about
    # ATTRIBUTION, not an observed per-row fill — for exactly the reason
    # `netted_prorated` is in FABRICATED_SOURCES — and it stays true however
    # measured the underlying record was. Handled as a suffix, not an
    # enumeration, because the base varies per reader
    # (`bybit_closed_pnl_prorated`, `ib_execution_prorated`, ...): before this,
    # `bybit_closed_pnl_prorated` fell through to UNVERIFIED, so a prorated
    # number read as merely-unrecorded rather than manufactured.
    if s.endswith(_PRORATED_SUFFIX) and len(s) > len(_PRORATED_SUFFIX):
        return FABRICATED
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
    return classify(raw, key), (raw or _ABSENT_RAW)


#: Bucket severity, worst first — used to combine evidence from several keys.
_SEVERITY = (FABRICATED, ESTIMATED, MEASURED)


def classify_pnl(row: Any) -> Tuple[str, str]:
    """Bucket a row's ``pnl`` using BOTH provenance keys. Returns ``(bucket, why)``.

    **Why this is not just ``classify_row(row, "pnl_source")``.** Measured
    against the live journal on 2026-07-30 (829-row closed population):

    ===================  ====================================================
    ``pnl_source``       ``(none)`` × 576, ``local_compute`` × 253 — and nothing else
    ``exit_price_source``  ``bybit_closed_pnl`` × 324, ``local_markprice`` × 206,
                         ``bybit_closed_pnl_rebuild`` × 131, ``(none)`` × 119,
                         ``recorded_exit_price`` × 46, …
    ===================  ====================================================

    So ``pnl_source`` alone is nearly information-free — keying coverage on it
    would report **0.0 for every window**, including the 504 rows whose exit
    price is genuine broker truth. Technically true, operationally useless, and
    a metric nobody can act on is one everybody learns to ignore — the precise
    failure mode this module exists to prevent.

    The rule: classify BOTH keys, discard the ones that say nothing
    (:data:`UNVERIFIED` — absent or unrecognised), and take the **worst
    remaining** bucket. ``pnl`` is only as trustworthy as the weakest evidence
    behind it: a locally-computed PnL derived from a mark-substituted exit price
    is fabricated no matter how the arithmetic was done. If neither key says
    anything, the result is ``UNVERIFIED`` — never promoted to measured.

    ``local_compute`` is deliberately NOT in any source set. It describes the
    *arithmetic*, not the *evidence*: its trustworthiness is entirely inherited
    from the exit price it was computed from, so leaving it unrecognised makes
    this function defer to ``exit_price_source``, which is exactly right.

    Against that live population this yields 504/829 = **60.8%** measured
    coverage, 206 fabricated, 119 unverified — numbers an operator can act on.
    """
    buckets = {}
    unrecognised = []
    for key in ("pnl_source", "exit_price_source"):
        bucket, raw = classify_row(row, key)
        if bucket != UNVERIFIED:
            buckets[bucket] = f"{key}={raw}"
        elif raw and raw != _ABSENT_RAW:
            # `classify_row` renders an absent key as the sentinel "(none)", so a
            # bare truthiness test counts ABSENCE as an unrecognised source --
            # which reverses this fix into the same collapse it removes. Caught by
            # testing the all-absent case rather than only the interesting one.
            unrecognised.append(f"{key}={raw}")
    for bucket in _SEVERITY:
        if bucket in buckets:
            return bucket, buckets[bucket]
    # ⚠️ THE BUCKET IS RIGHT; THE REASON USED TO BE A FALSE STATEMENT.
    # This returned "(no provenance on either key)" unconditionally, discarding
    # the raw strings — which contradicted this module's own documented contract
    # that "no record" and "explicitly declared unmeasurable" are told apart by
    # READING THE RAW SOURCE STRING (see UNVERIFIED / UNMEASURED_MARKER above).
    # Measured on trade 4164 (bybit_portfolio XRPUSDT, closed reconciler_filled):
    # order_monitor.py:6074 deliberately stamps
    # exit_price_source='entry_order_avg_price_unreliable' to say WHY the exit
    # could not be priced, and classify_pnl answered "no provenance on either
    # key". An auditor reading that goes looking for a missing writer that is not
    # missing — the wasted-investigation shape, caused by our own label.
    # The bucket stays UNVERIFIED (for TRUST the two are identical, exactly as
    # documented); only ACCOUNTABILITY is restored.
    if unrecognised:
        return UNVERIFIED, "unrecognised source: " + " · ".join(unrecognised)
    return UNVERIFIED, "(no provenance on either key)"


def is_measured(row: Any, key: str = "exit_price_source") -> bool:
    """True only for :data:`MEASURED`. ``UNVERIFIED`` is False — by design."""
    return classify_row(row, key)[0] == MEASURED


def pnl_is_trustworthy(notes: Any) -> bool:
    """True when a row's ``pnl`` rests on a MEASURED or ESTIMATED exit.

    Takes the raw ``trades.notes`` value (JSON string / mapping / None) rather
    than a row, because the dataset builders hold the notes blob directly and
    :func:`classify_pnl` needs a decoded mapping — handed a raw JSON *string*
    it reads ``row['pnl_source']`` off a ``str``, catches the ``TypeError``,
    and returns :data:`UNVERIFIED` for every row. That failure is silent and
    total, so the decode belongs here, once, and not in each caller.

    **This lives here, not in a dataset family, on purpose.** Two builders
    (``setup_labels``, ``trade_outcomes``) need the same predicate; a second
    copy is precisely how the two halves drift apart, which is the defect class
    this whole module exists to close.

    **Fail-CLOSED.** An unparseable or absent notes blob is untrustworthy, not
    waved through, and :data:`UNVERIFIED` is excluded alongside
    :data:`FABRICATED`: *no provenance recorded is not evidence of
    measurement*. For a training **label** the burden of proof belongs on the
    data — a permissive default is exactly what let fabrication into the
    labels in the first place.

    Note this is a **label**-grade predicate, deliberately stricter than
    :func:`is_measured`: it admits ESTIMATED (a candle anchored to the close is
    a defensible outcome) while refusing anything with no anchor at all.
    """
    bucket, _why = classify_pnl(_decode_notes(notes))
    return bucket in (MEASURED, ESTIMATED)


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
