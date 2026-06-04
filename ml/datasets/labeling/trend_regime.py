"""Forward trend-regime labeler (S-MLOPT-S15 / Phase 3.3).

Labels a bar's **forward** window as ``chop`` / ``transitional`` / ``trending``
on a **trend** axis — the taxonomy the regime router's policy table keys on
(``src/runtime/regime/policy.py``: ``{chop, transitional, trending}``), which
the ADX-14 detector produces today and which the existing vol-regime classifier
(``range``/``volatile``) does NOT. A model trained on this label is the
"trend-regime model" that can drop in for the ADX threshold detector at the
phase-4 seam (closes the taxonomy half of ``MB-20260601-002``).

The trend statistic is **Kaufman's Efficiency Ratio (ER)** over the forward
window — net directional move / gross path length:

    ER = |Σ r_j| / Σ |r_j|     for forward log-returns r_j ∈ [t+1 .. t+M]

ER ∈ [0, 1]: near 1 = a clean directional move (**trending**); near 0 = a
back-and-forth path that nets nowhere (**chop**). ER is the standard,
short-window-stable trend-strength measure — unlike ADX-14, which needs ~2×
its period to warm up and is unusable over a 5-bar forward window. Two
thresholds cut ER into the three regime labels.

**Leakage discipline:** ER is a property of the strictly-future window
``[t+1 .. t+M]`` — it is a LABEL, never a feature. Used as the target only;
the model's features stay past-only (the leakage gate forbids
``forward_*`` / ``*_regime_label`` as features). Pure stdlib so it unit-tests
in CI.
"""
from __future__ import annotations

from typing import Sequence

CHOP = "chop"
TRANSITIONAL = "transitional"
TRENDING = "trending"
TREND_REGIME_LABELS = (CHOP, TRANSITIONAL, TRENDING)


def efficiency_ratio(forward_log_returns: Sequence[float]) -> float | None:
    """Kaufman Efficiency Ratio of a forward log-return window.

    ``|sum(r)| / sum(|r|)`` ∈ [0, 1]. Returns ``None`` when the window is
    empty; ``0.0`` when the gross path is zero (a perfectly flat window — the
    most chop-like case).
    """
    rs = [float(r) for r in forward_log_returns if r is not None]
    if not rs:
        return None
    gross = sum(abs(r) for r in rs)
    if gross <= 0.0:
        return 0.0
    return abs(sum(rs)) / gross


def trend_regime_label(
    er: float | None, *, chop_max: float = 0.30, trend_min: float = 0.55
) -> str | None:
    """Map an efficiency ratio to a trend-regime label.

    ``er <= chop_max`` → ``chop``; ``er >= trend_min`` → ``trending``;
    in-between → ``transitional``. ``None`` ER → ``None`` (caller skips the row).
    """
    if er is None:
        return None
    if er <= chop_max:
        return CHOP
    if er >= trend_min:
        return TRENDING
    return TRANSITIONAL


def label_forward_window(
    forward_log_returns: Sequence[float],
    *,
    chop_max: float = 0.30,
    trend_min: float = 0.55,
) -> str | None:
    """Convenience: efficiency ratio → trend-regime label in one call."""
    return trend_regime_label(
        efficiency_ratio(forward_log_returns),
        chop_max=chop_max,
        trend_min=trend_min,
    )
