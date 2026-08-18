"""The M20 R-based exit levers, owned in ONE place.

`stale_stop` and `giveback_stop` lived only inside
`src/units/strategies/trend_donchian.py`, so the pullback family could not run
them however its YAML was written — while `scripts/backtest_pullback.py` HAS
modelled both since M20. That asymmetry is the defect: **the harness simulated a
book the live module could not execute**, which is the usual live-vs-train
complaint running backwards, and it makes any sweep result recommending
`stale_exit_bars` for a pullback leg unactionable — declaring it would produce
an ORPHANED DECLARE, a YAML key nothing reads.

Measured 2026-08-18 (`scripts/ops/exit_path_coverage.py`): 22 of 34 open trades
had no decision-driven exit path at all, and 11 of those are the pullback
family, whose unit implements exactly one of the four M20 mechanisms.

This module is the same shape `src/runtime/trail_decay.py` and
`src/runtime/trail_vol.py` already established — a lever both units import
rather than each owning a copy. The bodies here are `trend_donchian`'s,
moved VERBATIM: the only edits are the two donchian-specific pieces, and both
are now parameters rather than literals (`default_label` for the annotate row,
`since_entry` resolved here instead of per-module). trend_donchian's behaviour
must be, and is asserted to be, unchanged — it trades real money.

**This also collapses a duplicate.** `_since_entry` existed in BOTH unit
modules, byte-identical apart from its docstring. Two copies of a window
definition that every R measurement depends on is exactly what
`_regime_score_semantics.py` had to be written to stop.

THE DECLARE IS STILL THE GATE, unchanged from donchian's contract. A leg whose
YAML declares the lever gets a REAL close; an undeclared leg evaluates the
reference cell and writes one observe-only row to
`runtime_logs/exit_lever_soak.jsonl`, returning None. So importing this module
changes NO behaviour on its own — shipping it is inert until an operator
declares a key, which is the Tier-3 decision.

⚠️ **PRECEDENCE IS THE CALLER'S, AND THE TWO FAMILIES DISAGREE TODAY.** Both
harnesses evaluate giveback BEFORE stale (`backtest_trend.py` lines ~566/582 in
one per-bar loop; `backtest_pullback._levers` giveback → trend_flip → stale),
while live `trend_donchian.monitor` runs stale BEFORE giveback under a comment
claiming it "matches the harness's exit precedence" — it does not. Field beats
comment. This is LATENT, not active: no live leg declares both keys (checked
2026-08-18 against `config/strategies.yaml` — stale-only on 3 legs,
giveback-only on 1, both on none), and when only one is declared the order
cannot matter. It is filed as
BL-20260818-LIVE-DONCHIAN-INVERTS-THE-HARNESS-LEVER-PRECEDENCE rather than
fixed here, because flipping a live-money family's exit order is Tier-3 and
must not ride along inside a refactor. New callers should follow their OWN
harness.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pandas as pd

# Reference cells for the observe-only annotate soak — used ONLY when a leg has
# NOT declared its own params. A declared leg uses exactly what its YAML says.
# Values are trend_donchian's, moved unchanged so the soak corpus stays one
# series rather than splitting at this commit.
STALE_REF_BARS = 8
STALE_REF_BELOW_R = 0.0
GIVEBACK_REF_MIN_MFE_R = 1.0
GIVEBACK_REF_GIVEBACK_R = 1.0

_STALE_REF_BARS = STALE_REF_BARS
_STALE_REF_BELOW_R = STALE_REF_BELOW_R
_GIVEBACK_REF_MIN_MFE_R = GIVEBACK_REF_MIN_MFE_R
_GIVEBACK_REF_GIVEBACK_R = GIVEBACK_REF_GIVEBACK_R


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def since_entry(candles_df: pd.DataFrame, open_pkg: Dict[str, Any]) -> pd.DataFrame:
    """Restrict the candle window to bars at/after the package entry time.

    The Chandelier trail tracks the extreme SINCE ENTRY; the fetched
    window (limit=200) can include pre-entry bars whose extreme would
    move the trail too far. Falls back to the full frame when the entry
    time or a timestamp column is unavailable — the caller's
    correct-side-of-price guard still prevents an instant stop-out in
    that case.
    """
    meta = open_pkg.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:  # noqa: BLE001
            meta = {}
    entry_ts = (meta.get("entry_time") if isinstance(meta, dict) else None) or \
        open_pkg.get("created_at")
    if entry_ts is None or "timestamp" not in getattr(candles_df, "columns", []):
        return candles_df
    try:
        ts = pd.to_datetime(candles_df["timestamp"], utc=True, errors="coerce")
        cutoff = pd.to_datetime(entry_ts, utc=True, errors="coerce")
        if pd.isna(cutoff):
            return candles_df
        filtered = candles_df[ts >= cutoff]
        return filtered if len(filtered) > 0 else candles_df
    except Exception:  # noqa: BLE001
        return candles_df


def stale_stop_verdict(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    candles_df: pd.DataFrame,
    current_price: float,
    direction: str,
    default_label: str = "",
) -> Optional[Dict[str, Any]]:
    """M20 conditional stale-stop — close a position that is ≥ N native bars
    old and still below the declared open-R threshold at bar close.

    Declared (``stale_exit_bars`` in meta/cfg) ⇒ may return a real
    ``{"action": "close", "reason": "stale_stop"}`` verdict. Undeclared ⇒
    evaluates the reference cell (8 bars, < 0R) and writes one observe-only
    annotate row when it would fire, returning ``None`` (behaviour unchanged).
    Fail-safe: any missing input (entry_time, frozen risk, entry) skips both
    paths — never a spurious close. **Never raises.**
    """
    try:
        declared_bars = _coerce_int(
            meta.get("stale_exit_bars") if meta.get("stale_exit_bars") is not None
            else cfg_dict.get("stale_exit_bars")
        )
        below_r_raw = (
            meta.get("stale_exit_below_r")
            if meta.get("stale_exit_below_r") is not None
            else cfg_dict.get("stale_exit_below_r")
        )
        below_r = _coerce_float(below_r_raw)
        n_bars = declared_bars if declared_bars is not None else _STALE_REF_BARS
        threshold = below_r if (declared_bars is not None and below_r is not None) \
            else (_STALE_REF_BELOW_R if declared_bars is None else 0.0)

        entry = _coerce_float(open_pkg.get("entry"))
        risk = _coerce_float(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return None
        if not meta.get("entry_time"):
            return None  # age unknowable — fail-safe skip
        window = since_entry(candles_df, open_pkg)
        # _since_entry falls back to the FULL frame when the entry time can't
        # be matched; that would fake a huge age, so require a real restriction
        # (or a genuinely long-lived trade spanning the whole fetch window).
        if len(window) >= len(candles_df) and len(candles_df) > 0:
            # Ambiguous: either fallback or a trade older than the fetch
            # window (limit≈200 bars ≫ any sane stale_exit_bars). Treat a
            # full-window match as "at least window-length old" ONLY when the
            # first window bar is at/after the entry time; _since_entry
            # guarantees that when it actually filtered, so equality here
            # means fallback — skip (fail-safe).
            return None
        age_bars = max(0, len(window) - 1)  # bars strictly after the entry bar
        if age_bars < n_bars:
            return None
        open_r = ((current_price - entry) if direction == "long"
                  else (entry - current_price)) / risk
        if open_r >= threshold:
            return None
        if declared_bars is not None:
            return {"action": "close", "reason": "stale_stop",
                    "exit_price": current_price}
        # Annotate-only path (undeclared): observe, never act.
        try:
            from src.runtime.exit_lever_soak import record_exit_lever_annotation

            record_exit_lever_annotation(
                lever="stale_stop",
                strategy=str(meta.get("strategy_label")
                             or open_pkg.get("strategy_name") or default_label),
                symbol=str(open_pkg.get("symbol") or ""),
                direction=direction,
                order_package_id=open_pkg.get("order_package_id"),
                params={"stale_exit_bars": n_bars,
                        "stale_exit_below_r": threshold},
                state={"age_bars": age_bars, "open_r": round(open_r, 4),
                       "price": current_price, "entry": entry},
            )
        except Exception:  # noqa: BLE001 — annotate must never affect the path
            pass
        return None
    except Exception:  # noqa: BLE001 — monitor must never crash on this lever
        return None


def giveback_verdict(
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    candles_df: pd.DataFrame,
    current_price: float,
    direction: str,
    default_label: str = "",
) -> Optional[Dict[str, Any]]:
    """M20 giveback-stop — close a position that has seen at least
    ``giveback_min_mfe_r`` R of open profit (peak basis, since entry) and
    has given back at least ``giveback_r`` R from that peak at bar close.
    An R-based profit lock, distinct from the price/ATR chandelier trail —
    the harness reference is ``scripts/backtest_trend.py``'s giveback lever
    (identical peak_r/r_close math; the research copy was retired 2026-08-09).

    Declared (BOTH ``giveback_min_mfe_r`` AND ``giveback_r`` positive in
    meta/cfg) ⇒ may return a real ``{"action": "close", "reason":
    "giveback_stop"}`` verdict. Undeclared ⇒ evaluates the reference cell
    (1R giveback after 1R MFE) and writes one observe-only annotate row
    when it would fire, returning ``None`` (behaviour unchanged).
    Fail-safe: any missing input (entry, frozen risk, entry_time, an
    unrestrictable candle window whose pre-entry bars would fake the
    peak) skips both paths — never a spurious close. **Never raises.**
    """
    try:
        declared_min_mfe = _coerce_float(
            meta.get("giveback_min_mfe_r")
            if meta.get("giveback_min_mfe_r") is not None
            else cfg_dict.get("giveback_min_mfe_r")
        )
        declared_gb = _coerce_float(
            meta.get("giveback_r") if meta.get("giveback_r") is not None
            else cfg_dict.get("giveback_r")
        )
        declared = (declared_min_mfe is not None and declared_min_mfe > 0
                    and declared_gb is not None and declared_gb > 0)
        min_mfe_r = declared_min_mfe if declared else _GIVEBACK_REF_MIN_MFE_R
        giveback_r = declared_gb if declared else _GIVEBACK_REF_GIVEBACK_R

        entry = _coerce_float(open_pkg.get("entry"))
        risk = _coerce_float(meta.get("risk_per_unit"))
        if entry is None or risk is None or risk <= 0:
            return None
        if not meta.get("entry_time"):
            return None  # peak window unanchorable — fail-safe skip
        window = since_entry(candles_df, open_pkg)
        # Same ambiguity guard as _stale_stop_verdict: a full-frame
        # "restriction" means _since_entry fell back, and a pre-entry
        # extreme would fake a peak the trade never actually saw.
        if len(window) >= len(candles_df) and len(candles_df) > 0:
            return None
        if direction == "long":
            peak = _coerce_float(window["high"].max())
            if peak is None:
                return None
            peak_r = (peak - entry) / risk
            r_close = (current_price - entry) / risk
        else:
            peak = _coerce_float(window["low"].min())
            if peak is None:
                return None
            peak_r = (entry - peak) / risk
            r_close = (entry - current_price) / risk
        if not (peak_r >= min_mfe_r and (peak_r - r_close) >= giveback_r):
            return None
        if declared:
            return {"action": "close", "reason": "giveback_stop",
                    "exit_price": current_price}
        # Annotate-only path (undeclared): observe, never act.
        try:
            from src.runtime.exit_lever_soak import record_exit_lever_annotation

            record_exit_lever_annotation(
                lever="giveback_stop",
                strategy=str(meta.get("strategy_label")
                             or open_pkg.get("strategy_name") or default_label),
                symbol=str(open_pkg.get("symbol") or ""),
                direction=direction,
                order_package_id=open_pkg.get("order_package_id"),
                params={"giveback_min_mfe_r": min_mfe_r,
                        "giveback_r": giveback_r},
                state={"peak_r": round(peak_r, 4), "open_r": round(r_close, 4),
                       "price": current_price, "entry": entry},
            )
        except Exception:  # noqa: BLE001 — annotate must never affect the path
            pass
        return None
    except Exception:  # noqa: BLE001 — monitor must never crash on this lever
        return None
