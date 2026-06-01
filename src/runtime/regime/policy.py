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

``would_gate(*, strategy, side, regime, policy) -> dict``
    Evaluate one cell. Returns
    ``{gated: bool, reason: str, cell: "on"|"off"|"weight"|"default-on", regime, strategy, side}``.
    ``gated=True`` iff the cell is explicitly ``off`` for that direction.
    Unlisted (strategy, regime) or (strategy, regime, direction) cells
    return ``gated=False`` with ``cell="default-on"`` — the permissive
    default that mirrors the YAML-default philosophy of the two execution
    gates.
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


def would_gate(
    *,
    strategy: str,
    side: str,
    regime: Optional[str],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Phase-2 cell evaluator. Returns the per-intent shadow-gate verdict.

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
