"""Range-based volatility estimators (S-MLOPT-S9, M14 Phase 2.1).

Close-to-close realized vol (the `rolling_log_return_vol` feature) throws away
the intrabar high/low/open, so it needs many bars to estimate vol and reacts
slowly. Range-based estimators use the full OHLC of each bar and are far more
efficient per observation — the cheapest "better feature" lever for the regime
heads' `f1_volatile` separation (gap G5).

All four estimators here are computed over a window of bars and return the
**per-bar variance** (take ``math.sqrt`` for a vol comparable to
`rolling_log_return_vol`). Each reads only the OHLC of the bars it is given —
when fed a past-only window `[t-n+1 .. t]` the result is leakage-safe by
construction (the caller's responsibility, same as every other past-window
feature). Best-effort: bars with a non-positive price are skipped; an empty /
too-short window returns ``None``.

References: Parkinson (1980); Garman & Klass (1980); Rogers & Satchell (1991);
Yang & Zhang (2000). Yang-Zhang is drift-independent and handles overnight gaps
(~8× the efficiency of close-to-close), which is why the roadmap singles it out.
"""
from __future__ import annotations

import math
import statistics
from typing import Sequence

_LN2 = math.log(2.0)


def _ohlc_ok(o: float, h: float, lo: float, c: float) -> bool:
    return o > 0 and h > 0 and lo > 0 and c > 0


def parkinson_var(highs: Sequence[float], lows: Sequence[float]) -> float | None:
    """Parkinson (1980) high-low range variance: ``mean((ln(H/L))^2) / (4 ln2)``."""
    vals = []
    for h, lo in zip(highs, lows):
        if h > 0 and lo > 0:
            r = math.log(h / lo)
            vals.append(r * r)
    if not vals:
        return None
    return (sum(vals) / len(vals)) / (4.0 * _LN2)


def garman_klass_var(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> float | None:
    """Garman-Klass (1980): ``mean(0.5*(ln(H/L))^2 - (2 ln2 - 1)*(ln(C/O))^2)``.

    Clamped at 0 — the per-bar term can dip slightly negative, but the variance
    estimate over a window is non-negative; a clamp keeps the sqrt real.
    """
    vals = []
    for o, h, lo, c in zip(opens, highs, lows, closes):
        if _ohlc_ok(o, h, lo, c):
            hl = math.log(h / lo)
            co = math.log(c / o)
            vals.append(0.5 * hl * hl - (2.0 * _LN2 - 1.0) * co * co)
    if not vals:
        return None
    return max(sum(vals) / len(vals), 0.0)


def rogers_satchell_var(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> float | None:
    """Rogers-Satchell (1991): drift-independent, ``mean(ln(H/C)ln(H/O) + ln(L/C)ln(L/O))``."""
    vals = []
    for o, h, lo, c in zip(opens, highs, lows, closes):
        if _ohlc_ok(o, h, lo, c):
            vals.append(
                math.log(h / c) * math.log(h / o)
                + math.log(lo / c) * math.log(lo / o)
            )
    if not vals:
        return None
    return max(sum(vals) / len(vals), 0.0)


def yang_zhang_var(
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    prev_closes: Sequence[float | None],
) -> float | None:
    """Yang-Zhang (2000): ``var_overnight + k*var_open_close + (1-k)*var_RS``.

    Drift-independent and minimum-variance among range estimators, handling the
    overnight gap explicitly. ``prev_closes[i]`` is the close of the bar before
    ``opens[i]`` (``None`` for the very first bar, whose overnight term is then
    dropped). ``k = 0.34 / (1.34 + (n+1)/(n-1))`` with ``n`` = the count of bars
    that contributed an overnight + open-close return. Returns ``None`` when the
    window is too short (<2 usable bars) to take a sample variance.
    """
    overnight = []
    open_close = []
    for o, c, pc in zip(opens, closes, prev_closes):
        if o > 0 and c > 0 and pc is not None and pc > 0:
            overnight.append(math.log(o / pc))
            open_close.append(math.log(c / o))
    n = len(overnight)
    if n < 2:
        return None
    rs = rogers_satchell_var(opens, highs, lows, closes)
    if rs is None:
        return None
    var_o = statistics.variance(overnight)      # sample variance (N-1)
    var_c = statistics.variance(open_close)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return var_o + k * var_c + (1.0 - k) * rs


def _sqrt_or_zero(var: float | None) -> float:
    """sqrt of a variance estimate, or 0.0 when undefined — the feature-emit shape."""
    if var is None or var < 0:
        return 0.0
    return math.sqrt(var)
