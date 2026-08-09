#!/usr/bin/env python3
"""M20 — re-sweep `trend_donchian`'s TRAIL-DECAY levers on the CONVERGED engine.

WHY THIS EXISTS
---------------
``BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL``. The live
``trend_donchian`` leg declares ``trail_decay_arm_r: 6.49`` and
``trail_decay_tight_mult: 2.5`` TODAY — armed, order-affecting, ``execution:
live``, BTCUSDT 1h. Those values were fitted against
``scripts/research/backtest_trend.py``, whose trail multiplies the **current**
bar's rolling ATR, while ``trend_donchian.monitor()`` trails off the **frozen**
entry-bar ATR (it reads ``meta["atr"]``, stamped at ``order_package`` time).

Trail-decay's entire job is to tighten the trail once peak open profit reaches
``arm_r``. Both the R path that reaches 6.49 and the stop the tightened mult then
produces depend on the baseline trail's distance — the very thing that differed.
So the arm threshold and tight mult were fitted on a trail that behaves unlike
the one running on real money.

**This is a tuning-basis defect, not an incident.** The lever is reductive: it
tightens a stop and never loosens one (``trail_decay`` re-loosens the *mult* on a
new peak, but the caller's price ratchet never loosens the *stop*). The failure
mode is a suboptimal exit, not an unprotected position.

Now that PR #8633 ported all 15 levers into the live-faithful
``scripts/backtest_trend.py``, the sweep can finally be run on the engine the
live monitor matches. This tool is that sweep, committed so the answer is
re-runnable rather than a number in a chat log.

WHAT IT MEASURES, AND THE GATE IT APPLIES
-----------------------------------------
Config-exact cells (the leg's ACTUAL YAML params, not harness defaults) across a
grid of ``(arm_r, tight_mult)``, plus the lever **OFF** arm, over IS / OOS and
per-year walk-forward folds.

Per the `exit-refinement` skill P2/P4: **a lever ships only if it beats baseline
on net_R AND maxDD in BOTH IS and OOS**, with per-fold agreement — never an
in-sample-only optimum. The ROADMAP's own M8 history is the reason: an in-sample
trend optimum there was net-NEGATIVE out-of-sample, so an in-sample answer is
worse than none.

THE FIRST QUESTION IS NOT "WHICH CELL WINS" — IT IS "DOES THE LEVER FIRE AT ALL"
--------------------------------------------------------------------------------
``arm_r: 6.49`` only arms once a trade's peak open profit reaches 6.49R. If no
trade in the sample ever gets there, the live lever is INERT and the whole
optimisation is moot — a different and much more useful finding than a cell
ranking. Two independent reads are reported for that:

* ``max_mfe_r`` per window — the largest peak-R any trade reached. If it is below
  ``arm_r``, the lever provably cannot have fired in that window.
* an exact **identity check** against the OFF arm: if a cell's summary is
  byte-identical to lever-OFF, the lever changed nothing, whatever the theory
  says. This is the inert-conditional shape
  (``BL-20260808-INERT-CONDITIONAL-SHIPPED-AS-A-BEHAVIOUR-CHANGE``) applied to a
  config value instead of a code branch.

Tier-1, read-only: runs the harness, writes only its own ``--json``/stdout.
Proposes nothing; any config change is Tier-3 and goes to the operator.

Usage (trainer)::

    .venv/bin/python scripts/research/m20_trail_resweep.py \\
        --data /home/ubuntu/m27_data/BTCUSDT_15m.csv --resample 1h \\
        --split 2025-07-01 --json /tmp/m20_trail_resweep.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO = os.environ.get("M20_REPO") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS = os.path.join(REPO, "scripts", "backtest_trend.py")

#: config/strategies.yaml::trend_donchian as of 2026-08-09 — the LIVE values.
#: Read, not assumed: donchian 20 / atr_period 14 / atr_stop_mult 2.5 /
#: trail_mult 5.0 / min_confidence 0.7 / long_only true / 1h / BTCUSDT.
LIVE_ARM_R = 6.49
LIVE_TIGHT_MULT = 2.5

_SUMMARY_KEYS = ("total_trades", "net_total_r", "win_rate_pct",
                 "max_drawdown_r", "max_mfe_r", "net_expectancy_r")


def base_args(a) -> List[str]:
    out = ["--data", a.data, "--symbol", a.symbol, "--timeframe", a.timeframe,
           "--donchian", str(a.donchian), "--atr-period", str(a.atr_period),
           "--atr-stop-mult", str(a.atr_stop_mult),
           "--trail-mult", str(a.trail_mult),
           "--min-confidence", str(a.min_confidence)]
    if a.resample:
        out += ["--resample", a.resample]
    if a.long_only:
        out += ["--long-only"]
    return out


def run_cell(a, extra: List[str], start: Optional[str],
             end: Optional[str]) -> Dict[str, Any]:
    tmp = "/tmp/m20_trail_cell.json"
    cmd = [sys.executable, HARNESS] + base_args(a) + list(extra)
    if start:
        cmd += ["--start", start]
    if end:
        cmd += ["--end", end]
    cmd += ["--json", tmp]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
    if p.returncode != 0:
        # Surfaced, never swallowed: an ERR cell must not read as a clean zero.
        return {"error": (p.stderr or p.stdout).strip()[-200:]}
    try:
        with open(tmp, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"json read: {exc}"}


def cell_grid(a) -> List[Tuple[str, List[str]]]:
    cells: List[Tuple[str, List[str]]] = [("OFF", [])]
    for arm in a.arm_grid:
        for tight in a.tight_grid:
            cells.append((f"arm{arm:g}_tight{tight:g}",
                          ["--trail-decay-arm-r", str(arm),
                           "--trail-decay-tight-mult", str(tight)]))
    return cells


def _f(rec: Dict[str, Any], key: str, width: int) -> str:
    """Right-aligned cell. A missing/None value renders as an em-dash, never 0.

    A window with no trades has `None` here, and printing that as `0` would be
    the manufactured-number class: a reader cannot distinguish "no trades" from
    "trades that netted zero".
    """
    v = rec.get(key)
    return f"{'—' if v is None else v:>{width}}"


def comparable(summary: Dict[str, Any]) -> Dict[str, Any]:
    """The fields that decide whether two runs produced the same book."""
    return {k: summary.get(k) for k in _SUMMARY_KEYS}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--resample", default="1h")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="1h")
    # config-exact defaults = the live trend_donchian block
    p.add_argument("--donchian", type=int, default=20)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-stop-mult", type=float, default=2.5)
    p.add_argument("--trail-mult", type=float, default=5.0)
    p.add_argument("--min-confidence", type=float, default=0.7)
    p.add_argument("--long-only", action="store_true", default=True)
    p.add_argument("--both-sides", dest="long_only", action="store_false")
    p.add_argument("--split", default="2025-07-01",
                   help="IS = everything before; OOS = this date onward.")
    p.add_argument("--years", default="2023,2024,2025,2026",
                   help="CSV of per-year walk-forward folds.")
    p.add_argument("--arm-grid", default="3,4,5,6.49,8")
    p.add_argument("--tight-grid", default="2.0,2.5,3.0")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--json", dest="json_out", default=None)
    a = p.parse_args(argv[1:])
    a.arm_grid = [float(x) for x in a.arm_grid.split(",") if x.strip()]
    a.tight_grid = [float(x) for x in a.tight_grid.split(",") if x.strip()]
    years = [y.strip() for y in a.years.split(",") if y.strip()]

    windows: List[Tuple[str, Optional[str], Optional[str]]] = [
        ("full", None, None),
        ("IS", None, a.split),
        ("OOS", a.split, None),
    ] + [(f"y{y}", f"{y}-01-01", f"{y}-12-31") for y in years]

    cells = cell_grid(a)
    print(f"M20 trail re-sweep on the CONVERGED engine ({HARNESS})")
    print(f"  config-exact: donchian={a.donchian} atr_period={a.atr_period} "
          f"atr_stop_mult={a.atr_stop_mult} trail_mult={a.trail_mult} "
          f"min_confidence={a.min_confidence} long_only={a.long_only} "
          f"tf={a.timeframe} symbol={a.symbol}")
    print(f"  live cell under test: arm_r={LIVE_ARM_R} tight_mult={LIVE_TIGHT_MULT}")
    print(f"  {len(cells)} cells x {len(windows)} windows = "
          f"{len(cells) * len(windows)} harness runs\n")

    results: Dict[str, Dict[str, Any]] = {}
    print(f"{'cell':22s} {'window':7s} {'n':>5s} {'net_R':>10s} {'win%':>6s} "
          f"{'maxDD_R':>9s} {'max_mfe_R':>9s}")
    for name, extra in cells:
        for wname, start, end in windows:
            r = run_cell(a, extra, start, end)
            results.setdefault(name, {})[wname] = r
            if "error" in r:
                print(f"{name:22s} {wname:7s}   ERR  {r['error'][:70]}")
                continue
            print(f"{name:22s} {wname:7s} {_f(r, 'total_trades', 5)} "
                  f"{_f(r, 'net_total_r', 10)} {_f(r, 'win_rate_pct', 6)} "
                  f"{_f(r, 'max_drawdown_r', 9)} {_f(r, 'max_mfe_r', 9)}")

    # --- inertness: did the lever change ANYTHING vs OFF? -------------------
    off = results.get("OFF", {})
    print("\n--- INERTNESS vs lever-OFF (identical summary == the lever never fired)")
    inert: Dict[str, List[str]] = {}
    for name in results:
        if name == "OFF":
            continue
        same = [w for w, r in results[name].items()
                if "error" not in r and w in off and "error" not in off[w]
                and comparable(r) == comparable(off[w])]
        inert[name] = same
        flag = "INERT in ALL windows" if len(same) == len(windows) else \
               (f"inert in {len(same)}/{len(windows)}: {','.join(same)}"
                if same else "fires somewhere")
        print(f"  {name:22s} {flag}")

    # --- can the arm threshold even be reached? ----------------------------
    print("\n--- ARM REACHABILITY (max peak-R any trade reached, lever OFF)")
    for wname, _, _ in windows:
        r = off.get(wname) or {}
        mm = r.get("max_mfe_r")
        if "error" in r or mm is None:
            print(f"  {wname:7s} max_mfe_r unavailable")
            continue
        verdict = ("BELOW the live arm — the lever provably cannot fire"
                   if mm < LIVE_ARM_R else "above the live arm")
        print(f"  {wname:7s} max_mfe_r={mm:>8}  (live arm_r={LIVE_ARM_R}) "
              f"-> {verdict}  [n={_f(r, 'total_trades', 1).strip()} trades]")

    payload = {
        "harness": HARNESS,
        "config_exact": {"donchian": a.donchian, "atr_period": a.atr_period,
                         "atr_stop_mult": a.atr_stop_mult,
                         "trail_mult": a.trail_mult,
                         "min_confidence": a.min_confidence,
                         "long_only": a.long_only, "timeframe": a.timeframe,
                         "symbol": a.symbol, "resample": a.resample},
        "live_cell": {"trail_decay_arm_r": LIVE_ARM_R,
                      "trail_decay_tight_mult": LIVE_TIGHT_MULT},
        "windows": [w[0] for w in windows],
        "split": a.split,
        "results": results,
        "inert_windows_by_cell": inert,
    }
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, default=str))
        print(f"\nJSON -> {a.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
