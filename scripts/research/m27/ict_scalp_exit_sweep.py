#!/usr/bin/env python3
"""M27 — config-exact exit-lever IS/OOS sweep for the ict_scalp gold leg.

The exit-refinement skill's P2 stage for `ict_scalp_mgc_15m`, run against the
powered Dukascopy spot-XAU 15m proxy (the same 178k-bar dataset that carried the
entry decision — MGC's own IBKR 15m history is structurally too thin, see
docs/research/M27-P0-MGC-15m-findings-2026-07-28.md). Uses the NEW exit-lever
flags on scripts/backtest_ict_scalp.py.

CONFIG-EXACT: mirrors the M27 P0 invocation (scripts/research/m27/run_symbol_p0.py)
— `--symbol MGC --timeframe 15m --sim-breakeven` + the harness defaults, loading
the ict_scalp_5m YAML detection params (the mgc leg is a config-exact copy).

GATE (M20 IS/OOS pre-filter): a cell is a CANDIDATE only if it beats the
config-exact baseline on net_R (total_r, higher better) AND maxDD (max_drawdown_r,
lower better) in BOTH the in-sample and out-of-sample windows. Anything else is an
honest_negative at this stage. Candidates would then go to a yearly walk-forward
(the M20 confirmation step) before any Tier-3 live-monitor declare.

R-metrics are the harness's fee-free R (the established M20 lever-gate basis); the
baseline-vs-cell comparison is fee-neutral. Trade-count deltas are reported as a
fee-sensitivity proxy (an earlier exit can free earlier re-entries).

Tier-1 research tooling — never writes config. Usage:
    python3 scripts/research/m27/ict_scalp_exit_sweep.py \
        --data data/XAUUSD_15m_deep.csv --split 2025-07-01 --out <dir>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[3]
_HARNESS = _REPO / "scripts" / "backtest_ict_scalp.py"

# (cell_tag, matrix_lever, extra harness args) — mirrors
# scripts/research/m20_fleet_exit_sweep.py::cells_for for the stale/giveback
# levers (the only M20 levers that apply to a fixed-bracket scalp).
CELLS = [
    ("stale8_lt0R", "stale_stop", ["--stale-exit-bars", "8"]),
    ("stale12_lt0R", "stale_stop", ["--stale-exit-bars", "12"]),
    ("gb1R_afterMFE1R", "giveback_stop",
     ["--giveback-min-mfe-r", "1.0", "--giveback-r", "1.0"]),
    ("gb1R_afterMFE2R", "giveback_stop",
     ["--giveback-min-mfe-r", "2.0", "--giveback-r", "1.0"]),
]

# Config-exact base flags (M27 run_symbol_p0.py invocation, minus the
# regime-attribution flags which do not touch the exit path).
BASE_FLAGS = ["--symbol", "MGC", "--timeframe", "15m", "--sim-breakeven"]


def run_cell(data_csv: Path, extra: list[str], out_json: Path) -> dict:
    # Cache: a valid prior JSON is reused (each run is a pure function of its
    # args), so an interrupted sweep resumes instead of re-walking.
    if out_json.exists():
        try:
            return json.loads(out_json.read_text())
        except Exception:  # noqa: BLE001 — corrupt/partial, re-run
            pass
    cmd = [sys.executable, str(_HARNESS), "--data", str(data_csv),
           *BASE_FLAGS, *extra, "--json", str(out_json)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return {"error": res.stderr.strip()[-500:] or "nonzero exit"}
    return json.loads(out_json.read_text())


def metrics(summary: dict) -> dict:
    return {
        "trades": summary.get("total_trades", 0),
        "total_r": summary.get("total_r", 0.0),
        "max_dd_r": summary.get("max_drawdown_r", 0.0),
        "expectancy_r": summary.get("expectancy_r", 0.0),
        "win_rate_pct": summary.get("win_rate_pct", 0.0),
        "by_outcome": summary.get("by_outcome", {}),
        "error": summary.get("error"),
    }


def beats(cell: dict, base: dict) -> bool:
    """M20 gate: beat baseline on net_R AND maxDD (lower dd better)."""
    if cell.get("error") or base.get("error"):
        return False
    return (cell["total_r"] > base["total_r"]
            and cell["max_dd_r"] < base["max_dd_r"])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(_REPO / "data" / "XAUUSD_15m_deep.csv"))
    ap.add_argument("--split", default="2025-07-01",
                    help="IS/OOS boundary (UTC date; IS < split <= OOS).")
    ap.add_argument("--out", required=True, help="Output dir for slices + JSON.")
    args = ap.parse_args(argv[1:])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.data)
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    split = pd.Timestamp(args.split, tz="UTC")
    is_csv = out / "slice_is.csv"
    oos_csv = out / "slice_oos.csv"
    df[ts < split].to_csv(is_csv, index=False)
    df[ts >= split].to_csv(oos_csv, index=False)
    windows = {"IS": is_csv, "OOS": oos_csv}
    print(f"data: {args.data} ({len(df)} bars) split {args.split} -> "
          f"IS {int((ts < split).sum())} / OOS {int((ts >= split).sum())} bars",
          flush=True)

    # Build every (tag, window) job, then run them concurrently — each harness
    # subprocess is single-threaded, so a pool of 4 keeps the box busy. Every
    # run is byte-identical to the serial version (same args); only wall-clock
    # changes. A finished JSON is reused (cache), so an interrupted run resumes.
    jobs = []  # (result_key, window, extra, out_json)
    for w, csv in windows.items():
        jobs.append((("base", w), csv, [], out / f"base_{w}.json"))
    for tag, lever, extra in CELLS:
        for w, csv in windows.items():
            jobs.append(((tag, w), csv, extra, out / f"{tag}_{w}.json"))

    computed: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_cell, csv, extra, oj): key
                for key, csv, extra, oj in jobs}
        for fut, key in list(futs.items()):
            computed[key] = metrics(fut.result())

    base = {"IS": computed[("base", "IS")], "OOS": computed[("base", "OOS")]}
    for w in ("IS", "OOS"):
        print(f"  BASE {w:3s}: trades={base[w]['trades']} "
              f"total_R={base[w]['total_r']} maxDD={base[w]['max_dd_r']} "
              f"exp_R={base[w]['expectancy_r']}", flush=True)

    results = {"data": args.data, "split": args.split,
               "baseline": base, "cells": {}}
    for tag, lever, extra in CELLS:
        cell = {"IS": computed[(tag, "IS")], "OOS": computed[(tag, "OOS")]}
        is_beat = beats(cell["IS"], base["IS"])
        oos_beat = beats(cell["OOS"], base["OOS"])
        verdict = "CANDIDATE" if (is_beat and oos_beat) else "honest_negative"
        results["cells"][tag] = {"lever": lever, "extra": extra,
                                 "IS": cell["IS"], "OOS": cell["OOS"],
                                 "is_beat": is_beat, "oos_beat": oos_beat,
                                 "verdict": verdict}
        print(f"  {tag:18s} [{lever}]  "
              f"IS ΔR={cell['IS']['total_r'] - base['IS']['total_r']:+.2f} "
              f"ΔDD={cell['IS']['max_dd_r'] - base['IS']['max_dd_r']:+.2f} "
              f"(n{cell['IS']['trades']}) | "
              f"OOS ΔR={cell['OOS']['total_r'] - base['OOS']['total_r']:+.2f} "
              f"ΔDD={cell['OOS']['max_dd_r'] - base['OOS']['max_dd_r']:+.2f} "
              f"(n{cell['OOS']['trades']})  -> {verdict}", flush=True)

    (out / "verdicts.json").write_text(json.dumps(results, indent=2, default=str))
    cands = [t for t, c in results["cells"].items() if c["verdict"] == "CANDIDATE"]
    print(f"\nCANDIDATES (pass IS+OOS, would go to walk-forward): "
          f"{cands or 'NONE — all honest_negative'}", flush=True)
    print(f"verdicts.json -> {out / 'verdicts.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
