"""M30 · P5 — pure MFE/MAE excursion math for the exit-timing panel.

The exit-timing study (the operator's load-bearing prior — "edge lives in
exit/regime") needs, per closed trade, how far price ran **in favor** (max
favorable excursion, MFE) and **against** (max adverse excursion, MAE) between
entry and exit, in risk (R) units, plus how much of the peak was *given back* by
the actual exit. That requires the intrabar price **path** — a candle series over
the trade's holding window — which is NOT persisted in ``trade_journal.db`` (the
row carries only entry/exit price, SL, TP, and timestamps). The candle source is
supplied by the caller (the P5 builder fetches it); this module is the small,
**pure, unit-testable** core that turns a candle path + trade geometry into the
excursion outcome columns.

Everything here is deterministic arithmetic over the provided candles — **no I/O,
no network, no DB, no numpy** — so it is exhaustively testable with synthetic
candle paths and is Tier-1 by construction. The leakage discipline is the
caller's: the excursion outcomes are strictly FUTURE to the decision-time
features (they summarise the post-entry path), so a panel must treat them as
OUTCOME columns, never regressors.

Definitions (long; short is mirrored):
  risk            = |entry - stop_loss|                     (the R denominator)
  favorable(bar)  = high - entry                            (short: entry - low)
  adverse(bar)    = entry - low                             (short: high - entry)
  MFE / MAE       = max favorable / max adverse over the window (>= 0, price units)
  mfe_r / mae_r   = MFE / risk , MAE / risk                 (None when risk <= 0)
  realized_r      = (exit - entry) * dir / risk             (signed; None w/o exit)
  giveback_r      = max(0, mfe_r - max(realized_r, 0))      (peak PROFIT surrendered:
                                                             a loss below entry is not
                                                             "giveback", it's loss)
  capture_ratio   = realized_r / mfe_r                      (fraction of peak kept)
  bars_to_mfe     = index of the bar that first achieves MFE
  bars_held       = number of candles in the window
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# A candle is any mapping with numeric high/low (+ optional close/timestamp).
Candle = Dict[str, Any]

_LONG_ALIASES = {"buy", "long", "b", "1", "up"}
_SHORT_ALIASES = {"sell", "short", "s", "-1", "down"}


def _is_long(side: Any) -> Optional[bool]:
    """True=long, False=short, None=unresolved. Accepts the journal's textual
    ``direction``/``side`` spellings."""
    if side is None:
        return None
    s = str(side).strip().lower()
    if s in _LONG_ALIASES:
        return True
    if s in _SHORT_ALIASES:
        return False
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # reject NaN


def _bar_hl(candle: Candle) -> tuple[Optional[float], Optional[float]]:
    """(high, low) from a candle mapping, tolerant of missing keys."""
    if not isinstance(candle, dict):
        return None, None
    return _num(candle.get("high")), _num(candle.get("low"))


def compute_excursions(
    candles: Sequence[Candle],
    *,
    entry_price: Any,
    stop_loss: Any,
    side: Any,
    exit_price: Any = None,
    round_to: int = 6,
) -> Dict[str, Any]:
    """Return the excursion outcome columns for one trade over ``candles``.

    ``candles`` must already be the bars spanning the trade's holding window
    (the P5 builder slices/fetches to ``[entry_time, exit_time]``). Order is
    assumed chronological. Tolerant: missing geometry / empty candles yield an
    honest ``None``-filled record with a ``note``, never a raise.

    Every field is decision-*future* (an OUTCOME) — never feed one back as a
    regressor.
    """
    entry = _num(entry_price)
    stop = _num(stop_loss)
    exit_p = _num(exit_price)
    is_long = _is_long(side)

    out: Dict[str, Any] = {
        "bars_held": 0,
        "mfe": None,
        "mae": None,
        "mfe_r": None,
        "mae_r": None,
        "realized_r": None,
        "giveback_r": None,
        "capture_ratio": None,
        "bars_to_mfe": None,
        "time_to_mfe_frac": None,
        "risk": None,
        "note": None,
    }

    if entry is None or is_long is None:
        out["note"] = "missing entry_price or unresolved side"
        return out

    risk = abs(entry - stop) if stop is not None else None
    if risk is not None and risk > 0:
        out["risk"] = round(risk, round_to)

    # Scan the path for MFE / MAE (price units, always computable from candles).
    best_fav = None
    worst_adv = None
    bars_to_mfe = None
    n = 0
    for i, c in enumerate(candles):
        high, low = _bar_hl(c)
        if high is None or low is None:
            continue
        n += 1
        fav = (high - entry) if is_long else (entry - low)
        adv = (entry - low) if is_long else (high - entry)
        if best_fav is None or fav > best_fav:
            best_fav = fav
            bars_to_mfe = n - 1  # 0-based index among usable bars
        if worst_adv is None or adv > worst_adv:
            worst_adv = adv

    out["bars_held"] = n
    if n == 0:
        out["note"] = "no usable candles in the window"
        return out

    # MFE/MAE are excursions (>= 0): a trade that only ever went against you has
    # MFE = 0 (never traded above entry), not a negative number.
    mfe = max(best_fav, 0.0) if best_fav is not None else 0.0
    mae = max(worst_adv, 0.0) if worst_adv is not None else 0.0
    out["mfe"] = round(mfe, round_to)
    out["mae"] = round(mae, round_to)
    out["bars_to_mfe"] = bars_to_mfe
    if n > 1 and bars_to_mfe is not None:
        out["time_to_mfe_frac"] = round(bars_to_mfe / (n - 1), round_to)

    if risk and risk > 0:
        out["mfe_r"] = round(mfe / risk, round_to)
        out["mae_r"] = round(mae / risk, round_to)
        if exit_p is not None:
            realized = ((exit_p - entry) if is_long else (entry - exit_p)) / risk
            out["realized_r"] = round(realized, round_to)
            # Giveback is peak PROFIT surrendered — you can only give back profit
            # you actually had, so clamp realized at 0: a trade that never went
            # favorable (mfe_r 0) or ended below entry has giveback bounded by the
            # peak, and the loss below entry is loss, not giveback.
            out["giveback_r"] = round(max(0.0, (mfe / risk) - max(realized, 0.0)), round_to)
            if mfe > 0:
                out["capture_ratio"] = round(realized / (mfe / risk), round_to)
    return out


def excursion_feature_names() -> List[str]:
    """The excursion OUTCOME column names (for the panel's outcome-cols stamp)."""
    return [
        "mfe_r",
        "mae_r",
        "realized_r",
        "giveback_r",
        "capture_ratio",
        "bars_to_mfe",
        "time_to_mfe_frac",
        "bars_held",
    ]
