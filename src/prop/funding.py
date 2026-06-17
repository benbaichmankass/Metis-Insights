"""Perpetual-funding drag for the prop EV/survival Monte-Carlo (PB-20260616-004).

The cost-aware EV engine (``src/prop/montecarlo.py``) reduces the engine's
closed-trade ledger to sizing-independent R-multiples and bootstraps it. It
explicitly does NOT model **perp funding** — the recurring payment a perpetual
holder pays/receives every funding interval (Bybit linear perps: every 8h). The
research run that found ``trend_donchian`` +EV on Bybit alts was on Binance
*spot* klines with NO funding, so its own resolution criteria
(``PB-20260616-004``) require a re-validation on real Bybit *perp* candles
**with funding factored in**.

This module is the non-invasive bridge: it takes the engine's ``closed_trades``
ledger and returns a NEW ledger (plain dicts) whose ``pnl`` has each trade's
funding cost subtracted, leaving every other field intact. Because
``ledger_to_r_sequence`` reads ``pnl`` / ``entry_ts`` / ``exit_ts`` via a
dict-or-attr accessor, the funded ledger drops straight into ``run_montecarlo``
/ ``run_ev_montecarlo`` with no engine change.

Funding model
-------------
For one trade held over ``[entry_ts, exit_ts]`` with entry price ``entry`` and
quantity ``qty`` (so notional ``N = entry * qty``):

* **Real path (preferred).** Sum the actual funding events that fell inside the
  holding window. A *long* PAYS when the funding rate is positive (and receives
  when negative); a *short* is the mirror. So the cost charged to the trade is::

      funding_cost = sum(rate_e for e in events in (entry, exit]) * N * side_sign

  with ``side_sign = +1`` for a long, ``-1`` for a short. Subtracting this from
  ``pnl`` is exactly the cash a real perp position would have bled/earned.

* **Fallback (constant).** When no funding series is supplied, charge a constant
  per-interval rate prorated by holding time::

      intervals  = hold_seconds / (8 * 3600)
      funding_cost = const_rate_8h * N * intervals * side_sign

  Default ``const_rate_8h = 1e-4`` (0.01%/8h ≈ 0.03%/day) is Bybit's baseline
  clamp midpoint — a deliberately conservative, clearly-LABELLED assumption.
  The real path is always preferred; the constant exists only so the harness
  degrades gracefully if a funding pull fails.

Both paths are pure and deterministic. Tier-1 research tooling — no live-path
imports, no network (the series is fetched separately and passed in).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.prop.evaluator import _to_dt  # tz-aware UTC coercion (re-used)

_SECONDS_PER_FUNDING_INTERVAL = 8 * 3600.0  # Bybit linear perp: 8-hour funding


def _get(t: Any, key: str, default: Any = None) -> Any:
    if isinstance(t, dict):
        return t.get(key, default)
    return getattr(t, key, default)


def _side_sign(side: Any) -> float:
    """+1 for a long (pays positive funding), -1 for a short."""
    s = str(side or "").lower()
    if s in ("long", "buy", "b", "1"):
        return 1.0
    if s in ("short", "sell", "s", "-1"):
        return -1.0
    # Unknown side: treat as long (the conservative, pays-funding direction).
    return 1.0


def normalize_funding_series(
    rows: Sequence[Any],
) -> List[Tuple[Any, float]]:
    """Coerce a funding feed into a sorted ``[(utc_dt, rate_fraction), ...]`` list.

    Accepts rows as dicts with any of ``{ts, timestamp, fundingRateTimestamp}``
    for the time and ``{rate, fundingRate, funding_rate}`` for the rate, or as
    ``(ts, rate)`` 2-tuples. ``rate`` is a fraction per interval (Bybit's
    ``fundingRate`` is already a fraction, e.g. ``0.0001`` for 0.01%). Rows that
    can't be parsed are dropped. The result is sorted ascending by time.
    """
    out: List[Tuple[Any, float]] = []
    for r in rows or []:
        if isinstance(r, (tuple, list)) and len(r) >= 2:
            ts_raw, rate_raw = r[0], r[1]
        elif isinstance(r, dict):
            ts_raw = (
                r.get("ts")
                if r.get("ts") is not None
                else r.get("timestamp")
                if r.get("timestamp") is not None
                else r.get("fundingRateTimestamp")
            )
            rate_raw = (
                r.get("rate")
                if r.get("rate") is not None
                else r.get("fundingRate")
                if r.get("fundingRate") is not None
                else r.get("funding_rate")
            )
        else:
            continue
        # Bybit fundingRateTimestamp is epoch milliseconds (a numeric string).
        try:
            if isinstance(ts_raw, str) and ts_raw.isdigit():
                from datetime import datetime, timezone

                ts = datetime.fromtimestamp(int(ts_raw) / 1000.0, tz=timezone.utc)
            elif isinstance(ts_raw, (int, float)):
                from datetime import datetime, timezone

                # treat large numbers as ms, small as seconds
                div = 1000.0 if float(ts_raw) > 1e12 else 1.0
                ts = datetime.fromtimestamp(float(ts_raw) / div, tz=timezone.utc)
            else:
                ts = _to_dt(ts_raw)
            rate = float(rate_raw)
        except Exception:  # noqa: BLE001 — one bad row can't poison the series
            continue
        out.append((ts, rate))
    out.sort(key=lambda x: x[0])
    return out


def _funding_cost(
    *,
    entry: float,
    qty: float,
    side: Any,
    entry_dt: Any,
    exit_dt: Any,
    funding_series: Optional[List[Tuple[Any, float]]],
    const_rate_8h: float,
) -> float:
    """Funding cash bled by one trade over its holding window (see module doc)."""
    notional = abs(float(entry) * float(qty))
    sign = _side_sign(side)
    if notional <= 0 or entry_dt is None or exit_dt is None:
        return 0.0

    if funding_series:
        rate_sum = 0.0
        for ts, rate in funding_series:
            if ts is None:
                continue
            # funding charged at events strictly after entry, up to and incl. exit
            if entry_dt < ts <= exit_dt:
                rate_sum += rate
            elif ts > exit_dt:
                break  # series is sorted ascending
        return rate_sum * notional * sign

    # constant fallback, prorated by holding time
    try:
        hold_seconds = max(0.0, (exit_dt - entry_dt).total_seconds())
    except Exception:  # noqa: BLE001
        return 0.0
    intervals = hold_seconds / _SECONDS_PER_FUNDING_INTERVAL
    return const_rate_8h * notional * intervals * sign


def apply_funding_to_ledger(
    closed_trades: Sequence[Any],
    *,
    funding_series: Optional[Sequence[Any]] = None,
    const_rate_8h: float = 1e-4,
) -> List[Dict[str, Any]]:
    """Return a funding-adjusted copy of the ledger (list of dicts).

    Each output row carries every original field (``pnl`` reduced by the
    trade's funding cost) plus ``funding_cost`` (the cash subtracted) and
    ``pnl_pre_funding`` (the original). Non-invasive: the engine ledger is not
    mutated; the dicts drop straight into ``run_montecarlo`` /
    ``run_ev_montecarlo``.
    """
    series = normalize_funding_series(funding_series) if funding_series else None
    out: List[Dict[str, Any]] = []
    for t in closed_trades or []:
        pnl = float(_get(t, "pnl", 0.0) or 0.0)
        entry = float(_get(t, "entry", 0.0) or 0.0)
        qty = float(_get(t, "qty", 0.0) or 0.0)
        side = _get(t, "side")
        entry_ts = _get(t, "entry_ts")
        exit_ts = _get(t, "exit_ts")
        try:
            entry_dt = _to_dt(entry_ts)
        except Exception:  # noqa: BLE001
            entry_dt = None
        try:
            exit_dt = _to_dt(exit_ts)
        except Exception:  # noqa: BLE001
            exit_dt = None

        cost = _funding_cost(
            entry=entry, qty=qty, side=side, entry_dt=entry_dt, exit_dt=exit_dt,
            funding_series=series, const_rate_8h=const_rate_8h,
        )
        out.append({
            "owner": _get(t, "owner"),
            "side": side,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry": entry,
            "exit": _get(t, "exit"),
            "qty": qty,
            "pnl": pnl - cost,
            "pnl_pre_funding": pnl,
            "funding_cost": cost,
            "fee": _get(t, "fee"),
            "reason": _get(t, "reason"),
            "bars_held": _get(t, "bars_held"),
        })
    return out


def funding_summary(funded_ledger: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the funding drag applied to a funded ledger (for the report)."""
    costs = [float(r.get("funding_cost", 0.0) or 0.0) for r in funded_ledger or []]
    pre = sum(float(r.get("pnl_pre_funding", 0.0) or 0.0) for r in funded_ledger or [])
    total_cost = sum(costs)
    n = len(costs)
    return {
        "n_trades": n,
        "total_funding_cost_usd": round(total_cost, 2),
        "mean_funding_cost_usd": round(total_cost / n, 4) if n else 0.0,
        "pnl_pre_funding_usd": round(pre, 2),
        "pnl_post_funding_usd": round(pre - total_cost, 2),
        "funding_drag_pct_of_gross": (
            round(100.0 * total_cost / pre, 2) if pre else None
        ),
    }
