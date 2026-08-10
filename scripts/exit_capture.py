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
        "near_miss_r_left_on_table", "mfe_r_measured_n",
        "near_miss_not_applicable", "target_r_reached_n",
        "mfe_floor_r", "capture_lowmfe_n", "capture_mean_robust",
        "capture_winners_median", "capture_winners_n",
        "capture_r_weighted", "giveback_ladder")

# The giveback ladder's rungs, in R of maximum favourable excursion. A LADDER
# and not a threshold: the operator sets a cut-off from the measured shape, the
# same discipline `capital_efficiency` follows for net_r_per_capital_day.
GIVEBACK_RUNGS = (0.5, 1.0, 1.5, 2.0)

# Below this MFE the capture RATIO is denominator-noise, not a capture reading.
# This is a REPORTING floor for a statistic, NOT a gate threshold — it changes
# which mean is trustworthy, never whether a leg passes anything.
DEFAULT_MFE_FLOOR_R = 0.1


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN -> None


def mfe_r_of(row: Dict[str, Any]) -> Optional[float]:
    """Read a harness emitted-trade row's MFE in R, whichever shape it uses.

    ONE accessor, because two harness shapes exist and every consumer that
    re-derived the read got the same one wrong. `backtest_trend` /
    `backtest_pullback` / `backtest_squeeze` put `mfe_r` at the TOP LEVEL;
    `backtest_ict_scalp` nests it under `meta` beside `mae_r` / `bars_held` /
    `capital_bars`. Neither is wrong — but a consumer that reads only the top
    level sees `None` for every scalp trade and cannot tell that apart from a
    trade that never went favourable.

    Measured 2026-08-10: the census reported `meas=0/1102` for
    `ict_scalp_avax_5m` and 0-of-N for all five scalp legs it reached — 3,823
    trades, zero capture readings — and `winner_mfe_p80` (the M20 P4.4
    percentile arm) returned `None` for every ict_scalp leg, which ITS contract
    defines as "fewer than 30 winners". A leg with 1,102 trades reporting
    "not enough winners" is the unasserted-denominator class
    (`docs/CLAUDE-RULES-CANONICAL.md`); the arm was inert and said nothing.

    Returns None only when the row genuinely carries no MFE reading. Callers
    that need to distinguish "absent" from "present but non-positive" should
    check the returned value, not re-implement the lookup.
    """
    v = _num(row.get("mfe_r"))
    if v is not None:
        return v
    meta = row.get("meta")
    return _num(meta.get("mfe_r")) if isinstance(meta, dict) else None


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
              target_r: Optional[float] = None,
              mfe_floor_r: float = DEFAULT_MFE_FLOOR_R) -> Dict[str, Any]:
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
    mfes = [mfe_r_of(t) for t in rows]   # shape-agnostic; see mfe_r_of
    n = len(rows)
    winners = sum(1 for v in nets if v is not None and v > 0)
    losers = sum(1 for v in nets if v is not None and v < 0)

    caps = [c for c in (capture_ratio(a, b) for a, b in zip(nets, mfes))
            if c is not None]
    caps_sorted = sorted(caps)
    # A RATIO EXPLODES ON A SMALL DENOMINATOR, and the mean is where it shows.
    # Measured 2026-08-10: `fvg_range_15m` reported capture_mean = -14.13 and
    # `gld_pullback_1h` -12.85. Neither means "we gave back 1300% of the move" —
    # a loser that peaked at mfe_r 0.05 and closed at -1R contributes -20 all by
    # itself, so a handful of never-really-went-favourable trades set the mean.
    # The MEDIAN and the <30%/>75% buckets are robust to that; the raw mean is
    # not, and is kept only because dropping a number that was already reported
    # would hide the artifact rather than explain it. `capture_mean_robust`
    # restricts to trades whose MFE cleared `mfe_floor_r` — a REPORTING floor on
    # a statistic, never a gate, and `capture_lowmfe_n` always ships beside it so
    # the excluded population is stated rather than silently dropped.
    robust = [c for c, m in zip((capture_ratio(a, b) for a, b in zip(nets, mfes)), mfes)
              if c is not None and m is not None and m >= mfe_floor_r]
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
        "mfe_floor_r": mfe_floor_r,
        "capture_lowmfe_n": len(caps) - len(robust),
        "capture_mean_robust": (round(sum(robust) / len(robust), 4)
                                if robust else None),
    }

    # ---- the operator's complaint, stated for a leg with NO target ----------
    # "A trade got within cents of its take profit and then turned into a loss"
    # is only expressible as `near_miss` when a target exists, and 43 of the 52
    # live legs are trail-exit legs that have none. The target-free form of the
    # same question is: OF THE TRADES THAT WENT MEANINGFULLY FAVOURABLE, HOW
    # MANY STILL CLOSED RED, AND HOW MUCH R DID THAT COST?
    #
    # This is deliberately a LADDER of MFE rungs rather than one threshold: a
    # book where 60% of the trades that reached +2R still lost is a different
    # animal from one where that only happens at +0.5R, and picking a single
    # rung here would bake a judgement the operator has not made. Same
    # discipline as `capital_efficiency` — report the distribution, let the
    # threshold be set from it.
    #
    # Why this is more trustworthy than the capture mean: it is a COUNT over a
    # stated denominator, so it cannot explode on a small denominator the way a
    # ratio does, and each rung ships `mfe_ge_n` beside `lost_n`.
    pairs = [(nv, mv) for nv, mv in zip(nets, mfes)
             if nv is not None and mv is not None]
    ladder = []
    for rung in GIVEBACK_RUNGS:
        reached = [(nv, mv) for nv, mv in pairs if mv >= rung]
        lost = [(nv, mv) for nv, mv in reached if nv < 0]
        ladder.append({
            "mfe_ge_r": rung,
            "mfe_ge_n": len(reached),
            "lost_n": len(lost),
            "lost_pct": _pct(len(lost), len(reached)),
            "r_left": round(sum(mv - nv for nv, mv in lost), 3) if lost else 0.0,
        })
    out["giveback_ladder"] = ladder

    # Winners-only capture answers "when we DO win, how much of the move do we
    # keep?" — free of the structural drag a breakout book carries from small
    # pokes that fail, which is what makes the all-trades median hard to read.
    wcaps = [c for c, nv in zip((capture_ratio(a, b) for a, b in zip(nets, mfes)), nets)
             if c is not None and nv is not None and nv > 0]
    out["capture_winners_n"] = len(wcaps)
    out["capture_winners_median"] = _quantile(sorted(wcaps), 0.5)

    # R-weighted capture is the PORTFOLIO-level figure: of all the favourable
    # excursion the book was offered, what fraction did it actually bank? A
    # per-trade mean weights a 0.2R scratch the same as a 6R runner; this does
    # not, so it is the one that tracks money. None when no excursion was
    # offered at all — 0.0 would claim the book banked nothing.
    tot_mfe = sum(mv for _, mv in pairs if mv > 0)
    out["capture_r_weighted"] = (
        round(sum(nv for nv, mv in pairs if mv > 0) / tot_mfe, 4)
        if tot_mfe > 0 else None)

    def _no_near_miss(reason: str) -> Dict[str, Any]:
        out.update({"near_miss_measured_n": None, "near_miss_80_pct": None,
                    "near_miss_90_pct": None, "near_miss_95_pct": None,
                    "near_miss_r_left_on_table": None,
                    "near_miss_not_applicable": reason})
        return out

    out["near_miss_not_applicable"] = None
    # None, not 0: with no declared target, "how many reached it" is undefined,
    # and a 0 would read as "nothing ever reached the target" — a claim about
    # the book rather than about the absence of a target.
    out["target_r_reached_n"] = None
    tr = _num(target_r)
    if tr is None or tr <= 0:
        # Trail-exit leg: near-miss is UNDEFINED, not zero. See module docstring.
        return _no_near_miss("no_declared_target")

    # A DECLARED TARGET IS NOT NECESSARILY AN OPERATIVE ONE.
    # Measured 2026-08-10: eight pullback legs declare `tp_r: 50.0` — a 50R
    # take-profit on a trailing strategy, i.e. a DISABLED-TP sentinel, not a
    # target. Treating it as one made the census print `near_miss_90_pct: 0.0`
    # for `eth_pullback_2h` — reading as "0% near-misses, all clear" for the
    # very leg the operator cited as sitting 149 bars at -0.33R. Of course no
    # loser reached 45R.
    #
    # The test is derived from the POPULATION, not from a magic cut-off: if not
    # one trade in the book ever reached the declared target, the target is not
    # operative and every near-miss band computed against it is noise. `None`
    # plus a stated reason, never 0.0 — a fabricated zero here is the
    # reassuring-negative that `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
    # states" and the unasserted-denominator class both exist to stop.
    # The operative test reuses the WIDEST band already reported (80%) rather
    # than inventing a second constant: if not one trade in the book ever
    # entered even the loosest near-miss band, all three bands are structurally
    # empty and reporting 0.0 for them is noise dressed as an all-clear.
    #   sentinel case  max_mfe 3.0 vs target 50  -> 3.0 < 40   -> not operative
    #   real bracket   max_mfe 1.49 vs target 1.5 -> 1.49 >= 1.2 -> operative,
    #                  and 1.49 IS a near-miss, which is the point.
    measured_mfes = [m for m in mfes if m is not None]
    out["target_r_reached_n"] = sum(1 for m in measured_mfes if m >= tr)
    if not measured_mfes or max(measured_mfes) < 0.80 * tr:
        return _no_near_miss("target_never_approached_by_any_trade")

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
                "capture_measured_n": 0, "mfe_r_measured_n": 0,
                "target_r_reached_n": 0, "capture_winners_n": 0,
                "giveback_ladder": []})
    return out
