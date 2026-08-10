#!/usr/bin/env python3
"""Exit capture — the ONE definition of "how much of the move did we keep?"

WHY THIS MODULE EXISTS. The operator's standing complaint (2026-08-10) is
concrete: *"the amount of times that a trade has gotten within literally
several cents of its take profit, and then it drops and turns into a loss."*
The standard name for that is **MFE capture** — realized R over maximum
favorable excursion — and this repo has computed it since
`src/research/excursions.py` landed. **No gate, sweep verdict, or coverage-matrix
cell has ever read it** (`BL-20260810-EXIT-GATE-BLIND-TO-CAPTURE-AND-CAPITAL`).
M20's gate optimises `net_R` and `maxDD`, both of which are indifferent to
giving a winner back, so 266 of 400 cells were graded `honest_negative` by an
objective function that could not see the thing being complained about.

This is the third written-but-never-read instance here, after
`exit_price_source` (12 writers, one unrelated reader — the phantom −$6,358
leak) and `net_r_per_capital_day`. So this module is a **sibling of
`scripts/capital_efficiency.py`, deliberately shaped the same way**: one home
for the definition, harnesses own only the extraction, and it is imported
rather than copied. Two capture definitions drifting apart would make a
cross-leg comparison meaningless, which is the failure `scripts/candle_io.py`
and the trend-engine convergence guard both exist to prevent.

TWO QUESTIONS, AND THEY ARE NOT THE SAME QUESTION.

  * **near-miss** — "did price get almost to the fixed target, then lose?"
    Only well-posed for a leg with a FIXED R TARGET (`ict_scalp::tp_at_r`,
    `fade`/`fvg_range::tp_r`). A trail-exit leg (pullback / trend / squeeze)
    has no target to nearly reach — its exit IS the trail — so near-miss is
    `None` there, **never 0.0**. Reporting "0% near-misses" for a leg that
    cannot have one would read as a clean bill of health for the exact
    population the operator is worried about.

  * **capture** — "of the peak we actually reached, how much did we keep?"
    Well-posed for EVERY leg, target or trail. This is the axis that
    generalises.

Report both, apply each only where it is defined.

EXTERNAL REFERENCE POINTS (for orientation, NOT thresholds): the practitioner
literature puts capture below ~30% at "severe edge leakage, redesign the exit"
and above ~75% at "excellent". They are not encoded here — a gate threshold is
the operator's to set from a measured distribution, the same discipline
`capital_efficiency` follows.

Stdlib only, pure arithmetic; reads nothing, writes nothing. Tier-1 research
tooling.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# The keys this module owns. Every one is present on every summary (None when
# undefined) so a consumer never has to distinguish "absent" from "not computed".
KEYS = ("n_trades", "n_winners", "n_losers",
        "capture_measured_n", "capture_mean", "capture_median",
        "capture_p25", "capture_lt_30_pct", "capture_gt_75_pct",
        "target_r", "near_miss_measured_n",
        "near_miss_80_pct", "near_miss_90_pct", "near_miss_95_pct",
        "near_miss_r_left_on_table", "mfe_r_measured_n")


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def capture_ratio(net_r: Any, mfe_r: Any) -> Optional[float]:
    """realized R / peak R — the fraction of the excursion actually kept.

    `None`, never 0.0, when MFE is unknown or non-positive: a trade that never
    went favourable has an UNDEFINED capture, not a zero one, and averaging a
    fabricated 0.0 into a fleet mean would drag it toward a value no trade ever
    printed. Matches `src/research/excursions.py::compute_excursions`, which
    likewise emits `capture_ratio` only when `mfe > 0`.

    Deliberately NOT clamped to [0, 1]. A loser that peaked favourably yields a
    negative ratio, and that is the honest reading — clamping it to 0 would
    erase precisely the give-it-all-back case this module was built to count.
    """
    n, m = _num(net_r), _num(mfe_r)
    if n is None or m is None or m <= 0:
        return None
    return round(n / m, 4)


def _pct(numer: int, denom: int) -> Optional[float]:
    return round(100.0 * numer / denom, 2) if denom else None


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = int(q * (len(sorted_vals) - 1))
    return round(sorted_vals[idx], 4)


def summarize(trades: Iterable[Dict[str, Any]], *,
              target_r: Optional[float] = None) -> Dict[str, Any]:
    """The exit-capture block for one leg.

    `trades` are emitted-trade rows (the harnesses' ``--emit-trades`` JSONL):
    each carries ``net_r`` and ``mfe_r``. `target_r` is the leg's fixed R
    target when it HAS one (`tp_at_r` / `tp_r`); pass `None` for a trail-exit
    leg and every near-miss key comes back `None` rather than a misleading zero.

    `near_miss_r_left_on_table` is the summed `mfe_r - net_r` over near-miss
    LOSERS at the 90% band — the prize, in R, expressed on the same axis the
    gate already speaks. It is `None`, not 0.0, when the leg has no target.
    """
    rows = list(trades)
    nets = [_num(t.get("net_r")) for t in rows]
    mfes = [_num(t.get("mfe_r")) for t in rows]
    n = len(rows)
    winners = sum(1 for v in nets if v is not None and v > 0)
    losers = sum(1 for v in nets if v is not None and v < 0)

    caps = [c for c in (capture_ratio(a, b) for a, b in zip(nets, mfes))
            if c is not None]
    caps_sorted = sorted(caps)
    out: Dict[str, Any] = {
        "n_trades": n,
        "n_winners": winners,
        "n_losers": losers,
        "mfe_r_measured_n": sum(1 for m in mfes if m is not None),
        "capture_measured_n": len(caps),
        "capture_mean": (round(sum(caps) / len(caps), 4) if caps else None),
        "capture_median": _quantile(caps_sorted, 0.5),
        "capture_p25": _quantile(caps_sorted, 0.25),
        "capture_lt_30_pct": _pct(sum(1 for c in caps if c < 0.30), len(caps)),
        "capture_gt_75_pct": _pct(sum(1 for c in caps if c > 0.75), len(caps)),
        "target_r": target_r,
    }

    tr = _num(target_r)
    if tr is None or tr <= 0:
        # Trail-exit leg: near-miss is UNDEFINED, not zero. See module docstring.
        out.update({"near_miss_measured_n": None, "near_miss_80_pct": None,
                    "near_miss_90_pct": None, "near_miss_95_pct": None,
                    "near_miss_r_left_on_table": None})
        return out

    # Population is LOSING trades with a measured MFE — the denominator the
    # complaint is about. A loser with no MFE reading is excluded from both
    # sides rather than counted as a non-near-miss.
    pop = [(nv, mv) for nv, mv in zip(nets, mfes)
           if nv is not None and mv is not None and nv < 0]
    out["near_miss_measured_n"] = len(pop)
    for band, key in ((0.80, "near_miss_80_pct"), (0.90, "near_miss_90_pct"),
                      (0.95, "near_miss_95_pct")):
        out[key] = _pct(sum(1 for _, mv in pop if mv >= band * tr), len(pop))
    left = [mv - nv for nv, mv in pop if mv >= 0.90 * tr]
    out["near_miss_r_left_on_table"] = round(sum(left), 3) if pop else None
    return out


def empty() -> Dict[str, Any]:
    """The zero-trade block — every key present and None/0, never a fake rate."""
    out: Dict[str, Any] = {k: None for k in KEYS}
    out.update({"n_trades": 0, "n_winners": 0, "n_losers": 0,
                "capture_measured_n": 0, "mfe_r_measured_n": 0})
    return out
