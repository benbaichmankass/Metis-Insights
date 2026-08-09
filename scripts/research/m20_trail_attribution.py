#!/usr/bin/env python3
"""M20 — PER-TRADE attribution for the ``trend_donchian`` trail-decay retune.

WHY THIS EXISTS (it is not a second opinion on the sweep)
---------------------------------------------------------
``scripts/research/m20_trail_resweep.py`` answered *which cell ranks best*:
``arm6.49_tight2.0`` clears a non-worsening-maxDD gate that the LIVE
``arm6.49_tight2.5`` fails. The operator approved that retune **conditional on
testing it first**, and the sweep cannot discharge that condition, because an
aggregate delta says nothing about the sample it rests on.

A ``+7.1999R`` in-sample improvement built from four trades and the same figure
built from sixty are the same number and completely different evidence. The
sweep reports the first quantity and not the second, so this tool exists to
supply the **denominator**: how many trades ARM the lever at all, and of those,
how many actually exit somewhere else when ``tight_mult`` moves 2.5 → 2.0.

This is the ``ALWAYS STATE THE POPULATION`` rule (CLAUDE.md, promoted
2026-07-31) applied to a config proposal rather than to a PnL headline.

THE INERTNESS RESULT MAKES THIS THE DECIDING MEASUREMENT, NOT A FOOTNOTE
------------------------------------------------------------------------
The re-sweep already established that 2026-to-date max peak-R is **4.593**
against an arm of **6.49** over 35 trades — so the live lever provably has not
fired this year. Two consequences follow, and both are load-bearing:

1. **A live soak cannot test this retune.** ``exit_lever_soak`` would accrue
   ZERO rows, and a zero-row soak reads "clean" while having tested nothing.
   That is the unasserted-denominator failure (CLAUDE.md § "Diagnostic
   provenance", sub-class C) in its most seductive form: a green light from a
   measurement that never ran. The test therefore has to be backtest-side.
2. **The whole cell ranking may rest on a handful of trades.** If it does, the
   honest verdict is not "2.0 beats 2.5" but "there is not enough evidence to
   prefer either", which is a different recommendation.

WHAT IT MEASURES
----------------
Three config-exact arms over one tape — lever OFF, ``tight 2.5`` (live), and
``tight 2.0`` (proposed) — collected as full ``Trade`` objects via the harness's
``trades_out`` hook, then aligned and differenced per trade.

Reported, per window (ALL / IS / OOS):

* ``n_trades`` and ``n_armed`` — trades whose ``mfe_r`` reached ``arm_r``. This
  is the denominator; every downstream claim is scoped to it.
* ``n_differing`` — trades that actually exit at a different bar/price/outcome
  between the two arms, with each one's R delta, so the aggregate is traceable
  to the specific trades that produced it.
* ``n_unique_*`` — trades present in one arm and not the other.

WHY ALIGNMENT IS BY ``entry_time`` AND NOT BY INDEX
----------------------------------------------------
Changing an exit changes ``next_idx = exit_index + 1 + cooldown_bars``, so the
two arms can take **structurally different subsequent trades**. Zipping the two
lists positionally would silently compare trade *k* of one book against an
unrelated trade *k* of the other and attribute the difference to the lever. The
arms are therefore joined on ``entry_time``, and any trade without a counterpart
is reported as unique rather than dropped — a dropped row is a denominator that
lies.

Tier-1, read-only: runs the harness in-process and writes only its own
``--json``/stdout. Proposes nothing; the config change is Tier-3.

Usage (trainer)::

    .venv/bin/python scripts/research/m20_trail_attribution.py \\
        --data /home/ubuntu/m27_data/BTCUSDT_15m.csv --resample 1h \\
        --split 2025-07-01 --json /tmp/m20_trail_attribution.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = os.environ.get("M20_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_REL = "scripts/backtest_trend.py"

#: config/strategies.yaml::trend_donchian as of 2026-08-09 — the LIVE values.
LIVE_ARM_R = 6.49
LIVE_TIGHT_MULT = 2.5
PROPOSED_TIGHT_MULT = 2.0


def load_harness():
    """Import the live-faithful harness by path (it is a script, not a package)."""
    path = os.path.join(REPO, HARNESS_REL)
    if not os.path.exists(path):
        raise SystemExit(f"harness not found: {path}")
    spec = importlib.util.spec_from_file_location("_m20_harness", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load harness: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_arm(harness, a, tight_mult: float) -> Tuple[Dict[str, Any], List[Any]]:
    """Run one arm config-exact, returning (summary, trades).

    ``tight_mult <= 0`` is the lever-OFF arm (arm_r is also zeroed, so no code
    path in ``_effective_trail_mult`` can arm).
    """
    trades: List[Any] = []
    # The tape is loaded through the harness's OWN reader/resampler, not a
    # reimplementation, so this tool cannot drift from the engine it is auditing.
    df = harness._load_candles(a.data)
    if a.resample:
        df = harness._resample(df, a.resample)
    kwargs = dict(
        symbol=a.symbol, timeframe=a.timeframe, donchian=a.donchian,
        atr_period=a.atr_period, atr_stop_mult=a.atr_stop_mult,
        trail_mult=a.trail_mult, timeout_bars=a.timeout_bars,
        cooldown_bars=a.cooldown_bars, min_confidence=a.min_confidence,
        long_only=a.long_only, trades_out=trades,
    )
    if tight_mult > 0.0:
        kwargs["trail_decay_arm_r"] = a.arm_r
        kwargs["trail_decay_tight_mult"] = tight_mult
    summary = harness.run_backtest(df, **kwargs)
    return summary, trades


def _key(t) -> str:
    return str(t.entry_time)


def _differs(x, y) -> bool:
    """A trade differs when its EXIT moved — not when a float wobbles.

    Compared on exit bar, outcome and realised R. ``mfe_r`` is deliberately not
    part of the predicate: peak open profit is a property of the price path the
    trade lived through, and both arms see the same path up to the exit.
    """
    return (x.exit_index != y.exit_index
            or x.outcome != y.outcome
            or round(x.r_multiple, 6) != round(y.r_multiple, 6))


def window_of(t, split: Optional[str]) -> str:
    if not split:
        return "ALL"
    return "IS" if str(t.entry_time) < split else "OOS"


def attribute(live: List[Any], proposed: List[Any], arm_r: float,
              split: Optional[str]) -> Dict[str, Any]:
    """Join the two arms on entry_time and difference them per trade."""
    by_live = {_key(t): t for t in live}
    by_prop = {_key(t): t for t in proposed}
    shared = sorted(set(by_live) & set(by_prop))

    windows: Dict[str, Dict[str, Any]] = {}
    for w in ("ALL", "IS", "OOS"):
        windows[w] = {"n_trades_live": 0, "n_trades_proposed": 0,
                      "n_armed_live": 0, "n_armed_proposed": 0,
                      "n_shared": 0, "n_differing": 0,
                      "delta_r_total": 0.0, "differing_trades": []}

    def bump(w: str, field: str, by: float = 1) -> None:
        windows[w][field] += by
        if w != "ALL":
            windows["ALL"][field] += by

    for t in live:
        w = window_of(t, split)
        if w != "ALL":
            bump(w, "n_trades_live")
            if t.mfe_r >= arm_r:
                bump(w, "n_armed_live")
        else:
            windows["ALL"]["n_trades_live"] += 1
            if t.mfe_r >= arm_r:
                windows["ALL"]["n_armed_live"] += 1
    for t in proposed:
        w = window_of(t, split)
        if w != "ALL":
            bump(w, "n_trades_proposed")
            if t.mfe_r >= arm_r:
                bump(w, "n_armed_proposed")
        else:
            windows["ALL"]["n_trades_proposed"] += 1
            if t.mfe_r >= arm_r:
                windows["ALL"]["n_armed_proposed"] += 1

    for k in shared:
        x, y = by_live[k], by_prop[k]
        w = window_of(x, split)
        if w != "ALL":
            bump(w, "n_shared")
        else:
            windows["ALL"]["n_shared"] += 1
        if not _differs(x, y):
            continue
        d = round(y.r_multiple - x.r_multiple, 4)
        if w != "ALL":
            bump(w, "n_differing")
            bump(w, "delta_r_total", d)
        else:
            windows["ALL"]["n_differing"] += 1
            windows["ALL"]["delta_r_total"] += d
        row = {"entry_time": k, "mfe_r": x.mfe_r,
               "live_outcome": x.outcome, "live_r": x.r_multiple,
               "proposed_outcome": y.outcome, "proposed_r": y.r_multiple,
               "delta_r": d, "window": w}
        windows["ALL"]["differing_trades"].append(row)
        if w != "ALL":
            windows[w]["differing_trades"].append(row)

    for w in windows.values():
        w["delta_r_total"] = round(w["delta_r_total"], 4)

    return {
        "windows": windows,
        "unique_to_live": sorted(set(by_live) - set(by_prop)),
        "unique_to_proposed": sorted(set(by_prop) - set(by_live)),
    }


def verdict(res: Dict[str, Any], arm_r: float,
            live_tight: float, proposed_tight: float) -> Dict[str, Any]:
    """State plainly whether the evidence can support a preference at all.

    Three outcomes, kept distinct because collapsing any two of them is how a
    no-evidence result gets read as a green light:

    * ``INERT``     — nothing armed anywhere; the two arms are the same book and
                      the cell ranking is measuring noise.
    * ``TOO_THIN``  — the arms differ, but on fewer than ``--min-differing``
                      trades, so the aggregate is not a sample worth acting on.
    * ``MEASURABLE`` — enough differing trades to judge on.
    """
    all_w = res["windows"]["ALL"]
    armed = max(all_w["n_armed_live"], all_w["n_armed_proposed"])
    diff = all_w["n_differing"]
    if armed == 0:
        state = "INERT"
        why = (f"no trade in the sample reached arm_r={arm_r}; the lever cannot "
               "fire, so tight_mult is unobservable on this tape")
    elif diff == 0:
        state = "INERT"
        # The values are interpolated, never spelled out: a label that names
        # numbers the run did not use is the sub-class-A diagnostic-provenance
        # defect (CLAUDE.md), and it read "2.5 and 2.0" on a 2.5-vs-2.5 control.
        why = (f"{armed} trade(s) armed but NONE exited differently between "
               f"tight {live_tight:g} and {proposed_tight:g} — the two configs "
               "produce the same book")
    else:
        state = "MEASURABLE"
        why = f"{diff} trade(s) exit differently out of {armed} armed"
    return {"state": state, "n_armed": armed, "n_differing": diff, "why": why}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True)
    p.add_argument("--resample", default="1h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--donchian", type=int, default=20)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=5.0)
    p.add_argument("--min-confidence", type=float, default=0.7)
    # Harness CLI defaults (scripts/backtest_trend.py) — kept explicit so a
    # change there is a visible mismatch here rather than a silent divergence.
    p.add_argument("--timeout-bars", type=int, default=200)
    p.add_argument("--cooldown-bars", type=int, default=1)
    p.add_argument("--long-only", action="store_true", default=True)
    p.add_argument("--both-sides", dest="long_only", action="store_false")
    p.add_argument("--arm-r", type=float, default=LIVE_ARM_R)
    p.add_argument("--live-tight", type=float, default=LIVE_TIGHT_MULT)
    p.add_argument("--proposed-tight", type=float, default=PROPOSED_TIGHT_MULT)
    p.add_argument("--split", default="2025-07-01")
    p.add_argument("--min-differing", type=int, default=10,
                   help="below this many differing trades the result is TOO_THIN")
    p.add_argument("--json", dest="json_out", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    harness = load_harness()

    off_s, off_t = run_arm(harness, a, 0.0)
    live_s, live_t = run_arm(harness, a, a.live_tight)
    prop_s, prop_t = run_arm(harness, a, a.proposed_tight)

    res = attribute(live_t, prop_t, a.arm_r, a.split)
    v = verdict(res, a.arm_r, a.live_tight, a.proposed_tight)
    if v["state"] == "MEASURABLE" and v["n_differing"] < a.min_differing:
        v["state"] = "TOO_THIN"
        v["why"] += f" — below the --min-differing={a.min_differing} bar"

    out = {
        "population": {
            "data": a.data, "resample": a.resample, "symbol": a.symbol,
            "split": a.split, "arm_r": a.arm_r,
            "live_tight_mult": a.live_tight, "proposed_tight_mult": a.proposed_tight,
            "config": {"donchian": a.donchian, "atr_period": a.atr_period,
                       "atr_stop_mult": a.atr_stop_mult, "trail_mult": a.trail_mult,
                       "min_confidence": a.min_confidence, "long_only": a.long_only},
        },
        "summaries": {
            "OFF": {k: off_s.get(k) for k in
                    ("total_trades", "net_total_r", "max_drawdown_r", "max_mfe_r")},
            "live_tight": {k: live_s.get(k) for k in
                           ("total_trades", "net_total_r", "max_drawdown_r", "max_mfe_r")},
            "proposed_tight": {k: prop_s.get(k) for k in
                               ("total_trades", "net_total_r", "max_drawdown_r", "max_mfe_r")},
        },
        "attribution": res,
        "verdict": v,
    }

    print(f"POPULATION: {a.symbol} {a.resample} split={a.split} "
          f"arm_r={a.arm_r} tight {a.live_tight} -> {a.proposed_tight}")
    print(f"  OFF            : {out['summaries']['OFF']}")
    print(f"  tight={a.live_tight} (live)    : {out['summaries']['live_tight']}")
    print(f"  tight={a.proposed_tight} (proposed): {out['summaries']['proposed_tight']}")
    for w in ("ALL", "IS", "OOS"):
        d = res["windows"][w]
        print(f"  [{w}] trades live/prop={d['n_trades_live']}/{d['n_trades_proposed']} "
              f"armed={d['n_armed_live']} differing={d['n_differing']} "
              f"delta_R={d['delta_r_total']}")
    print(f"  unique_to_live={len(res['unique_to_live'])} "
          f"unique_to_proposed={len(res['unique_to_proposed'])}")
    print(f"VERDICT: {v['state']} — {v['why']}")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"wrote {a.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
