#!/usr/bin/env python3
"""MI-165 — the PATH-AWARE `be_floor_r` sweep MI-163 said was the missing run.

WHAT QUESTION THIS ANSWERS
--------------------------
MI-163 (`docs/research/banking-half-2026-09-07.md`) measured that on 36 of 44
enabled+live legs a monotone Chandelier ratchet exists but is calibrated far
beyond where trades actually go: reaching break-even costs
`trail_mult / atr_stop_mult` R of excursion (median **2.00R**) against a median
live peak of **0.84R**, so the stop reached break-even on 14 of 89 (15.7%) and
banked +1R on 4 of 89 (4.5%).

It then produced a benefit table peaking at **+16.87R gross at +0.75R** and
REFUSED to propose a value, because that table is **not a net result**: the live
telemetry carries `peak_r`, `open_r` and `giveback_r` and **no intra-trade
path**, so a stop parked at +X exits on the FIRST retrace to X and forgoes
whatever a trade that later resumed would have paid. That forgone continuation
is the cost term, and it is unmeasurable from that corpus.

This script supplies it. A backtest harness replays the bar path, so when the
break-even floor stops a trade out at entry the harness records THAT exit and
the continuation is truncated — **the cost is measured by construction, not
estimated**. The sweep is therefore net, not gross.

⚠️ **IT PROPOSES; IT ARMS NOTHING.** `be_floor_r` is not declared in any config
and this script writes no config. Arming it is Tier-3.

⚠️ **THE POPULATION IS NOT MI-163's 89 ROWS AND MUST NOT BE QUOTED AS IF IT
WERE.** Those are LIVE telemetry rows and they are exactly what cannot be
replayed. This is a BACKTEST population over each leg's own declared parameters
and its own instrument history. The two denominators answer different questions
and are never pooled: MI-163 measured *what the live fleet did*, this measures
*what the lever would do on history*.

METHOD
------
For each ratchet leg (`monitor_unit_for` resolves it to one of the three units
that run a Chandelier trail), the leg's OWN declared parameters from
`config/strategies.yaml` are handed to the harness that models its ENTRY —
donchian -> `backtest_trend`, pullback -> `backtest_pullback`, squeeze ->
`backtest_squeeze`. Every arm therefore differs from the control in exactly one
lever, which is what makes the delta attributable.

  * arms          `be_floor_r` in {0.0 (control), 0.5, 0.75, 1.0, 1.5}
  * folds         contiguous equal-CALENDAR slices of the leg's history
  * fold panel    {3, 4, 5} — FIXED, never a caller's `--folds`

⚠️ **FOLDS ARE BY CALENDAR, NOT BY TRADE ORDER**, which is a deliberate
departure from `direction_walkforward.analyze` (equal-COUNT by trade order).
That convention folds ONE arm's own trades; here five arms must be compared to
each other, and they produce different trade counts, so equal-count folds would
put different wall-clock windows in "fold 2" for different arms and the delta
would not be a comparison. A common partition is a precondition for a delta.

⚠️ **THE FOLD PANEL IS FIXED BECAUSE A VERDICT THAT FLIPS ON FOLD COUNT IS NOT A
VERDICT** — `BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP`, the same reasoning
`regime_cell_walkforward.FOLD_PANEL` states. Odd and even members, so a result
must hold under both parities.

⚠️ **AN INERT FOLD IS NOT A WIN.** A fold in which the lever never fired has
`d_net_r == 0.0 AND d_max_dd == 0.0` and is counted in its own bucket. The rule
is IMPORTED from `m20_wf_effective.is_inert` rather than restated, so the two
readers cannot drift — that script exists precisely because a raw `N/M` counted
no-ops as wins (`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`).

# wiring: manual-only - a research sweep, not a scheduled job. It needs a
# candle corpus fetched on demand (data/ohlcv/ is deliberately empty in git)
# and it answers a ONE-OFF commissioned question whose verdict is recorded in
# docs/research/be-floor-sweep-2026-09-07.md. Putting it on a timer would
# re-run a settled measurement against a moving corpus and invite a later
# session to quote a fresh number with no memo behind it. Re-run it by hand
# when the question is re-opened.
Usage:
    python3 scripts/research/be_floor_sweep.py --data-dir /tmp/corpus
    python3 scripts/research/be_floor_sweep.py --data-dir DIR --leg trend_donchian
    python3 scripts/research/be_floor_sweep.py --data-dir DIR --json out.json
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402


def _load_by_path(name: str, path: Path):
    """Import a harness by FILE PATH, never by bare module name.

    ⚠️ Load-bearing. This file lives in `scripts/research/`, which Python puts
    at `sys.path[0]`, and `scripts/research/backtest_trend.py` is a RETIRED shim
    whose `__getattr__` raises `RetiredEngineError` — it was the second,
    non-live-faithful trend engine (rolling-ATR trail, opposite-signal flip
    exit, no cooldown), retired 2026-08-09. A bare `import backtest_trend` from
    here therefore resolves to the WRONG engine. The shim's guard catches it
    loudly, which is why this is a comment and not an incident; importing by
    path removes the ambiguity rather than relying on sys.path ordering.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


backtest_trend = _load_by_path("_bt_trend", ROOT / "scripts/backtest_trend.py")
backtest_pullback = _load_by_path("_bt_pullback", ROOT / "scripts/backtest_pullback.py")
backtest_squeeze = _load_by_path("_bt_squeeze", ROOT / "scripts/backtest_squeeze.py")
is_inert = _load_by_path("_m20wf", ROOT / "scripts/research/m20_wf_effective.py").is_inert

# The control arm is FIRST and is `0.0` — the ungated fleet exactly as it runs
# today. Every delta in this script is against it.
ARMS: tuple = (0.0, 0.5, 0.75, 1.0, 1.5)

# Fixed internal fold panel, independent of any caller flag, so a PASS/FAIL
# cannot flip on the fold count (BL-20260730-WF-FOLDCOUNT-VERDICT-FLIP).
FOLD_PANEL: tuple = (3, 4, 5)

# Minimum CONTROL-arm trades for a leg's result to be decision-grade. This is
# the repo's own live-sample floor (`backtest_fidelity_calibrate.MIN_LIVE_N`),
# the same 30 MI-155 graded every leg against. A leg below it is reported as
# `insufficient_n` and excluded from the verdict — never silently pooled.
MIN_TRADES = 30

# Which unit owns the Chandelier trail -> which harness models that unit's
# ENTRY. A leg must be run on the harness for its OWN entries; running a
# pullback leg through the donchian engine would measure a strategy that is not
# on the fleet.
HARNESS = {
    "trend_donchian": backtest_trend,
    "htf_pullback_trend_2h": backtest_pullback,
    "squeeze_breakout_4h": backtest_squeeze,
}

# Config key -> harness kwarg. Only keys a harness actually models are passed;
# `_kwargs_for` filters by the real signature and REPORTS what it dropped, so a
# leg whose live lever the harness does not model is a stated bound rather than
# a silent difference between the live leg and the replayed one.
CONFIG_TO_KWARG = {
    "donchian": "donchian",
    "atr_period": "atr_period",
    "atr_stop_mult": "atr_stop_mult",
    "trail_mult": "trail_mult",
    "tp_r": "tp_r",
    "min_confidence": "min_confidence",
    "long_only": "long_only",
    "side_filter": "side_filter",
    "adx_min": "adx_min",
    "stale_exit_bars": "stale_exit_bars",
    "stale_exit_below_r": "stale_exit_below_r",
    "giveback_r": "giveback_r",
    "giveback_min_mfe_r": "giveback_min_mfe_r",
    "trail_decay_arm_r": "trail_decay_arm_r",
    "trail_decay_stall_bars": "trail_decay_stall_bars",
    "trail_decay_tight_mult": "trail_decay_tight_mult",
    "skip_hours": "skip_hours",
    "vol_skip_above_pctl": "vol_skip_above_pctl",
    "vol_skip_below_pctl": "vol_skip_below_pctl",
    "trend_lookback": "trend_lookback",
    "pullback_lookback": "pullback_lookback",
    "pullback_frac": "pullback_frac",
    "bb_period": "bb_period",
    "bb_std": "bb_std",
    "kc_mult": "kc_mult",
}

# Harness-run parameters that `config/strategies.yaml` does not declare because
# they are not live levers — the live monitor has no bar-count timeout. These
# are each harness's OWN documented CLI defaults, copied from its `main()`
# (`backtest_trend.py:1001-1002`, `backtest_pullback.py:979-980`,
# `backtest_squeeze.py:560-561`), so a replay uses the convention that harness
# already publishes rather than a value chosen here. They are held CONSTANT
# across every arm, so they cannot contribute to any delta.
HARNESS_RUN_DEFAULTS = {
    "_bt_trend": {"timeout_bars": 200, "cooldown_bars": 1},
    "_bt_pullback": {"timeout_bars": 200, "cooldown_bars": 1},
    "_bt_squeeze": {"timeout_bars": 48, "cooldown_bars": 1},
}

# Base bar each symbol's file is stored at. Intraday legs resample UP from 1h;
# daily legs read 1d directly. A leg finer than its file's base bar cannot be
# built and is refused rather than approximated.
_BASE_TF = {"1h": "1h", "2h": "1h", "4h": "1h", "1d": "1d"}
_RESAMPLE = {"1h": None, "2h": "2h", "4h": "4h", "1d": None}


def ratchet_legs() -> Dict[str, Dict[str, Any]]:
    """The enabled+live legs whose monitor runs a Chandelier ratchet.

    Resolved through `pipeline.monitor_unit_for` — the SAME resolver the live
    order-monitor uses — not by pattern-matching the leg name. MI-163 measured
    this population at 36 (19 donchian + 16 pullback + 1 squeeze); the other 8
    are `ict_scalp`, whose one-shot break-even mechanism cannot accrue R at all
    and which this lever therefore does not address.
    """
    from src.runtime.pipeline import monitor_unit_for  # local: heavy import

    cfg = yaml.safe_load((ROOT / "config/strategies.yaml").read_text())["strategies"]
    out = {}
    for name, leg in cfg.items():
        if not isinstance(leg, dict):
            continue
        if leg.get("enabled") is not True or leg.get("execution", "live") != "live":
            continue
        unit = monitor_unit_for(name)
        if unit in HARNESS:
            out[name] = {**leg, "_unit": unit}
    return out


def _kwargs_for(mod, leg: Dict[str, Any], symbol: str, timeframe: str,
                be_floor_r: float, emit_path: str) -> tuple:
    """Build the harness call, and report every declared key it cannot model."""
    sig = set(inspect.signature(mod.run_backtest).parameters)
    kw: Dict[str, Any] = {"timeframe": timeframe, "symbol": symbol,
                          "be_floor_r": be_floor_r, "emit_path": emit_path}
    kw.update(HARNESS_RUN_DEFAULTS.get(mod.__name__, {}))
    dropped: List[str] = []
    for ck, kk in CONFIG_TO_KWARG.items():
        if ck not in leg:
            continue
        if kk in sig:
            kw[kk] = leg[ck]
        else:
            dropped.append(ck)
    # A harness that requires a param the config omits keeps its own default;
    # that default is the harness's documented one, not an invention here.
    missing = [p for p, v in inspect.signature(mod.run_backtest).parameters.items()
               if v.default is inspect._empty and p not in ("df",) and p not in kw]
    for p in missing:
        raise SystemExit(f"harness {mod.__name__} requires '{p}' which the leg does not declare")
    return kw, dropped


def _fold_bounds(t0, t1, k: int) -> List[tuple]:
    """k contiguous equal-CALENDAR slices spanning [t0, t1]."""
    span = (t1 - t0) / k
    return [(t0 + i * span, t0 + (i + 1) * span) for i in range(k)]


def _fold_stats(trades: List[dict], lo, hi) -> Dict[str, float]:
    """Net R and max drawdown over the trades whose ENTRY falls in [lo, hi).

    `net_r` is the harness's own cost-net per-trade figure (fee + slippage +
    funding), read from its emit rather than recomputed, so the cost policy is
    whatever the harness that produced the trade applied. The drawdown recursion
    reproduces `_summarize`'s exactly (peak-to-trough of the cumulative net-R
    curve in trade order).
    """
    sel = [t for t in trades if lo <= t["_t"] < hi]
    cum = peak = mdd = 0.0
    for t in sel:
        cum += t["net_r"]
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {"n": len(sel), "net_r": round(cum, 6), "max_dd": round(mdd, 6)}


def run_leg(name: str, leg: Dict[str, Any], data_dir: Path) -> Dict[str, Any]:
    import pandas as pd

    mod = HARNESS[leg["_unit"]]
    symbol = (leg.get("symbols") or [None])[0]
    tf = leg.get("timeframe")
    rec: Dict[str, Any] = {"leg": name, "unit": leg["_unit"], "symbol": symbol,
                           "timeframe": tf, "harness": mod.__name__}
    if tf not in _BASE_TF:
        rec["state"] = "unsupported_timeframe"
        return rec
    path = data_dir / f"{symbol}_{_BASE_TF[tf]}.csv"
    if not path.exists() or path.stat().st_size == 0:
        # "we could not look", never "the lever did nothing here".
        rec["state"] = "no_data"
        rec["data_path"] = str(path)
        return rec

    df = mod._load_candles(str(path))
    # ⚠️ A DEGENERATE SERIES IS "we could not look", NEVER "insufficient_n".
    # Graded with the repo's own single grader rather than a lookalike, so this
    # reader and `candle_io`/`check_candle_fixture_variance` cannot disagree
    # about what counts as degenerate. Without this branch a venue that served
    # one usable close (measured: SPLG, 2026-09-07) produces zero trades and
    # reports as a real sample floor — the exact collapse CLAUDE.md names.
    from candle_variance import grade_close_variance  # noqa: E402
    grade = grade_close_variance(df["close"])
    # The grader's three states: `non_degenerate` (usable) · `degenerate` (a
    # series that does not move) · `not_gradeable` (fewer than 2 usable closes —
    # "we could not look"). Only the first is usable; the other two are refused
    # here rather than replayed, and they are refused SEPARATELY from
    # `insufficient_n` so a venue that served nothing never reads as a leg that
    # traded too little.
    if grade.state != "non_degenerate":
        rec["state"] = "degenerate_series"
        rec["variance_state"] = grade.state
        rec["usable_closes"] = grade.rows
        rec["data_path"] = str(path)
        return rec
    if _RESAMPLE[tf]:
        df = mod._resample(df, _RESAMPLE[tf])
    rec["bars"] = len(df)
    rec["data_start"], rec["data_end"] = str(df["timestamp"].iloc[0]), str(df["timestamp"].iloc[-1])

    arms: Dict[str, Any] = {}
    trades_by_arm: Dict[float, List[dict]] = {}
    for arm in ARMS:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            emit = fh.name
        try:
            kw, dropped = _kwargs_for(mod, leg, symbol, tf, arm, emit)
            summary = mod.run_backtest(df, **kw)
            rows = []
            for line in Path(emit).read_text().splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                d["_t"] = pd.Timestamp(d["entry_time"])
                rows.append(d)
        finally:
            os.unlink(emit)
        trades_by_arm[arm] = rows
        arms[str(arm)] = {"trades": summary.get("total_trades", 0),
                          "net_total_r": summary.get("net_total_r", 0.0),
                          "max_drawdown_r": summary.get("max_drawdown_r", 0.0),
                          "win_rate_pct": summary.get("win_rate_pct", 0.0)}
        rec["dropped_config_keys"] = dropped

    rec["arms"] = arms
    ctrl_n = arms[str(ARMS[0])]["trades"]
    rec["state"] = "graded" if ctrl_n >= MIN_TRADES else "insufficient_n"
    rec["control_trades"] = ctrl_n

    # Walk-forward over the fixed panel, on the COMMON calendar partition.
    t0 = min(t["_t"] for a in trades_by_arm.values() for t in a) if any(trades_by_arm.values()) else None
    t1 = max(t["_t"] for a in trades_by_arm.values() for t in a) if t0 is not None else None
    wf: Dict[str, Any] = {}
    if t0 is not None:
        import datetime as _dt
        t1 = t1 + _dt.timedelta(seconds=1)   # half-open upper bound includes the last trade
        for k in FOLD_PANEL:
            bounds = _fold_bounds(t0, t1, k)
            per_arm = {}
            for arm in ARMS[1:]:
                folds = []
                for (lo, hi) in bounds:
                    c = _fold_stats(trades_by_arm[ARMS[0]], lo, hi)
                    a = _fold_stats(trades_by_arm[arm], lo, hi)
                    folds.append({"control_n": c["n"], "arm_n": a["n"],
                                  "control_net_r": c["net_r"], "arm_net_r": a["net_r"],
                                  "d_net_r": round(a["net_r"] - c["net_r"], 6),
                                  "d_max_dd": round(a["max_dd"] - c["max_dd"], 6)})
                inert = [f for f in folds if is_inert(f)]
                live = [f for f in folds if not is_inert(f)]
                per_arm[str(arm)] = {
                    "folds": folds,
                    "inert": len(inert),
                    "exercised": len(live),
                    "wins": sum(1 for f in live if f["d_net_r"] > 0),
                    "losses": sum(1 for f in live if f["d_net_r"] < 0),
                }
            wf[str(k)] = per_arm
    rec["walk_forward"] = wf
    return rec


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", required=True, help="Directory of <SYMBOL>_<1h|1d>.csv")
    p.add_argument("--leg", default=None, help="Run one leg only.")
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])

    legs = ratchet_legs()
    if a.leg:
        legs = {k: v for k, v in legs.items() if k == a.leg}
        if not legs:
            print(f"no such ratchet leg: {a.leg}", file=sys.stderr)
            return 2

    results = []
    for name in sorted(legs):
        r = run_leg(name, legs[name], Path(a.data_dir))
        results.append(r)
        st = r.get("state")
        if st in ("no_data", "unsupported_timeframe", "degenerate_series"):
            print(f"{name:26s} {st}")
        else:
            base = r["arms"][str(ARMS[0])]["net_total_r"]
            deltas = " ".join(
                f"{arm}:{r['arms'][str(arm)]['net_total_r'] - base:+8.2f}" for arm in ARMS[1:])
            print(f"{name:26s} n={r['control_trades']:4d} {st:14s} ctrl={base:+9.2f}  d[{deltas}]")

    out = {"arms": list(ARMS), "fold_panel": list(FOLD_PANEL),
           "min_trades": MIN_TRADES, "legs": results}
    out["pooled"] = pooled(results)
    _print_pooled(out["pooled"])
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(out, indent=2, default=str))
    return 0


def pooled(results: List[dict]) -> Dict[str, Any]:
    """Pool the GRADED legs only, and say how many were excluded and why."""
    graded = [r for r in results if r.get("state") == "graded"]
    agg: Dict[str, Any] = {
        "graded_legs": len(graded),
        "excluded": {
            "insufficient_n": sum(1 for r in results if r.get("state") == "insufficient_n"),
            "no_data": sum(1 for r in results if r.get("state") == "no_data"),
            "unsupported_timeframe": sum(1 for r in results
                                         if r.get("state") == "unsupported_timeframe"),
            # "we could not look" — kept apart from insufficient_n, which is a
            # real sample floor on a real series.
            "degenerate_series": sum(1 for r in results
                                     if r.get("state") == "degenerate_series"),
        },
        "control_net_total_r": round(sum(r["arms"][str(ARMS[0])]["net_total_r"] for r in graded), 4),
        "control_trades": sum(r["control_trades"] for r in graded),
        "by_arm": {},
    }
    for arm in ARMS[1:]:
        net = sum(r["arms"][str(arm)]["net_total_r"] for r in graded)
        legs_better = sum(1 for r in graded
                          if r["arms"][str(arm)]["net_total_r"] > r["arms"][str(ARMS[0])]["net_total_r"])
        legs_worse = sum(1 for r in graded
                         if r["arms"][str(arm)]["net_total_r"] < r["arms"][str(ARMS[0])]["net_total_r"])
        legs_inert = len(graded) - legs_better - legs_worse
        panel = {}
        for k in FOLD_PANEL:
            won = sum(r["walk_forward"][str(k)][str(arm)]["wins"] for r in graded)
            lost = sum(r["walk_forward"][str(k)][str(arm)]["losses"] for r in graded)
            never = sum(r["walk_forward"][str(k)][str(arm)]["inert"] for r in graded)
            panel[str(k)] = {"wins": won, "losses": lost, "inert": never,
                             "exercised": won + lost,
                             "majority_win": won > lost}
        agg["by_arm"][str(arm)] = {
            "net_total_r": round(net, 4),
            "d_net_r_vs_control": round(net - agg["control_net_total_r"], 4),
            "legs_better": legs_better, "legs_worse": legs_worse, "legs_inert": legs_inert,
            "panel": panel,
            # The gate: pooled net must IMPROVE, and a strict majority of
            # EXERCISED folds must be wins under EVERY panel member. Inert folds
            # are excluded from the majority, never counted as wins.
            "clears_gate": (net > agg["control_net_total_r"]
                            and all(panel[str(k)]["majority_win"] for k in FOLD_PANEL)),
        }
    return agg


def _print_pooled(a: Dict[str, Any]) -> None:
    print("\n=== POOLED (graded legs only) ===")
    print(f"graded legs: {a['graded_legs']}   excluded: {a['excluded']}")
    print(f"control (be_floor_r=0.0): net {a['control_net_total_r']:+.2f}R "
          f"over {a['control_trades']} trades")
    for arm, v in a["by_arm"].items():
        pan = " ".join(f"k={k}:{p['wins']}W/{p['losses']}L/{p['inert']}inert"
                       for k, p in v["panel"].items())
        print(f"  be_floor_r={arm:<5} net {v['net_total_r']:+9.2f}R  "
              f"d {v['d_net_r_vs_control']:+8.2f}R  "
              f"legs +{v['legs_better']}/-{v['legs_worse']}/={v['legs_inert']}  "
              f"{pan}  GATE={'PASS' if v['clears_gate'] else 'FAIL'}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
