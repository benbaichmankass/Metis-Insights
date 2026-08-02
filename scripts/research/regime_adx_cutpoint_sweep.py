#!/usr/bin/env python3
"""Sweep the ADX regime-attribution cut-points per strategy (RESEARCH-PROGRAM R2).

The whole regime-selectivity apparatus rests on **two global constants** — the
ADX-14 cut-points ``CHOP_MAX_ADX = 20`` / ``TREND_MIN_ADX = 25``
(`src/runtime/regime/detector.py`). Every ``config/regime_policy.yaml`` cell keys
on which of {chop, transitional, trending} a bar lands in, and that bucketing is a
pure function of those two numbers, which have **never been swept**. R2's question:
*is a live OFF-cell's verdict robust to the cut-point choice, or does it flip under
an equally-defensible alternative?* — the same fold-split discipline that caught the
June ADX overfit, applied to the attribution axis itself.

**What this measures — and, per the diagnostic-provenance rule, what it does NOT.**
The ADX cut-points here gate **regime ATTRIBUTION** (which bucket a taken trade is
counted in), NOT trade ENTRY. The strategy's OWN entry filter is its `adx_min`/
`adx_max` (a different config axis the harness already applies). So this sweep
re-buckets the SAME emitted trade set under each candidate ``(chop_max, trend_min)``
pair — it never changes which trades the strategy takes. That is exactly the axis a
``regime_policy.yaml`` cell keys on, so it is the correct sensitivity to report;
stating it here so a reader never mistakes it for an entry-gate sweep.

Engine (one candle fetch + one harness run, then N in-process re-buckets):
  resolve_strategy -> resolve_feed -> _fetch_csv -> build_harness_cmd (--emit-trades)
  -> per (chop_max, trend_min): re-tag each trade by its entry-bar ADX -> filter to
     the target regime -> direction_walkforward.analyze over the FIXED FOLD_PANEL
  -> regime_cell_walkforward.cell_verdict (the SAME fold-count-invariant gate every
     other cell is graded on, so the sweep and the live-cell audit agree).

Sandbox firewalls Yahoo, so equity/ETF/futures strategies only run on a free
GitHub runner (`.github/workflows/regime-adx-cutpoint-sweep.yml`). Read-only:
authors no cell; a surviving/flipping verdict is a Tier-3 draft for the operator.

Usage:
  python scripts/research/regime_adx_cutpoint_sweep.py \
      --strategy gld_pullback_1h --regime trending \
      --chop-grid 15,18,20,22 --trend-grid 25,28,30,32 --days 730 --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import direction_walkforward as dwf  # type: ignore  # noqa: E402
import regime_cell_walkforward as rcwf  # type: ignore  # noqa: E402
import regime_debt_matrix as rdm  # type: ignore  # noqa: E402
from backtest_trend import _load, _resample  # type: ignore  # noqa: E402
from regime_matrix import _adx  # type: ignore  # noqa: E402

import pandas as pd  # noqa: E402

# The live global cut-points every current cell inherits (detector.py). Marked in
# the output so a reader can read the swept grid against the shipped choice.
LIVE_CHOP_MAX = 20.0
LIVE_TREND_MIN = 25.0


def _regime_at(adx_value: float, chop_max: float, trend_min: float) -> str:
    """Bucket one ADX reading under an arbitrary cut-point pair.

    Mirrors ``regime_matrix._regime`` exactly, but with the two thresholds as
    parameters instead of module globals — the whole point of the sweep.
    """
    if adx_value != adx_value:  # NaN
        return "unknown"
    if adx_value < chop_max:
        return "chop"
    if adx_value < trend_min:
        return "transitional"
    return "trending"


def _entry_adx(trades: List[dict], adx: pd.Series, df: pd.DataFrame) -> List[dict]:
    """Attach ``_adx`` (entry-bar ADX) to each trade, dropping unparseable entries.

    The nearest bar at/just-before the trade's entry — the same primitive
    ``regime_tag_emitted.annotate_trades_with_regime`` buckets on, lifted here so
    the ADX lookup is computed ONCE and reused across every cut-point pair.
    """
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    out: List[dict] = []
    for t in trades:
        et = pd.to_datetime(t.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            continue
        idx = ts.searchsorted(et, side="right") - 1
        a = float(adx.iloc[idx]) if 0 <= idx < len(adx) else float("nan")
        row = dict(t)
        row["_adx"] = a
        out.append(row)
    return out


def _read_trades(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _grade_cell(adx_trades: List[dict], regime: str, chop_max: float,
                trend_min: float, workdir: str) -> dict:
    """Re-bucket at (chop_max, trend_min), filter to `regime`, run the fold panel.

    Returns the ``cell_verdict`` shape used everywhere else (short/long stable-drag
    + fold-sensitivity + pooled), so a swept verdict reads identically to a live
    cell audit.
    """
    kept = [t for t in adx_trades
            if _regime_at(t["_adx"], chop_max, trend_min) == regime]
    if not kept:
        return {"regime_trades": 0, "note": "no trades in this regime at these cut-points"}
    # direction_walkforward.analyze reads JSONL files; write the regime-filtered
    # slice once and fold it at every panel fold count.
    fd, tmp = tempfile.mkstemp(suffix=".jsonl", dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for t in kept:
                fh.write(json.dumps({k: v for k, v in t.items() if k != "_adx"}) + "\n")
        panel = {k: dwf.analyze([tmp], k, f"{regime}@{chop_max}/{trend_min}")
                 for k in rcwf.FOLD_PANEL}
    finally:
        os.unlink(tmp)
    verdict = rcwf.cell_verdict(panel, regime)
    verdict["regime_trades"] = len(kept)
    return verdict


def run_sweep(strategy: str, regime: str, chop_grid: List[float],
              trend_grid: List[float], days: int, workdir: str) -> dict:
    out: Dict[str, Any] = {"strategy": strategy, "regime": regime,
                           "live_cutpoints": [LIVE_CHOP_MAX, LIVE_TREND_MIN],
                           "chop_grid": chop_grid, "trend_grid": trend_grid,
                           "days": days}
    cfg = rdm.resolve_strategy(strategy)
    if not cfg:
        out["error"] = f"strategy {strategy!r} not found in config/strategies.yaml"
        return out
    harness = rdm.classify(cfg)
    sym = (cfg.get("symbols") or [None])[0]
    tf = cfg.get("timeframe")
    if harness is None or not sym or not tf:
        out["error"] = "unclassifiable (no donchian/pullback/squeeze params or no symbol/timeframe)"
        return out
    out["symbol"], out["timeframe"], out["harness"] = sym, tf, harness

    os.makedirs(workdir, exist_ok=True)
    feed = rdm.resolve_feed(sym, tf)
    resample = feed["resample"]
    csv = os.path.join(workdir, f"{strategy}__data.csv")
    emit = os.path.join(workdir, f"{strategy}__trades.jsonl")
    jout = os.path.join(workdir, f"{strategy}__bt.json")
    try:
        rdm._fetch_csv(feed, days, csv)
    except Exception as e:  # noqa: BLE001  # allow-silent: surfaced in out["error"], returned to caller — not an empty result
        out["error"] = f"fetch failed: {type(e).__name__}: {e}"
        return out
    argv, faithful, omitted = rdm.build_harness_cmd(strategy, cfg, harness, csv,
                                                    resample, emit, jout)
    out["fidelity"] = "faithful" if faithful else "approximate"
    out["omitted_levers"] = omitted
    try:
        subprocess.run(argv, check=True, cwd=rdm.REPO,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        out["error"] = f"harness failed: {(e.stderr or b'').decode()[-300:]}"
        return out

    trades = _read_trades(emit)
    out["emitted_trades"] = len(trades)
    df = _resample(_load(csv), resample)
    adx = _adx(df, int(cfg.get("adx_period", 14)))
    adx_trades = _entry_adx(trades, adx, df)
    out["labelled_trades"] = len(adx_trades)

    cells: List[dict] = []
    for chop_max in chop_grid:
        for trend_min in trend_grid:
            if chop_max >= trend_min:
                continue  # a valid regime ladder needs chop_max < trend_min
            v = _grade_cell(adx_trades, regime, float(chop_max), float(trend_min), workdir)
            v["chop_max"], v["trend_min"] = float(chop_max), float(trend_min)
            v["is_live_cutpoint"] = (float(chop_max) == LIVE_CHOP_MAX
                                     and float(trend_min) == LIVE_TREND_MIN)
            cells.append(v)
    out["cells"] = cells
    out["sensitivity"] = _summarize(cells, regime)
    return out


def _summarize(cells: List[dict], regime: str) -> dict:
    """Does the OFF-cell verdict FLIP across the grid? — the R2 payoff.

    Reports, over every cut-point pair that produced a gradeable cell, how many
    say short-side / long-side stable-drag. A verdict that holds across the whole
    grid is robust to the un-swept constants; one that flips is cut-point-fragile
    and its live cell rests on the specific 20/25 choice.
    """
    graded = [c for c in cells if "short_stable_drag" in c]
    if not graded:
        return {"regime": regime, "gradeable_cut_points": 0,
                "note": "no cut-point pair produced a gradeable cell"}
    short_drag = sum(1 for c in graded if c.get("short_stable_drag"))
    long_drag = sum(1 for c in graded if c.get("long_stable_drag"))
    live = next((c for c in graded if c.get("is_live_cutpoint")), None)
    return {
        "regime": regime,
        "gradeable_cut_points": len(graded),
        "short_stable_drag_count": short_drag,
        "long_stable_drag_count": long_drag,
        "short_verdict_robust": short_drag in (0, len(graded)),
        "long_verdict_robust": long_drag in (0, len(graded)),
        "live_cutpoint_short_stable_drag": (live or {}).get("short_stable_drag"),
        "live_cutpoint_long_stable_drag": (live or {}).get("long_stable_drag"),
    }


def _parse_grid(s: str) -> List[float]:
    return [float(x) for x in str(s).split(",") if x.strip() != ""]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, help="strategy name (config/strategies.yaml)")
    ap.add_argument("--regime", default="trending",
                    choices=["trending", "transitional", "chop"],
                    help="the regime whose cell verdict to grade at each cut-point")
    ap.add_argument("--chop-grid", default="15,18,20,22",
                    help="CSV of candidate CHOP_MAX_ADX values (live = 20)")
    ap.add_argument("--trend-grid", default="25,28,30,32",
                    help="CSV of candidate TREND_MIN_ADX values (live = 25)")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--workdir", default="/tmp/regime_adx_sweep")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    res = run_sweep(a.strategy, a.regime, _parse_grid(a.chop_grid),
                    _parse_grid(a.trend_grid), a.days, a.workdir)
    if a.json:
        print(json.dumps(res, indent=2, default=str))
        return 0
    if res.get("error"):
        print(f"{a.strategy} [{a.regime}] — ERROR: {res['error']}")
        return 0
    s = res.get("sensitivity", {})
    print(f"strategy={res['strategy']} regime={res['regime']} "
          f"fidelity={res.get('fidelity')} emitted={res.get('emitted_trades')}")
    print(f"gradeable cut-points: {s.get('gradeable_cut_points')} | "
          f"short_stable_drag {s.get('short_stable_drag_count')} | "
          f"long_stable_drag {s.get('long_stable_drag_count')} | "
          f"short_robust={s.get('short_verdict_robust')} long_robust={s.get('long_verdict_robust')}")
    print(f"live (20/25): short_stable_drag={s.get('live_cutpoint_short_stable_drag')} "
          f"long_stable_drag={s.get('live_cutpoint_long_stable_drag')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
