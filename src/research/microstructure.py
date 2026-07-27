"""M30 · intrabar-OHLCV-shape microstructure features for the research panel.

**Why this exists.** The M30 backtest research panel (``build_backtest_panel.py``)
instruments each simulated trade with the strategy's decision-time ICT/regime
*geometry* (``src.research.component_vector``). Study 8 (ledger) showed that vector
gives ict_scalp only a **thin regime-conditioned ENTRY edge** (``cat_regime``, OOS
AUC ~0.54) and **null exits** — the geometry is close to exhausted. The
unexplored, operator-endorsed Track-1 frontier
(``POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md``) is a **different feature class**:
the **intrabar OHLCV shape** at the entry bar — realized-vol term structure,
short-horizon return autocorrelation, intrabar close-in-range position (a buying-
pressure proxy), and volume z-score. That is a *modest but real* information
step-up over the geometry (finer-resolution price/volume structure), NOT full
order-flow microstructure — true tape/book has no free historical feed (the S0
finding). This module is the canonical, pure, PIT extractor of that feature class
for the **decision bar** of a trade, so the panel can test whether OHLCV shape
adds OOS trade-outcome discrimination **beyond** the geometry.

**Single source of truth for the feature math.** The five feature definitions are
the same ones the S0/S2 feasibility probe (``scripts/micro/microstructure_probe.py``)
uses to measure feature→next-bar-return IC. Here they are consumed for a
*different question* (feature→**trade-outcome** under the full FDR × purged-WF-CV
bar, joined per simulated trade). To prevent the two implementations drifting, a
parity unit test (``tests/test_microstructure.py``) asserts this module's output
equals the probe's ``build_feature_panel`` last-bar row on a shared fixture.

**Leakage discipline (the platform's binding invariant).** Every feature is
computed **point-in-time** from candles up to & INCLUDING the decision bar — the
same bar the strategy signalled on (a closed bar). The simulated fill is the NEXT
bar (``build_backtest_panel._window_candles`` slices ``[entry_index+1 ..]`` for the
outcome excursions), so these features are strictly decision-time and can never
see the post-entry path. The caller passes a window ending at the decision bar;
the trailing-window functions use only data at or before it.

**Observe-only, Tier-1.** Pure arithmetic over the provided candles — no I/O, no
network, no DB, no numpy, no ``src``/``config`` write, no order path. Any malformed
input degrades to a ``None`` feature, never an exception.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence

# A candle is any mapping with numeric OHLCV. Both the panel spelling
# (open/high/low/close/volume) and the probe spelling (o/h/l/c/v) are accepted so
# this module reads a build_backtest_panel candle-frame record AND a probe bar.
Candle = Dict[str, Any]

# The canonical microstructure feature names (without the panel's ``feat_``
# prefix). Namespaced ``ms_`` so they never collide with a component-vector graded
# feature. The builder writes each as ``feat_ms_<name>``.
FEATURE_NAMES = (
    "ms_realized_vol",
    "ms_rv_term_structure",
    "ms_ret_autocorr_lag1",
    "ms_range_position",
    "ms_volume_zscore",
)

DEFAULT_VOL_SHORT = 6
DEFAULT_VOL_LONG = 24
_MIN_DENOM = 1e-12


# ---------------------------------------------------------------------------
# Tolerant accessors (accept both candle spellings; never raise)
# ---------------------------------------------------------------------------


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _get(candle: Candle, *keys: str) -> Optional[float]:
    """First present numeric value among ``keys`` (tolerant of spelling)."""
    if not isinstance(candle, dict):
        return None
    for k in keys:
        v = _num(candle.get(k))
        if v is not None:
            return v
    return None


def _close(c: Candle) -> Optional[float]:
    return _get(c, "close", "c")


def _high(c: Candle) -> Optional[float]:
    return _get(c, "high", "h")


def _low(c: Candle) -> Optional[float]:
    return _get(c, "low", "l")


def _volume(c: Candle) -> Optional[float]:
    return _get(c, "volume", "v", "vol")


# ---------------------------------------------------------------------------
# Pure feature functions (definitionally identical to microstructure_probe;
# parity-tested against it). Each returns None rather than fabricating a value.
# ---------------------------------------------------------------------------


def log_returns(closes: Sequence[Optional[float]]) -> List[float]:
    """Bar-to-bar log returns (len n-1). A non-positive close yields ``0.0`` for
    that step (kept aligned, never dropped silently)."""
    out: List[float] = []
    for a, b in zip(list(closes)[:-1], list(closes)[1:]):
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
        else:
            out.append(0.0)
    return out


def realized_vol(rets: Sequence[float], window: int) -> Optional[float]:
    """Population stdev of the trailing ``window`` returns (``None`` if < 2 avail)."""
    w = list(rets)[-window:] if window else list(rets)
    if len(w) < 2:
        return None
    return statistics.pstdev(w)


def rv_term_structure(rets: Sequence[float], short_w: int, long_w: int) -> Optional[float]:
    """Short-window RV ÷ long-window RV — a vol-expansion ratio (>1 = expanding).
    ``None`` when either leg is unavailable or the long leg is ~0."""
    sv, lv = realized_vol(rets, short_w), realized_vol(rets, long_w)
    if sv is None or lv is None or lv < _MIN_DENOM:
        return None
    return sv / lv


def ret_autocorr_lag1(rets: Sequence[float], window: int) -> Optional[float]:
    """Lag-1 autocorrelation of the trailing ``window`` returns. ``None`` when
    < 3 points or the series is constant (zero variance)."""
    w = list(rets)[-window:] if window else list(rets)
    if len(w) < 3:
        return None
    m = statistics.fmean(w)
    num = sum((a - m) * (b - m) for a, b in zip(w[:-1], w[1:]))
    den = sum((a - m) ** 2 for a in w)
    if den < _MIN_DENOM:
        return None
    return num / den


def range_position(candle: Candle) -> Optional[float]:
    """Where the close sits in the bar's high-low range, ``(c - l)/(h - l)`` in
    [0,1] — a cheap intrabar buying-pressure proxy (1 = closed on the high).
    ``None`` on a zero-range or malformed bar. PIT: uses only this bar."""
    h, lo, c = _high(candle), _low(candle), _close(candle)
    if h is None or lo is None or c is None or (h - lo) < _MIN_DENOM:
        return None
    return (c - lo) / (h - lo)


def volume_zscore(vols: Sequence[Optional[float]], window: int) -> Optional[float]:
    """Z-score of the latest volume vs the trailing ``window`` (inclusive).
    ``None`` when < 2 usable points or zero dispersion."""
    clean = [v for v in (list(vols)[-window:] if window else list(vols)) if v is not None]
    if len(clean) < 2:
        return None
    sd = statistics.pstdev(clean)
    if sd < _MIN_DENOM:
        return None
    return (clean[-1] - statistics.fmean(clean)) / sd


# ---------------------------------------------------------------------------
# Public API — decision-bar feature block
# ---------------------------------------------------------------------------


def decision_bar_features(
    candles: Sequence[Candle],
    *,
    vol_short: int = DEFAULT_VOL_SHORT,
    vol_long: int = DEFAULT_VOL_LONG,
    round_to: int = 6,
) -> Dict[str, float]:
    """Microstructure features for the **last** (decision) bar of ``candles``.

    ``candles`` is the chronological window ENDING at & INCLUDING the decision bar
    (the caller slices ``[entry_index - lookback .. entry_index]``). Mirrors the
    probe's ``build_feature_panel`` last-bar row exactly: the trailing-window
    features (realized_vol / rv_term_structure / ret_autocorr) use the returns up
    to the decision bar; ``range_position`` uses the decision bar itself;
    ``volume_zscore`` uses the volumes up to & including it.

    Returns ``{ms_* : float}`` — a feature whose inputs are missing / degenerate
    is **omitted** (callers test membership, mirroring ``component_vector.extract``).
    Pure + total: any malformed input degrades to fewer features, never a raise.
    """
    bars = [c for c in candles if isinstance(c, dict)]
    if not bars:
        return {}

    closes = [_close(c) for c in bars]
    vols = [_volume(c) for c in bars]
    # Log returns over the closes present up to the decision bar. A None close
    # breaks the chain to 0.0 for that step (log_returns' aligned-null contract).
    rets = log_returns([c if c is not None else 0.0 for c in closes])

    raw: Dict[str, Optional[float]] = {
        "ms_realized_vol": realized_vol(rets, vol_long),
        "ms_rv_term_structure": rv_term_structure(rets, vol_short, vol_long),
        "ms_ret_autocorr_lag1": ret_autocorr_lag1(rets, vol_long),
        "ms_range_position": range_position(bars[-1]),
        "ms_volume_zscore": volume_zscore(vols, vol_long),
    }

    out: Dict[str, float] = {}
    for name, val in raw.items():
        v = _num(val)
        if v is not None:
            out[name] = round(v, round_to)
    return out


def feature_names() -> List[str]:
    """The canonical microstructure feature names (for the manifest stamp)."""
    return list(FEATURE_NAMES)
