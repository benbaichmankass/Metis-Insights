#!/usr/bin/env python3
"""Capital efficiency — the ONE definition of "did the capital earn its keep?"

WHY THIS MODULE EXISTS. `.claude/skills/exit-refinement/SKILL.md` § P2 has
always declared the gate's tiebreak as *"net_R per position-day"*, and **no
harness ever computed it**. `bars_held` was written into per-trade meta and
never aggregated; no sweep verdict, no matrix cell, no gate check has ever
referenced hold time. So a trade that reaches TP after 149 bars scores
IDENTICALLY to one that reached it in 10 — which is the operator's live
complaint in one sentence (2026-08-10): *"a 12 day eth trade that's going
nowhere ... we could be missing winning trades on other strategies because
we're just sitting on a trade that goes nowhere."*

WHY IT IS SHARED, not copied per harness. The first implementation landed
inside `scripts/backtest_ict_scalp.py`. Copying it into `backtest_pullback.py`
(and then trend / squeeze / fade) is precisely the two-definitions-that-drift
shape this repo has been bitten by repeatedly — two candle readers diverging on
JSONL, two trend engines diverging on trail semantics, two probes independently
re-deriving the shadow log's `score` and both getting it wrong the same day. A
cross-harness metric MUST have one home or a cross-harness comparison is
meaningless. `scripts/candle_io.py` is the precedent.

THE INTERFACE IS BARS, NOT TRADES — deliberately. Harness Trade shapes differ
(`backtest_ict_scalp` carries a `meta` dict with `bars_held`/`capital_bars`;
`backtest_pullback` carries bare `entry_index`/`exit_index` and no meta). A
signature over a Trade object would either force a writer change in every
harness or quietly special-case them here. The caller computes its own bars and
passes two scalars, so this module holds the DEFINITION and each harness holds
the extraction.

CAPITAL bars vs POSITION bars. They differ only when a lever releases part of
the position early — partial-TP banking frees `bank_frac` at the rung, so that
fraction stops consuming capital from that bar on:

    capital_bars = bank_frac·(rung_bar − entry_bar) + (1−bank_frac)·(exit_bar − entry_bar)

Measuring banking on UNWEIGHTED hold would score it identically to doing
nothing, which is exactly the blind spot. A harness with no such lever passes
`capital_bars == position_bars` and the two columns agree, honestly.

Tier-1 research tooling. Pure arithmetic; reads nothing, writes nothing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# The keys this module owns. A harness merges these into its summary; the
# CONTRACT is that every one is present on every run (None when unmeasurable)
# so a consumer never has to distinguish "absent" from "not computed".
KEYS = ("bar_minutes", "position_days", "capital_days", "mean_bars_held",
        "net_r_per_position_day", "net_r_per_capital_day")


def bar_minutes_from_frame(df: Any) -> Optional[float]:
    """Median bar length in minutes, MEASURED from the frame's own timestamps.

    Never inferred from a `--timeframe` label: a mislabelled or resampled frame
    would silently rescale every number derived from it, and the label is a
    claim about the data while the timestamps ARE the data (*field beats
    comment*). Returns None — never a default — when it cannot be measured.
    """
    try:
        import pandas as pd
        if "timestamp" not in getattr(df, "columns", []) or len(df) <= 2:
            return None
        deltas = pd.to_datetime(df["timestamp"]).diff().dropna()
        if not len(deltas):
            return None
        med = deltas.median().total_seconds() / 60.0
        return float(med) if med > 0 else None
    except Exception:  # noqa: BLE001 — advisory metric, never blocks a run
        return None


def days_from_bars(bars: float, bar_minutes: Optional[float]) -> Optional[float]:
    """Bars → days, or **None when the bar length is unknown**.

    None, never 0.0. "We could not measure the hold" and "the hold was zero"
    are opposite statements, and a fabricated zero would make a per-day rate
    either infinite or silently enormous (docs/CLAUDE-RULES-CANONICAL.md
    § "Collapsed states").
    """
    if bar_minutes is None or bars is None or bars <= 0:
        return None
    return bars * bar_minutes / 1440.0


def _per_day(total_r: Optional[float], days: Optional[float]) -> Optional[float]:
    if days is None or days <= 0 or total_r is None:
        return None
    return round(float(total_r) / days, 4)


def summarize(*, bar_minutes: Optional[float], position_bars: float,
              capital_bars: float, net_total_r: Optional[float],
              n_trades: int) -> Dict[str, Any]:
    """The capital-efficiency block, ready to merge into a harness summary.

    `net_total_r` should be the harness's NET (post-cost) figure — a gross-R
    rate would flatter a lever that trades more often, and cost is exactly what
    holding longer accrues (funding).

    Every key in `KEYS` is always present; unmeasurable values are None.
    """
    position_days = days_from_bars(position_bars, bar_minutes)
    capital_days = days_from_bars(capital_bars, bar_minutes)
    return {
        "bar_minutes": bar_minutes,
        "position_days": (round(position_days, 4)
                          if position_days is not None else None),
        "capital_days": (round(capital_days, 4)
                         if capital_days is not None else None),
        "mean_bars_held": (round(position_bars / n_trades, 3)
                           if n_trades else None),
        "net_r_per_position_day": _per_day(net_total_r, position_days),
        "net_r_per_capital_day": _per_day(net_total_r, capital_days),
    }


def empty() -> Dict[str, Any]:
    """The zero-trade block — every key present and None, never 0.0.

    A run with no trades has an UNDEFINED rate, not a zero one; emitting 0.0
    would rank an un-run cell alongside a genuinely flat one.
    """
    return {k: None for k in KEYS}
