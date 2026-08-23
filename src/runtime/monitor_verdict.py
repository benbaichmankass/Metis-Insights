"""The ONE interpreter of a strategy `monitor()` verdict.

A strategy's ``monitor(cfg, candles, open_pkg)`` returns a small dict. Turning
that dict into a DECISION ("close fully", "close half and roll TP", "trail the
stop", "do nothing") is separate from EFFECTUATING it (DB + exchange writes on
the live path; an in-memory position in the backtest harness). This module owns
the decision; each caller owns only its own effectuation.

WHY IT EXISTS (P2 · the unified engine, 2026-08-07)
---------------------------------------------------
``scripts/backtest_system.py`` already calls each strategy's REAL ``monitor()``
— the signal is faithful. But it then re-implemented the interpretation at the
call site::

    if verdict.get("action") == "close":  _close(pos, c[i], ...)   # bar close
    elif "sl" in verdict:                 pos.sl = ...
    elif "tp" in verdict:                 pos.tp = ...

Measured against the 9 roster monitors on 2026-08-07, that dropped **three**
signals the live path acts on — the "computed correctly, then dropped at the
output boundary" class:

===========================  ====================================  ==================
verdict key                  live behaviour                        harness behaviour
===========================  ====================================  ==================
``exit_price``               exit fills AT it (no exchange fill)    ignored; bar close
``close_qty_pct`` < 1        partial close, runner stays open       closed 100%
``next_tp``                  rolls the package TP forward           ignored
===========================  ====================================  ==================

Population for those three: ``exit_price`` is emitted by **4 of 9** roster
monitors (`trend_donchian`, `fade_breakout_4h`, `squeeze_breakout_4h`,
`htf_pullback_trend_2h` — including `trend_donchian`, the calibration target),
``close_qty_pct`` + ``next_tp`` by **1 of 9** (`turtle_soup`, whose TP1
scale-out therefore has no runner at all in backtest).

Two further divergences are **latent, not live** — worth fixing structurally,
but no current monitor triggers them, and this docstring says so rather than
inflating the count:

* the ``elif`` chain applied ``sl`` OR ``tp``; live applies both independently.
  **0 of 9** monitors emit both in one verdict today.
* live drops a modify whose new value differs from the current one by less than
  ``MEANINGFUL_MODIFY_REL_TOL`` (the XRP SL-spam incident — see the tolerance's
  comment in ``order_monitor.py``); the harness ratcheted on float noise. Real
  trail steps are ATR-scaled and clear the tolerance by a wide margin, so this
  changes little — but "little" is not "nothing", and the point of a shared
  interpreter is that nobody has to re-derive which.

The semantics below are extracted VERBATIM from
``order_monitor._apply_update`` as it stood at extraction time; the module adds
no policy of its own. ``tests/test_monitor_verdict.py`` pins that equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

#: Relative tolerance below which an sl/tp change is float noise, not a real
#: move. The XRP SL-spam incident (2026-07-22): a monitor recomputing a trail off a forming
#: candle returns a "new" stop every tick, which fired an exchange amend + a
#: "TRADE UPDATED" ping essentially every tick for 5 days on a trail the
#: operator correctly perceived as static. Deliberately loose (0.05%) — real
#: trail steps are ATR-scaled and clear it easily.
MEANINGFUL_MODIFY_REL_TOL = 0.0005

#: Kinds a verdict can decide. `none` always carries a `rejection` naming WHY,
#: so a caller never has to report "nothing happened" without a cause — an
#: unexplained no-op is the silent-empty class.
KIND_NONE = "none"
KIND_CLOSE = "close"
KIND_PARTIAL_CLOSE = "partial_close"
KIND_MODIFY = "modify"

DEFAULT_CLOSE_REASON = "monitor_close"


@dataclass(frozen=True)
class VerdictDecision:
    """What a verdict decided, independent of how it gets effectuated."""

    kind: str
    reason: Optional[str] = None
    exit_price: Optional[float] = None
    close_qty_pct: Optional[float] = None
    next_tp: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    #: Set exactly when ``kind == KIND_NONE`` — never a bare "no".
    rejection: Optional[str] = None

    @property
    def is_close(self) -> bool:
        """True for a FULL close only. A partial is deliberately not a close:
        conflating them is what made turtle_soup's runner vanish in backtest."""
        return self.kind == KIND_CLOSE


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _none(rejection: str) -> VerdictDecision:
    return VerdictDecision(kind=KIND_NONE, rejection=rejection)


def interpret_verdict(verdict: Any, *,
                      current_sl: Any = None,
                      current_tp: Any = None) -> VerdictDecision:
    """Interpret one monitor verdict.

    ⚠️ ``current_sl`` / ``current_tp`` ARE WHAT WE *BELIEVE* RESTS AT THE VENUE,
    NOT WHAT DOES. This docstring used to call them "the position's present
    levels", which is false and was false in the direction that hides a defect.
    Every live caller passes the JOURNAL — ``order_monitor.py`` supplies
    ``open_pkg.get("sl")`` — and nothing on this path ever reads the venue's
    resting price. So the meaningful-change filter below compares an intent to a
    RECORD, and the record can be stale.

    The consequence is not a rounding nuisance, it is a permanent one: once the
    journal and the venue disagree, the strategy recomputes the journal's own
    level, ``abs(updates[key] - cur) <= tol`` deletes it, and this function
    returns ``no_meaningful_change`` — forever, on every pass, for every leg and
    every venue. `BL-20260823-MODIFY-IDEMPOTENCE-COMPARES-INTENT-TO-JOURNAL-NEVER-TO-VENUE`.
    Measured instance: MES 4350 sat 68.79 ticks ($1,289.73 on 15 contracts)
    below its declared stop from 2026-08-20, with a healthy monitor on a
    connected session, because the divergence is invisible from here.

    **Reconciling that belief with what actually rests is owned elsewhere, on
    purpose** — ``src/runtime/protection_reassert.py``, driven from the
    cadence-gated broker naked sweeps in ``order_monitor``, which already hold
    the resting prices. It is deliberately NOT done here: this path runs per
    open position per pass, and adding a broker read to it is the shape of both
    June 2026 wedges. Do not "fix" this by widening ``current_sl`` into a venue
    read.

    Pass them when known; when they are None the filter cannot apply and every
    parseable modify is kept (fail-permissive — the same direction live takes
    when ``open_pkg[key]`` won't parse).
    """
    if not isinstance(verdict, dict):
        return _none("not_a_dict")

    if verdict.get("action") == "close":
        raw_pct = verdict.get("close_qty_pct")
        close_qty_pct: Optional[float] = None
        if raw_pct is not None:
            close_qty_pct = _as_float(raw_pct)
            if close_qty_pct is None:
                return _none("invalid_close_qty_pct")
            if close_qty_pct <= 0.0 or close_qty_pct > 1.0:
                return _none("close_qty_pct_out_of_range")

        reason = str(verdict.get("reason") or DEFAULT_CLOSE_REASON)
        exit_price = _as_float(verdict.get("exit_price"))
        next_tp = _as_float(verdict.get("next_tp"))

        # pct == 1.0 is a FULL close (live: "falls through to full-close below").
        if close_qty_pct is not None and close_qty_pct < 1.0:
            return VerdictDecision(
                kind=KIND_PARTIAL_CLOSE, reason=reason, exit_price=exit_price,
                close_qty_pct=close_qty_pct, next_tp=next_tp,
            )
        return VerdictDecision(
            kind=KIND_CLOSE, reason=reason, exit_price=exit_price,
            close_qty_pct=1.0 if close_qty_pct is not None else None,
        )

    # Modification — sl / tp, INDEPENDENTLY (never elif). Other keys ignored.
    updates: Dict[str, float] = {}
    for key in ("sl", "tp"):
        if key in verdict:
            parsed = _as_float(verdict[key])
            if parsed is not None:
                updates[key] = parsed
    if not updates:
        return _none("unknown_verdict_shape")

    for key, current in (("sl", current_sl), ("tp", current_tp)):
        if key not in updates:
            continue
        cur = _as_float(current)
        if cur is None:
            continue
        tol = max(abs(cur) * MEANINGFUL_MODIFY_REL_TOL, 1e-8)
        if abs(updates[key] - cur) <= tol:
            del updates[key]
    if not updates:
        return _none("no_meaningful_change")

    return VerdictDecision(kind=KIND_MODIFY, sl=updates.get("sl"),
                           tp=updates.get("tp"))
