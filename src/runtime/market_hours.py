"""Market-hours / session gate (M15 Phase 1).

The pipeline was built for 24/7 crypto and has never had a trading
calendar (the ``killzone`` naming in older modules is a shim, not
session logic). The non-24/7 venues M15 adds need one:

- ``fx``        — 24/5: closed from Friday 21:00 UTC to Sunday 21:00 UTC.
- ``us_equity`` — US cash session: Mon–Fri 14:30–21:00 UTC.
- ``crypto``    — always open.

**WIRED** (M15): ``is_market_open`` is a live signal gate in
``strategy_signal_builders.py`` (the ``fx`` / ``us_equity`` session checks set
``side=none`` when the venue is closed) — skip fetch +
signal for a symbol whose market is closed, so closed-market stale
candles can never produce entries.

Research-grade limitations, by design (documented, not hidden):

- No US holiday calendar and no half-days — a holiday reads "open".
  Harmless for signal gating (no fresh bars arrive anyway); revisit
  before any equities strategy goes paper-live (M15 Phase 3).
- US DST is handled by month approximation (EST = UTC-5 in Nov–Feb →
  session 14:30–21:00 UTC standard, 13:30–20:00 UTC in DST months).
  Exact second-Sunday boundaries are NOT modeled; the affected edge
  weeks read conservatively (closed-when-actually-open at the margins).
"""
from __future__ import annotations

from datetime import datetime, time, timezone

ASSET_CLASSES = ("crypto", "fx", "us_equity")

# Months fully inside US daylight-saving time. March and November are
# transition months — treated as standard time (the conservative edge).
_FULL_DST_MONTHS = {4, 5, 6, 7, 8, 9, 10}


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def is_market_open(asset_class: str, ts: datetime | None = None) -> bool:
    """True when *asset_class*'s market is open at *ts* (default: now).

    Unknown asset classes return ``True`` (fail-permissive, matching the
    Prime Directive posture — a gate bug must never strand a live
    capability; crypto/unknown symbols keep today's 24/7 behaviour).
    """
    ts = _utc(ts or datetime.now(timezone.utc))
    cls = str(asset_class or "").strip().lower()

    if cls == "fx":
        # Closed Fri 21:00 UTC -> Sun 21:00 UTC.
        wd, t = ts.weekday(), ts.time()
        if wd == 4 and t >= time(21, 0):  # Friday evening
            return False
        if wd == 5:  # Saturday
            return False
        if wd == 6 and t < time(21, 0):  # Sunday before reopen
            return False
        return True

    if cls == "us_equity":
        wd, t = ts.weekday(), ts.time()
        if wd >= 5:  # weekend
            return False
        if ts.month in _FULL_DST_MONTHS:
            open_t, close_t = time(13, 30), time(20, 0)
        else:
            open_t, close_t = time(14, 30), time(21, 0)
        return open_t <= t < close_t

    return True  # crypto / unknown: 24/7


def asset_class_for_exchange(exchange: str) -> str:
    """Map an exchange name to the asset class its symbols trade as."""
    e = str(exchange or "").strip().lower()
    if e == "oanda":
        return "fx"
    if e == "alpaca":
        return "us_equity"
    return "crypto"
