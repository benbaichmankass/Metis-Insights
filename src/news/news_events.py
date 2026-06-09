"""Economic-calendar event_risk source (M9 news influence, step 3).

Computes ``event_risk ∈ [0, 1]`` for a traded symbol base — how much an imminent
high-impact scheduled event (FOMC, CPI, NFP, EIA, …) could knock a trade off
course. The news-influence operator (`src/news/news_influence.py`) folds this in
as a **consideration** that can only *reduce* exposure, discounted when the trade
is aligned with the prevailing news direction. It is never a blackout.

    event_risk = impact × proximity

`proximity` ramps 0 → 1 over `pre_window_minutes` before the event, holds at 1
through it, and decays to 0 over `post_window_minutes` after. The most
threatening in-window event for the symbol's relevant classes wins (max).

Config lives in `config/economic_calendar.yaml`. The loader **never raises**:
a missing/malformed file (or no in-window event) yields ``0.0`` — i.e. the
influence layer falls back to acting on news direction alone. Source-agnostic:
a future job can refresh the `events` list from a live feed without touching
this math.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# src/news/news_events.py -> parents[2] == repo root.
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "economic_calendar.yaml"

_DEFAULT_PRE_WINDOW = 60.0
_DEFAULT_POST_WINDOW = 15.0


def _base_of(tag: str) -> str:
    base = str(tag or "").upper().split("/")[0]
    for suffix in ("USDT", "PERP", "USD"):
        if base.endswith(suffix) and base != suffix:
            return base[: -len(suffix)]
    return base


@lru_cache(maxsize=1)
def load_calendar() -> Dict[str, Any]:
    """Parse ``economic_calendar.yaml``. Empty-but-valid structure on any error."""
    empty: Dict[str, Any] = {"defaults": {}, "symbol_event_classes": {}, "events": []}
    try:
        import yaml
    except Exception:  # noqa: BLE001
        return empty
    try:
        if not _CONFIG_PATH.exists():
            return empty
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("news_events: failed to load %s — %s", _CONFIG_PATH, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    return {
        "defaults": data.get("defaults") or {},
        "symbol_event_classes": data.get("symbol_event_classes") or {},
        "events": data.get("events") or [],
    }


def reload_calendar() -> None:
    """Drop the cached calendar (for tests / hot-reload)."""
    load_calendar.cache_clear()


def _parse_time(raw: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _proximity(now: datetime, event_time: datetime, pre_window: float, post_window: float) -> float:
    """Ramp 0→1 over `pre_window` min before the event, 1 through it, →0 over
    `post_window` min after. Out of window → 0."""
    delta_min = (event_time - now).total_seconds() / 60.0
    if delta_min >= 0:  # event is ahead
        if delta_min > pre_window:
            return 0.0
        return 1.0 - (delta_min / pre_window) if pre_window > 0 else 1.0
    # event has passed
    elapsed = -delta_min
    if elapsed > post_window:
        return 0.0
    return 1.0 - (elapsed / post_window) if post_window > 0 else 0.0


def event_risk_for_symbol(
    symbol_or_base: str, now: Optional[datetime] = None,
) -> tuple[float, Dict[str, Any]]:
    """Return ``(event_risk, meta)`` for a traded symbol. Never raises.

    ``event_risk ∈ [0, 1]`` = max(impact × proximity) over the symbol's relevant,
    in-window events. ``meta`` names the dominating event (or why the risk is 0).
    """
    try:
        cal = load_calendar()
        base = _base_of(symbol_or_base)
        classes = cal.get("symbol_event_classes", {}).get(base)
        if not classes:
            return 0.0, {"reason": "no_event_classes_for_symbol", "base": base}
        classes = {str(c).lower() for c in classes}
        now = now or datetime.now(timezone.utc)
        defaults = cal.get("defaults", {})
        d_pre = float(defaults.get("pre_window_minutes", _DEFAULT_PRE_WINDOW))
        d_post = float(defaults.get("post_window_minutes", _DEFAULT_POST_WINDOW))

        best_risk = 0.0
        best: Dict[str, Any] = {"reason": "no_imminent_event", "base": base}
        for ev in cal.get("events", []):
            if not isinstance(ev, dict):
                continue
            cls = str(ev.get("class", "")).lower()
            if cls not in classes:
                continue
            et = _parse_time(ev.get("time"))
            if et is None:
                continue
            try:
                impact = max(0.0, min(1.0, float(ev.get("impact", 0.0))))
            except (TypeError, ValueError):
                continue
            pre = float(ev.get("pre_window_minutes", d_pre))
            post = float(ev.get("post_window_minutes", d_post))
            risk = impact * _proximity(now, et, pre, post)
            if risk > best_risk:
                best_risk = risk
                best = {"class": cls, "time": str(ev.get("time")), "impact": impact,
                        "risk": round(risk, 6), "base": base}
        return max(0.0, min(1.0, round(best_risk, 6))), best
    except Exception as exc:  # noqa: BLE001
        logger.warning("event_risk_for_symbol failed: %s", exc)
        return 0.0, {"reason": "error", "error": str(exc)}
