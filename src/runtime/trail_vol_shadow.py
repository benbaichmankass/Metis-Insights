"""Log-only P5 live-parity instrument for the ``vol_trail`` lever (M20-EXITS).

Why this exists: exit-refinement P5 requires diffing live-logged trigger rows
against the offline recompute BEFORE a Tier-3 declare. ``vol_trail`` is
declared on NO live leg, so ``trail_vol.resolve_vol_trail_mult`` never logs and
there is nothing to diff (PR #14053 review, PI-20260929-AQRK6CL1-0012).
``strategies.yaml`` keys are account-wide, so declaring the lever to "soak it
on bybit_1" would also arm it on bybit_2 (real money) — the instrument
therefore computes the lever SHADOW-ONLY and never returns anything to the
caller. **Behaviour change: none.** The monitor's stop is computed exactly as
before; this module only appends a row.

Each managed closed bar on ``ada_pullback_2h`` (the only leg the reference
cell was graded for) writes one row to ``runtime_logs/vol_trail_shadow.jsonl``:
the ATR percentile at the last CLOSED bar (what the harness sees) AND at the
raw, possibly-forming last row (so the CA-B01 skew is measurable), whether the
reference cell (hot>0.9, tight 2.5, window 200) would fire, and the live stop
candidate versus the shadow stop candidate. Deduped per (order_package_id,
closed-bar open time). Never raises; skips entirely when vol_trail is declared
(the real path then logs to exit_lever_soak.jsonl).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SHADOW_LOG_NAME = "vol_trail_shadow.jsonl"
SHADOW_STRATEGY = "ada_pullback_2h"
REF_ABOVE_PCTL = 0.9
REF_TIGHT_MULT = 2.5
REF_WINDOW = 200

_SEEN: set = set()


def shadow_log_path():
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SHADOW_LOG_NAME


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _candidate(direction: str, window, mult: float, atr: float, sl: float,
               price: float) -> Optional[float]:
    """Same ratchet arithmetic as htf_pullback_trend_2h.monitor's tail."""
    try:
        if direction == "long":
            c = float(window["high"].max()) - mult * atr
            return round(c, 8) if (c > sl and c < price) else None
        c = float(window["low"].min()) + mult * atr
        return round(c, 8) if (c < sl and c > price) else None
    except Exception:  # noqa: BLE001
        return None


def record_vol_trail_shadow(
    *,
    meta: Dict[str, Any],
    cfg_dict: Dict[str, Any],
    open_pkg: Dict[str, Any],
    candles_df: Any,
    window: Any,
    live_mult: float,
    atr: float,
    sl: float,
    current_price: float,
    direction: str,
) -> Optional[Dict[str, Any]]:
    """Append one shadow row (best-effort). Returns the row, or None. Never raises."""
    try:
        def _pick(key: str) -> Any:
            mv = meta.get(key)
            return mv if mv is not None else cfg_dict.get(key)

        label = str(meta.get("strategy_label") or open_pkg.get("strategy_name") or "")
        if label != SHADOW_STRATEGY:
            return None
        if (_f(_pick("trail_vol_tight_mult")) or 0) > 0:
            return None  # declared: the real path logs; nothing to shadow
        if candles_df is None or len(candles_df) < REF_WINDOW:
            return None

        from src.runtime.closed_bars import drop_forming_bar
        from src.units.strategies.trend_donchian import _atr, _trailing_atr_pctl

        period = int(_pick("atr_period") or 14)
        tf = str(_pick("timeframe") or "")
        closed = drop_forming_bar(candles_df, tf) if tf else candles_df
        pctl_raw = _trailing_atr_pctl(_atr(candles_df, period), len(candles_df) - 1, REF_WINDOW)
        pctl_closed = (
            _trailing_atr_pctl(_atr(closed, period), len(closed) - 1, REF_WINDOW)
            if len(closed) >= REF_WINDOW else None
        )
        bar_ts = str(closed["timestamp"].iloc[-1]) if "timestamp" in closed else str(len(closed))
        key = (str(open_pkg.get("order_package_id") or ""), bar_ts)
        if key in _SEEN:
            return None

        fires_closed = pctl_closed is not None and pctl_closed > REF_ABOVE_PCTL
        fires_raw = pctl_raw is not None and pctl_raw > REF_ABOVE_PCTL
        shadow_mult = min(float(live_mult), REF_TIGHT_MULT) if fires_closed else float(live_mult)
        live_c = _candidate(direction, window, float(live_mult), atr, sl, current_price)
        shadow_c = _candidate(direction, window, shadow_mult, atr, sl, current_price)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": label,
            "account_id": open_pkg.get("account_id") or open_pkg.get("account"),
            "symbol": str(open_pkg.get("symbol") or ""),
            "direction": direction,
            "order_package_id": open_pkg.get("order_package_id"),
            "closed_bar_open_ts": bar_ts,
            "raw_last_bar_open_ts": str(candles_df["timestamp"].iloc[-1]) if "timestamp" in candles_df else None,
            "ref_cell": {"above": REF_ABOVE_PCTL, "tight": REF_TIGHT_MULT, "window": REF_WINDOW},
            "atr_pctl_closed": None if pctl_closed is None else round(pctl_closed, 4),
            "atr_pctl_raw": None if pctl_raw is None else round(pctl_raw, 4),
            "would_fire_closed": bool(fires_closed),
            "would_fire_raw": bool(fires_raw),
            "live_mult": float(live_mult),
            "shadow_mult": shadow_mult,
            "atr": atr,
            "sl": sl,
            "price": current_price,
            "live_stop_candidate": live_c,
            "shadow_stop_candidate": shadow_c,
            "stops_differ": live_c != shadow_c,
        }
        path = shadow_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        _SEEN.add(key)
        return row
    except Exception:  # noqa: BLE001 — observe-only; must never affect the monitor
        logger.debug("trail_vol_shadow failed", exc_info=True)
        return None
