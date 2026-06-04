"""Cross-asset / macro conditioning features for MES (S-MLOPT-S12, M14 Phase 2.4).

MES (Micro E-mini S&P 500) is a macro-driven index instrument — its regime is
conditioned by the volatility complex (VIX + its term structure), the dollar
(DXY), and the rates curve far more than BTCUSDT is. These are cheap, currently
**unused** feature families for the MES regime/decision heads, and the weakest
leg (MES `f1_volatile` never separated under the range-vol A/B, S9) is exactly
where a *different kind* of input — not another vol estimator on the same OHLC —
has the best chance of helping.

The research nuance the roadmap bakes in (optimization-roadmap.md § Session 2.4):

> DXY / VIX-term-structure / rates **conditioning** features — the signal is in
> the *level relative to its own recent distribution* and in the *term-structure
> shape*, not the raw quote.

So the headline features are **z-scores** (level vs its own trailing window) and
the **VIX term-structure slope** (VIX vs VIX3M — backwardation = stress), plus a
short DXY momentum read and the 3m-10y rates slope.

### Cadence + leakage discipline (the load-bearing part)

Macro series are **daily** while MES trades intraday (5m/15m). Two rules keep the
join leakage-safe:

1. **Compute the features at daily cadence, here** — each day's z-score / slope
   uses only that day and prior days (a past-only trailing window). The fully
   computed per-day feature row is what `market_features` as-of joins; it never
   re-windows a step-function across intraday bars.
2. **Lag one day.** A day-`D` feature is built from day-`D`'s *close* values, which
   are not known until day `D` ends. So the row is stamped at the **start of day
   `D+1`** (`<D+1>T00:00:00Z`). An intraday MES bar on day `D+1` then sees day
   `D`'s closed macro reads — never a same-day close before it printed. This is
   the standard macro-on-intraday leakage guard; without it the as-of join would
   serve day `D`'s VIX close to a 14:30 bar on day `D` (future information).

All functions are **pure** and operate on already-sorted daily observations; fed
a past-only window they are leakage-safe by construction. ``None`` propagates and
is converted to ``0.0`` at emit time (neutral), exactly like the funding/OI and
microstructure families.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

# The macro feature columns this family contributes to `market_features`. Kept
# here as the single source of truth so the builder schema, the side-stream
# producer, and the tests agree.
MACRO_FEATURE_COLUMNS: tuple[str, ...] = (
    "vix_level",
    "vix_zscore",
    "vix_term_slope",
    "dxy_zscore",
    "dxy_return",
    "ust10y_level",
    "ust_slope_3m10y",
)


def rolling_zscore(window: Sequence[float | None], *, min_n: int = 5) -> float | None:
    """Z-score of the LAST value vs the mean/stdev of ``window`` (past-only).

    Mirrors ``funding_oi_features.rolling_zscore`` but with a macro-appropriate
    default ``min_n`` (a few trading days). Returns ``None`` when too few usable
    points, the last value is ``None``, or the window has ~zero variance.
    """
    vals = [v for v in window if v is not None]
    if len(vals) < min_n or window[-1] is None:
        return None
    mean = statistics.fmean(vals)
    stdev = statistics.pstdev(vals)
    if stdev <= 1e-12:
        return None
    return (float(window[-1]) - mean) / stdev


def term_structure_slope(near: float | None, far: float | None) -> float | None:
    """``near / far - 1`` — the VIX term-structure shape (near=VIX, far=VIX3M).

    Positive ⇒ **backwardation** (near > far): the front of the vol curve is bid
    over the deferred — the classic stress / risk-off signature. Negative ⇒
    contango (the normal, calm state). ``None`` when either input is missing or
    ``far`` is non-positive.
    """
    if near is None or far is None or far <= 0:
        return None
    return float(near) / float(far) - 1.0


def level_spread(a: float | None, b: float | None) -> float | None:
    """``a - b`` — a curve slope in level terms (e.g. 10y yield minus 3m yield)."""
    if a is None or b is None:
        return None
    return float(a) - float(b)


def rolling_return(window: Sequence[float | None]) -> float | None:
    """``ln(window[-1] / first_positive)`` — short-horizon momentum of a level.

    Uses the earliest positive value in the window as the base (so one missing
    leading observation doesn't void it). ``None`` when the last value is
    missing/non-positive or no positive base exists.
    """
    if not window or window[-1] is None or window[-1] <= 0:
        return None
    base = None
    for v in window:
        if v is not None and v > 0:
            base = v
            break
    if base is None or base <= 0:
        return None
    return math.log(float(window[-1]) / float(base))


def _finite_or_zero(value: float | None) -> float:
    """``None`` / non-finite → ``0.0`` (neutral) — the feature-emit shape."""
    if value is None or not math.isfinite(value):
        return 0.0
    return float(value)


def _date_of(ts: str) -> str:
    """The UTC calendar date (``YYYY-MM-DD``) of an ISO timestamp/date string."""
    text = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Bare date.
        return text[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _next_day_start_iso(date_str: str) -> str:
    """``<date+1>T00:00:00Z`` — the leakage-lag stamp for a day's macro reads."""
    d = datetime.fromisoformat(date_str).date() + timedelta(days=1)
    return f"{d.isoformat()}T00:00:00Z"


def compute_macro_feature_rows(
    daily: Sequence[Mapping[str, Any]],
    *,
    zscore_window_n: int = 20,
    return_window_n: int = 5,
) -> list[dict[str, Any]]:
    """Per-day macro feature rows from raw daily level observations.

    ``daily`` is an ascending-by-date sequence of
    ``{date|ts, vix?, vix3m?, dxy?, ust10y?, ust3m?}`` (any field may be
    missing/``None``). For each day with enough history the function emits one
    row carrying the :data:`MACRO_FEATURE_COLUMNS`, **stamped at the start of the
    following day** (the one-day leakage lag described in the module docstring).

    ``zscore_window_n`` is the trailing window (in trading days) for the VIX/DXY
    z-scores; ``return_window_n`` the short window for DXY momentum.
    """
    rows = sorted(daily, key=lambda r: _date_of(str(r.get("date") or r.get("ts") or "")))
    n = len(rows)
    if n == 0:
        return []

    def _series(key: str) -> list[float | None]:
        out: list[float | None] = []
        for r in rows:
            v = r.get(key)
            try:
                out.append(float(v) if v is not None else None)
            except (TypeError, ValueError):
                out.append(None)
        return out

    vix = _series("vix")
    vix3m = _series("vix3m")
    dxy = _series("dxy")
    ust10y = _series("ust10y")
    ust3m = _series("ust3m")

    out_rows: list[dict[str, Any]] = []
    for i in range(n):
        date_str = _date_of(str(rows[i].get("date") or rows[i].get("ts") or ""))
        if not date_str:
            continue
        zs = max(0, i - zscore_window_n + 1)
        rs = max(0, i - return_window_n + 1)
        out_rows.append(
            {
                "ts": _next_day_start_iso(date_str),
                "vix_level": _finite_or_zero(vix[i]),
                "vix_zscore": _finite_or_zero(rolling_zscore(vix[zs : i + 1])),
                "vix_term_slope": _finite_or_zero(
                    term_structure_slope(vix[i], vix3m[i])
                ),
                "dxy_zscore": _finite_or_zero(rolling_zscore(dxy[zs : i + 1])),
                "dxy_return": _finite_or_zero(rolling_return(dxy[rs : i + 1])),
                "ust10y_level": _finite_or_zero(ust10y[i]),
                "ust_slope_3m10y": _finite_or_zero(level_spread(ust10y[i], ust3m[i])),
            }
        )
    return out_rows
