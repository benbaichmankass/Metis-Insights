#!/usr/bin/env python3
"""M20 exit-lever validation sweep — full-history A/B of the exit levers the
90-day truncation counterfactuals surfaced (stale-stop / trend-flip exit),
run through the SAME standalone harnesses that validated the strategies
(delta-vs-base on one engine, the research_sweep discipline). Tier-1,
trainer-side, read-only.

Each cell prints ONE compact line: in-sample (through --split) and OOS
(after --split) net_R / trades / win / maxDD so the OOS >= in-sample gate is
readable at a glance. Usage:
    python3 scripts/research/m20_exit_sweep.py \
        --btc /tmp/BTC_15m.csv --eth /tmp/ETH_15m.csv --sol /tmp/SOL_15m.csv \
        --split 2025-07-01
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Run from the repo root (the trainer runs a /tmp copy, so __file__ is not a
# reliable anchor); allow an explicit override.
import os  # noqa: E402

REPO = Path(os.environ.get("M20_REPO", Path.cwd()))


def run_cell(harness: str, args: list[str]) -> dict:
    tmp = "/tmp/m20_cell.json"
    cmd = [sys.executable, str(REPO / harness), *args, "--json", tmp]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout)[-300:]}
    try:
        return json.loads(Path(tmp).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": f"json read: {exc}"}


def line(tag: str, res_in: dict, res_oos: dict) -> str:
    def s(res: dict) -> str:
        if "error" in res:
            return "ERR:" + str(res["error"]).strip()[-140:].replace("\n", " | ")
        return (f"n={res.get('total_trades')} net_R={res.get('net_total_r')} "
                f"win={res.get('win_rate_pct')} maxDD={res.get('max_drawdown_r')}")
    return f"{tag:44s} IS[{s(res_in)}]  OOS[{s(res_oos)}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", default=None)
    ap.add_argument("--sol", default=None)
    ap.add_argument("--split", default="2025-07-01")
    a = ap.parse_args()

    def windows() -> list[list[str]]:
        return [["--end", a.split], ["--start", a.split]]

    print("== M20 exit-lever sweep (IS = through %s, OOS = after) ==" % a.split)

    # --- htf_pullback family (2h) ------------------------------------------
    pull_cells = [
        ("base", []),
        ("stale2b(4h)<0R", ["--stale-exit-bars", "2"]),
        ("stale4b(8h)<0R", ["--stale-exit-bars", "4"]),
        ("stale4b(8h)<.25R", ["--stale-exit-bars", "4", "--stale-exit-below-r", "0.25"]),
        ("flip1", ["--flip-exit-bars", "1"]),
        ("flip2", ["--flip-exit-bars", "2"]),
        ("stale4b<0R+flip2", ["--stale-exit-bars", "4", "--flip-exit-bars", "2"]),
    ]
    for sym, data, extra in [("BTCUSDT", a.btc, []),
                             ("ETHUSDT", a.eth, ["--adx-min", "25"])]:
        if not data:
            continue
        print(f"-- htf_pullback 2h {sym} --")
        for tag, cell in pull_cells:
            res = []
            for w in windows():
                res.append(run_cell(
                    "scripts/backtest_pullback.py",
                    ["--data", data, "--resample", "2h", "--symbol", sym,
                     "--timeframe", "2h", *extra, *cell, *w]))
            print(line(f"pullback|{sym}|{tag}", res[0], res[1]))

    # --- trend_donchian family (1h) ----------------------------------------
    don_cells = [
        ("base", []),
        ("stale8b(8h)<0R", ["--stale-exit-bars", "8"]),
        ("stale8b(8h)<.25R", ["--stale-exit-bars", "8", "--stale-exit-below-r", "0.25"]),
        ("stale24b(24h)<.25R", ["--stale-exit-bars", "24", "--stale-exit-below-r", "0.25"]),
        ("timeout168b(7d)", ["--timeout-bars", "168"]),
    ]
    don_syms = [("BTCUSDT", a.btc, ["--long-only", "--min-confidence", "0.6"]),
                ("ETHUSDT", a.eth, ["--min-confidence", "0.6"]),
                ("SOLUSDT", a.sol, ["--min-confidence", "0.6"])]
    for sym, data, extra in don_syms:
        if not data:
            continue
        print(f"-- trend_donchian 1h {sym} --")
        for tag, cell in don_cells:
            res = []
            for w in windows():
                res.append(run_cell(
                    "scripts/research/backtest_trend.py",
                    ["--data", data, "--resample", "1h", "--symbol", sym,
                     "--timeframe", "1h", "--donchian", "20",
                     "--trail-mult", "5.0", *extra, *cell, *w]))
            print(line(f"donchian|{sym}|{tag}", res[0], res[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
