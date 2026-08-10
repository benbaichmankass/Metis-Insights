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

# M20 `exit_ladder` (partial-TP bank) cells — added 2026-08-09, once
# backtest_ict_scalp.py gained bank_frac/bank_at_r. The eight live ict_scalp
# legs read `blocked:no_harness_levers` for this column until then.
#
# The rungs are CONFIG-RELATIVE, as FRACTIONS of the leg's own tp_at_r, and
# this is load-bearing rather than tidiness: ict_scalp is a FIXED-bracket
# strategy, so a rung at or above tp_at_r coincides with the take-profit and
# the blend `f*tp + (1-f)*tp` returns tp exactly — a provable no-op. The
# fleet-wide default grid (m20_fleet_exit_sweep / m20_exit_sweep) uses
# bank_at_r in (1.0, 1.5); on a tp_at_r=1.5 leg HALF those cells would measure
# nothing and still report a confident "no effect". Fractions cannot drift
# into that. Proven in tests/test_ict_scalp_exit_levers.py::
# test_rung_at_or_above_tp_is_a_provable_noop.
_RUNG_FRACS_OF_TP = (1.0 / 3.0, 2.0 / 3.0)
_BANK_FRACS = (0.25, 0.5)


def ladder_cells(tp_at_r: float) -> list:
    """(tag, matrix_lever, extra_args) for the partial-TP ladder grid."""
    out = []
    for frac in _BANK_FRACS:
        for rf in _RUNG_FRACS_OF_TP:
            rung = round(tp_at_r * rf, 3)
            if rung <= 0 or rung >= tp_at_r:      # never emit a no-op cell
                continue
            out.append((f"bank{frac:g}@{rung:g}R", "exit_ladder",
                        ["--bank-frac", str(frac), "--bank-at-r", str(rung)]))
    return out


def declared_lever_flags(leg_cfg: dict) -> list:
    """The leg's OWN shipped exit levers — part of its config-exact BASE.

    A new lever cell is measured ON TOP of what the leg already ships (the
    fleet sweep's `declared_levers` rule). Verified 2026-08-09 against
    config/strategies.yaml: of the eight ict_scalp legs only
    `ict_scalp_eth_15m` declares any (stale_exit_bars 12, stale_exit_below_r
    0.0), so omitting this would silently sweep that ONE leg against a
    baseline it does not actually run live.
    """
    flags = []
    for flag, key in (("--stale-exit-bars", "stale_exit_bars"),
                      ("--stale-exit-below-r", "stale_exit_below_r"),
                      ("--giveback-min-mfe-r", "giveback_min_mfe_r"),
                      ("--giveback-r", "giveback_r")):
        v = leg_cfg.get(key)
        if v is not None:
            flags.extend([flag, str(v)])
    return flags

# Config-exact base flags (M27 run_symbol_p0.py invocation, minus the
# regime-attribution flags which do not touch the exit path). SYMBOL/TIMEFRAME
# are filled per-leg by main(); every ict_scalp leg is a config-exact copy so the
# harness self-loads the shared ict_scalp_5m detection params for all of them.
def base_flags(symbol: str, timeframe: str, declared: list | None = None) -> list:
    return (["--symbol", symbol, "--timeframe", timeframe, "--sim-breakeven"]
            + list(declared or []))


# module-level, set in main() so run_cell stays a pure (data, extra)->metrics call
BASE_FLAGS: list[str] = ["--symbol", "MGC", "--timeframe", "15m", "--sim-breakeven"]


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
    """M20 IS/OOS pre-filter: beat baseline on net_R AND maxDD (lower dd better)."""
    if cell.get("error") or base.get("error"):
        return False
    return (cell["total_r"] > base["total_r"]
            and cell["max_dd_r"] < base["max_dd_r"])


def beats_or_ties(cell: dict, base: dict) -> bool:
    """Walk-forward per-fold rule: beat-or-tie baseline on net_R AND maxDD."""
    if cell.get("error") or base.get("error"):
        return False
    return (cell["total_r"] >= base["total_r"]
            and cell["max_dd_r"] <= base["max_dd_r"])


# Yearly walk-forward folds (mirror scripts/research/m20_fleet_exit_sweep.py).
FOLDS = [("2021", "2021-01-01", "2022-01-01"), ("2022", "2022-01-01", "2023-01-01"),
         ("2023", "2023-01-01", "2024-01-01"), ("2024", "2024-01-01", "2025-01-01"),
         ("2025", "2025-01-01", "2026-01-01"), ("2026", "2026-01-01", None)]
_WF_MIN_TRADES = 10  # a fold with fewer baseline trades is not usable


def walk_forward(df, ts, out: Path, cell_tags: dict) -> dict:
    """M20 confirmation gate for the IS/OOS candidate cells: run baseline vs each
    candidate cell on every yearly fold; a cell PASSES if it beats-or-ties the
    baseline on net_R AND maxDD in >= ceil(2/3) of the USABLE folds (usable =
    baseline has >= _WF_MIN_TRADES trades). Returns {tag: {folds, pass, usable,
    verdict}}."""
    import math
    wf: dict = {}
    # slice + run baseline once per fold
    fold_base = {}
    fold_csv = {}
    for name, start, end in FOLDS:
        mask = (ts >= pd.Timestamp(start, tz="UTC"))
        if end is not None:
            mask &= (ts < pd.Timestamp(end, tz="UTC"))
        csv = out / f"wf_{name}.csv"
        df[mask].to_csv(csv, index=False)
        fold_csv[name] = csv
        fold_base[name] = metrics(run_cell(csv, [], out / f"wf_base_{name}.json"))
    for tag, extra in cell_tags.items():
        rows = []
        pass_n = usable = 0
        for name, _s, _e in FOLDS:
            base_m = fold_base[name]
            if base_m.get("error") or base_m["trades"] < _WF_MIN_TRADES:
                rows.append({"fold": name, "usable": False})
                continue
            usable += 1
            cell_m = metrics(run_cell(fold_csv[name], extra, out / f"wf_{tag}_{name}.json"))
            ok = beats_or_ties(cell_m, base_m)
            pass_n += 1 if ok else 0
            rows.append({"fold": name, "usable": True, "pass": ok,
                         "d_netR": round(cell_m["total_r"] - base_m["total_r"], 2),
                         "d_maxDD": round(cell_m["max_dd_r"] - base_m["max_dd_r"], 2)})
        need = math.ceil(2 * usable / 3) if usable else 99
        verdict = ("PASS" if (usable >= 3 and pass_n >= need)
                   else "honest_negative")
        wf[tag] = {"pass_folds": pass_n, "usable_folds": usable,
                   "need": need, "verdict": verdict, "folds": rows}
    return wf


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(_REPO / "data" / "XAUUSD_15m_deep.csv"))
    ap.add_argument("--symbol", default="MGC",
                    help="Bot symbol for the leg (default MGC — the gold leg).")
    ap.add_argument("--timeframe", default="15m",
                    help="Leg timeframe label (default 15m).")
    ap.add_argument("--split", default="2025-07-01",
                    help="IS/OOS boundary (UTC date; IS < split <= OOS).")
    ap.add_argument("--leg", default=None,
                    help="Strategy leg name in config/strategies.yaml (e.g. "
                         "ict_scalp_eth_15m). Resolves symbol, timeframe, "
                         "tp_at_r and the leg's DECLARED exit levers, so the "
                         "base is config-exact. Overrides --symbol/--timeframe.")
    ap.add_argument("--cells", default=None,
                    help="CSV of matrix levers to restrict cells to (e.g. "
                         "exit_ladder) — skips already-verdicted columns.")
    ap.add_argument("--walkforward", action="store_true",
                    help="After IS/OOS, run the yearly walk-forward confirmation "
                         "(M20 gate) on any cell that passed the IS/OOS pre-filter.")
    ap.add_argument("--out", required=True, help="Output dir for slices + JSON.")
    args = ap.parse_args(argv[1:])

    symbol, timeframe, declared, tp_at_r, leg_name = (
        args.symbol, args.timeframe, [], 1.5, None)
    if args.leg:
        import yaml
        legs = (yaml.safe_load((_REPO / "config" / "strategies.yaml").read_text())
                or {}).get("strategies") or {}
        cfg = legs.get(args.leg)
        if not isinstance(cfg, dict):
            print(f"ERROR: leg {args.leg!r} not in config/strategies.yaml",
                  file=sys.stderr)
            return 2
        leg_name = args.leg
        symbol = (cfg.get("symbols") or [args.symbol])[0]
        timeframe = str(cfg.get("timeframe") or args.timeframe)
        declared = declared_lever_flags(cfg)
        # tp_at_r drives the ladder rungs; the harness default is the fallback.
        tp_at_r = float(cfg.get("tp_at_r") or 1.5)

    global BASE_FLAGS
    BASE_FLAGS = base_flags(symbol, timeframe, declared)

    # Cells = the stale/giveback grid + the config-relative ladder grid,
    # optionally filtered to one matrix lever.
    cells = list(CELLS) + ladder_cells(tp_at_r)
    if args.cells:
        want = {c.strip() for c in args.cells.split(",") if c.strip()}
        cells = [c for c in cells if c[1] in want]
    if not cells:
        print(f"ERROR: no cells selected (--cells {args.cells!r}); "
              f"available levers: "
              f"{sorted({c[1] for c in list(CELLS) + ladder_cells(tp_at_r)})}",
              file=sys.stderr)
        return 2
    print(f"leg={leg_name or '(explicit)'} symbol={symbol} tf={timeframe} "
          f"tp_at_r={tp_at_r} declared_base={declared or 'none'}", flush=True)
    print(f"cells ({len(cells)}): {[c[0] for c in cells]}", flush=True)

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
    for tag, lever, extra in cells:
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

    results = {"data": args.data, "split": args.split, "leg": leg_name,
               "symbol": symbol, "timeframe": timeframe, "tp_at_r": tp_at_r,
               "declared_base": declared, "baseline": base, "cells": {}}
    for tag, lever, extra in cells:
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

    cands = [t for t, c in results["cells"].items() if c["verdict"] == "CANDIDATE"]
    print(f"\nCANDIDATES (pass IS+OOS pre-filter): "
          f"{cands or 'NONE — all honest_negative'}", flush=True)

    # Walk-forward confirmation (M20 gate) for the IS/OOS candidates.
    if args.walkforward and cands:
        print("\n=== yearly walk-forward (M20 gate: beat-or-tie net_R AND maxDD "
              ">= ceil(2/3) usable folds) ===", flush=True)
        cell_tags = {t: dict(results["cells"][t])["extra"] for t in cands}
        wf = walk_forward(df, ts, out, cell_tags)
        results["walkforward"] = wf
        for tag, r in wf.items():
            results["cells"][tag]["walkforward_verdict"] = r["verdict"]
            fold_str = " ".join(
                f"{f['fold']}:{'PASS' if f.get('pass') else ('-' if f.get('usable') else 'skip')}"
                for f in r["folds"])
            print(f"  {tag:18s} {r['pass_folds']}/{r['usable_folds']} usable folds "
                  f"(need {r['need']}) -> {r['verdict']}   [{fold_str}]", flush=True)
        survivors = [t for t, r in wf.items() if r["verdict"] == "PASS"]
        print(f"\nWALK-FORWARD SURVIVORS (M20-gated, -> Tier-3 proposal): "
              f"{survivors or 'NONE — candidates fail walk-forward, honest_negative'}",
              flush=True)

    (out / "verdicts.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"verdicts.json -> {out / 'verdicts.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
