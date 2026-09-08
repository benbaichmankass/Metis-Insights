"""R-multiple provenance — was this row's R computed from the trade's INITIAL stop?

The direct sibling of :mod:`src.runtime.provenance`, one derivative up. That
module asks whether a row's ``pnl`` — the R **numerator** — is a MEASUREMENT or
a MANUFACTURE. This one asks the same question of the R **denominator**: the
risk basis ``|entry_price - stop_loss|``.

WHY IT EXISTS
-------------
``trades.stop_loss`` does not hold the trade's INITIAL stop. It holds the
**current** one. ``src/runtime/order_monitor.py::_apply_update`` mirrors every
confirmed trailing amend onto the row::

    trade_sync["stop_loss"] = updates["sl"]
    db.update_trade(int(trade_id), trade_sync)

That write is CORRECT for what it was built for — ``/api/bot/positions`` reads
``trades.stop_loss`` to show the operator where the stop is *now*, and before
the mirror landed every operator surface showed a stale entry-time level
(the fix `order_monitor._apply_update`'s own comment block
records as 'part 2' of the XRP SL-spam work, 2026-07-22). The same write is destructive for R, because R
is defined against the risk the trade was **entered** with. A stop trailed to
breakeven-plus leaves ``|entry - stop|`` near zero, and ``pnl / risk`` explodes.

This module does not fix that. It makes it VISIBLE, so a consumer can refuse a
number rather than silently read a corrupt one.

THE STATES — four, never collapsed
----------------------------------
``CONTAMINATED``
    PROVEN not to be the initial stop. The sole proof is arithmetic and
    threshold-free: the stored stop sits on the **wrong side of entry**. A long
    whose stop is above its entry, or a short whose stop is below it, cannot be
    an initial stop — that is not a risk level, it is a locked-in profit. The
    row's R is computed from a distance that never was the trade's risk.

``CONFIRMED_INITIAL``
    The stored stop is on the correct side AND its distance from entry matches
    an **independent** signal-time record of the initial risk — the strategy's
    own ``risk_per_unit``, written into ``order_packages.meta`` when the signal
    was built and never touched by ``_apply_update`` (which writes only
    ``sl``/``tp``). Both halves are required: matching the declared distance
    while sitting on the wrong side is not a confirmation, it is a
    contradiction, and ``CONTAMINATED`` takes precedence.

``UNVERIFIED``
    **We could not look.** The stop is side-plausible, and either no declared
    initial-risk record exists for the row, or one exists and disagrees.
    ⚠️ THIS IS NOT "CLEAN". It is the largest bucket by construction, and a
    consumer that renders it as verified has reintroduced the bug: a stop
    trailed to *just short of* entry is side-plausible and just as wrong as one
    trailed past it. It is simply not PROVABLE from the stored row alone.

``NO_BASIS``
    There is no R to grade. ``entry_price`` / ``stop_loss`` / ``qty`` is
    missing, or the computed risk is non-positive — exactly the rows
    :func:`src.web.api._clean_trades.r_multiple` already returns ``None`` for
    and ``rCoverage`` already excludes. It is a state, not an absence, so the
    four buckets **sum to the population by construction** and the partition is
    checkable rather than trusted.

WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------
⚠️ **THIS SECTION READ "It does not exclude anything from any aggregate" UNTIL
2026-09-06 — do not re-quote that.** It was true of the four GRADING states
above and it is still true of them: :func:`classify_r` filters nothing, and
``rProvenance`` reports over the whole population.

It stopped being true of the module as a whole when :func:`initial_risk_usd`
landed (MI-144). Its reasoning — *silently dropping the contaminated rows would
convert a visible-wrong number into an invisible-wrong one over an unstated
population* — is correct, and the operative word is **silently**. The escape it
always left open is its own last clause, *"publish the count; let the consumer
decide"*: no consumer COULD decide, because ``expectancyR`` was itself the
number being consumed, by the promotion gates.

MEASURED 2026-09-06 (live journal via ``/api/bot/db/table/{trades,
order_packages}``, 5518 + 4435 rows; the reproduction matched the endpoint's own
totals first, as a positive control): the 30d real-money window published
``expectancyR +0.9818`` on a window that LOST money (``totalPnl -3.6266``,
``profitFactor 0.9507``), with 12 of 39 rows (30.8%) carrying 117.1% of that R.
Whole journal, n=1287: 104 contaminated rows — **8.1%** — carried **96.6%** of
``totalR``. Single-row max: ``R = +3672.3``.

So a PROVABLY impossible risk is now REFUSED, and the refusal is **declared**
rather than silent: the basis is published per row and counted per window
(``rBasis.refusedWrongSide``), and ``rCoverage`` falls correspondingly, so the
population is stated at the point of reading. A refusal counts in neither the R
numerator nor its denominator — the discipline a missing stop has always had.
⚠️ And it is the LAST resort: a wrong-side row carrying a declared
``risk_per_unit`` is COMPUTED on that record, never refused.

*It does not treat a disagreement with the declared risk as PROOF.* Measured
over the live journal (see :data:`DISAGREEMENT_RATIO_BAR`), the ratio
``declared_risk / stored_distance`` has a dense mass just above 1.0 whose cause
this module's author did not establish — it may be trailing, it may be a basis
or rounding offset. Calling that mass contaminated would be an inference wearing
a measurement's clothes. The RATIO is published as a number; only the bar-
crossing COUNT is reported, and it is reported BESIDE the four states rather
than folded into them.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

# ── the four states ────────────────────────────────────────────────────────
R_CONTAMINATED = "contaminated"
R_CONFIRMED_INITIAL = "confirmed_initial"
R_UNVERIFIED = "unverified"
R_NO_BASIS = "no_basis"

R_STATES = (R_CONTAMINATED, R_CONFIRMED_INITIAL, R_UNVERIFIED, R_NO_BASIS)

# Relative tolerance for "the stored distance MATCHES the declared initial
# risk". Tight on purpose: this is the CONFIRMING direction, so a loose bar
# would hand out confirmations the evidence does not support. A row that misses
# it lands in UNVERIFIED (we could not look), never in CONTAMINATED — a
# disagreement is not a proof.
CONFIRM_REL_TOL = 1e-4

# The bar at which a stored stop is REPORTED as materially tighter than the
# trade's declared initial risk. CHOSEN, not tuned, and it gates a REPORTED
# COUNT rather than a verdict.
#
# Basis — MEASURED, live journal copy `/home/ubuntu/ict-trading-bot/data/
# trade_journal.db` on the trainer VM (mtime 2026-09-02T04:28:35Z,
# max(created_at) 2026-09-02T04:11:21Z; trader serving sha 2c7ae605).
# Population: trades with status='closed' AND pnl IS NOT NULL AND
# is_backtest=0 AND stop_loss/entry_price NOT NULL, n=1325, of which 682 carry
# a declared `risk_per_unit`.
#
# ⚠️ STATE THE POPULATION — the two side-populations have COMPLETELY DIFFERENT
# ratio distributions, and pooling them (which an earlier pass of this
# measurement did) produces a number that describes neither:
#
#   CORRECT-SIDE rows, n=456: median 1.0000 (exactly) · p75 1.0262 · p90 1.1411
#     · p95 1.3370 · max 211.7. And 111 of them (24.3%) read BELOW 0.99 — the
#     stored stop WIDER than declared, which trailing cannot produce. So the
#     dense mass at ~1.0 is two-sided basis/rounding noise, NOT a trail, and
#     calling it contamination would be an inference wearing a measurement's
#     clothes. At this bar, 12 of 456 (2.6%) cross.
#   WRONG-SIDE rows,   n=226: median 1.0353 · p75 5.3117 · p90 17.9519 ·
#     p95 62.5968 · max 3014.2. At this bar, 91 of 226 (40.3%) cross.
#
# That the two populations separate this cleanly is INDEPENDENT corroboration
# that the side test discriminates rather than merely fires.
#
# The bar sits above the correct-side p90 and far below its max — clear of the
# noise mass, inside the tail that rounding cannot explain. The 12 correct-side
# rows that cross it are the ones the side test CANNOT SEE, which is exactly
# why this count is published beside the states instead of being folded away.
DISAGREEMENT_RATIO_BAR = 2.0

_LONG = ("buy", "long")
_SHORT = ("sell", "short")


def _num(value: Any) -> Optional[float]:
    """Coerce to a finite float, or None. Never fabricates a zero."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return f


def stop_is_wrong_side(direction: Any, entry_price: Any, stop_loss: Any) -> Optional[bool]:
    """Is the stored stop on the wrong side of entry for this direction?

    ``True``  — a long with its stop ABOVE entry, or a short with it BELOW.
                Impossible for an initial stop.
    ``False`` — the stop is on the risk side (or exactly at entry, which is a
                zero-risk row and is graded ``NO_BASIS`` by the classifier, not
                here).
    ``None``  — **we could not look**: a missing price, or a direction string
                this module does not recognise. Never coerced to ``False``; a
                caller that reads an unreadable direction as "side is fine" has
                collapsed the two states this return value exists to separate.
    """
    entry = _num(entry_price)
    stop = _num(stop_loss)
    if entry is None or stop is None:
        return None
    d = str(direction or "").strip().lower()
    if d in _LONG:
        return stop > entry
    if d in _SHORT:
        return stop < entry
    return None


def declared_initial_risk(package_meta: Any) -> Optional[float]:
    """The signal-time ``risk_per_unit`` from an ``order_packages.meta`` blob.

    This is the INDEPENDENT record: the strategy's signal builder writes it once
    when the package is created (``src/units/strategies/*.py`` — donchian,
    pullback, ict_scalp, turtle_soup, squeeze, fade, fvg_range, hf_*), and
    ``order_monitor._apply_update`` writes only ``sl``/``tp``, so a trailing
    amend cannot reach it.

    Accepts a JSON string or an already-decoded mapping. Returns ``None`` — not
    ``0.0`` — when the blob is absent, unparseable, carries no
    ``risk_per_unit``, or carries a non-positive one. A zero risk is not a
    reading; it is the absence of one.
    """
    meta: Any = package_meta
    if isinstance(meta, (str, bytes, bytearray)):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError):
            return None
    if not isinstance(meta, Mapping):
        return None
    risk = _num(meta.get("risk_per_unit"))
    if risk is None or risk <= 0:
        return None
    return risk


def disagreement_ratio(
    entry_price: Any, stop_loss: Any, package_meta: Any
) -> Optional[float]:
    """``declared_initial_risk / |entry - stop|``, or ``None`` when either side
    is unavailable.

    Greater than 1 means the STORED stop is TIGHTER than the risk the trade
    declared at entry — the trailing signature, and the direction in which R is
    INFLATED (a smaller denominator). Less than 1 means the stored stop is
    WIDER than the declared risk, which trailing does not produce and which this
    module does not explain; it is returned honestly rather than clamped.
    """
    declared = declared_initial_risk(package_meta)
    entry = _num(entry_price)
    stop = _num(stop_loss)
    if declared is None or entry is None or stop is None:
        return None
    dist = abs(entry - stop)
    if dist <= 0:
        return None
    return declared / dist


def classify_r(row: Mapping) -> Tuple[str, str]:
    """Grade one closed-trade row. Returns ``(state, reason)``.

    Row keys consulted (all optional; a missing one degrades toward
    ``NO_BASIS`` / ``UNVERIFIED``, never toward a confirmation):
    ``direction``, ``entry_price``, ``stop_loss``, ``qty`` (or
    ``position_size``), and ``package_meta`` (or ``pkg_meta`` / ``meta``) —
    the raw ``order_packages.meta`` blob.

    ⚠️ **The side test alone MISFIRES on a direction-mirrored row, and that is
    checked for rather than assumed.** Measured on the live journal (2026-09-02,
    n=1325), 34 rows matched their declared initial risk to within 1e-4 AND sat
    on the wrong side of entry — a contradiction between the two instruments.
    Resolved, not smoothed over: all 34 are ``setup_type='intent_reduce'``, and
    ``trades.direction`` is the OPPOSITE of ``order_packages.direction`` on
    every one (108 rows disagree that way overall). That is correct by design —
    a reduce leg's own direction is the closing side, while its SL/TP are
    inherited from the ORIGINAL position — so it is the SIDE TEST'S INPUT that
    is unreliable there, not evidence of a trail.

    The discriminator is proof-grade and needs no threshold: **a trail moves the
    STOP only, so a row whose TAKE-PROFIT is ALSO on the wrong side has a
    mirrored bracket, not a trailed stop.** All 34 rows have an incoherent
    entry/sl/tp ordering; such a row is graded ``UNVERIFIED`` with reason
    ``bracket_mirrored_vs_direction`` — we could not look, never a confirmation
    and never a false proof. ⚠️ These rows reach the aggregate: ``/performance``
    applies the reconciler / superseded / reset-flat exclusions but **not**
    ``exclude_intent_reduce_predicate``, so they are inside the R population.

    Precedence: NO_BASIS > mirrored-bracket UNVERIFIED > CONTAMINATED >
    CONFIRMED_INITIAL > UNVERIFIED.
    """
    entry = _num(row.get("entry_price"))
    stop = _num(row.get("stop_loss"))
    qty = _num(row.get("qty") if row.get("qty") is not None else row.get("position_size"))

    if entry is None or stop is None or qty is None or qty == 0:
        return R_NO_BASIS, "risk_inputs_missing"
    if abs(entry - stop) <= 0:
        return R_NO_BASIS, "risk_not_positive"

    meta = row.get("package_meta")
    if meta is None:
        meta = row.get("pkg_meta")
    if meta is None:
        meta = row.get("meta")
    declared = declared_initial_risk(meta)

    wrong = stop_is_wrong_side(row.get("direction"), entry, stop)

    if wrong is True:
        # A trail moves the STOP. If the TAKE-PROFIT is on the wrong side too,
        # the whole bracket is mirrored relative to `direction` and the side
        # test's input is unreliable for this row — see the docstring.
        tp = _num(row.get("take_profit_1") if row.get("take_profit_1") is not None
                  else row.get("tp"))
        if tp is not None:
            d = str(row.get("direction") or "").strip().lower()
            tp_wrong = (tp < entry) if d in _LONG else ((tp > entry) if d in _SHORT else None)
            if tp_wrong is True:
                return R_UNVERIFIED, "bracket_mirrored_vs_direction"
        return R_CONTAMINATED, "wrong_side_of_entry"

    if wrong is None:
        # Direction unreadable — the side test could not run at all. This is
        # "we did not look", so it can never be a confirmation regardless of
        # what the distance says.
        return R_UNVERIFIED, "direction_unreadable"

    if declared is None:
        return R_UNVERIFIED, "no_declared_initial_risk_record"
    if abs(abs(entry - stop) - declared) / declared <= CONFIRM_REL_TOL:
        return R_CONFIRMED_INITIAL, "matches_declared_initial_risk"
    return R_UNVERIFIED, "disagrees_with_declared_initial_risk"


# ── the RISK BASIS: which number was R actually divided by? ────────────────
#
# The four states above GRADE the stored stop. These four say which risk the
# published R was computed FROM — a different question, and the one that
# decides whether the number is usable at all.
#
# ⚠️ THEY ARE NOT A RENAMING OF THE STATES ABOVE. A row can be graded
# ``unverified`` (we could not prove the stored stop is the initial one) and
# still be computed on the ``declared_initial`` basis, because the declared
# record is INDEPENDENT of the stored stop. Collapsing the two vocabularies
# would hide exactly that case, which is the majority one.
R_BASIS_DECLARED = "declared_initial"
R_BASIS_STORED_STOP = "stored_stop"
R_BASIS_REFUSED_WRONG_SIDE = "refused_wrong_side"
R_BASIS_NO_BASIS = "no_basis"

R_BASES = (
    R_BASIS_DECLARED,
    R_BASIS_STORED_STOP,
    R_BASIS_REFUSED_WRONG_SIDE,
    R_BASIS_NO_BASIS,
)


def empty_basis_counts() -> Dict[str, int]:
    """A zeroed count for EVERY basis — explicit zeros, never a missing key."""
    return {basis: 0 for basis in R_BASES}


def initial_risk_usd(row: Mapping, contract_value_usd: Any) -> Tuple[Optional[float], str]:
    """The USD risk R should be divided by, and WHICH basis produced it.

    ``risk_usd = <risk per unit> * |qty| * contract_value_usd`` — the same
    absolute-USD scale as the stored multiplier-aware ``pnl``, so this is a
    drop-in replacement for the denominator
    :func:`src.web.api._clean_trades.r_multiple` builds. The ONLY thing that
    changes is *which* per-unit risk is used, and that the impossible case is
    REFUSED instead of being ``abs()``-ed into a finite number.

    Precedence, and each step is a different claim:

    ``declared_initial``
        The signal's own ``risk_per_unit`` from ``order_packages.meta``.
        Preferred whenever it exists, because it is the risk the trade was
        ENTERED with and ``order_monitor._apply_update`` — which writes only
        ``sl``/``tp`` — cannot reach it. This is the fix: R is *defined*
        against entry-time risk, and ``trades.stop_loss`` holds the EXIT-time
        stop.

    ``stored_stop``
        ``|entry - stop|``, used only when no declared record exists AND the
        stored stop is not PROVEN wrong-side. Identical to the legacy
        behaviour, deliberately — where there is no better basis, publishing
        the old number is honest and dropping the row is not.

    ``refused_wrong_side``
        Returns ``None``. The stop sits on the wrong side of entry, which
        cannot be an initial stop, and no declared record exists to fall back
        to. ``abs()`` turns that impossibility into a plausible small
        denominator and an enormous R — the single mechanism behind a losing
        window publishing a positive ``expectancyR``. A refusal is the honest
        output: the caller excludes the row from BOTH the R numerator and its
        denominator, exactly as it already does for a missing stop.

        ⚠️ The refusal uses :func:`classify_r`, not a bare side test, so a
        direction-mirrored ``intent_reduce`` row (whose whole bracket is
        inverted relative to ``direction``) is graded ``unverified`` and keeps
        its stored basis rather than being refused for a trail it never had.

    ``no_basis``
        Returns ``None``. There is no R to compute — a missing price/size, or
        a non-positive risk. The state :func:`src.web.api._clean_trades.r_multiple`
        already returns ``None`` for.

    The four bases sum to the population by construction, so a consumer can
    check the partition with arithmetic rather than trusting it. ⚠️ The
    stronger invariant a consumer may want — ``rTradeCount ==
    declared_initial + stored_stop`` — additionally requires every row to carry
    a non-NULL ``pnl``; ``/api/bot/performance`` guarantees that in SQL, an
    arbitrary caller does not.
    """
    qty = _num(row.get("qty") if row.get("qty") is not None else row.get("position_size"))
    entry = _num(row.get("entry_price"))
    cv = _num(contract_value_usd)
    if qty is None or qty == 0 or cv is None or cv <= 0:
        return None, R_BASIS_NO_BASIS

    meta = row.get("package_meta")
    if meta is None:
        meta = row.get("pkg_meta")
    if meta is None:
        meta = row.get("meta")
    declared = declared_initial_risk(meta)
    if declared is not None:
        risk = declared * abs(qty) * cv
        if risk > 0:
            return risk, R_BASIS_DECLARED

    stop = _num(row.get("stop_loss"))
    if entry is None or stop is None or abs(entry - stop) <= 0:
        return None, R_BASIS_NO_BASIS

    if classify_r(row)[0] == R_CONTAMINATED:
        return None, R_BASIS_REFUSED_WRONG_SIDE

    risk = abs(entry - stop) * abs(qty) * cv
    if risk <= 0:
        return None, R_BASIS_NO_BASIS
    return risk, R_BASIS_STORED_STOP


def r_multiple_provenanced(
    row: Mapping, contract_value_usd: Any
) -> Tuple[Optional[float], str]:
    """``(R, basis)`` for one row — the provenanced sibling of
    :func:`src.web.api._clean_trades.r_multiple`.

    ``None`` for R means the row counts in NEITHER the R numerator nor its
    denominator; ``basis`` says WHY, and is never absent. On the
    ``stored_stop`` basis this returns exactly what the legacy helper returns
    — asserted by a test rather than claimed here, so the two cannot drift.
    """
    risk, basis = initial_risk_usd(row, contract_value_usd)
    pnl = _num(row.get("pnl"))
    if risk is None or pnl is None:
        # ⚠️ The basis reports what the RISK resolution found, even when the
        # numerator is absent. Overwriting it with `no_basis` on a missing
        # ``pnl`` would collapse "there was no risk to divide by" into "there
        # was nothing to divide" — two different facts, and this module exists
        # to keep exactly that pair apart.
        return None, basis
    return pnl / risk, basis


def empty_counts() -> Dict[str, int]:
    """A zeroed count for EVERY state — explicit zeros, never a missing key.

    A key that vanishes makes a consumer branch on absence, and absence is not
    one of the states.
    """
    return {state: 0 for state in R_STATES}


def summarize(rows: Iterable[Mapping]) -> Dict[str, Any]:
    """Grade a population and return the partition plus the disagreement count.

    ``counts`` sums to ``graded`` **by construction**, so the partition is
    checkable with arithmetic rather than trusted — the cross-check this repo
    keeps finding is the only one that catches a structural error.

    ``tightened_vs_declared`` is REPORTED BESIDE the states, never folded into
    them: it counts rows whose stored stop is at least
    :data:`DISAGREEMENT_RATIO_BAR` times tighter than their declared initial
    risk. ``declared_risk_records`` is its denominator — a bar-crossing count
    over an unstated denominator is not a claim.
    """
    counts = empty_counts()
    graded = 0
    declared_records = 0
    tightened = 0
    for row in rows:
        graded += 1
        state, _reason = classify_r(row)
        counts[state] += 1
        ratio = disagreement_ratio(
            row.get("entry_price"), row.get("stop_loss"),
            row.get("package_meta") if row.get("package_meta") is not None
            else (row.get("pkg_meta") if row.get("pkg_meta") is not None else row.get("meta")),
        )
        if ratio is not None:
            declared_records += 1
            if ratio >= DISAGREEMENT_RATIO_BAR:
                tightened += 1
    return {
        "graded": graded,
        "counts": counts,
        "declared_risk_records": declared_records,
        "tightened_vs_declared": tightened,
        "disagreement_ratio_bar": DISAGREEMENT_RATIO_BAR,
    }
