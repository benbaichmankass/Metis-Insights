"""What counts as an order that REACHED THE EXCHANGE — the one definition.

`scripts/ops/dead_leg_audit.py` (the offline report) and
`src/runtime/silent_refusal_alert.py` (the live latched alert) both have to
answer "did this order become a position, or die before it?", and they answer
it over the same `trades.status` column. Two copies of that vocabulary is how
they start disagreeing — the report grading a leg healthy while the alert calls
it dead, or a new status the reconciler starts writing quietly changing one and
not the other. So the vocabulary lives here and both import it.

Deliberately NOT in `scripts/`: the live trader tick imports this, and `src/`
depending on `scripts/` inverts the layering. The script imports down into
`src/`, which is the direction everything else already runs.

THE THIRD BUCKET IS LOAD-BEARING. An unrecognised status is neither placed nor
refused; it lands in `other` and gets its own verdict. Folding an unknown into
either bucket would let a status nobody has seen before silently change every
leg's grade — and it would do so in the direction of a confident answer, which
is the worst direction for a signal that exists to be believed.

AND SO IS THE FOURTH (2026-08-24, ``BL-20260824-SILENT-REFUSAL-CANNOT-SEE-A-DECLARED-DRY-RUN``).
"refused because the account is BROKEN" and "refused because the account is
deliberately SWITCHED OFF" are opposite facts that produce byte-identical
``trades.status`` rows. A ``mode: dry_run`` account refusing every order is the
execution gate working exactly as designed — the repo already said so, in
``execution_diagnostics.EXPECTED_DISPATCH_SKIP_REASONS``, by operator directive
2026-07-15, *after shelving ``alpaca_live`` to dry_run made every tick fire both
banners*. ``silent_refusal_alert`` shipped in August without consulting that
classifier and re-created the alarm the operator had already had suppressed: it
latched ``alerting: true`` on ``alpaca_live`` from 2026-08-21 and held it for
three days, on an account whose refusals were correct, wearing an
``account_class: real_money`` label that reads maximally alarming. That is the
desensitized-alarm P1 this repo names as its own worst failure mode, self-
inflicted on its newest detector.

So a refusal whose reason is a declared policy skip lands in ``policy_skipped``
and gets its OWN verdict, ``refusing_by_declaration``. The predicate is NOT
re-derived here — it is imported from ``execution_diagnostics``, because a
second copy of "is this refusal deliberate?" is free to drift from the first and
the two would then disagree about whether to wake the operator.

WIDENED 2026-08-25 FROM THE NARROWER OF TWO PREDICATES TO THE BROADER ONE
(``BL-20260825-DECLARED-POLICY-HOLDS-GRADE-AS-REFUSALS-IN-THE-DEAD-LEG-VOCABULARY``,
operator decision). Importing rather than re-deriving was right and was not
enough: ``execution_diagnostics`` holds TWO predicates, and this module imported
``is_expected_dispatch_skip`` (is the ACCOUNT declared off?) when the question it
actually asks is ``is_policy_hold``'s (did the system DECIDE not to send this
order?). The broad rule was already the incumbent in two sibling call sites and
had been since 2026-05/07 — it was simply trapped in nested closures nothing
could import, so the third caller re-derived a narrower one. Three vocabularies
agreed; this one, alone, did not.

Measured before the change (1000-row diag window, 2026-07-26 → 2026-08-25):
**77 of the 93 refused rows across all six ``signalled_never_placed`` legs —
82.8% — carried a declared token**, against 3 genuine capability failures. The
worst leg, ``mgc_trend_1h``, graded on 68 refusals of which 58 were the system
deliberately holding; it now grades on 10, and the real-signal density on that
leg goes from 3/68 to 3/10.

⚠️ **This does NOT by itself make item 0.3's condition the thing that pages.**
``mgc_trend_1h`` still clears ``SILENT_REFUSAL_MIN_ROWS`` after the change (10 ≥
5) and its dominant cause becomes ``risk_refused`` — the risk manager working.
That is the second half of the same finding and it is a separate decision; see
``BL-20260825-BALANCE-UNREADABLE-CAN-NEVER-REACH-ITS-OWN-ALERT-THRESHOLD``.

FAIL-SAFE, opposite polarity to ``account_side_filter``. That module is fail-
PERMISSIVE because it gates an order. This one gates an ALARM, so an
unrecognised reason stays a real refusal and still alerts: the failure we refuse
is a genuine outage silenced by a classifier that could not read it.

EVALUATION LIVENESS IS A SEPARATE AXIS FROM ORDER OUTCOME (2026-08-25, Lane 0).
Everything above grades legs from ``trades`` rows — which can only speak about
legs that PRODUCED a row. A leg with zero rows is deliberately not graded
(``verdict_for`` says so, and the audit's population string repeats it), because
"no rows" is not observable from counts.

But that single absence covers two OPPOSITE facts:

  * the leg RAN and found no actionable setup — the ordinary state of most legs
    most of the time, and entirely healthy;
  * the leg DID NOT RUN AT ALL — dropped from the loaded set, throwing, or
    wedged — which is a real defect.

Both render as "absent from the report", so the second hides inside the first.
Measured 2026-08-25: all three Alpaca accounts had produced no ``trades`` row
since 2026-08-21 while bybit and ib journalled normally that morning, which
reads alarming — and the ``signals`` table showed every one of those legs
evaluating normally, the last batch stopping within a **13-second band at
19:59Z**, which is 15:59 ET, the US equity close. Venue-shut, not broken. A
detector that could not tell those apart would have fired on every US-equity leg
every night, which is the desensitized-alarm P1 this repo names as its own worst
failure mode.

So the axis is graded from ``signals`` (the ``*_eval`` dual-write) and kept in
its own four-state field, NEVER folded into the order verdict. ``unknown`` sits
on the refusing side: a missing or unreadable ``signals`` table is *we did not
look*, which is not *the leg is fine*.
"""
from __future__ import annotations

from typing import Any, Dict

#: Terminal statuses meaning THE ORDER REACHED THE EXCHANGE AND BECAME A
#: POSITION. This is the numerator that matters: not "did we try", but "did a
#: position exist". `orphaned` counts as PLACED — an orphan is a position the
#: journal lost track of, which is a different (also bad) problem, and folding
#: it into "never placed" would blame order construction for a reconciler fault.
PLACED_STATUSES = ("open", "closed", "orphaned")

#: Statuses meaning the order DIED BEFORE BECOMING A POSITION. Kept as distinct
#: buckets rather than one "failed" count, because they route to different
#: owners: a venue rejection is an order-construction bug, a risk refusal is a
#: sizing/limits question, and conflating them is how a fix gets aimed wrong.
REFUSED_STATUSES = (
    "rejected",              # refused before submission (risk / intent layer)
    "exchange_rejected",     # the venue bounced it
    "rejected_too_small",    # sub-minimum qty
)


def bucket_for(status: Any, reason: Any = None) -> str:
    """``"placed"`` / ``"refused"`` / ``"policy_skipped"`` / ``"other"``.

    *reason* is the row's ``entry_reason``. It is consulted ONLY to separate a
    deliberate policy skip from a real refusal; omitting it preserves the
    pre-2026-08-24 three-bucket behaviour exactly, so every existing caller is
    unaffected until it opts in by passing the reason.
    """
    if status in PLACED_STATUSES:
        return "placed"
    if status in REFUSED_STATUSES:
        if reason is not None and _is_declared_policy_skip(reason):
            return "policy_skipped"
        return "refused"
    return "other"


def _is_declared_policy_skip(reason: Any) -> bool:
    """True when *reason* is a declared, expected policy skip OR a declared
    no-op — i.e. the system decided not to send this order, rather than trying
    and failing.

    Delegates to ``execution_diagnostics.is_policy_hold``. ⚠️ **Note which of
    the two predicates that is.** Until 2026-08-25 this delegated to
    ``is_expected_dispatch_skip`` and said it was calling "the one module that
    owns 'is this refusal deliberate?'" — true of the module, false of the
    predicate. That module holds TWO answers to the question, and this picked
    the narrower one, which is a strict subset of the rule the same module
    already used for its own operator alerting. The broader rule was
    unimportable (a nested closure), which is the mechanical reason the
    vocabularies diverged rather than anyone deciding they should
    (``BL-20260825-DECLARED-POLICY-HOLDS-GRADE-AS-REFUSALS-IN-THE-DEAD-LEG-VOCABULARY``).

    Imported lazily so this module stays cheap for the live tick, and
    fail-SAFE: if the predicate cannot be loaded the row counts as a REAL
    refusal, because silencing a genuine outage is the worse error.
    """
    try:
        from src.runtime.execution_diagnostics import is_policy_hold
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(is_policy_hold(reason))
    except Exception:  # noqa: BLE001
        return False


def verdict_for(counts: Dict[str, int]) -> str:
    """Grade one leg or account from its ``{placed, refused, other}`` counts.

    ``signalled_never_placed`` is the finding this whole family exists to
    surface: rows EXIST and not one of them reached the exchange. It is
    strictly distinct from having no rows at all, which is not a verdict here
    because it is not observable from counts — a caller with zero rows has
    observed nothing and must say so rather than grade it.
    """
    placed, refused, other = (
        counts.get("placed", 0), counts.get("refused", 0), counts.get("other", 0))
    skipped = counts.get("policy_skipped", 0)
    if placed == 0 and refused == 0 and skipped > 0:
        # Every refusal was a DECLARED skip. The account is off, not broken.
        return "refusing_by_declaration"
    if placed == 0 and refused > 0:
        return "signalled_never_placed"
    if placed == 0 and other > 0:
        return "no_placed_rows_unrecognised_status_only"
    if placed > 0 and refused == 0:
        return "healthy"
    return "partially_refused"


#: Evaluation-liveness states. A leg's ORDER verdict and its EVAL state answer
#: different questions and are never merged into one string — a leg can be
#: `evaluating` and `signalled_never_placed` at once (it runs, and every order
#: dies), which is precisely the AVAX shape.
EVAL_STATES = ("evaluating", "not_evaluating", "never_evaluated", "unknown")


def eval_state_for(
    evals_in_window: Any,
    evals_ever: Any,
    *,
    table_present: bool = True,
) -> str:
    """Grade one leg's evaluation liveness. See EVAL_STATES.

    ``unknown`` when the ``signals`` dual-write is absent or unreadable — the
    honest value for *we could not look*, and deliberately NOT ``evaluating``
    (which would report a leg healthy on the strength of a table nobody read)
    nor ``not_evaluating`` (which would alarm on every leg the moment
    ``SIGNAL_DUAL_WRITE_DISABLED`` is set).

    ``never_evaluated`` is kept apart from ``not_evaluating`` because they route
    to different owners: a leg that has NEVER evaluated is a wiring question
    (registered? enabled? routed?), while one that evaluated and stopped is a
    runtime question (throwing? dropped from the loaded set?).

    ⚠️ ``not_evaluating`` IS WINDOW-SENSITIVE AND MEANS NOTHING ON A SHORT ONE.
    Legs stop evaluating whenever their venue is shut, so on a window narrower
    than the longest venue closure every US-equity leg grades ``not_evaluating``
    overnight and every weekend — correctly, and uselessly. The caller owns the
    window; the audit's default is deliberately wider than any weekend.
    """
    if not table_present:
        return "unknown"
    try:
        ever = int(evals_ever or 0)
        in_window = int(evals_in_window or 0)
    except (TypeError, ValueError):
        # A count we cannot read is not a count of zero.
        return "unknown"
    if ever <= 0:
        return "never_evaluated"
    if in_window > 0:
        return "evaluating"
    return "not_evaluating"


#: Signal-vs-journal states. A THIRD axis, orthogonal to both the order
#: ``verdict`` and the ``eval_state`` above, and never merged into either.
#:
#: ⚠️ **THIS AXIS EXISTS BECAUSE THE OTHER TWO CANNOT SEE ITS FINDING.**
#: ``verdict_for`` grades a leg from its journal-row counts and says so in its
#: own docstring: *a caller with zero rows has observed nothing and must say so
#: rather than grade it*. ``eval_state_for`` asks only whether the leg RAN. So a
#: leg that evaluates constantly, emits actionable buy/sell signals, and writes
#: NOTHING to the journal grades ``evaluating`` on one axis and is absent
#: entirely from the other — which is not a gap in either, it is a question
#: neither was asked.
#:
#: Measured 2026-08-30 on the live journal, which is why this is not
#: hypothetical: ``trend_donchian_sol`` is ``enabled: true`` / ``execution:
#: live`` and routed to ``bybit_1``. It emitted **144 actionable buy signals**
#: between 2026-08-02 and 2026-08-29, and its most recent journal row of ANY
#: kind is **2026-06-29** — two months earlier. Every one of its 7 trade rows
#: is on ``breakout_1``; it has **never** written a row on ``bybit_1``. Nothing
#: alerted for two months, because ``/health-review``'s silence check reads
#: ``*_eval`` events (it evaluates, so: fine), ``silent_refusal_alert`` grades
#: per ACCOUNT (``bybit_1`` places plenty for OTHER legs, so: fine), and this
#: audit's own leg table is built from ``trades`` rows (it has none, so: absent).
SIGNAL_JOURNAL_STATES = (
    "journaling", "signals_never_journaled", "no_actionable_signals", "unknown",
)


def signal_journal_state_for(
    actionable_signals_in_window: Any,
    journal_rows_in_window: Any,
    *,
    table_present: bool = True,
) -> str:
    """Grade one STRATEGY on "it signalled — did it journal anything?".

    ``signals_never_journaled`` is the finding: the leg asked for an order and
    the journal has no record of one being attempted, refused, or placed.

    ``no_actionable_signals`` is **not** health and must never be rendered as
    such — it means the leg produced nothing to compare, which is the ordinary
    state of a breakout leg sitting inside its channel. Whether that silence is
    itself wrong is ``eval_state_for``'s question, not this one.

    ``unknown`` when the ``signals`` dual-write is absent or unreadable, or a
    count will not parse — the honest value for *we did not look*. It is
    deliberately NOT ``no_actionable_signals``: reading a missing table as
    "this leg never signalled" would silence the entire fleet the moment
    ``SIGNAL_DUAL_WRITE_DISABLED`` is set, which is the failure this whole
    family exists to prevent (a detector that cannot fire is worse than none,
    because its silence reads as a clean bill of health).

    ⚠️ **THE ROW COUNT MUST INCLUDE REFUSALS AND ORDER PACKAGES, NOT JUST
    FILLS.** A refused order IS a journal record — the leg reached the journal
    and was turned away, which is ``verdict_for``'s question and already has an
    owner. Counting fills only would re-report every refusing leg here and bury
    the one leg that writes nothing at all.
    """
    if not table_present:
        return "unknown"
    try:
        signals = int(actionable_signals_in_window or 0)
        rows = int(journal_rows_in_window or 0)
    except (TypeError, ValueError):
        # A count we cannot read is not a count of zero.
        return "unknown"
    if signals <= 0:
        return "no_actionable_signals"
    if rows <= 0:
        return "signals_never_journaled"
    return "journaling"


__all__ = [
    "PLACED_STATUSES", "REFUSED_STATUSES", "EVAL_STATES",
    "SIGNAL_JOURNAL_STATES",
    "bucket_for", "verdict_for", "eval_state_for", "signal_journal_state_for",
]
