"""Gross-exposure: observation, policy, and verdict — deliberately three things.

Design record: ``docs/design/gross-exposure-governance-DESIGN.md`` § 4.

WHY THIS MODULE EXISTS
----------------------
``RiskManager.gross_exposure()`` used to serve two masters at once — it was both
the *measurement* of an account's exposure and the *gate* that refused trades on
it. One function, two jobs, and the seam between them is where the money path
broke:

    def gross_exposure(self):
        if self.max_gross_exposure_pct <= 0:
            return None      # "no policy" and "no data" are the SAME answer

That conflation had a concrete cost. An operator asked to choose a ceiling had
no way to see what the account currently runs at, because the measurement was
gated on the ceiling already being declared. So phase 2 of
``BL-20260807-ALPACA-PAPER-ZERO-BUYING-POWER-REFUSES-ALL`` — declaring values —
was deferred and never happened, and the feature has sat inert since.

Ungating the measurement in place was attempted on 2026-08-08 and **reverted**:
it is a fleet-wide trading halt by two independent paths, both keyed on the
undeclared default of ``0.0``.

    1. ``evaluate()``      — ``multiple >= 0.0`` is true for ANY exposure,
                             including a flat account. Every trade refused.
    2. ``position_size()`` — headroom ``max(0, 0.0*equity - notional)`` is
                             ``0.0``. Every position clamped to nothing.

The first was guarded on that attempt; the second was missed entirely and was
caught only by a pre-existing test. Two halt vectors in ten minutes on the money
path is the signal that a guard is the wrong remedy — the states have to be
unreachable **by construction**.

THE SPLIT
---------
``observe``  — what IS the exposure? Never reads policy, so it has no path to a
               refusal and is safe to call from a report, a diag route, or a
               dashboard panel.
``policy``   — what ceiling did we DECLARE? A pure config read (``RiskManager``
               owns this; it is the account's own config).
``verdict``  — given both, what do we DO? Pure, no I/O, and policy-first: the
               absence of a policy short-circuits before any arithmetic can run.

This is the same remedy ``src/runtime/exit_anchor.py`` already applies with its
three-way ``anchored`` / ``deferred`` / ``no_anchor`` status, which states the
rule directly: *collapsing any two of those reintroduces a defect.* Nothing here
is novel — it is an established local pattern applied to code that predates it.

NOTHING IN THIS MODULE PERFORMS I/O. That is load-bearing, not incidental: a
function that cannot read the world cannot accidentally acquire the authority to
stop it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── observation status ───────────────────────────────────────────────────────
MEASURED = "measured"
UNMEASURABLE = "unmeasurable"

# ── verdict actions ──────────────────────────────────────────────────────────
ALLOW = "allow"
REFUSE = "refuse"
CLAMP = "clamp"

# ── why an observation could not be made (never collapsed into "flat") ───────
REASON_NO_EQUITY = "equity_unavailable"
REASON_NO_NOTIONAL = "notional_unreadable"

# ── why a verdict allowed (an ALLOW is not one thing) ────────────────────────
REASON_NO_POLICY = "no_policy_declared"
REASON_UNMEASURABLE = "exposure_unmeasurable"
REASON_AT_CEILING = "at_or_over_ceiling"


@dataclass(frozen=True)
class ExposureObservation:
    """What the account's gross exposure IS, independent of any policy.

    ``status`` is the contract. ``unmeasurable`` means *we could not look* —
    an unreadable journal, an equity we have no snapshot for. It is emphatically
    NOT the same statement as an exposure of zero, which means *we looked and
    the account is flat*. Collapsing the two would silently authorise a fresh
    position on an account that is actually at its limit, which is the failure
    the whole feature exists to prevent.
    """

    status: str
    notional: Optional[float] = None
    equity: Optional[float] = None
    multiple: Optional[float] = None
    reason: Optional[str] = None

    @property
    def measured(self) -> bool:
        return self.status == MEASURED


@dataclass(frozen=True)
class ExposureVerdict:
    """What to DO about an observation under a policy.

    ``headroom_usd`` is populated for ``CLAMP`` (how much more may be opened)
    and for ``REFUSE`` (always ``0.0``). It is ``None`` for ``ALLOW`` — and that
    null is the signal a caller must not clamp on, distinct from a headroom of
    ``0.0``, which means "measured, and there is none left".
    """

    action: str
    headroom_usd: Optional[float] = None
    reason: Optional[str] = None


def unmeasurable(reason: str) -> ExposureObservation:
    """An observation we could not make, carrying why."""
    return ExposureObservation(status=UNMEASURABLE, reason=reason)


def measured(notional: float, equity: float) -> ExposureObservation:
    """An observation we did make. ``equity`` must be positive.

    Raises on a non-positive equity rather than returning a multiple divided by
    zero or, worse, a silently substituted one. A caller that cannot establish
    a positive equity has an ``unmeasurable`` observation, not a measured one —
    ``RiskManager.observe_exposure`` makes exactly that call before arriving
    here.
    """
    eq = float(equity)
    if eq <= 0:
        raise ValueError(
            "measured() requires a positive equity; "
            "an account with no known equity is unmeasurable(), not measured"
        )
    n = float(notional)
    return ExposureObservation(
        status=MEASURED, notional=n, equity=eq, multiple=n / eq,
    )


def exposure_verdict(
    observation: ExposureObservation, policy: Optional[float],
) -> ExposureVerdict:
    """Decide, given an observation and a declared ceiling.

    Pure. The ORDER of these branches is the safety property, not a style
    choice — each of the first two exists to make one of the measured halt
    vectors unreachable:

      1. ``policy`` absent -> ALLOW, **before any comparison or arithmetic**.
         An undeclared ceiling can therefore never be compared against and can
         never become a headroom. A non-positive value is treated as absent for
         the same reason: a ceiling of zero is not a policy, it is the absence
         of one, and the whole incident class comes from reading it as the
         former. This normalisation is deliberate belt-and-braces — the caller
         is expected to pass ``None``, and this guarantees the outcome even if
         one day it does not.

      2. ``unmeasurable`` -> ALLOW. We did not look; that is not evidence of a
         breach. Fail-open here is correct because the alternative is that an
         unreadable journal becomes a self-inflicted trading outage — the
         Prime-Directive shape is a per-trade refusal with a logged cause, never
         a capability that silently stops.

    Only past those two does the ceiling bind: REFUSE at or over it, CLAMP into
    whatever remains below it. That split is the point of the feature — a policy
    should shape size on the way up and refuse only at the boundary, which is
    exactly what delegating to the broker's available-margin wall did not do.
    """
    if policy is None or policy <= 0:
        return ExposureVerdict(action=ALLOW, reason=REASON_NO_POLICY)

    if not observation.measured:
        return ExposureVerdict(action=ALLOW, reason=REASON_UNMEASURABLE)

    # Both are non-None whenever status is MEASURED (enforced by measured()).
    multiple = float(observation.multiple or 0.0)
    equity = float(observation.equity or 0.0)
    notional = float(observation.notional or 0.0)

    if multiple >= policy:
        return ExposureVerdict(
            action=REFUSE, headroom_usd=0.0, reason=REASON_AT_CEILING,
        )

    return ExposureVerdict(
        action=CLAMP, headroom_usd=max(0.0, (policy * equity) - notional),
    )
