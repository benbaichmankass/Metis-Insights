#!/usr/bin/env python3
"""Book-level volatility-targeting overlay (deep-review memo P1).

Applies a CONSTANT realized-vol budget to a daily book return series and reports
the before/after improvement. The book is produced by
``scripts/portfolio_combine.py`` (stack several strategy ``--emit-trades`` JSONL
files into one daily series); this overlay scales the book's gross exposure each
period so its realized vol tracks a fixed annualized target.

Overlay logic (memo P1 spec):
  - Realized-vol estimate blends a short + long lookback to damp turnover:
      vol_est_t = w*std(last Sshort daily_r) + (1-w)*std(last Slong daily_r)
    computed on PAST data only — the multiplier for day t is built from returns
    through t-1 (a 1-day shift; NO look-ahead).
  - Target vol is ANNUALIZED (``--target-vol``, e.g. 0.10 = 10%/yr); converted
    to a daily budget via ÷sqrt(252). multiplier = target_daily / vol_est.
  - The multiplier is capped to [--cap-lo, --cap-hi].
  - Re-target cadence (``--retarget {daily,weekly,monthly}``): the multiplier is
    chosen at the START of each period and HELD constant within it (memo: target
    once per period to damp turnover). Daily re-targets every day.
  - Scaled daily return = multiplier_t * book_r_t.
  - Turnover cost: whenever the multiplier CHANGES between periods, charge
    ``--turnover-bps`` on |Δmultiplier| as a one-off drag on that period's first
    day. Cost model is explicit + conservative (see ``_turnover_drag``).

Output: BASELINE vs VOL-TARGETED side-by-side — annualized Sharpe, total return
(sum of daily r), annualized realized vol, max drawdown (R), and the Sharpe
delta; plus multiplier-series stats (mean/min/max, % periods pinned at a cap)
and the total turnover cost charged. ``--json`` + a text summary.

The headline question P1 answers: does the overlay improve Sharpe NET of
turnover, and by how much?

Research only (Tier-1): reads a CSV (or trade JSONL via the shared combine), and
writes JSON/text. Never touches the order path or live config.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the canonical combine logic so book construction is identical to
# portfolio_combine.py (no duplicated parse).
from scripts.portfolio_combine import (  # noqa: E402
    TRADING_DAYS_PER_YEAR,
    build_book,
    sharpe_annualized,
)


def _parse_date(s: str) -> date:
    return datetime.fromisoformat(str(s).strip().split(" ")[0]).date()


def load_daily_csv(path: str) -> List[Tuple[date, float, int]]:
    """Read a date,book_r[,n_trades] CSV into [(day, book_r, n_trades), ...].

    Robust: skips a header row and any malformed line; sorted by date.
    """
    rows: List[Tuple[date, float, int]] = []
    src = sys.stdin if path == "-" else open(path, "r", encoding="utf-8")
    close = path != "-"
    try:
        reader = csv.reader(src)
        for cells in reader:
            if not cells:
                continue
            head = cells[0].strip().lower()
            if head in ("date", ""):  # header / blank
                continue
            try:
                d = _parse_date(cells[0])
                r = float(cells[1])
            except (ValueError, IndexError):
                continue
            if not math.isfinite(r):
                continue
            n = 0
            if len(cells) >= 3:
                try:
                    n = int(float(cells[2]))
                except (ValueError, IndexError):
                    n = 0
            rows.append((d, r, n))
    finally:
        if close:
            src.close()
    rows.sort(key=lambda x: x[0])
    return rows


def _period_key(d: date, retarget: str) -> Any:
    """Identity of the re-target period a given day belongs to."""
    if retarget == "daily":
        return d.toordinal()
    if retarget == "weekly":
        iso = d.isocalendar()
        return (iso[0], iso[1])  # (ISO year, ISO week)
    if retarget == "monthly":
        return (d.year, d.month)
    raise ValueError(f"unknown retarget cadence: {retarget!r}")


def _vol_est(history: Sequence[float], short: int, long: int, blend: float
             ) -> Optional[float]:
    """Blended short/long realized daily-vol estimate over PAST returns only.

    ``history`` must already exclude the current day (the caller shifts). Returns
    None until there are >= 2 points in the shorter usable window.
    """
    if len(history) < 2:
        return None
    s_win = history[-short:] if short > 0 else history
    l_win = history[-long:] if long > 0 else history
    if len(s_win) < 2 or len(l_win) < 2:
        return None
    s_std = statistics.pstdev(s_win)
    l_std = statistics.pstdev(l_win)
    return blend * s_std + (1.0 - blend) * l_std


def compute_multipliers(daily: Sequence[Tuple[date, float, int]], *,
                        target_vol: float, short: int, long: int, blend: float,
                        cap_lo: float, cap_hi: float, retarget: str
                        ) -> Tuple[List[float], List[bool]]:
    """Per-day exposure multiplier with NO look-ahead.

    The multiplier for day t is computed from the vol estimate over returns
    through t-1 (strict 1-day shift). On a re-target boundary a fresh multiplier
    is computed and then HELD for the rest of that period. Before the vol
    estimate is defined (warm-up), the multiplier is 1.0 (un-scaled).

    Returns ``(multipliers, at_cap_flags)`` aligned to ``daily``; ``at_cap`` is
    True for the FIRST day of any period whose chosen multiplier hit a cap.
    """
    returns = [r for (_d, r, _n) in daily]
    n = len(daily)
    mult = [1.0] * n
    at_cap = [False] * n
    target_daily = target_vol / math.sqrt(TRADING_DAYS_PER_YEAR)

    current_mult = 1.0
    current_at_cap = False
    prev_period: Any = None
    for t in range(n):
        d = daily[t][0]
        period = _period_key(d, retarget)
        if period != prev_period:
            # Re-target at the start of a new period using PAST returns only:
            # everything strictly before today (returns[:t]) — the 1-day shift.
            history = returns[:t]
            ve = _vol_est(history, short, long, blend)
            if ve is None or ve <= 0:
                # Warm-up / degenerate: no scaling yet.
                current_mult, current_at_cap = 1.0, False
            else:
                raw = target_daily / ve
                capped = min(max(raw, cap_lo), cap_hi)
                current_mult = capped
                current_at_cap = capped in (cap_lo, cap_hi) and raw != capped
            prev_period = period
            at_cap[t] = current_at_cap
        mult[t] = current_mult
    return mult, at_cap


def _turnover_drag(mult: Sequence[float], turnover_bps: float
                   ) -> Tuple[List[float], float]:
    """Per-day turnover-cost drag and the total charged.

    Conservative, explicit cost model: each time the held multiplier CHANGES
    from one period to the next, the book must re-balance its gross exposure by
    |Δmultiplier| units of risk. We charge ``turnover_bps`` (basis points of one
    unit of risk) on that |Δmultiplier|, expressed in R:
        drag_R = |Δmultiplier| * (turnover_bps / 10_000)
    booked as a one-off subtraction on the first day the new multiplier applies.
    The very first multiplier (from 1.0 at the start) is NOT charged — that is
    the initial position, not a re-balance. Returns ``(per_day_drag, total)``.
    """
    n = len(mult)
    drag = [0.0] * n
    total = 0.0
    cost_per_unit = turnover_bps / 10_000.0
    prev = None
    for t in range(n):
        if prev is None:
            prev = mult[t]
            continue
        if mult[t] != prev:
            d = abs(mult[t] - prev) * cost_per_unit
            drag[t] = d
            total += d
            prev = mult[t]
    return drag, round(total, 6)


def _annualized_vol(daily_r: Sequence[float]) -> Optional[float]:
    if len(daily_r) < 2:
        return None
    return round(statistics.pstdev(daily_r) * math.sqrt(TRADING_DAYS_PER_YEAR), 6)


def _max_drawdown_r(daily_r: Sequence[float]) -> float:
    cum = peak = mdd = 0.0
    for r in daily_r:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return round(mdd, 6)


def _stats_block(daily_r: Sequence[float]) -> Dict[str, Any]:
    return {
        "n_days": len(daily_r),
        "total_return_r": round(sum(daily_r), 6),
        "sharpe_annualized": sharpe_annualized(daily_r),
        "annualized_vol": _annualized_vol(daily_r),
        "max_drawdown_r": _max_drawdown_r(daily_r),
    }


def run_vol_target(daily: Sequence[Tuple[date, float, int]], *,
                   target_vol: float, short: int, long: int, blend: float,
                   cap_lo: float, cap_hi: float, retarget: str,
                   turnover_bps: float) -> Dict[str, Any]:
    """Baseline vs vol-targeted comparison over the daily book series."""
    baseline_r = [r for (_d, r, _n) in daily]
    mult, at_cap = compute_multipliers(
        daily, target_vol=target_vol, short=short, long=long, blend=blend,
        cap_lo=cap_lo, cap_hi=cap_hi, retarget=retarget)
    drag, total_turnover = _turnover_drag(mult, turnover_bps)
    scaled_r = [mult[t] * baseline_r[t] - drag[t] for t in range(len(daily))]

    baseline = _stats_block(baseline_r)
    targeted = _stats_block(scaled_r)
    sharpe_delta = None
    if baseline["sharpe_annualized"] is not None and targeted["sharpe_annualized"] is not None:
        sharpe_delta = round(targeted["sharpe_annualized"] - baseline["sharpe_annualized"], 4)

    # Multiplier stats over the days where scaling was actually active (vol est
    # defined). A pure warm-up 1.0 isn't informative, but include all for honesty.
    n_periods = len(set(_period_key(d, retarget) for (d, _r, _n) in daily))
    n_at_cap_periods = sum(1 for f in at_cap if f)
    return {
        "params": {
            "target_vol_annualized": target_vol,
            "short_lookback": short, "long_lookback": long, "blend": blend,
            "cap_lo": cap_lo, "cap_hi": cap_hi, "retarget": retarget,
            "turnover_bps": turnover_bps,
        },
        "data_start": str(daily[0][0]) if daily else None,
        "data_end": str(daily[-1][0]) if daily else None,
        "baseline": baseline,
        "vol_targeted": targeted,
        "sharpe_delta": sharpe_delta,
        "multiplier": {
            "mean": round(statistics.fmean(mult), 4) if mult else None,
            "min": round(min(mult), 4) if mult else None,
            "max": round(max(mult), 4) if mult else None,
            "n_periods": n_periods,
            "n_periods_at_cap": n_at_cap_periods,
            "pct_periods_at_cap": round(100.0 * n_at_cap_periods / n_periods, 2) if n_periods else 0.0,
        },
        "total_turnover_cost_r": total_turnover,
        "run_date": str(date.today()),
    }


def _fmt(out: Dict[str, Any]) -> str:
    b, v = out["baseline"], out["vol_targeted"]
    p = out["params"]
    m = out["multiplier"]
    lines = [
        f"backtest_vol_target — {out.get('data_start')} -> {out.get('data_end')}  "
        f"target_vol={p['target_vol_annualized']} retarget={p['retarget']} "
        f"caps=[{p['cap_lo']},{p['cap_hi']}] turnover_bps={p['turnover_bps']}",
        f"  blend={p['blend']} short={p['short_lookback']} long={p['long_lookback']}",
        f"  {'metric':<22}{'BASELINE':>14}{'VOL-TARGETED':>16}",
        f"  {'days':<22}{b['n_days']:>14}{v['n_days']:>16}",
        f"  {'total_return_r':<22}{b['total_return_r']:>14}{v['total_return_r']:>16}",
        f"  {'sharpe_annualized':<22}{str(b['sharpe_annualized']):>14}{str(v['sharpe_annualized']):>16}",
        f"  {'annualized_vol':<22}{str(b['annualized_vol']):>14}{str(v['annualized_vol']):>16}",
        f"  {'max_drawdown_r':<22}{b['max_drawdown_r']:>14}{v['max_drawdown_r']:>16}",
        f"  -> Sharpe delta (targeted - baseline) = {out['sharpe_delta']}",
        f"  multiplier: mean={m['mean']} min={m['min']} max={m['max']}  "
        f"periods={m['n_periods']} at_cap={m['n_periods_at_cap']} ({m['pct_periods_at_cap']}%)",
        f"  total turnover cost charged = {out['total_turnover_cost_r']} R",
    ]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description="Book-level volatility-targeting overlay (memo P1): does a "
                    "constant-vol budget improve Sharpe net of turnover?")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--daily", metavar="CSV",
                     help="Daily book CSV (date,book_r[,n_trades]) from "
                          "portfolio_combine.py --emit-daily. '-' reads stdin.")
    src.add_argument("--trades", nargs="+", metavar="PATH",
                     help="emit-trades JSONL files (combined internally via the "
                          "shared portfolio_combine logic).")
    p.add_argument("--basis", choices=["entry", "exit", "auto"], default="auto",
                   help="Day-attribution basis when --trades is used. Default auto.")
    p.add_argument("--target-vol", type=float, default=0.10,
                   help="Annualized vol budget (0.10 = 10%%/yr). Default 0.10.")
    p.add_argument("--short", type=int, default=20, help="Short vol lookback (days).")
    p.add_argument("--long", type=int, default=60, help="Long vol lookback (days).")
    p.add_argument("--blend", type=float, default=0.5,
                   help="Weight on the short lookback (0..1). Default 0.5.")
    p.add_argument("--cap-lo", type=float, default=0.5, help="Multiplier floor.")
    p.add_argument("--cap-hi", type=float, default=1.5, help="Multiplier cap.")
    p.add_argument("--retarget", choices=["daily", "weekly", "monthly"],
                   default="weekly",
                   help="Re-target cadence; multiplier held constant within a "
                        "period to damp turnover. Default weekly.")
    p.add_argument("--turnover-bps", type=float, default=2.0,
                   help="Cost in bps charged on |Δmultiplier| each re-balance "
                        "(drag_R = |Δmult| * bps/1e4). Conservative default 2.0.")
    p.add_argument("--json", dest="json_out", default=None,
                   help="Write the JSON payload here ('-' for stdout).")
    a = p.parse_args(argv[1:])

    try:
        if a.daily is not None:
            daily = load_daily_csv(a.daily)
        else:
            daily, _summary = build_book(a.trades, basis=a.basis)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: load failed: {exc}", file=sys.stderr)
        return 1

    if not daily:
        print("ERROR: no daily rows loaded (empty book).", file=sys.stderr)
        return 1

    out = run_vol_target(
        daily, target_vol=a.target_vol, short=a.short, long=a.long,
        blend=a.blend, cap_lo=a.cap_lo, cap_hi=a.cap_hi, retarget=a.retarget,
        turnover_bps=a.turnover_bps)
    print(_fmt(out))

    if a.json_out:
        payload = json.dumps(out, indent=2, default=str)
        if a.json_out == "-":
            print(payload)
        else:
            Path(a.json_out).write_text(payload)
            print(f"JSON -> {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
