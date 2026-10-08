"""The TP-revision contract: how a strategy MOVES its take-profit prediction.

TP doctrine clause 2 (`docs/ARCHITECTURE-CANONICAL.md` § "TP doctrine",
operator 2026-10-06): *the TP is a LIVE prediction, re-estimated through the
trade's life from current market conditions.* The exchange half of the path
already existed and had never been fed (`monitor()` -> `{"tp": x}` ->
`monitor_verdict.interpret_verdict` -> `order_monitor._apply_update` ->
`execute.modify_open_order`); the prop half passed `take_profit=None`. This
module is the ONE place a revision is decided, so every consumer -- the live
`monitor()`, the prop trail step and the backtest harness -- moves the same
TP for the same bars.

THE VERDICT SHAPE (B1)
----------------------
A monitor that revises its target returns, alone or merged into its SL verdict::

    {"tp": <price>, "tp_reason": "<rule>: <why, with the numbers>"}

`tp` is the only key the amend path reads; `tp_reason` rides through
`interpret_verdict` onto `VerdictDecision.tp_reason` and into the
order_monitor log line and the TRADE UPDATED ping, so every TP move on a
venue carries the prediction that produced it. A close verdict
(`{"action": "close", ...}`) always wins: `merge_verdict` never attaches a TP
to a close.

WHAT A REVISION MAY NOT DO (checked here, before any consumer sees it)
----------------------------------------------------------------------
* rest on the wrong side of the current price (an instant fill is a close
  nobody decided -- `through_price`);
* rest beyond the venue cap measured from the CURRENT price (Bybit ErrCode
  10001's reference is the base price, not the entry): the rule's target is
  clamped to the cap and the reason SAYS it was clamped, because a clamped
  level is the venue's limit, not the prediction (`target_expectation`'s
  `clamped` state);
* be non-finite or non-positive.

A rule runs only for a leg that DECLARES it (`tp_revision: <rule>` or
`tp_revision: {rule: <rule>, ...}` in `config/strategies.yaml`, threaded into
the package meta like every other exit lever). Undeclared = no revision: a
rule is strategy logic and declaring it on a leg is Tier-3.

PURE: no I/O, no venue calls, never raises out of `plan_tp_revision`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT

__all__ = [
    "TP_KEY", "TP_REASON_KEY", "DECLARATION_KEY", "TpRevision", "RULES",
    "register_rule", "declared_rule", "rule_params", "tp_revision_rejection",
    "clamp_to_venue_cap", "plan_tp_revision", "as_verdict", "merge_verdict",
]

TP_KEY = "tp"
TP_REASON_KEY = "tp_reason"
#: The per-leg declaration key (config/strategies.yaml, threaded into meta).
DECLARATION_KEY = "tp_revision"

REJECT_INVALID = "invalid_tp"
REJECT_THROUGH_PRICE = "through_price"
REJECT_DIRECTION = "unknown_direction"


@dataclass(frozen=True)
class TpRevision:
    """One revised prediction, ready to be placed."""

    tp: float
    rule: str
    reason: str
    clamped: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)


#: rule name -> fn(*, direction, entry, risk, bars, entry_time, params) ->
#: (target, detail) | None. `bars` is a DataFrame of CLOSED bars only.
RuleFn = Callable[..., Optional[tuple]]
RULES: Dict[str, RuleFn] = {}


def register_rule(name: str) -> Callable[[RuleFn], RuleFn]:
    def deco(fn: RuleFn) -> RuleFn:
        RULES[name] = fn
        return fn
    return deco


def _f(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def declared_rule(cfg: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The rule a leg/package declares, or None. Accepts the bare string form
    and the mapping form (`{rule: ..., <params>}`)."""
    if not isinstance(cfg, Mapping):
        return None
    raw = cfg.get(DECLARATION_KEY)
    if isinstance(raw, Mapping):
        raw = raw.get("rule")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def rule_params(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    raw = (cfg or {}).get(DECLARATION_KEY) if isinstance(cfg, Mapping) else None
    if isinstance(raw, Mapping):
        return {k: v for k, v in raw.items() if k != "rule"}
    return {}


def tp_revision_rejection(direction: str, tp: Any, ref_price: Any) -> Optional[str]:
    """Why this TP may not be placed against ``ref_price``, or None."""
    t, p = _f(tp), _f(ref_price)
    if t is None or t <= 0 or p is None or p <= 0:
        return REJECT_INVALID
    if direction == "long":
        return REJECT_THROUGH_PRICE if t <= p else None
    if direction == "short":
        return REJECT_THROUGH_PRICE if t >= p else None
    return REJECT_DIRECTION


def clamp_to_venue_cap(direction: str, tp: float, ref_price: float,
                       cap_pct: float = TP_VENUE_CAP_PCT) -> tuple:
    """(tp, clamped) -- the level that can actually rest at the venue."""
    if direction == "long":
        cap = ref_price * (1.0 + cap_pct)
        return (cap, True) if tp > cap else (tp, False)
    cap = ref_price * (1.0 - cap_pct)
    return (cap, True) if tp < cap else (tp, False)


def plan_tp_revision(*, leg: Optional[Mapping[str, Any]], direction: str, entry: Any,
                     risk: Any, bars: Any, entry_time: Any, ref_price: Any,
                     cap_pct: float = TP_VENUE_CAP_PCT) -> Optional[TpRevision]:
    """The declared rule's current prediction for one open trade, or None.

    ``leg`` is the leg config or the package meta (both carry the
    declaration). ``bars`` are CLOSED bars (the forming bar excluded) with
    enough history before entry for the rule's lookback. ``ref_price`` is the
    latest price the venue will judge the TP against. None when the leg
    declares no rule, the rule is unknown, it cannot compute, or the result
    fails `tp_revision_rejection` -- a revision that cannot be placed is not
    sent, and the resting TP stays.
    """
    try:
        name = declared_rule(leg)
        if name is None or direction not in ("long", "short"):
            return None
        fn = RULES.get(name)
        e, r, p = _f(entry), _f(risk), _f(ref_price)
        if fn is None or e is None or r is None or r <= 0 or p is None:
            return None
        out = fn(direction=direction, entry=e, risk=r, bars=bars,
                 entry_time=entry_time, params=rule_params(leg))
        if not out:
            return None
        target, detail = out
        target = _f(target)
        if target is None:
            return None
        placed, clamped = clamp_to_venue_cap(direction, target, p, cap_pct)
        if tp_revision_rejection(direction, placed, p) is not None:
            return None
        target_r = (placed - e) / r if direction == "long" else (e - placed) / r
        reason = (f"{name}: target {placed:.8g} ({target_r:+.2f}R from entry)"
                  + (f", CLAMPED to the venue cap from {target:.8g}" if clamped else ""))
        return TpRevision(tp=round(placed, 8), rule=name, reason=reason, clamped=clamped,
                          detail={**(detail or {}), "target_r": round(target_r, 4),
                                  "unclamped": target})
    except Exception:  # noqa: BLE001 -- a revision must never break its caller
        return None


def as_verdict(rev: TpRevision) -> Dict[str, Any]:
    return {TP_KEY: rev.tp, TP_REASON_KEY: rev.reason}


def merge_verdict(base: Optional[Dict[str, Any]],
                  rev: Optional[TpRevision]) -> Optional[Dict[str, Any]]:
    """Attach a TP revision to a monitor verdict. A close verdict wins and is
    returned untouched; ``None`` + a revision is the revision alone."""
    if rev is None:
        return base
    if base is None:
        return as_verdict(rev)
    if base.get("action") == "close":
        return base
    return {**base, **as_verdict(rev)}
