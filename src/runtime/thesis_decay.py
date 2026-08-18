"""In-trade ENTRY-THESIS decay — is the reason we opened this trade still true?

WHY THIS EXISTS (operator directive, 2026-08-18).

    "They often don't seem to have the momentum or the edge or the factors that
    went into entering the trade in the first place ... they're not really
    relevant to the market structure even."

Every exit lever this repo has built or swept is **PATH-based**: `stale_stop`
(bars elapsed below a level), `giveback_stop` (R surrendered from MFE),
`trail_decay` (R reached), `rr_floor` (R remaining vs R at risk). Each asks a
question about the TRADE. **None asks a question about the MARKET** — none
re-evaluates whether the conditions that admitted the entry still hold.

That is a categorical gap, not a tuning gap, and no amount of sweeping the
path-based cells can close it. `src/runtime/regime_flip_exit.py` is the one
prior attempt at a market-side predicate; it is blocked on a separate finding
(43 of 47 live legs have no `config/regime_policy.yaml` cell, so its verdict is
`default-on` for 91.5% of the fleet — see
`scripts/ops/regime_policy_coverage.py`). This module does not depend on the
regime table at all: it re-asks **the leg's own entry filters**.

THE MODULE OWNS THE DECISION, NEVER THE COMPUTATION
---------------------------------------------------
`evaluate` takes **already-computed** component readings and returns a verdict.
It does not compute a Donchian midline and it does not compute an ADX. This is
deliberate and it is the whole correctness argument:

  * The thesis check must mean **exactly** what the entry filter meant. The only
    way to guarantee that is for the numbers to BE the entry filter's numbers,
    supplied by the strategy module that already computed them.
  * Recomputing them here would create a second implementation of a decision
    predicate — the drift shape `regime_flip_exit`'s docstring documents this
    repo paying for repeatedly, and the shape that produced the per-tick
    reconstruction error on trade 4163 (a locally recomputed ATR vs the frozen
    `meta["atr"]` the monitor actually uses).

So a caller that cannot supply a reading passes `None` for it, and `None` means
**"we did not look"** — never "the component is fine".

FOUR STATES, NEVER COLLAPSED
----------------------------
  ``not_declared`` — the leg declares no thesis spec. We did not look, and
                     nothing may act on it. NOT the same as `intact`.
  ``unmeasured``   — declared, but at least one declared component could not be
                     read (indicator warm-up, NaN, absent frame). We looked and
                     failed. NOT the same as `intact`, and NOT the same as
                     `not_declared`: one is a configuration fact, the other is a
                     runtime fact, and conflating them hides a broken feed
                     behind an unconfigured leg.
  ``intact``       — every declared component still holds.
  ``decayed``      — at least one declared component no longer holds.

`unmeasured` is deliberately NOT decayed. An exit driven by a warm-up NaN would
be an exit manufactured out of missing data, which is the fabrication class this
repo registers provenance contracts for.

OBSERVE-ONLY. Nothing on the order path calls this. A lever that READS this
verdict to close a position is a Tier-3 change and needs its own evidence — the
sweep column does not exist yet, which is precisely why the first build is the
measurement and not the lever.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: `config/strategies.yaml` block a leg sets to opt in. Absent => `not_declared`.
THESIS_SPEC_KEY = "entry_thesis"

STATE_NOT_DECLARED = "not_declared"
STATE_UNMEASURED = "unmeasured"
STATE_INTACT = "intact"
STATE_DECAYED = "decayed"

#: Component ids this module knows how to grade. A spec naming anything else is
#: reported as `unmeasured` with reason `unknown_component_<id>` rather than
#: silently ignored — an unrecognised component must never read as satisfied.
TREND_MIDLINE = "trend_midline"
ADX_BAND = "adx_band"
KNOWN_COMPONENTS = (TREND_MIDLINE, ADX_BAND)

_LONG = "long"
_SHORT = "short"


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or `None` for anything we cannot read.

    `None`, a NaN, and an infinity are all *"we did not look"* — they are never
    coerced to 0.0. A zeroed indicator would be a manufactured reading.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _norm_side(side: Any) -> Optional[str]:
    """Map an order side to `long`/`short`, or `None` if non-directional.

    Accepts the several spellings this repo's layers use (`buy`/`sell` from the
    exchange payloads, `long`/`short` from the intent layer). A side we cannot
    resolve is `None` — a directional thesis cannot be graded without one.
    """
    if not isinstance(side, str):
        return None
    s = side.strip().lower()
    if s in ("long", "buy"):
        return _LONG
    if s in ("short", "sell"):
        return _SHORT
    return None


@dataclass(frozen=True)
class ComponentVerdict:
    """One component's grading, carrying the inputs that produced it.

    The readings ride along deliberately (diagnostic provenance): a soak row
    recording only `holds=False` cannot later be checked, and cannot say WHICH
    side of the line the market was on when the thesis broke.
    """

    component: str
    holds: Optional[bool]          # None => could not be read
    reason: str
    readings: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ThesisVerdict:
    """Why this module did or did not judge the entry thesis broken."""

    state: str
    components: List[ComponentVerdict] = field(default_factory=list)
    decayed_components: List[str] = field(default_factory=list)
    unmeasured_components: List[str] = field(default_factory=list)
    close_reason: Optional[str] = None

    @property
    def should_exit(self) -> bool:
        """True ONLY on a positive decay. `not_declared`/`unmeasured` never exit."""
        return self.state == STATE_DECAYED


def spec_for(strategy_cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The leg's declared thesis spec, or `None` when it has not opted in.

    Default OFF. A missing key, a `None`, a non-dict, or an empty dict all mean
    the leg declares nothing. Only a non-empty mapping arms the check.
    """
    if not isinstance(strategy_cfg, dict):
        return None
    raw = strategy_cfg.get(THESIS_SPEC_KEY)
    if not isinstance(raw, dict) or not raw:
        return None
    return raw


def _grade_trend_midline(
    *, side: Optional[str], close: Optional[float], midline: Optional[float],
) -> ComponentVerdict:
    """Does price still sit on the entry side of the trend midline?

    The htf-pullback / donchian families admit a LONG only above the Donchian
    midline and a SHORT only below it. That is the trend filter — the durable
    half of the entry. (The pullback retracement is the other half and is
    deliberately NOT graded here: it is an entry-TIMING condition that is
    *expected* to stop holding on the bar after entry, so treating it as thesis
    would mark every trade decayed immediately.)
    """
    readings = {"side": side, "close": close, "midline": midline}
    if side is None:
        return ComponentVerdict(TREND_MIDLINE, None, "non_directional_side", readings)
    if close is None or midline is None:
        return ComponentVerdict(TREND_MIDLINE, None, "reading_unavailable", readings)
    holds = close > midline if side == _LONG else close < midline
    return ComponentVerdict(
        TREND_MIDLINE,
        holds,
        "on_entry_side" if holds else "crossed_midline",
        readings,
    )


def _grade_adx_band(
    *, adx: Optional[float], adx_min: Optional[float], adx_max: Optional[float],
) -> ComponentVerdict:
    """Is trend strength still inside the band the entry filter required?

    A spec that declares the component but supplies neither bound has declared
    nothing gradeable; that is `unmeasured` (`no_band_declared`), never a pass —
    an empty band silently reading as satisfied is how a component gets wired
    and never actually checks anything.
    """
    readings = {"adx": adx, "adx_min": adx_min, "adx_max": adx_max}
    if adx_min is None and adx_max is None:
        return ComponentVerdict(ADX_BAND, None, "no_band_declared", readings)
    if adx is None:
        return ComponentVerdict(ADX_BAND, None, "reading_unavailable", readings)
    if adx_min is not None and adx < adx_min:
        return ComponentVerdict(ADX_BAND, False, "below_adx_min", readings)
    if adx_max is not None and adx > adx_max:
        return ComponentVerdict(ADX_BAND, False, "above_adx_max", readings)
    return ComponentVerdict(ADX_BAND, True, "inside_band", readings)


def evaluate(
    *,
    strategy_cfg: Optional[Dict[str, Any]],
    side: Any,
    close: Any = None,
    midline: Any = None,
    adx: Any = None,
) -> ThesisVerdict:
    """The single decision. Observe-only — nothing calls this on the order path.

    `close` / `midline` / `adx` are the strategy's OWN readings for the current
    in-trade bar (see the module docstring on why they are supplied rather than
    computed). Any of them may be `None`, which means *we could not look*.

    Order of checks is load-bearing: the declaration is tested FIRST, so an
    undeclared leg never touches a reading and can never be reported as
    "evaluated, thesis fine".
    """
    spec = spec_for(strategy_cfg)
    if spec is None:
        return ThesisVerdict(state=STATE_NOT_DECLARED)

    norm_side = _norm_side(side)
    close_f, midline_f, adx_f = _finite(close), _finite(midline), _finite(adx)

    verdicts: List[ComponentVerdict] = []
    for name, raw in spec.items():
        key = str(name)
        if not raw:
            # An explicitly falsy component is opted OUT of, not failed.
            continue
        if key == TREND_MIDLINE:
            verdicts.append(_grade_trend_midline(
                side=norm_side, close=close_f, midline=midline_f))
        elif key == ADX_BAND:
            band = raw if isinstance(raw, dict) else {}
            verdicts.append(_grade_adx_band(
                adx=adx_f,
                adx_min=_finite(band.get("min")),
                adx_max=_finite(band.get("max")),
            ))
        else:
            verdicts.append(ComponentVerdict(
                key, None, f"unknown_component_{key}", {}))

    if not verdicts:
        # Declared, but every component was opted out of / empty. Nothing was
        # graded, so this is `unmeasured` — not a clean pass.
        return ThesisVerdict(state=STATE_UNMEASURED, components=[],
                             unmeasured_components=[])

    decayed = [v.component for v in verdicts if v.holds is False]
    unmeasured = [v.component for v in verdicts if v.holds is None]

    if decayed:
        return ThesisVerdict(
            state=STATE_DECAYED,
            components=verdicts,
            decayed_components=decayed,
            unmeasured_components=unmeasured,
            close_reason="thesis_decay_" + "_".join(sorted(decayed)),
        )
    if unmeasured:
        # At least one declared component could not be read. We do NOT get to
        # call the thesis intact on a partial read: the unread component is
        # exactly the one that might have broken.
        return ThesisVerdict(
            state=STATE_UNMEASURED,
            components=verdicts,
            unmeasured_components=unmeasured,
        )
    return ThesisVerdict(state=STATE_INTACT, components=verdicts)
