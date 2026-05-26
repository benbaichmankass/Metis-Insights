"""Adaptive policy for the VWAP strategy — a skip-list (regimes
where the strategy bleeds at every tested threshold) plus a
narrow allow-list of per-regime threshold overrides that have
≥3 same-direction backtest samples behind them.

Design (post-#1536 retest)
--------------------------
Earlier iterations (#1474, #1511) tried to assign a per-regime entry
threshold tuned to small-n backtests. The #1511 → #1533 comparison
exposed the failure mode: with n=1 per regime, a single-day shift
in the data window flipped ``weak-up/medium`` from +19 R to -24 R
at the same 1.2σ threshold. Most per-regime threshold picks are
noise we cannot yet measure reliably.

#1536 (24 random 14-day windows × 365 days) gave the first
properly-powered look. Per-regime n ranged from 1 to 6:

  regime           n    policy        mean_R     positive  evidence
  ---------------  ---  ------------  ---------  --------  --------
  strong-up/low    6    2.0σ entry     +7.98      5/6      n≥3 ✓ (kept)
  strong-up/medium 3    0.8σ entry     -4.87      1/3      n≥3 but losing — drop
  weak-down/low    3    1.5σ entry     +0.08      2/3      n≥3 but flat — SKIP
  strong-down/low  2    0.8σ entry    +10.73      1/2      n<3 — drop (revisit)
  sideways/low     3    SKIP            0.00      —        skip held up ✓
  weak-up/low      3    SKIP            0.00      —        skip held up ✓
  (others)         1    various         noise     —        n<3 — drop

The threshold for keeping a per-regime override is *both* n≥3 *and*
positive mean_R. Only ``strong-up/low @ 2.0σ`` clears that bar today.
``strong-up/medium`` has n=3 but is losing — dropped to the default
threshold. ``weak-down/low`` has n=3 but is flat — moved to SKIP
(2026-05-26, see entry rationale) after the live signal-firing rate
at the 1.0σ fall-through proved to be pure noise (15 reinforcement
fires in 2h, all aggregated to target_qty=0). The n=1/n=2 picks are
all dropped; allowed regimes fall through to the module-level
``ENTRY_STD_THRESHOLD``.

When the live signal builder fires:
  1. Classify current regime via ``regime.classify_regime``
  2. Look up policy via ``lookup_policy``
  3. If ``allow == False`` → no signal (skip)
  4. Else if ``threshold`` is not None → override the module entry
     threshold with ``threshold`` for this signal
  5. Else use the module constant ``ENTRY_STD_THRESHOLD`` as-is

Re-introducing more per-regime overrides requires the same
threshold (n≥3 same-direction samples *and* positive mean_R). New
candidates fall out of the next ≥24-window sweep.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# Per-regime policy. Keys are ``"<trend>/<volatility>"`` strings
# emitted by ``regime.classify_regime``. Values are policy dicts:
#
#   {"allow": bool, "threshold": float | None, "rationale": str}
#
# ``threshold=None`` on an ``allow=True`` entry means "use the
# module-level ENTRY_STD_THRESHOLD" (no override). A regime not in
# the table falls back to ``DEFAULT_POLICY``.
POLICY_TABLE: Dict[str, Dict[str, Any]] = {
    # Skipped regimes — historical evidence shows the strategy
    # loses regardless of threshold. Stand down.
    "weak-up/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1474 backtest: 3 windows × 5 thresholds, ALL "
            "lose (-4 to -10 R/window). #1536 reconfirmed at n=3: "
            "skipped windows go to zero, no recovery at any "
            "threshold. Mean-reversion longs into a slow drift get "
            "steamrolled by the trend."
        ),
    },
    "sideways/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1511 adaptive backtest: 2 windows × 1.2σ "
            "(prior best-of-bad-lot pick from #1474) gave -2.92 R "
            "mean. #1536 reconfirmed at n=3: chop with no consistent "
            "edge at any tested threshold."
        ),
    },
    "weak-down/low": {
        "allow": False,
        "threshold": None,
        "rationale": (
            "issue #1536 24-window adaptive: n=3 @ 1.5σ entry, mean "
            "+0.08 R, 2/3 positive — flat at the *tightened* threshold, "
            "and the table previously dropped this to the default 1.0σ "
            "fall-through (looser than the already-flat 1.5σ). "
            "2026-05-26 health-review confirmed the live cost: 15 same-"
            "direction reinforcement fires in a 2h window, every one "
            "aggregating to target_qty=0 against an open trend_donchian "
            "long — zero placements, pure review-time noise (BL-009 "
            "ghost packages). Skip is the consistent move with the n≥3 "
            "flat-mean_R drop rule for non-strong-up regimes."
        ),
    },

    # Active per-regime overrides — kept only when ≥3 same-direction
    # backtest samples agree on a positive mean_R at the override
    # threshold. Today the bar is cleared by exactly one regime.
    "strong-up/low": {
        "allow": True, "threshold": 2.0,
        "rationale": (
            "issue #1536 24-window adaptive: n=6, mean +7.98 R, "
            "5/6 windows positive at 2.0σ entry. Tighter entry "
            "qualifies fewer counter-trend longs in a strong-up "
            "regime, which is the only structural reason the "
            "edge survives where 1.0σ-default would bleed."
        ),
    },
}


# Fallback for any regime not in the table: allow the trade, do
# not override the module-level threshold. The adaptive backtest
# and live signal builder both treat ``threshold=None`` as "use
# ``vwap.ENTRY_STD_THRESHOLD`` as-is".
DEFAULT_POLICY: Dict[str, Any] = {
    "allow": True,
    "threshold": None,
    "rationale": (
        "regime not in policy table — use module "
        "ENTRY_STD_THRESHOLD. Per-regime overrides require n≥3 "
        "same-direction samples + positive mean_R; only "
        "strong-up/low cleared that bar in #1536."
    ),
}


def lookup_policy(regime: str) -> Dict[str, Any]:
    """Return the policy dict for *regime*.

    Always returns a dict with at minimum ``{"allow", "threshold",
    "rationale"}``. Falls back to ``DEFAULT_POLICY`` for any regime
    not in the table.
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
