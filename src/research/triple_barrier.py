"""M30 × M20 — triple-barrier + time-stop forward label for the exit head.

The LABEL half of the per-bar exit panel. At an in-trade decision bar ``t`` the
head must answer *should I keep holding, or exit now?* — so the label is the
outcome of **continuing to hold** from ``t``, resolved by a López-de-Prado
triple barrier over the strictly-future bars ``[t+1 .. t+time_stop_bars]``:

- **upper barrier** = a profit target ``entry + tp_r · R`` (favorable),
- **lower barrier** = the position's stop ``stop_loss`` (adverse, = −1R),
- **vertical barrier** = the **time-stop** at ``t + time_stop_bars``
  (the literature's highest-value lever on 5m: cap how long a stalled position
  is held).

The realized R of holding, ``forward_r``, is measured from ENTRY (so it is
directly comparable to the exit-now alternative ``upnl_r`` the feature module
reports), and first-touch wins. This is the ``ml/datasets/labeling/triple_barrier``
idea applied from the *in-trade* bar rather than the entry bar — the exit-timing
label.

**Strictly future.** ``forward_candles`` are bars AFTER ``t``; the feature module
reads only bars up to ``t``. The two windows are disjoint by construction, which
is the leakage invariant. Pure arithmetic — no I/O, no numpy — exhaustively
testable with synthetic forward paths.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from src.research.excursions import _is_long, _num

Candle = Dict[str, Any]


def triple_barrier_forward(
    forward_candles: Sequence[Candle],
    *,
    entry_price: Any,
    stop_loss: Any,
    side: Any,
    tp_r: float = 2.0,
    time_stop_bars: int = 12,
    round_to: int = 6,
) -> Dict[str, Any]:
    """The triple-barrier outcome of holding from a decision bar over the future.

    ``forward_candles`` = the bars strictly AFTER the decision bar (``[t+1 …]``),
    chronological; only the first ``time_stop_bars`` are consulted. Returns
    ``{forward_r, touch, touch_offset, tp_price, note}`` where ``forward_r`` is the
    realized R (from entry) of continuing to hold to the first barrier touch, and
    ``touch ∈ {'tp','sl','time','none'}``. Tolerant: missing geometry / no future
    bars → an honest ``None`` ``forward_r`` with a ``note``.
    """
    out: Dict[str, Any] = {
        "forward_r": None,
        "touch": "none",
        "touch_offset": None,
        "tp_price": None,
        "note": None,
    }
    entry = _num(entry_price)
    stop = _num(stop_loss)
    is_long = _is_long(side)
    if entry is None or stop is None or is_long is None:
        out["note"] = "missing entry/stop or unresolved side"
        return out
    risk = abs(entry - stop)
    if risk <= 0:
        out["note"] = "degenerate risk (entry == stop)"
        return out

    dir_sign = 1.0 if is_long else -1.0
    tp_price = entry + dir_sign * tp_r * risk
    out["tp_price"] = round(tp_price, round_to)

    horizon = max(1, int(time_stop_bars))
    bars = [c for c in forward_candles if isinstance(c, dict)][:horizon]
    if not bars:
        out["note"] = "no future bars"
        return out

    for i, c in enumerate(bars, start=1):
        hi, lo = _num(c.get("high")), _num(c.get("low"))
        if hi is None or lo is None:
            continue
        if is_long:
            hit_sl = lo <= stop
            hit_tp = hi >= tp_price
        else:
            hit_sl = hi >= stop
            hit_tp = lo <= tp_price
        # Conservative on a same-bar double touch: assume the ADVERSE leg fills
        # first (we cannot see intrabar order), so the stop wins the tie.
        if hit_sl:
            out.update(forward_r=-1.0, touch="sl", touch_offset=i)
            return out
        if hit_tp:
            out.update(forward_r=round(tp_r, round_to), touch="tp", touch_offset=i)
            return out

    # Vertical barrier (time-stop): mark-to-market at the last consulted bar.
    last_close = _num(bars[-1].get("close"))
    if last_close is None:
        out["note"] = "time-stop reached but no close on the final bar"
        return out
    forward_r = (last_close - entry) * dir_sign / risk
    out.update(forward_r=round(forward_r, round_to), touch="time", touch_offset=len(bars))
    return out


def hold_meta_label(
    forward_r: Optional[float], upnl_r: Optional[float], *, cost_r: float = 0.0
) -> Dict[str, Any]:
    """The take/skip meta-label + sizing magnitude for the exit head.

    Compares HOLDING (``forward_r`` = the triple-barrier outcome of continuing to
    hold) against EXITING NOW (``upnl_r`` = the current mark-to-market R):

      advantage_r = forward_r - upnl_r - cost_r
      label_hold  = 1 if advantage_r > 0 else 0      (take = keep holding)

    ``cost_r`` is a net-of-fee buffer in R (the marginal cost of the extra
    holding leg) so a razor-thin advantage does not flip the label. Returns
    ``{advantage_r, label_hold, size}`` with ``size = |advantage_r|`` (the sizing
    target for the magnitude head). ``None`` inputs → an honest all-``None`` record.
    """
    if forward_r is None or upnl_r is None:
        return {"advantage_r": None, "label_hold": None, "size": None}
    advantage = forward_r - upnl_r - float(cost_r)
    return {
        "advantage_r": round(advantage, 6),
        "label_hold": 1 if advantage > 0 else 0,
        "size": round(abs(advantage), 6),
    }
