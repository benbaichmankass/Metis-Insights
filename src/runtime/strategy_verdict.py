"""Canonical strategy ``monitor()`` verdict schema + validator (P1).

A live trade / order-package is *owned* by the strategy that opened it. While
the trade is open, the order-monitor calls that strategy's module-level
``monitor(cfg, candles_df, open_pkg)`` once per tick
(``src/runtime/order_monitor.py::_call_strategy_monitor``). ``monitor()``
returns a **verdict** — its opinion on what the integration should do to the
live order this tick — or ``None`` when it has no opinion (a healthy
ran-no-action tick).

Until P1 this verdict was a *convention*: each strategy emitted a dict of an
ad-hoc shape and nothing validated it. This module formalises the shape into
one **canonical, documented, validated** contract so a new strategy can't ship
a verdict the integration side can't apply, and so the integration side has a
single schema to code against.

This module is **pure and dependency-free** (stdlib typing only) and
``validate_verdict`` **never raises** — it returns ``(ok, reason)``. It is
import-safe from any layer (signal builders, order-monitor, tests, CI guards)
and performs no I/O.

The canonical verdict schema
============================

A verdict is exactly one of:

1. ``None`` — **no-op.** The strategy ran and has no change to request this
   tick. Always valid.

2. **Adjust stop-loss** — ``{"sl": <positive float>}``. Move the live SL to
   this absolute price level (e.g. trail to break-even / chandelier trail).
   Emitted by the trend/breakout strategies + ``_base.monitor_breakeven_sl``.

3. **Adjust take-profit** — ``{"tp": <positive float>}``. Move the live TP to
   this absolute price level. Part of the contract (documented in
   ``_base.monitor_breakeven_sl``); accepted whether or not a strategy emits it
   today.

4. **Close (full or partial)** — ``{"action": "close", "reason": <str>, ...}``
   with these **optional** fields:
   - ``"close_qty_pct"`` — fraction of the open qty to close, in ``(0, 1]``.
     ``1.0`` (or omitted) = full close; ``< 1.0`` = a partial close
     (e.g. turtle_soup's TP1 scale-out at ``0.25``).
   - ``"exit_price"`` — the price the close was decided at (positive float),
     used for local-PnL / mark-to-market.
   - ``"next_tp"`` — on a partial close, the rolled-forward TP for the runner
     (positive float; turtle_soup's TP1→TP2 roll).
   ``"reason"`` is a free-form non-empty string tag (``sl_cross``, ``tp_cross``,
   ``tp1_partial``, ``tp2_cross``, ``vwap_cross``, ``time_decay``, …).

Shapes #2 / #3 / #4 are **mutually exclusive** — a single verdict adjusts SL,
or adjusts TP, or closes; it does not combine an ``sl``/``tp`` adjust key with
an ``action`` in one dict. (A strategy that wants both adjusts on consecutive
ticks.)

This enumeration is the union of every shape the 8 monitor-owning modules
actually emit on ``main`` (turtle_soup, vwap, ict_scalp, trend_donchian,
htf_pullback_trend_2h, fade_breakout_4h, squeeze_breakout_4h, fvg_range_15m)
plus the documented-but-not-yet-emitted ``{"tp": ...}`` adjust. The validator
accepts ALL of them; it is deliberately permissive about *extra* keys on a
close verdict (forward-compat) but strict about the value types of the keys it
knows.
"""
from __future__ import annotations

import math
from typing import Any, Tuple

__all__ = ["validate_verdict", "is_close_verdict", "CLOSE_ACTION"]

# The only recognised value of the ``action`` key.
CLOSE_ACTION = "close"

# Keys that, when present at top level, mean "this is an SL/TP adjust verdict".
_ADJUST_KEYS = ("sl", "tp")


def _is_positive_number(value: Any) -> bool:
    """True iff ``value`` is a real, finite, strictly-positive number.

    Rejects bools (``True`` is an ``int`` in Python but never a price),
    NaN/inf, strings, and non-positive values. Prices and TP/SL levels are
    always positive.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


def is_close_verdict(verdict: Any) -> bool:
    """True iff ``verdict`` is a dict requesting a close (``action == close``).

    Cheap structural check used by callers that branch on close-vs-adjust
    without re-validating; pair with :func:`validate_verdict` for correctness.
    """
    return isinstance(verdict, dict) and verdict.get("action") == CLOSE_ACTION


def validate_verdict(verdict: Any) -> Tuple[bool, str]:
    """Validate a strategy ``monitor()`` verdict against the canonical schema.

    Pure, dependency-free, and **never raises**. Returns ``(ok, reason)``:

    - ``(True, "ok")`` when ``verdict`` conforms (incl. ``None`` no-op).
    - ``(False, "<why>")`` describing the first violation otherwise.

    See the module docstring for the canonical schema.
    """
    # 1. None — the no-op verdict.
    if verdict is None:
        return True, "ok"

    if not isinstance(verdict, dict):
        return False, f"verdict must be a dict or None, got {type(verdict).__name__}"

    has_action = "action" in verdict
    has_adjust = any(k in verdict for k in _ADJUST_KEYS)

    # An adjust key and an action key are mutually exclusive.
    if has_action and has_adjust:
        return False, (
            "verdict mixes an 'action' with an 'sl'/'tp' adjust key — "
            "a verdict either closes or adjusts, not both"
        )

    # 2 / 3. SL or TP adjust.
    if has_adjust:
        if "sl" in verdict and "tp" in verdict:
            return False, "verdict sets both 'sl' and 'tp' — adjust one at a time"
        key = "sl" if "sl" in verdict else "tp"
        if not _is_positive_number(verdict[key]):
            return False, f"'{key}' must be a positive number, got {verdict[key]!r}"
        return True, "ok"

    # 4. Close.
    if has_action:
        action = verdict["action"]
        if action != CLOSE_ACTION:
            return False, f"unsupported action {action!r} (only {CLOSE_ACTION!r})"

        reason = verdict.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, "close verdict requires a non-empty string 'reason'"

        if "close_qty_pct" in verdict:
            pct = verdict["close_qty_pct"]
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                return False, f"'close_qty_pct' must be a number, got {pct!r}"
            fpct = float(pct)
            if not math.isfinite(fpct) or not (0.0 < fpct <= 1.0):
                return False, f"'close_qty_pct' must be in (0, 1], got {pct!r}"

        if "exit_price" in verdict and not _is_positive_number(verdict["exit_price"]):
            return False, (
                f"'exit_price' must be a positive number, got "
                f"{verdict['exit_price']!r}"
            )

        if "next_tp" in verdict and not _is_positive_number(verdict["next_tp"]):
            return False, (
                f"'next_tp' must be a positive number, got {verdict['next_tp']!r}"
            )

        return True, "ok"

    # An empty dict / a dict with none of the recognised keys is not a verdict.
    return False, (
        "verdict dict has no recognised key — expected one of 'sl', 'tp', "
        "or 'action'"
    )
