"""Regime × strategy × direction policy evaluator (PERF-20260601-002 phase 2).

Phase 2 of the regime router (`docs/research/regime-router-design-2026-06-01.md`
§ 4.2 / § 5.2): SHADOW the policy table. For every intent the aggregator
sees, evaluate the cell at ``(regime, strategy, direction)`` and emit a
``regime_shadow_gate`` audit row when an ``off`` cell WOULD suppress the
intent — without actually changing the aggregator's decision. The point is
to compare a week of would-gate counts against actual fills before phase 3
turns the gates live.

API
---
``load_policy(path=None) -> dict``
    Read the YAML at ``path`` (default: ``config/regime_policy.yaml``).
    Returns ``{}`` (treated as permissive everywhere) if the file is
    missing or malformed — phase 2 is observability-only and must NEVER
    break a tick. Logs a warning.

``would_gate(*, strategy, side, regime, policy, vol_regime=None) -> dict``
    Evaluate one cell. Returns
    ``{gated: bool, reason: str, cell: "on"|"off"|"weight"|"default-on", regime, strategy, side}``.
    ``gated=True`` iff the cell is explicitly ``off`` for that direction.
    Unlisted (strategy, regime) or (strategy, regime, direction) cells
    return ``gated=False`` with ``cell="default-on"`` — the permissive
    default that mirrors the YAML-default philosophy of the two execution
    gates.

    **2-D vol axis (S-MLOPT-S15b).** When ``vol_regime`` is provided, the
    verdict additionally carries ``{vol_regime, vol_gated, vol_cell,
    vol_reason}`` from the optional ``trend_vol`` block of the policy
    (``policy["trend_vol"][regime][vol_regime][strategy][side]``). This is
    **observe-only** — the vol cell never changes the 1-D ``gated`` decision;
    it accrues would-gate evidence for a later Tier-3 2-D enforcement decision.
    When ``vol_regime`` is ``None`` the return is **byte-identical** to the
    pre-S15b 1-D shape (no ``vol_*`` keys), so every existing caller is
    unchanged.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_REGIME_POLICY_PATH = os.environ.get(
    "REGIME_POLICY_PATH",
    str(Path(__file__).resolve().parents[3] / "config" / "regime_policy.yaml"),
)
_VALID_REGIMES = {"chop", "transitional", "trending"}
_VALID_SIDES = {"long", "short"}
# Vol axis (S-MLOPT-S15b). 2-class to match the existing classifier; see
# ``src.runtime.regime.vol_detector``.
_VALID_VOL_REGIMES = {"calm", "volatile"}
# Optional top-level block holding the 2-D trend × vol cells. Absent → the vol
# axis is fully permissive (vol_cell="default-on") and the 1-D behaviour is
# unchanged. Shape: ``{regime: {vol_regime: {strategy: {side: on|off}}}}``.
_TREND_VOL_KEY = "trend_vol"


def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the policy YAML; never raises.

    Phase 2 is observability-only — a missing or malformed file must NOT
    block the tick. We log a warning and return ``{}`` (permissive
    everywhere) so the aggregator's behaviour is unchanged when the
    operator hasn't yet authored the table.
    """
    p = path or _REGIME_POLICY_PATH
    if not os.path.isfile(p):
        logger.warning("regime_policy: file not found at %s; treating as permissive", p)
        return {}
    try:
        import yaml  # local import so the runtime can boot even if pyyaml is unavailable
    except ImportError:
        logger.warning("regime_policy: PyYAML not importable; treating as permissive")
        return {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001 — observability-only
        logger.warning("regime_policy: failed to parse %s (%s); treating as permissive", p, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("regime_policy: %s did not parse to a mapping; treating as permissive", p)
        return {}
    return raw


def _evaluate_trend_cell(
    *,
    strategy: str,
    side: str,
    regime: Optional[str],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """The 1-D trend-axis verdict (unchanged pre-S15b logic).

    Output is the audit-row payload (minus the ``event``/``ts`` envelope —
    those are added by the audit logger). Stable shape:

      {
        "gated":   bool,     # would phase 3 suppress this intent?
        "reason":  str,      # short tag for grep-ability
        "cell":    str,      # "off" | "on" | "weight" | "default-on" | "unknown-*"
        "regime":  str,      # echoed for log-stream filterability
        "strategy": str,
        "side":    str,
      }

    Rules:
      * ``side`` not in {long, short} → ``gated=False`` (flat / unknown sides
        are never gated; only directional intents go through the policy).
      * ``regime`` not in {chop, transitional, trending} → ``gated=False``,
        ``cell="unknown-regime"``. Reflects an ADX warmup / detector failure
        upstream; permissive default applies.
      * Strategy / regime not in policy → ``gated=False``, ``cell="default-on"``.
      * Cell is explicitly ``off`` for the requested side → ``gated=True``,
        ``cell="off"``, ``reason="regime_gated_<regime>"``.
      * Cell is explicitly ``on`` → ``gated=False``, ``cell="on"``.
      * Cell is a numeric weight (future phase) → ``gated=False``,
        ``cell="weight"``, ``reason="weight_pending_phase4"``.
      * Anything else (string we don't recognise) → ``gated=False``,
        ``cell="unknown-value"`` — permissive on malformed cells; logged once.
    """
    base = {
        "gated": False,
        "reason": "permissive_default",
        "cell": "default-on",
        "regime": regime,
        "strategy": strategy,
        "side": side,
    }
    if side not in _VALID_SIDES:
        # Flat / unknown direction is never gated by direction-based policy.
        base["reason"] = f"non_directional_side_{side}"
        return base
    if regime not in _VALID_REGIMES:
        # ADX warmup or detector failure upstream; permissive.
        base["cell"] = "unknown-regime"
        base["reason"] = f"regime_unknown_{regime}"
        return base
    regime_block = policy.get(regime)
    if not isinstance(regime_block, dict):
        # Regime not in the table → permissive default.
        return base
    cell = regime_block.get(strategy)
    if not isinstance(cell, dict):
        # Strategy not listed in this regime → permissive default.
        return base
    value = cell.get(side)
    # PyYAML maps `on`/`off` -> True/False (Python booleans) by default;
    # tolerate the literal strings too so a hand-edit doesn't accidentally
    # change semantics.
    if value is False or value == "off":
        return {
            "gated": True,
            "reason": f"regime_gated_{regime}",
            "cell": "off",
            "regime": regime,
            "strategy": strategy,
            "side": side,
        }
    if value is True or value == "on":
        return {
            "gated": False,
            "reason": "regime_allow_explicit",
            "cell": "on",
            "regime": regime,
            "strategy": strategy,
            "side": side,
        }
    if isinstance(value, (int, float)):
        # Future phase-4 soft weight — not active yet; permissive.
        return {
            "gated": False,
            "reason": "weight_pending_phase4",
            "cell": "weight",
            "regime": regime,
            "strategy": strategy,
            "side": side,
        }
    if value is None:
        # Direction not specified for this strategy → permissive default.
        return base
    # Anything else (typo, unexpected type) → permissive + flag.
    return {
        "gated": False,
        "reason": f"unrecognised_cell_value_{type(value).__name__}",
        "cell": "unknown-value",
        "regime": regime,
        "strategy": strategy,
        "side": side,
    }


def _evaluate_vol_cell(
    *,
    strategy: str,
    side: str,
    regime: Optional[str],
    vol_regime: Optional[str],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """The 2-D ``trend × vol`` verdict (S-MLOPT-S15b, observe-only).

    Returns ``{vol_regime, vol_gated, vol_cell, vol_reason}``. The cell is
    looked up at ``policy["trend_vol"][regime][vol_regime][strategy][side]``;
    every missing level is the permissive default (``vol_cell="default-on"``),
    mirroring the 1-D evaluator. Same ``on``/``off``/numeric-weight tolerance.
    Never gates on a non-directional side or an unknown trend/vol regime.
    """
    base = {
        "vol_regime": vol_regime,
        "vol_gated": False,
        "vol_cell": "default-on",
        "vol_reason": "vol_permissive_default",
    }
    if side not in _VALID_SIDES:
        base["vol_reason"] = f"non_directional_side_{side}"
        return base
    if regime not in _VALID_REGIMES:
        base["vol_cell"] = "trend-unknown"
        base["vol_reason"] = f"trend_regime_unknown_{regime}"
        return base
    if vol_regime not in _VALID_VOL_REGIMES:
        base["vol_cell"] = "vol-unknown"
        base["vol_reason"] = f"vol_regime_unknown_{vol_regime}"
        return base
    trend_vol = policy.get(_TREND_VOL_KEY)
    if not isinstance(trend_vol, dict):
        return base  # no 2-D table → permissive
    regime_block = trend_vol.get(regime)
    if not isinstance(regime_block, dict):
        return base
    vol_block = regime_block.get(vol_regime)
    if not isinstance(vol_block, dict):
        return base
    cell = vol_block.get(strategy)
    if not isinstance(cell, dict):
        return base
    value = cell.get(side)
    if value is False or value == "off":
        return {
            "vol_regime": vol_regime,
            "vol_gated": True,
            "vol_cell": "off",
            "vol_reason": f"vol_gated_{regime}_{vol_regime}",
        }
    if value is True or value == "on":
        return {
            "vol_regime": vol_regime,
            "vol_gated": False,
            "vol_cell": "on",
            "vol_reason": "vol_allow_explicit",
        }
    if isinstance(value, (int, float)):
        return {
            "vol_regime": vol_regime,
            "vol_gated": False,
            "vol_cell": "weight",
            "vol_reason": "vol_weight_pending_phase4",
        }
    if value is None:
        return base
    return {
        "vol_regime": vol_regime,
        "vol_gated": False,
        "vol_cell": "unknown-value",
        "vol_reason": f"vol_unrecognised_cell_value_{type(value).__name__}",
    }


def would_gate(
    *,
    strategy: str,
    side: str,
    regime: Optional[str],
    policy: Dict[str, Any],
    vol_regime: Optional[str] = None,
) -> Dict[str, Any]:
    """Per-intent shadow-gate verdict (trend axis, + optional vol axis).

    Default-preserving: with ``vol_regime=None`` the return is the exact 1-D
    trend verdict (no ``vol_*`` keys) — byte-identical to the pre-S15b shape,
    so every existing caller is unchanged. With ``vol_regime`` supplied the
    verdict is augmented with the **observe-only** 2-D ``trend × vol`` keys
    ``{vol_regime, vol_gated, vol_cell, vol_reason}`` (see ``_evaluate_vol_cell``);
    the 1-D ``gated`` decision is never altered by the vol axis.
    """
    trend = _evaluate_trend_cell(
        strategy=strategy, side=side, regime=regime, policy=policy,
    )
    if vol_regime is None:
        return trend
    vol = _evaluate_vol_cell(
        strategy=strategy, side=side, regime=regime,
        vol_regime=vol_regime, policy=policy,
    )
    return {**trend, **vol}
