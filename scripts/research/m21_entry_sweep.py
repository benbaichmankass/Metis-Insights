#!/usr/bin/env python3
"""M21 E-2 — fleet ENTRY-filter sweep (donchian family, round 1).

Per runnable donchian leg (config-exact from config/strategies.yaml, same
resolvers + gate as the M20 fleet exit sweep): evaluate the entry-filter
cells the E-1 baseline selected for the family —

  * confirm_1 / confirm_2  — require the close to hold beyond the signal
    bar's channel edge for N further closed bars (--confirm-bars N)
  * depth+0.10 / depth+0.20 — tighter breakout-depth gate (the leg's own
    configured min_confidence + delta; the harness --min-confidence)

Gate (identical to M20): cell beats the config-exact base on net_R AND
maxDD in BOTH IS and OOS windows (--split, default 2025-07-01), then
yearly walk-forward PASS >= 2/3 usable folds (>= 4 usable). One lever per
leg ships unless a combo A/B passes.

Tier-1 research tooling. Run on the trainer, detached:
  nohup .venv/bin/python3 scripts/research/m21_entry_sweep.py \
      --data-dir ~/ict-trading-bot/data \
      --out ~/ict-trading-bot/runtime_logs/m21_entry_sweep >/tmp/e2.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "research"))

from m20_fleet_exit_sweep import (  # noqa: E402
    FAMILY_HARNESS, FOLDS, base_args, beats, classify, resolve_data, run_cell)


def entry_cells(cfg: dict, fam: str) -> list[tuple[str, str, list[str]]]:
    """(cell_tag, matrix_lever, extra_args) — round 1 is donchian-only."""
    if fam != "donchian":
        return []
    cells = [
        ("confirm_1", "confirmation_bars", ["--confirm-bars", "1"]),
        ("confirm_2", "confirmation_bars", ["--confirm-bars", "2"]),
    ]
    base_conf = float(cfg.get("min_confidence") or 0.0)
    for delta in (0.10, 0.20):
        v = round(base_conf + delta, 2)
        cells.append((f"depth_{v:g}", "depth_threshold",
                      ["--min-confidence", str(v)]))
    return cells


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(REPO / "data"))
    ap.add_argument("--split", default="2025-07-01")
    ap.add_argument("--out", default=str(REPO / "runtime_logs" / "m21_entry_sweep"))
    ap.add_argument("--only", default=None,
                    help="CSV of leg names to restrict to (debug)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv[1:])

    strategies = (yaml.safe_load((REPO / "config" / "strategies.yaml")
                                 .read_text()) or {}).get("strategies") or {}
    only = set(a.only.split(",")) if a.only else None
    data_dir = Path(a.data_dir)
    run_dir = Path(a.out) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan, skipped = [], []
    for name, cfg in strategies.items():
        if not isinstance(cfg, dict) or (only and name not in only):
            continue
        fam = classify(name)
        cells = entry_cells(cfg, fam) if fam else []
        if not cells:
            skipped.append({"leg": name, "reason": "no_entry_cells"})
            continue
        sym = (cfg.get("symbols") or [None])[0]
        tf = str(cfg.get("timeframe") or "1h")
        data, proxy, resample = resolve_data(str(sym), tf, data_dir)
        if data is None:
            skipped.append({"leg": name, "reason": f"data_missing:{sym}"})
            continue
        plan.append({"leg": name, "family": fam, "symbol": sym, "tf": tf,
                     "harness": FAMILY_HARNESS[fam], "data": data,
                     "proxy": proxy,
                     "base": base_args(name, cfg, fam, data, resample),
                     "cells": cells})

    print(f"plan: {len(plan)} legs runnable, {len(skipped)} skipped")
    if a.list:
        for p in plan:
            print(f"  RUN {p['leg']:28s} cells={[c[0] for c in p['cells']]}")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    results = (run_dir / "results.jsonl").open("a", encoding="utf-8")

    def log_result(row: dict) -> None:
        results.write(json.dumps(row) + "\n")
        results.flush()

    verdicts: dict = {}
    for p in plan:
        leg = p["leg"]
        print(f"== {leg} ({p['symbol']} {p['tf']}) ==", flush=True)
        base_is = run_cell(p["harness"], p["base"], end=a.split)
        base_oos = run_cell(p["harness"], p["base"], start=a.split)
        log_result({"leg": leg, "cell": "base", "window": "IS", **base_is})
        log_result({"leg": leg, "cell": "base", "window": "OOS", **base_oos})
        if "error" in base_is or "error" in base_oos:
            verdicts[leg] = {"status": "harness_error",
                             "error": base_is.get("error") or base_oos.get("error")}
            continue
        leg_v = {"proxy": p["proxy"], "levers": {}}
        for tag, lever, extra in p["cells"]:
            args = p["base"] + extra
            c_is = run_cell(p["harness"], args, end=a.split)
            c_oos = run_cell(p["harness"], args, start=a.split)
            log_result({"leg": leg, "cell": tag, "window": "IS", **c_is})
            log_result({"leg": leg, "cell": tag, "window": "OOS", **c_oos})
            if "error" in c_is or "error" in c_oos:
                leg_v["levers"].setdefault(lever, []).append(
                    {"cell": tag, "verdict": "error"})
                continue
            candidate = beats(c_is, base_is) and beats(c_oos, base_oos)
            entry = {"cell": tag, "is_oos_pass": candidate,
                     "is": {"base_net_r": base_is.get("net_total_r"),
                            "cell_net_r": c_is.get("net_total_r"),
                            "base_dd": base_is.get("max_drawdown_r"),
                            "cell_dd": c_is.get("max_drawdown_r")},
                     "oos": {"base_net_r": base_oos.get("net_total_r"),
                             "cell_net_r": c_oos.get("net_total_r"),
                             "base_dd": base_oos.get("max_drawdown_r"),
                             "cell_dd": c_oos.get("max_drawdown_r")}}
            if candidate:
                wins = usable = 0
                for fname, fs, fe in FOLDS:
                    fb = run_cell(p["harness"], p["base"], start=fs, end=fe)
                    fc = run_cell(p["harness"], args, start=fs, end=fe)
                    log_result({"leg": leg, "cell": f"{tag}@wf{fname}",
                                "window": "fold", "base": fb, "lever": fc})
                    if "error" in fb or "error" in fc:
                        continue
                    usable += 1
                    try:
                        ok = (float(fc["net_total_r"]) >= float(fb["net_total_r"])
                              and float(fc["max_drawdown_r"]) <= float(fb["max_drawdown_r"]))
                    except (KeyError, TypeError, ValueError):
                        ok = False
                    wins += 1 if ok else 0
                entry["walkforward"] = f"{wins}/{usable}"
                entry["verdict"] = ("PASS" if usable >= 4 and wins * 3 >= usable * 2
                                    else "wf_fail")
            else:
                entry["verdict"] = "is_oos_fail"
            leg_v["levers"].setdefault(lever, []).append(entry)
            print(f"   {tag:14s} -> {entry['verdict']}"
                  f"{' wf=' + entry.get('walkforward', '') if 'walkforward' in entry else ''}",
                  flush=True)
        verdicts[leg] = leg_v

    (run_dir / "verdicts.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "split": a.split, "skipped": skipped, "verdicts": verdicts}, indent=1))
    lines = ["# M21 E-2 fleet entry-filter sweep (donchian round 1)", ""]
    for leg, v in verdicts.items():
        if "levers" not in v:
            lines.append(f"- **{leg}**: {v.get('status')}")
            continue
        passes = [e["cell"] for es in v["levers"].values() for e in es
                  if e.get("verdict") == "PASS"]
        lines.append(f"- **{leg}**{' [PROXY]' if v['proxy'] else ''}: "
                     f"{'PASS ' + ', '.join(passes) if passes else 'no pass'}")
    (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(f"done -> {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
