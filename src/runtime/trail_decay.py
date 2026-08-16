"""M20 P4.1 — live trail-decay-on-exhaustion lever (shared by the
trend/pullback family monitors).

Harness reference: the ``--trail-decay-*`` lever in
``scripts/backtest_trend.py`` / ``scripts/backtest_pullback.py``
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


# The since-entry peak, as ONE definition (M31 P2).
#
# `resolve_trail_mult` below and `src/runtime/position_telemetry.py` both need
# the same quantity — the since-entry favourable extreme in R, i.e. MFE. Before
# M31 this math existed twice already (here and
# `trend_donchian._giveback_verdict`), computed on every pass and discarded, and
# adding a third copy for telemetry is exactly how `_regime_score_semantics.py`
# had to be written: two probes independently re-derived "what is the shadow
# log's score?" and BOTH got it wrong on the same day.
#
# FOUR states, never collapsed. Three of them mean "we could not measure" and
# they are NOT interchangeable — a caller that treats `thin_window` as
# `peak_r = 0.0` has fabricated a flat trade out of a short frame.
# The assignment sites below emit these LITERALS rather than the constants, so
# a reader of the producer sees every state it can return without chasing a
# name (and `collapsed-state-guard`'s producer check can see them too). The
# constants remain the vocabulary consumers import; a test pins the two equal.
PEAK_MEASURED = "measured"
PEAK_UNANCHORED = "unanchored"      # no entry_time — the window is not since-entry
PEAK_THIN_WINDOW = "thin_window"    # < 2 bars: no excursion is observable
PEAK_NO_RISK = "no_risk"            # risk missing/non-positive: R is undefined


def since_entry_peak(window, entry, risk, direction, anchored: bool = True) -> Dict[str, Any]:
    """MFE in R over the since-entry window, with the reason when it is absent.

    ``anchored`` is the caller's assertion that ``window`` really is the
    since-entry frame (i.e. ``meta['entry_time']`` was present). A full-frame
    fallback fakes the peak, so it is reported as `unanchored` rather than
    silently measured — the same fail-safe `resolve_trail_mult` has always
    applied, now named instead of implicit.

    Never raises: a malformed frame returns a state, not an exception.
    """
    out: Dict[str, Any] = {"peak_state": "no_risk", "peak": None, "peak_r": None,
                           "bars_since_peak": None, "bars": 0}
    e, r = _f(entry), _f(risk)
    if e is None or r is None or r <= 0:
        return out
    if not anchored:
        out["peak_state"] = "unanchored"
        return out
    try:
        n = len(window) if window is not None else 0
    except TypeError:
        n = 0
    out["bars"] = int(n)
    if n < 2:
        out["peak_state"] = "thin_window"
        return out
    try:
        if direction == "long":
            arr = window["high"].astype(float).to_numpy()
            peak = float(arr.max())
            peak_idx = int(arr.argmax())
            peak_r = (peak - e) / r
        else:
            arr = window["low"].astype(float).to_numpy()
            peak = float(arr.min())
            peak_idx = int(arr.argmin())
            peak_r = (e - peak) / r
    except (KeyError, ValueError, TypeError, AttributeError):
        out["peak_state"] = "thin_window"
        return out
    if not math.isfinite(peak_r):
        out["peak_state"] = "no_risk"
        return out
    out.update({"peak_state": "measured", "peak": peak,
                "peak_r": peak_r, "bars_since_peak": (n - 1) - peak_idx})
    return out


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

        # ONE definition of the since-entry peak (see `since_entry_peak` above).
        # The guards this replaces are unchanged: the `entry_time` and
        # `len(window) < 2` checks already returned `base_mult` before reaching
        # here, so a non-`measured` state cannot occur on this path — it is
        # handled anyway rather than assumed away.
        peak_res = since_entry_peak(window, entry, risk, direction)
        if peak_res["peak_state"] != PEAK_MEASURED:
            return base_mult
        peak_r = float(peak_res["peak_r"])
        bars_since_peak = int(peak_res["bars_since_peak"])

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
