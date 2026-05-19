"""Adaptive policy for the VWAP strategy — a skip-list over market
regimes where the strategy consistently bleeds, regardless of
threshold.

Design (post-#1536 retest)
--------------------------
Earlier iterations (#1474, #1511) tried to assign a per-regime entry
threshold tuned to small-n backtests. The 24-window retest in #1533
exposed the failure mode: with n=1 per regime, a single-day shift in
the data window flipped ``weak-up/medium`` from +19 R to -24 R at
the same 1.2σ threshold. The per-regime threshold picks are noise we
cannot yet measure reliably; the **skip-list** is where the signal is.

This module is now a pure skip-list. For every regime not in the
skip set, ``lookup_policy`` returns ``threshold=None``, which the
adaptive backtest interprets as "do not override the module-level
``ENTRY_STD_THRESHOLD``" — i.e. use the live-trader default.

Skip-list (regimes the strategy loses in across all tested thresholds):

  regime           evidence
  ---------------  -------------------------
  weak-up/low      #1474 backtest: 3 windows × 5 thresholds, ALL lose
                   (-4 to -10 R/window). Mean-reversion longs into a
                   slow drift get steamrolled by the trend.
  sideways/low     #1511 adaptive backtest: 2 windows × 1.2σ (the
                   prior best-of-bad-lot pick) → -2.92 R mean. Chop
                   with no consistent edge at any tested threshold.

When the live signal builder fires:
  1. Classify current regime via ``regime.classify_regime``
  2. Look up policy via ``lookup_policy``
  3. If ``allow == False`` → no signal (skip)
  4. Else use the module constant ``ENTRY_STD_THRESHOLD`` as-is

Why skip-only beats per-regime thresholds
-----------------------------------------
The #1511 → #1533 comparison: same seed, +1 day of data, mean_total_r
collapsed from +8.38 R to +1.32 R. The active-regime threshold
picks (0.8σ for strong-down, 1.5σ for weak-down, 1.2σ for weak-up/medium)
swung wildly between draws because each was estimated from n=1. The
*skip* rules held up: skipped windows reliably went to zero in both
runs. Hence: keep what works, drop what doesn't.

Re-introducing per-regime thresholds will require ≥3 same-direction
samples per regime — until then, the trader's existing
``ENTRY_STD_THRESHOLD`` is the least-regret choice.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# Per-regime policy. Keys are ``"<trend>/<volatility>"`` strings
# emitted by ``regime.classify_regime``. Values are policy dicts:
#
#   {"allow": bool, "threshold": float | None, "rationale": str}
#
# ``threshold=None`` means "use the module-level ENTRY_STD_THRESHOLD"
# (do not override). Active entries here would be regime-specific
# overrides; with the post-#1536 skip-only design there are none.
#
# A regime not in the table falls back to ``DEFAULT_POLICY``.
POLICY_TABLE: Dict[str, Dict[str, Any]] = {
    "weak-up/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1474 backtest: 3 windows × 5 thresholds, ALL "
            "lose (-4 to -10 R/window). Mean-reversion longs into "
            "a slow drift get steamrolled by the trend."
        ),
    },
    "sideways/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1511 adaptive backtest: 2 windows × 1.2σ "
            "(the prior best-of-bad-lot pick from #1474) gave "
            "-2.92 R mean. Chop regime with no consistent edge — "
            "every threshold tested in #1474 was marginal-to-losing."
        ),
    },
}


# Fallback for any regime not in the skip-list: allow the trade, do
# not override the module-level threshold. The adaptive backtest
# and live signal builder both treat ``threshold=None`` as "use
# ``vwap.ENTRY_STD_THRESHOLD`` as-is".
DEFAULT_POLICY: Dict[str, Any] = {
    "allow": True,
    "threshold": None,
    "rationale": (
        "regime not in skip-list — use module ENTRY_STD_THRESHOLD. "
        "Per-regime threshold tuning removed in skip-only refactor "
        "after #1533 showed n=1 picks swing wildly between adjacent "
        "data windows."
    ),
}


def lookup_policy(regime: str) -> Dict[str, Any]:
    """Return the policy dict for *regime*.

    Always returns a dict with at minimum ``{"allow", "threshold",
    "rationale"}``. Falls back to ``DEFAULT_POLICY`` for any regime
    not in the skip-list.
    """
    pol = POLICY_TABLE.get(regime)
    if pol is None:
        out = dict(DEFAULT_POLICY)
        out["regime"] = regime
        out["fallback"] = True
        return out
    out = dict(pol)
    out["regime"] = regime
    out["fallback"] = False
    return out


def policy_for_candles(candles_df) -> Dict[str, Any]:
    """Convenience: classify candles then look up the policy in one
    call. Returns the policy dict augmented with the regime metadata
    (so callers can log both decisions in one place)."""
    from src.units.strategies.regime import classify_regime
    regime_info = classify_regime(candles_df)
    pol = lookup_policy(regime_info.get("regime") or "unknown")
    pol["_regime_info"] = regime_info
    return pol


def is_active_regime(regime: str) -> bool:
    """True when the policy allows trading *regime* (i.e. not in the
    skip-list). Useful for telemetry / per-regime trade-count
    auditing."""
    pol = lookup_policy(regime)
    return bool(pol.get("allow"))


def threshold_for(regime: str) -> Optional[float]:
    """Convenience: return the entry-σ threshold override for
    *regime*, or ``None`` when the policy says skip OR when no
    override applies (use the module constant).

    Note: ``None`` is overloaded — callers should also check
    ``is_active_regime`` to distinguish "skip" from "no override".
    """
    pol = lookup_policy(regime)
    if not pol.get("allow"):
        return None
    return pol.get("threshold")
