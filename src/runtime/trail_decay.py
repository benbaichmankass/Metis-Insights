"""M20 P4.1 — live trail-decay-on-exhaustion lever (shared by the
trend/pullback family monitors).

Harness reference: the ``--trail-decay-*`` lever in
``scripts/research/backtest_trend.py`` / ``scripts/backtest_pullback.py``
(design: ``docs/research/M20-momentum-exhaustion-DESIGN.md`` § P4.1) — the
effective chandelier trail mult TIGHTENS once the move shows exhaustion:

* **R-armed** — the since-entry favourable extreme has reached
  ``trail_decay_arm_r`` R (one-way: peak R only grows).
* **Stall-armed** — ``trail_decay_stall_bars`` or more bars have printed
  since the last new favourable extreme; a new peak re-loosens the MULT
  (the price-ratcheted STOP in the caller never loosens).

Contract (identical to the stale/giveback levers):

* **Declared** — ``trail_decay_tight_mult`` > 0 in the package meta (threaded
  from strategy YAML by ``order_package``) or live cfg ⇒
  :func:`resolve_trail_mult` returns the tightened mult while armed.
  Tier-3 per leg — a YAML declare only ships with operator approval.
* **Undeclared** ⇒ the base mult is returned unchanged (byte-identical
  monitor behaviour) and, when the REFERENCE cell (stall-6, tight = half the
  base floored at 1.5) would be armed, ONE observe-only annotate row is
  written to ``exit_lever_soak.jsonl`` — the pre-declare evidence trail.
* Fail-safe on every missing input; **never raises** into the monitor.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Reference cell for the observe-only annotate soak (fleet decay sweep's
# stall6 cell) — used ONLY when a strategy has not declared its own params.
_REF_STALL_BARS = 6


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def resolve_trail_mult(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    window,
    base_mult: float,
    direction: str,
) -> float:
    """Return the EFFECTIVE trail mult for this bar (base or tightened).

    ``window`` is the since-entry candle frame the caller already computed
    (``_since_entry``); pre-entry-fallback ambiguity is the caller's concern —
    a full-frame window fakes the peak, so we fail-safe to ``base_mult``
    whenever the entry anchor is unknowable (no ``entry_time`` in meta).
    """
    try:
        tight = _f(meta.get("trail_decay_tight_mult")
                   if meta.get("trail_decay_tight_mult") is not None
                   else cfg_dict.get("trail_decay_tight_mult"))
        arm_r = _f(meta.get("trail_decay_arm_r")
                   if meta.get("trail_decay_arm_r") is not None
                   else cfg_dict.get("trail_decay_arm_r")) or 0.0
        stall = meta.get("trail_decay_stall_bars")
        if stall is None:
            stall = cfg_dict.get("trail_decay_stall_bars")
        try:
            stall = int(stall) if stall is not None else 0
        except (TypeError, ValueError):
            stall = 0
        declared = tight is not None and tight > 0 and (arm_r > 0 or stall > 0)

        entry = _f(open_pkg.get("entry"))
        risk = _f(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return base_mult
        if not meta.get("entry_time"):
            return base_mult  # peak window unanchorable — fail-safe
        if window is None or len(window) < 2:
            return base_mult

        is_long = direction == "long"
        highs = window["high"].astype(float).to_numpy()
        lows = window["low"].astype(float).to_numpy()
        if is_long:
            peak = float(highs.max())
            peak_idx = int(highs.argmax())
            peak_r = (peak - entry) / risk
        else:
            peak = float(lows.min())
            peak_idx = int(lows.argmin())
            peak_r = (entry - peak) / risk
        bars_since_peak = (len(window) - 1) - peak_idx

        if declared:
            armed = ((arm_r > 0 and peak_r >= arm_r)
                     or (stall > 0 and bars_since_peak >= stall))
            return float(tight) if armed else base_mult

        # Annotate-only path (undeclared): evaluate the reference cell and
        # log ONE observe-only row per package when it would arm — the
        # pre-declare soak. Behaviour is unchanged (base mult returned).
        if bars_since_peak >= _REF_STALL_BARS:
            ref_tight = max(1.5, round(base_mult / 2.0, 1))
            try:
                from src.runtime.exit_lever_soak import record_exit_lever_annotation

                record_exit_lever_annotation(
                    lever="trail_decay",
                    strategy=str(meta.get("strategy_label")
                                 or open_pkg.get("strategy_name") or "unknown"),
                    symbol=str(open_pkg.get("symbol") or ""),
                    direction=direction,
                    order_package_id=open_pkg.get("order_package_id"),
                    params={"trail_decay_stall_bars": _REF_STALL_BARS,
                            "trail_decay_tight_mult": ref_tight,
                            "base_trail_mult": base_mult},
                    state={"bars_since_peak": int(bars_since_peak),
                           "peak_r": round(peak_r, 4)},
                )
            except Exception:  # noqa: BLE001 — annotate must never affect the path
                pass
        return base_mult
    except Exception:  # noqa: BLE001 — the monitor must never feel this
        logger.debug("trail_decay: resolve failed", exc_info=True)
        return base_mult
