#!/usr/bin/env python3
"""P1 execution-realism A/B: does adding funding+slippage move a leg's backtest↔live
agreement toward the gate? (FAITHFUL-BACKTEST-PLATFORM-DESIGN § 3.B + § 5a).

For ONE (strategy, symbol) it fetches the config-exact feed once, runs the harness
with the execution-realism cost flags ON, then derives BOTH arms from the SINGLE
cost-on emit (which carries the per-component cost breakdown):

  fee_only_r = gross_r − cost_fee_r            (the current, fees-only harness)
  cost_on_r  = net_r = gross_r − fee − slip − funding   (execution-realism)

and calibrates EACH against the live journal (measured-provenance only, the scarce
TRUSTED set), reporting the KS/win-rate for both + the delta. Because fees/slippage/
funding never feed back into signal generation, the TRADE SET is identical across
arms — the delta is a clean cost attribution, isolated from any window slide.

Also emits a direction-stratified cost-on agreement so a reader can separate a
UNIFORM cost-model gap from a CONCENTRATED regime/small-sample bias (the § 5a caveat).

This is the measurement instrument for P1 — reproducible so the operator can re-run
it per leg / per cost setting. It composes existing, tested tools (regime_debt_matrix
.emit_trades_for + build_harness_cmd; backtest_fidelity_calibrate.agreement); the only
new logic is deriving the two R-samples from the emit.

Usage (on the trainer / a runner with the feed reachable):
    python scripts/research/backtest_fidelity_cost_ab.py \
      --strategy htf_pullback_trend_2h --symbol BTCUSDT \
      --live-db data/trade_journal.db --days 730 \
      --slippage-bps-roundtrip 5 --funding-bps-per-window 1 \
      --out comms/research/cost_ab_htf_pullback_trend_2h_BTCUSDT.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))


def _read_emit(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _arm(live_r: list[float], bt_r: list[float]) -> dict[str, Any]:
    import backtest_fidelity_calibrate as cal
    a = cal.agreement(live_r, bt_r)
    return {"verdict": a["verdict"], "n_backtest": a["n_backtest"],
            "backtest_win_rate": a["backtest_win_rate"], "win_rate_diff": a["win_rate_diff"],
            "ks_realized_r": a["ks_realized_r"], "backtest_mean_r": a["backtest_mean_r"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--live-db", required=True)
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--slippage-bps-roundtrip", type=float, default=5.0)
    p.add_argument("--funding-bps-per-window", type=float, default=1.0)
    p.add_argument("--workdir", default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    from regime_debt_matrix import (  # tested feed+harness primitives
        resolve_strategy, resolve_feed, classify, build_harness_cmd, emit_trades_for)
    import backtest_fidelity_calibrate as cal

    cfg = resolve_strategy(a.strategy)
    if cfg is None:
        print(json.dumps({"error": f"{a.strategy} not declared in strategies.yaml"}))
        return 1

    workdir = a.workdir or tempfile.mkdtemp(prefix="cost_ab_")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    # 1) Fetch the config-exact feed + fee-only emit (also the CSV we reuse).
    emit = emit_trades_for(a.strategy, cfg, workdir, a.days, symbol_override=a.symbol)
    if emit.get("error"):
        print(json.dumps({"strategy": a.strategy, "symbol": a.symbol,
                          "error": f"emit failed: {emit['error']}"}))
        return 2
    csv = os.path.join(workdir, f"{a.strategy}__{a.symbol}__data.csv")

    # 2) Cost-ON run over the SAME CSV: reuse the config-exact cmd, append the
    #    execution-realism flags. The emit then carries the per-component breakdown.
    eff = {**cfg, "symbols": [a.symbol]}
    harness = classify(eff)
    feed = resolve_feed(a.symbol, eff["timeframe"])
    coston_emit = os.path.join(workdir, f"{a.strategy}__{a.symbol}__coston.jsonl")
    argv2, faithful, omitted = build_harness_cmd(
        a.strategy, eff, harness, csv, feed["resample"], coston_emit,
        os.path.join(workdir, f"{a.strategy}__{a.symbol}__coston.json"))
    argv2 += ["--slippage-bps-roundtrip", str(a.slippage_bps_roundtrip),
              "--funding-bps-per-window", str(a.funding_bps_per_window)]
    subprocess.run(argv2, check=True, cwd=str(REPO),
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    rows = _read_emit(coston_emit)
    # Derive the two arms from the single cost-on emit.
    fee_only_r = [round(float(r["gross_r"]) - float(r.get("cost_fee_r", 0.0)), 4)
                  for r in rows]
    cost_on_r = [float(r["net_r"]) for r in rows]
    cost_on_dicts = [{"r": float(r["net_r"]), "direction": r.get("direction"),
                      "ts": r.get("entry_time")} for r in rows]

    live_rows = cal._live_rows(a.live_db, a.strategy, a.symbol)
    live_r = [r["r"] for r in live_rows]

    fee_arm = _arm(live_r, fee_only_r)
    cost_arm = _arm(live_r, cost_on_r)
    n = len(rows) or 1
    mean_cost = {k: round(sum(float(r.get(k, 0.0)) for r in rows) / n, 5)
                 for k in ("cost_fee_r", "cost_slippage_r", "cost_funding_r")}
    mean_cost["funding_windows"] = round(
        sum(float(r.get("funding_windows", 0.0)) for r in rows) / n, 3)

    result = {
        "strategy": a.strategy, "symbol": a.symbol,
        "fidelity_label": emit.get("fidelity"), "omitted_levers": omitted,
        "cost_config": {"slippage_bps_roundtrip": a.slippage_bps_roundtrip,
                        "funding_bps_per_window": a.funding_bps_per_window},
        "n_live_measured_prov": len(live_r),
        "live_win_rate": (sum(1 for x in live_r if x > 0) / len(live_r)) if live_r else None,
        "n_backtest": len(rows),
        "mean_cost_r": mean_cost,
        "fee_only": fee_arm,
        "cost_on": cost_arm,
        "ks_delta_fee_to_cost": (
            round(cost_arm["ks_realized_r"] - fee_arm["ks_realized_r"], 4)
            if (cost_arm["ks_realized_r"] is not None
                and fee_arm["ks_realized_r"] is not None) else None),
        "cost_on_by_direction": cal.stratified_agreement(
            live_rows, cost_on_dicts, key="direction"),
    }
    out = json.dumps(result, indent=2)
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
