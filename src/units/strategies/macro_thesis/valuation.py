"""M28 P1 — asset-class valuation metrics (the "value" core).

Pure, stdlib-only, network-free functions that turn already-fetched raw series
values into **valuation metrics**, and — the heart of it — a
``value_read`` engine that decides whether a metric reads **cheap / fair / rich**
*relative to its own history*. "Value" is meaningful only against a baseline: an
equity-risk-premium of 3% is cheap or rich only versus where it usually sits, so
every metric flows through ``value_read`` against a historical window.

Design of record: ``docs/research/M28-macro-value-speculation-DESIGN.md`` §1a
(what "value" means for the ETF/options universe). This module is **layer-1**
(asset-class relative value, FRED-derived); layer-2 (SEC EDGAR company
fundamentals) reuses the same ``value_read`` engine with company metrics.

Everything here is:
- **pure** — no I/O, no global state, deterministic;
- **fail-permissive / honest-null** — bad or insufficient input yields a
  ``ValueRead`` with ``label="unknown"`` and null stats, never an exception and
  never a fabricated number (the house rule for the whole sleeve);
- **orientation-aware** — each metric declares whether a *higher* value means
  *cheaper* (e.g. ERP: higher premium = equities cheaper) or *richer*, so the
  ``cheap_score`` is comparable across metrics.

None of this touches an order path.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

# Default percentile cut points for the cheap/fair/rich label (on the
# orientation-normalized "cheap axis": 1.0 = maximally cheap).
_DEFAULT_CHEAP_PCT = 0.70
_DEFAULT_RICH_PCT = 0.30


def _is_finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _clean_history(history: Sequence[float]) -> list[float]:
    """Keep only finite numeric samples; drop NaN/inf/None/bools."""
    if not history:
        return []
    return [float(x) for x in history if _is_finite(x)]


# ---------------------------------------------------------------------------
# Metric constructors (raw value from raw inputs). Each returns None on bad
# input rather than raising — the caller treats None as "not computable".
# ---------------------------------------------------------------------------


def equity_risk_premium(earnings_yield: float, real_yield_10y: float) -> Optional[float]:
    """Equity risk premium = earnings yield − real 10y yield.

    ``earnings_yield`` = index earnings / price (e.g. S&P 500 E/P). Higher ERP =
    equities **cheaper** relative to real risk-free duration. Inputs in the same
    units (both fractions, e.g. 0.045 for 4.5%, or both percent — the difference
    is unit-consistent either way; be consistent per feed).
    """
    if not (_is_finite(earnings_yield) and _is_finite(real_yield_10y)):
        return None
    return float(earnings_yield) - float(real_yield_10y)


def real_yield(nominal_yield: float, breakeven_inflation: float) -> Optional[float]:
    """Real yield = nominal yield − breakeven inflation (Fisher approximation).

    Use when a direct TIPS real-yield series (FRED ``DFII10``) isn't at hand and
    only nominal (``DGS10``) + breakeven (``T10YIE``) are. Higher real yield =
    duration (TLT/IEF) **richer** / less attractive, all else equal.
    """
    if not (_is_finite(nominal_yield) and _is_finite(breakeven_inflation)):
        return None
    return float(nominal_yield) - float(breakeven_inflation)


def gold_silver_ratio(gold_price: float, silver_price: float) -> Optional[float]:
    """Gold / silver price ratio. High ratio = silver **cheap** vs gold.

    None on non-positive silver (division guard)."""
    if not (_is_finite(gold_price) and _is_finite(silver_price)):
        return None
    if float(silver_price) <= 0:
        return None
    return float(gold_price) / float(silver_price)


def credit_spread(oas: float) -> Optional[float]:
    """Pass-through for a credit OAS reading (HY/IG option-adjusted spread).

    Wider OAS = credit/risk **cheap** (risk-off, higher compensation). Kept as a
    named constructor so the orientation is documented at the call site."""
    if not _is_finite(oas):
        return None
    return float(oas)


def term_slope(long_yield: float, short_yield: float) -> Optional[float]:
    """Yield-curve slope = long − short (e.g. 10y − 3m). A macro-context read
    (steepening = reflation/early-cycle), not a direct value metric — flows
    through ``value_read`` like the others when a cheap/rich orientation is set."""
    if not (_is_finite(long_yield) and _is_finite(short_yield)):
        return None
    return float(long_yield) - float(short_yield)


# ---------------------------------------------------------------------------
# The value_read engine — cheap vs its own history.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueRead:
    """A metric's cheap/fair/rich read against a historical window.

    ``cheap_score`` is the orientation-normalized position on a [0,1] "cheap
    axis" (1.0 = maximally cheap for the asset the metric governs), so scores are
    comparable across metrics regardless of each metric's raw direction.
    ``label`` ∈ {cheap, fair, rich, unknown}. Stats are ``None`` when the history
    is too thin to compute them (honest-null)."""

    metric: str
    value: Optional[float]
    percentile: Optional[float]  # rank of value in history on the RAW axis, [0,1]
    z_score: Optional[float]
    cheap_score: Optional[float]  # orientation-normalized, [0,1], 1 = cheapest
    label: str
    n: int = 0
    higher_is_cheaper: bool = True
    note: str = field(default="")


def _percentile_rank(value: float, hist: Sequence[float]) -> float:
    """Fraction of history <= value, in [0,1] (midrank for ties)."""
    n = len(hist)
    below = sum(1 for h in hist if h < value)
    equal = sum(1 for h in hist if h == value)
    return (below + 0.5 * equal) / n


def value_read(
    metric: str,
    value: Optional[float],
    history: Sequence[float],
    *,
    higher_is_cheaper: bool = True,
    cheap_pct: float = _DEFAULT_CHEAP_PCT,
    rich_pct: float = _DEFAULT_RICH_PCT,
) -> ValueRead:
    """Read whether *value* is cheap/fair/rich vs its own *history*.

    - ``percentile`` is the raw-axis rank of ``value`` within ``history`` [0,1].
    - ``cheap_score`` re-orients that to the cheap axis: if ``higher_is_cheaper``
      it equals the percentile (high raw = cheap); else ``1 − percentile``.
    - ``label`` = cheap when ``cheap_score >= cheap_pct``, rich when
      ``<= rich_pct``, else fair.
    - ``z_score`` = (value − mean) / stdev on the raw axis (None if stdev 0 or
      history < 2).

    Honest-null: a non-finite ``value`` or empty history ⇒ ``label="unknown"``,
    null stats. Never raises. ``cheap_pct``/``rich_pct`` are validated
    (0 < rich_pct < cheap_pct < 1); bad thresholds fall back to defaults.
    """
    if not (0.0 < rich_pct < cheap_pct < 1.0):
        cheap_pct, rich_pct = _DEFAULT_CHEAP_PCT, _DEFAULT_RICH_PCT

    hist = _clean_history(history)
    n = len(hist)

    if not _is_finite(value):
        return ValueRead(
            metric=metric, value=None, percentile=None, z_score=None,
            cheap_score=None, label="unknown", n=n,
            higher_is_cheaper=higher_is_cheaper, note="value_not_finite",
        )
    v = float(value)

    if n == 0:
        return ValueRead(
            metric=metric, value=v, percentile=None, z_score=None,
            cheap_score=None, label="unknown", n=0,
            higher_is_cheaper=higher_is_cheaper, note="empty_history",
        )

    pct = _percentile_rank(v, hist)
    cheap_score = pct if higher_is_cheaper else (1.0 - pct)

    z: Optional[float] = None
    if n >= 2:
        try:
            sd = statistics.pstdev(hist)
            if sd > 0:
                z = (v - statistics.fmean(hist)) / sd
        except statistics.StatisticsError:
            z = None

    if cheap_score >= cheap_pct:
        label = "cheap"
    elif cheap_score <= rich_pct:
        label = "rich"
    else:
        label = "fair"

    return ValueRead(
        metric=metric, value=v, percentile=pct, z_score=z,
        cheap_score=cheap_score, label=label, n=n,
        higher_is_cheaper=higher_is_cheaper,
        note=("thin_history" if n < 20 else ""),
    )


def value_to_direction(read: ValueRead, *, min_cheap_score: float = _DEFAULT_CHEAP_PCT) -> str:
    """Map a ``ValueRead`` to a signal direction for the asset it governs.

    cheap → ``"bullish"`` (buy the cheap asset), rich → ``"bearish"``, else
    ``"neutral"``. ``unknown``/null ⇒ ``"neutral"`` (never fabricate a side). The
    threshold mirrors ``value_read``'s ``cheap_pct`` so the mapping agrees with
    the label by default."""
    if read.cheap_score is None or read.label == "unknown":
        return "neutral"
    if read.cheap_score >= min_cheap_score:
        return "bullish"
    if read.cheap_score <= (1.0 - min_cheap_score):
        return "bearish"
    return "neutral"
