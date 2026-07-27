"""M36 Track C · C4 — the conditioned-lifecycle exit over an injected price path.

This is the pure scoring seam the C4 backtest needs: given a thesis's entry, its
direction, the move it bets on (``expected_move_pct``) + its ``horizon_days``, and
the **realized daily price path** from entry to horizon, decide **when the
conditioned lifecycle would exit** — driving the *actually-shipped* C2
(``thesis_progress``) + C3 (``crowding_read``) functions, not a re-implementation.

The lifecycle it scores (Move B1 + B2 of ``M36-macro-intelligence-and-crowding-DESIGN.md``):

  * walk each path day; at each, compute C2 :func:`compute_progress` +
    :func:`progress_action` (target reached before horizon → **trim**; overshoot →
    **exit**);
  * optionally fold C3 :func:`crowding_read` (from the price **over-extension** of
    the realized move — the only crowding input reconstructable point-in-time over
    decades) via :func:`conditioned_exit`, which advances a crowded **near-target**
    hold to a trim;
  * **exit at the first day the action is trim/exit**, marking that day's close;
  * else **hold to horizon** (the baseline behaviour) — the last path close.

So the conditioned arm can only ever exit **earlier** than the baseline (it never
extends the hold) — the reductive "priced-in-early → move the exit up" contract.
Pure, stdlib-only, no I/O, no clock: the path + params are injected, so the read
is deterministic and replayable at backtest time. Observe-only — nothing here
touches an order path; graduating any exit to live is C4-gated + Tier-3.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .crowding_read import conditioned_exit, crowding_read
from .thesis import TradeThesis
from .thesis_progress import compute_progress, progress_action


def _days_between(a_iso: str, b_iso: str) -> float:
    """Calendar days from ``a_iso`` to ``b_iso`` (date-part only; ≥ 0 in practice).

    Both are ``YYYY-MM-DD`` (or longer ISO); only the date is used. Best-effort:
    an unparseable pair yields ``0.0`` (treated as same-day — never a raise)."""
    import datetime as _dt

    try:
        a = _dt.date.fromisoformat(str(a_iso)[:10])
        b = _dt.date.fromisoformat(str(b_iso)[:10])
    except ValueError:
        return 0.0
    return float((b - a).days)


def _synth_thesis(
    thesis_id: str,
    direction: str,
    entry_price: float,
    expected_move_pct: float,
    horizon_days: float,
) -> TradeThesis:
    """A minimal thesis carrying exactly what C2 resolves (entry/target/horizon).

    ``target`` is expressed as a **signed** ``expected_move_pct`` so C2's
    ``_resolve_target_value`` derives ``target = entry·(1 + pct)`` — the sign
    carries the direction (long → +pct, short → −pct), matching C2's signed
    ``expected_move`` convention (so long/short need no special-casing)."""
    signed = abs(expected_move_pct) if str(direction).lower() == "long" else -abs(expected_move_pct)
    return TradeThesis(
        thesis_id=thesis_id,
        created_at="",
        updated_at="",
        direction=str(direction),
        entry_plan={"entry": float(entry_price)},
        target={"expected_move_pct": float(signed)},
        horizon_days=float(horizon_days),
    )


def conditioned_exit_on_path(
    *,
    thesis_id: str,
    direction: str,
    entry_price: float,
    as_of: str,
    path: Sequence[tuple],
    horizon_days: float,
    expected_move_pct: float,
    use_crowding: bool = True,
    target_reached: float = 1.0,
    overshoot_at: float = 1.25,
    crowded_at: float = 0.6,
    near_target: float = 0.7,
) -> Optional[dict]:
    """Resolve the conditioned lifecycle's exit over the realized ``path``.

    ``path`` is the ascending ``[(date_iso, close), ...]`` of the instrument's
    daily closes **after** the entry date up to and including the horizon date
    (the caller slices ``as_of < date <= exit_at``). Returns
    ``{exit_price, hold_days, exit_reason, exit_index, move_progress}`` — the day
    the C2/C3 lifecycle would have exited, or the final path close (hold to
    horizon) if no trigger fired. ``None`` when the path is empty / entry is
    non-positive (uncomputable — the caller drops it, never a fabricated exit).
    """
    try:
        entry = float(entry_price)
    except (TypeError, ValueError):
        return None
    if not (entry > 0) or not path:
        return None

    synth = _synth_thesis(thesis_id, direction, entry, expected_move_pct, horizon_days)

    for i, row in enumerate(path):
        try:
            day, close = str(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        elapsed = _days_between(as_of, day)
        read = compute_progress(synth, close, elapsed,
                                target_reached=target_reached, overshoot_at=overshoot_at)
        action = progress_action(read, target_reached=target_reached, overshoot_at=overshoot_at)
        if use_crowding:
            # Price over-extension is the only crowding input reconstructable
            # point-in-time over decades: how far the move has run in the thesis
            # direction (move_progress, clamped to [0,1] by crowding_read._unit).
            ext = read.move_progress
            cr = crowding_read(move_extension=ext) if ext is not None else crowding_read()
            action = conditioned_exit(action, cr, crowded_at=crowded_at, near_target=near_target)
        if action.get("action") in ("trim", "exit"):
            return {
                "exit_price": close,
                "hold_days": elapsed,
                "exit_reason": action.get("reason"),
                "exit_index": i,
                "move_progress": read.move_progress,
            }

    # No trigger fired across the path → hold to horizon (the baseline exit).
    last_day, last_close = str(path[-1][0]), float(path[-1][1])
    return {
        "exit_price": last_close,
        "hold_days": _days_between(as_of, last_day),
        "exit_reason": "held to horizon (no conditioned exit)",
        "exit_index": len(path) - 1,
        "move_progress": (float(last_close) - entry) / (entry * (
            abs(expected_move_pct) if str(direction).lower() == "long" else -abs(expected_move_pct)
        )) if expected_move_pct else None,
    }
