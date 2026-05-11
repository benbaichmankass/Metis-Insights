"""S-064 prereq — per-symbol liquidity-zone state writer.

The pipeline (`ict-trader-live.service`) and the web API
(`ict-web-api.service`) are separate processes. This module is the
disk-handoff for liquidity zones: the pipeline calls
``write_state(symbol, candles_df)`` from each signal builder after
``fetch_candles``, and ``GET /api/bot/liquidity`` (S-064 main PR)
reads ``runtime_logs/liquidity_state.json``.

The detector (``src/ict_detection/liquidity.py``) and swing-point
detector (``src/ict_detection/swing_points.py``) already exist and
are unit-tested; this module is the glue that turns a
fetched-candles DataFrame into a JSON state file.

Atomic write semantics: render into a sibling ``.tmp`` then
``os.replace`` so a reader never sees a half-written file. Same
pattern as ``src/web/runtime_status.py``.

The whole call chain is best-effort — the pipeline tick must never
raise because of liquidity bookkeeping. ``write_state`` swallows
any exception and logs it, mirroring ``runtime_status.write_status``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import runtime_logs_dir

logger = logging.getLogger(__name__)

LIQUIDITY_STATE_PATH = runtime_logs_dir() / "liquidity_state.json"
SCHEMA_VERSION = 1

# Cap how many pools / sweeps we persist per symbol so the file stays small
# (the dashboard is the only consumer and only renders ~25 of each).
MAX_POOLS_PER_SIDE = 50
MAX_SWEEPS = 50


def _to_iso(dt: Any) -> Optional[str]:
    """Best-effort ISO-8601 (UTC) for whatever the detector hands us.

    ``LiquidityDetector`` puts pandas Timestamps from the candle index
    into ``first_touch`` / ``last_touch`` / ``sweep_time``. Coerce to
    a tz-aware ISO string; return ``None`` if we can't.
    """
    if dt is None:
        return None
    try:
        # pandas Timestamp / datetime both have isoformat
        if hasattr(dt, "tz_localize") and dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        elif isinstance(dt, datetime) and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        iso = dt.isoformat()
        return iso.replace("+00:00", "Z")
    except Exception:
        try:
            return str(dt)
        except Exception:
            return None


def _normalize_pool(pool: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a detector-side pool dict into JSON-safe wire shape.

    Wire shape (matches the dashboard's `LiquidityZone` type that
    S-064 main PR will introduce):

        { side: "buy" | "sell",
          price: float,
          touches: int,
          first_touch: ISO,
          last_touch: ISO,
          swept: bool,
          sweep_time: ISO | null }
    """
    return {
        "side": "buy" if pool.get("type") == "buy_side" else "sell",
        "price": float(pool["price"]),
        "touches": int(pool.get("touches", 0)),
        "first_touch": _to_iso(pool.get("first_touch")),
        "last_touch": _to_iso(pool.get("last_touch")),
        "swept": bool(pool.get("swept", False)),
        "sweep_time": _to_iso(pool.get("sweep_time")),
    }


def build_state(
    symbol: str,
    candles_df: Any,
    *,
    now_utc: Optional[datetime] = None,
    tolerance: float = 0.001,
    min_touches: int = 2,
    lookback: int = 50,
    swing_left: int = 5,
    swing_right: int = 5,
) -> Dict[str, Any]:
    """Build the per-symbol liquidity payload. Pure function for tests.

    Returns a dict with ``equal_highs``, ``equal_lows`` and
    ``recent_sweeps`` arrays, ordered most-recent-first within each
    list. ``as_of`` is always set; arrays may be empty.
    """
    from src.ict_detection.liquidity import LiquidityDetector
    from src.ict_detection.swing_points import SwingPointDetector

    now = now_utc or datetime.now(timezone.utc)

    df = candles_df.copy()
    swing = SwingPointDetector(left_bars=swing_left, right_bars=swing_right)
    if "swing_high" not in df.columns:
        df["swing_high"] = swing.detect_swing_highs(df)
    if "swing_low" not in df.columns:
        df["swing_low"] = swing.detect_swing_lows(df)

    detector = LiquidityDetector(tolerance=tolerance)
    pools = detector.detect_all_liquidity(df, min_touches=min_touches, lookback=lookback)

    equal_highs: List[Dict[str, Any]] = []
    equal_lows: List[Dict[str, Any]] = []
    sweeps: List[Dict[str, Any]] = []
    for pool in pools:
        wire = _normalize_pool(pool)
        if wire["side"] == "buy":
            equal_highs.append(wire)
        else:
            equal_lows.append(wire)
        if wire["swept"] and wire["sweep_time"]:
            sweeps.append({
                "side": wire["side"],
                "price": wire["price"],
                "swept_at": wire["sweep_time"],
            })

    # Most-recent-first by last_touch / swept_at where available.
    equal_highs.sort(key=lambda p: p.get("last_touch") or "", reverse=True)
    equal_lows.sort(key=lambda p: p.get("last_touch") or "", reverse=True)
    sweeps.sort(key=lambda s: s.get("swept_at") or "", reverse=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "as_of": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "equal_highs": equal_highs[:MAX_POOLS_PER_SIDE],
        "equal_lows": equal_lows[:MAX_POOLS_PER_SIDE],
        "recent_sweeps": sweeps[:MAX_SWEEPS],
    }


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    os.replace(tmp, path)


def _load_all(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read the multi-symbol state file as a {symbol: state} dict.

    Returns ``{}`` on missing / unreadable / malformed file.
    """
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # S-067 borderline: was silently `return {}`. Log so a
        # corrupted liquidity_state.json (which would silently
        # blank the dashboard's Liquidity Maps tab) is visible in
        # bot.log.
        logger.warning(
            "liquidity_state: load_all read failed: %s: %s",
            type(exc).__name__, exc,
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def write_state(
    symbol: str,
    candles_df: Any,
    *,
    path: Optional[Path] = None,
    **build_kwargs: Any,
) -> None:
    """Atomically merge per-symbol state into the multi-symbol file.

    Best-effort: never raises into the tick loop. Other symbols'
    entries are preserved; only ``symbol``'s slot is replaced.
    """
    target = path or LIQUIDITY_STATE_PATH
    try:
        state = build_state(symbol, candles_df, **build_kwargs)
        all_state = _load_all(target)
        all_state[symbol] = state
        _atomic_write_json(target, all_state)
    except Exception:
        logger.exception("liquidity_state write failed for symbol=%s", symbol)


def read_state(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Read the full multi-symbol state. Returns ``{}`` if missing."""
    return _load_all(path or LIQUIDITY_STATE_PATH)
