"""Adaptive policy for the VWAP strategy — maps market regime to
entry threshold (and a skip flag for regimes where no threshold
works).

Why a lookup table
------------------
Issue #1474's 365-day backtest revealed regime-dependent edge:

  regime           best threshold  total R (8 windows × 14d)
  ---------------  --------------  -------------------------
  strong-down/low  0.8σ            +26.9
  weak-down/low    1.5σ            +26.8
  weak-up/medium   1.2σ            +19.2
  weak-up/low      ANY = LOSS      -5 to -10  (3/3 windows)
  sideways/low     2.0σ            marginal

No single fixed threshold wins across the year — but a policy that
picks the per-regime optimum (and refuses to trade `weak-up/low`)
beats every fixed-threshold variant on the same data. This module
is the lookup table.

When the live signal builder fires:
  1. Classify current regime via ``regime.classify_regime``
  2. Look up policy via ``lookup_policy``
  3. If ``allow == False`` → no signal
  4. Else use the policy's ``entry_std_threshold`` instead of the
     module constant ``ENTRY_STD_THRESHOLD``

Sample-size caveat
------------------
The 365-day backtest had 8 random 14-day windows distributed across
5 regimes. Some regimes have n=1. Optimal-threshold picks are
provisional and should be re-validated as the trader collects more
live data per regime. The skip flag for ``weak-up/low`` is the
highest-confidence call (n=3 windows, all losing, all thresholds).
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# Per-regime policy. Keys are ``"<trend>/<volatility>"`` strings
# emitted by ``regime.classify_regime``. Values are policy dicts:
#
#   {"allow": bool, "threshold": float | None, "rationale": str}
#
# A regime not in the table falls back to ``DEFAULT_POLICY``.
POLICY_TABLE: Dict[str, Dict[str, Any]] = {
    # Skipped regimes — historical evidence shows the strategy
    # loses regardless of threshold. Stand down.
    "weak-up/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1474 backtest: 3 windows × 5 thresholds, ALL "
            "lose (-4 to -10 R/window). Mean-reversion longs into "
            "a slow drift get steamrolled by the trend."
        ),
    },

    # Active regimes with per-regime threshold tuned to backtest.
    "strong-down/low": {
        "allow": True, "threshold": 0.8,
        "rationale": "issue #1474: +26.9 R at 0.8σ (n=1)",
    },
    "strong-down/medium": {
        "allow": True, "threshold": 2.0,
        "rationale": "issue #1471 (90d): +29.1 R at 2.0σ (n=1)",
    },
    "weak-down/low": {
        "allow": True, "threshold": 1.5,
        "rationale": "issue #1474: +26.8 R at 1.5σ (n=1)",
    },
    "weak-down/medium": {
        # No direct data — borrow from weak-down/low (same trend,
        # higher volatility usually means same threshold works).
        "allow": True, "threshold": 1.5,
        "rationale": "extrapolated from weak-down/low (no direct data)",
    },
    "sideways/low": {
        "allow": True, "threshold": 1.2,
        "rationale": "issue #1474: 1.2σ best of bad lot in this regime",
    },
    "sideways/medium": {
        "allow": True, "threshold": 0.8,
        "rationale": "issue #1471 (90d): +29.0 R at 0.8σ (n=1)",
    },
    "weak-up/medium": {
        "allow": True, "threshold": 1.2,
        "rationale": "issue #1474: +19.2 R at 1.2σ (n=1)",
    },
    "strong-up/low": {
        "allow": True, "threshold": 2.0,
        "rationale": "issue #1471 (90d): +14.3 R at 2.0σ (n=1)",
    },
    "strong-up/medium": {
        "allow": True, "threshold": 0.8,
        "rationale": "issue #1471 (90d): +39.4 R at 0.8σ (n=2)",
    },
}


# Fallback when the regime isn't in the table (e.g. high-volatility
# regimes we haven't sampled, or "unknown"). 1.2σ was the best
# overall fixed threshold on the 365d backtest, so it's the least-
# regret default.
DEFAULT_POLICY: Dict[str, Any] = {
    "allow": True,
    "threshold": 1.2,
    "rationale": "no per-regime data; 1.2σ best overall on 365d sweep",
}


def lookup_policy(regime: str) -> Dict[str, Any]:
    """Return the policy dict for *regime*.

    Always returns a dict with at minimum ``{"allow", "threshold",
    "rationale"}``. Falls back to ``DEFAULT_POLICY`` for unknown
    regimes.
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
    """True when the policy table has a non-skip entry for *regime*.
    Useful for telemetry / per-regime trade-count auditing."""
    pol = POLICY_TABLE.get(regime)
    return bool(pol and pol.get("allow"))


def threshold_for(regime: str) -> Optional[float]:
    """Convenience: return the entry-σ threshold for *regime*, or
    ``None`` when the policy says skip."""
    pol = lookup_policy(regime)
    if not pol.get("allow"):
        return None
    return pol.get("threshold")
