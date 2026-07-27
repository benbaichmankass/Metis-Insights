"""M36 Track C · C2 — ``thesis_progress``: the "priced-in-early → move the exit up"
read (Move B1).

The operator's headline example: a thesis is meant to play out over ~weeks, but if
the market **prices it in early** (reaches the target before the horizon) — or
**overshoots** it — the remaining hold is uncompensated risk, so the exit should
move up. This module is the pure read that detects it:

  * **move_progress** = how far the realized price move has gone toward the thesis
    ``target``, signed by the thesis direction (``expected_move`` carries the sign,
    so long/short need no special-casing): 1.0 = target reached, >1 = overshoot,
    <0 = moving against the thesis.
  * **time_progress** = elapsed / ``horizon_days``.
  * a would-be **action** ∈ {hold, trim, exit} keyed on the two: target reached
    **before** the horizon → **trim** (advance the exit / take the move); overshoot
    → **exit** (the market over-priced it).

**Pure, stdlib-only, observe-only.** No I/O, no clock — ``current_price`` and
``elapsed_days`` are injected (the caller reads them at the as-of date), so the
read is deterministic + replayable at backtest time. It returns a *would-be*
action for the soak (mirroring ``thesis.would_transition``); it never trims,
exits, or touches an order path. Graduating the action to a real exit is the
C4-backtest + Tier-3 step. Entry/target are resolved tolerantly from the thesis
(``entry_plan`` / ``target`` — the latter as C1's ``scenario_read`` populates it);
anything unresolvable yields an honest ``None``-progress read with a ``note``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .thesis import TradeThesis


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _first(d: Any, *keys: str) -> Optional[float]:
    """First numeric value among ``keys`` in a mapping, else None."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = _num(d.get(k))
        if v is not None:
            return v
    return None


def _resolve_entry(thesis: TradeThesis) -> Optional[float]:
    """The thesis entry price (tolerant of the entry_plan key spelling)."""
    return _first(thesis.entry_plan, "entry", "entry_price", "price", "level")


def _resolve_target_value(thesis: TradeThesis, entry: Optional[float]) -> Optional[float]:
    """The target price. Prefers an explicit value; else derives it from a
    ``expected_move_pct`` applied to the entry (C1 scenario_read's target shape)."""
    tv = _first(thesis.target, "expected_value", "price", "target_price", "value")
    if tv is not None:
        return tv
    pct = _first(thesis.target, "expected_move_pct")
    if pct is not None and entry is not None:
        return entry * (1.0 + pct)
    return None


@dataclass(frozen=True)
class ProgressRead:
    """How far / how fast a thesis has played out toward its target."""

    thesis_id: str
    entry: Optional[float]
    target_value: Optional[float]
    current_price: Optional[float]
    realized_move: Optional[float]  # signed: current - entry
    expected_move: Optional[float]  # signed: target - entry (carries direction)
    move_progress: Optional[float]  # realized / expected (1.0 = target reached)
    time_progress: Optional[float]  # elapsed / horizon
    overshoot: bool = False
    early: bool = False  # target reached (or beyond) before the horizon elapsed
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "thesis_id": self.thesis_id,
            "entry": self.entry,
            "target_value": self.target_value,
            "current_price": self.current_price,
            "realized_move": self.realized_move,
            "expected_move": self.expected_move,
            "move_progress": self.move_progress,
            "time_progress": self.time_progress,
            "overshoot": self.overshoot,
            "early": self.early,
            "note": self.note,
        }


def compute_progress(
    thesis: TradeThesis,
    current_price: Any,
    elapsed_days: Any,
    *,
    target_reached: float = 1.0,
    overshoot_at: float = 1.25,
) -> ProgressRead:
    """Return the progress read for an active thesis (pure).

    ``current_price`` is the instrument's as-of price; ``elapsed_days`` is
    calendar days since the thesis opened. Tolerant: a missing entry/target/price
    yields ``move_progress=None`` + a ``note`` (nothing to act on), never a raise.
    """
    entry = _resolve_entry(thesis)
    cur = _num(current_price)
    target_value = _resolve_target_value(thesis, entry)
    horizon = _num(thesis.horizon_days)
    elapsed = _num(elapsed_days)

    time_progress = None
    if horizon is not None and horizon > 0 and elapsed is not None:
        time_progress = elapsed / horizon

    if entry is None or cur is None or target_value is None:
        return ProgressRead(
            thesis_id=thesis.thesis_id, entry=entry, target_value=target_value,
            current_price=cur, realized_move=None, expected_move=None,
            move_progress=None, time_progress=time_progress,
            note="missing entry/target/price — progress not computable",
        )

    realized = cur - entry
    expected = target_value - entry
    if expected == 0.0:
        return ProgressRead(
            thesis_id=thesis.thesis_id, entry=entry, target_value=target_value,
            current_price=cur, realized_move=realized, expected_move=0.0,
            move_progress=None, time_progress=time_progress,
            note="degenerate target (== entry) — progress undefined",
        )
    move_progress = realized / expected
    overshoot = move_progress >= overshoot_at
    early = move_progress >= target_reached and (time_progress is None or time_progress < 1.0)
    return ProgressRead(
        thesis_id=thesis.thesis_id, entry=entry, target_value=target_value,
        current_price=cur, realized_move=realized, expected_move=expected,
        move_progress=move_progress, time_progress=time_progress,
        overshoot=overshoot, early=early,
    )


def progress_action(
    read: ProgressRead,
    *,
    target_reached: float = 1.0,
    overshoot_at: float = 1.25,
    stall_at: float = 0.25,
) -> dict:
    """The would-be exit action from a progress read — observe-only (soak record).

    Returns ``{thesis_id, action ∈ {hold, trim, exit}, reason, move_progress,
    time_progress}``. Never applies anything — the caller logs it; graduating to
    a real exit is C4-backtest + Tier-3 gated.
      * overshoot (move_progress ≥ overshoot_at) → **exit** (market over-priced it).
      * target reached **before** the horizon → **trim** (advance the exit / take
        the move) — the operator's "move up the exit".
      * horizon elapsed but the move stalled (move_progress < stall_at) → **hold**
        + a ``stalled`` reason flag (invalidation is handled by the lifecycle, not
        here).
      * else → **hold**.
    """
    mp = read.move_progress
    tp = read.time_progress
    base = {"thesis_id": read.thesis_id, "move_progress": mp, "time_progress": tp}
    if mp is None:
        return {**base, "action": "hold", "reason": read.note or "no progress read"}
    if mp >= overshoot_at:
        return {**base, "action": "exit", "reason": "overshoot — target exceeded"}
    if mp >= target_reached and (tp is None or tp < 1.0):
        return {**base, "action": "trim", "reason": "target reached before horizon — advance exit"}
    if tp is not None and tp >= 1.0 and mp < stall_at:
        return {**base, "action": "hold", "reason": "horizon elapsed, move stalled — review invalidation"}
    return {**base, "action": "hold", "reason": "in progress"}
