"""Prop SL/TP crossing alert — one-shot ping when price crosses an open prop
trade's stop-loss or take-profit level.

The prop account is a manual bridge: the bot has no broker feed, so it never
learns when the executor actually closed a trade. This module bridges that gap:
once per tick it fetches the current price for every open prop position that has
SL/TP levels set, and fires a one-shot Telegram + FCM alert when price crosses
either level — prompting the operator to check the terminal and report back.

Design notes:

- **One alert per level per position.** Once SL or TP has been alerted, that
  flag persists so a subsequent tick where price is still past the level doesn't
  re-fire. Cleared only when the position closes (i.e. the key drops from the
  state file because ``find_open_prop_positions`` no longer returns it).
- **State file:** ``runtime_logs/prop_sl_tp_alert.json`` —
  ``{position_key: {sl_alerted_at: ISO|null, tp_alerted_at: ISO|null}}``.
  Pruned to currently-open keys on each save.
- **Price fetch:** uses ``connector_for_symbol`` + ``fetch_candles`` (the same
  path the signal builders use) — last close bar. Best-effort; a fetch failure
  skips the position for this tick without raising.
- **Direction logic:** BUY/long — SL crossed when price ≤ SL, TP crossed when
  price ≥ TP; SELL/short — SL crossed when price ≥ SL, TP crossed when
  price ≤ TP.
- **Baseline, no enable gate** (Prime Directive). ``PROP_SL_TP_ALERT_DISABLED``
  env (truthy) is the sanctioned kill-switch without a redeploy. Best-effort +
  isolated; never raises into the caller.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.prop.prop_monitor_pulse import find_open_prop_positions

logger = logging.getLogger(__name__)

_STATE_FILENAME = "prop_sl_tp_alert.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path() -> str:
    from src.utils.paths import runtime_logs_dir
    return str(runtime_logs_dir() / _STATE_FILENAME)


def _load_state() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    try:
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("prop_sl_tp_alert: state save failed: %s", exc)


def _fetch_current_price(symbol: str, settings: dict) -> Optional[float]:
    """Fetch the latest close price for *symbol*. Returns None on any failure."""
    try:
        from src.runtime.market_data import connector_for_symbol, fetch_candles
        client = connector_for_symbol(symbol, settings)
        if client is None:
            return None
        df = fetch_candles(symbol, "5m", settings=settings,
                           exchange_client=client, limit=3)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["close"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_sl_tp_alert: price fetch failed for %s: %s", symbol, exc)
        return None


def _sl_crossed(direction: str, current: float, sl: float) -> bool:
    d = direction.lower()
    if d in ("buy", "long"):
        return current <= sl
    if d in ("sell", "short"):
        return current >= sl
    return False


def _tp_crossed(direction: str, current: float, tp: float) -> bool:
    d = direction.lower()
    if d in ("buy", "long"):
        return current >= tp
    if d in ("sell", "short"):
        return current <= tp
    return False


def run_prop_sl_tp_alert(
    *,
    now: Optional[datetime] = None,
    settings: Optional[dict] = None,
    emitter: Optional[Callable[[Dict[str, Any], str, float], None]] = None,
) -> Dict[str, Any]:
    """Check open prop positions against current price; alert on SL/TP crossings.

    Called once per trader tick. Fires at most one SL alert and one TP alert per
    position over its lifetime. Returns stats ``{open, sl_fired, tp_fired,
    disabled}``. Never raises.
    """
    stats = {"open": 0, "sl_fired": 0, "tp_fired": 0, "disabled": False}
    if str(os.environ.get("PROP_SL_TP_ALERT_DISABLED", "")).strip().lower() in (
        "1", "true", "yes"
    ):
        stats["disabled"] = True
        return stats

    now = now or _now()

    if settings is None:
        try:
            from src.runtime.validation import build_settings_from_env
            settings = build_settings_from_env()
        except Exception as exc:  # noqa: BLE001
            logger.warning("prop_sl_tp_alert: settings build failed: %s", exc)
            settings = {}

    try:
        positions = find_open_prop_positions(now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prop_sl_tp_alert: open-position scan failed: %s", exc)
        return stats
    stats["open"] = len(positions)

    if emitter is None:
        from src.prop.breakout_notify import emit_prop_sl_tp_alert as emitter  # type: ignore

    state = _load_state()
    new_state: Dict[str, Dict[str, Any]] = {}
    state_changed = False

    for pos in positions:
        key = pos["key"]
        sl = pos.get("sl")
        tp = pos.get("tp")
        symbol = str(pos.get("symbol") or "")
        direction = str(pos.get("direction") or "")

        prior = state.get(key, {})
        new_entry: Dict[str, Any] = {
            "sl_alerted_at": prior.get("sl_alerted_at"),
            "tp_alerted_at": prior.get("tp_alerted_at"),
        }

        if not sl and not tp:
            new_state[key] = new_entry
            continue

        current_price = _fetch_current_price(symbol, settings)
        if current_price is None:
            new_state[key] = new_entry
            continue

        if sl and not new_entry["sl_alerted_at"]:
            try:
                if _sl_crossed(direction, current_price, float(sl)):
                    emitter(pos, "sl", current_price)
                    new_entry["sl_alerted_at"] = now.isoformat()
                    stats["sl_fired"] += 1
                    state_changed = True
                    logger.info(
                        "prop_sl_tp_alert: SL alert fired %s %s "
                        "price=%.4f sl=%.4f (key=%s)",
                        symbol, direction, current_price, float(sl), key,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "prop_sl_tp_alert: SL emit failed for %s: %s", key, exc
                )

        if tp and not new_entry["tp_alerted_at"]:
            try:
                if _tp_crossed(direction, current_price, float(tp)):
                    emitter(pos, "tp", current_price)
                    new_entry["tp_alerted_at"] = now.isoformat()
                    stats["tp_fired"] += 1
                    state_changed = True
                    logger.info(
                        "prop_sl_tp_alert: TP alert fired %s %s "
                        "price=%.4f tp=%.4f (key=%s)",
                        symbol, direction, current_price, float(tp), key,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "prop_sl_tp_alert: TP emit failed for %s: %s", key, exc
                )

        new_state[key] = new_entry

    # Prune state to open keys only; save when anything changed.
    open_keys = {p["key"] for p in positions}
    if state_changed or open_keys != set(state.keys()):
        _save_state(new_state)

    if stats["sl_fired"] or stats["tp_fired"]:
        logger.info(
            "prop_sl_tp_alert: open=%d sl_fired=%d tp_fired=%d",
            stats["open"], stats["sl_fired"], stats["tp_fired"],
        )
    return stats


__all__ = ["run_prop_sl_tp_alert"]
