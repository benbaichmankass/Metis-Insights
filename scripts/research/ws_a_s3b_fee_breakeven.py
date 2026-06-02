#!/usr/bin/env python3
"""WS-A S3b — fee/commission headroom for the S3 passers.

The 2.0 bps placeholder is the weakest assumption in the WS-A chain. This
runs the two significance-passing configs (Copper/pullback, Gold/pullback)
across a round-trip-cost grid and reports where net-R crosses zero — i.e.
how much real NinjaTrader commission the edge can absorb before it dies.
Answers the exact "verify commissions before live" risk quantitatively.

Output: ~/ws_a_s3b_out/<UTC-date>/SUMMARY.md (printed for the relay).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FEE_GRID = [0.0, 2.0, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0]

PASSERS = [
    ("HG=F", "Copper", "scripts/backtest_pullback.py",
     {"--pullback-lookback": 15, "--pullback-frac": 0.5, "--atr-stop-mult": 2.0, "--trail-mult": 4.0}),
    ("GC=F", "Gold", "scripts/backtest_pullback.py",
     {"--pullback-lookback": 15, "--pullback-frac": 0.618, "--atr-stop-mult": 2.0, "--trail-mult": 4.0}),
]


def find_csv(ticker: str) -> Path | None:
    safe = ticker.replace("=", "_")
    hits = sorted(glob.glob(str(Path.home() / "ws_a_sweep_out" / "*" / "data" / f"{safe}.csv")))
    return Path(hits[-1]) if hits else None


def run_fee(script: str, csv: Path, params: dict, fee: float) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as jf:
        jpath = Path(jf.name)
    cmd = [sys.executable, str(REPO / script), "--data", str(csv),
           "--json", str(jpath), "--fee-bps-roundtrip", str(fee)]
    for k, v in params.items():
        cmd += [k, str(v)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        d = json.loads(jpath.read_text())
        return {"net_r": d.get("net_total_r"), "exp": d.get("net_expectancy_r"),
                "trades": d.get("total_trades")}
    finally:
        jpath.unlink(missing_ok=True)


def main() -> int:
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path.home() / "ws_a_s3b_out" / date
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"# WS-A S3b — Fee/Commission Headroom ({date})", "",
             "Net total-R of the S3 passers across round-trip cost (bps), full "
             "history. **Breakeven fee** = where net-R crosses 0 — the "
             "commission the edge can absorb.", ""]
    for ticker, label, script, params in PASSERS:
        csv = find_csv(ticker)
        if csv is None:
            lines += [f"## {label} — no cached data, skipped", ""]
            continue
        curve = [(fee, run_fee(script, csv, params, fee)) for fee in FEE_GRID]
        n = curve[0][1].get("trades")
        lines.append(f"## {label} / pullback (n={n})")
        lines.append("")
        lines.append("| round-trip bps | net total-R | exp/trade |")
        lines.append("|---|---|---|")
        breakeven = None
        prev = None
        for fee, m in curve:
            nr = m.get("net_r")
            lines.append(f"| {fee:g} | {nr:+.1f} | {m.get('exp'):+.3f} |"
                         if isinstance(nr, (int, float)) else f"| {fee:g} | err | err |")
            if breakeven is None and prev is not None and isinstance(nr, (int, float)):
                pfee, pnr = prev
                if pnr > 0 >= nr and (pnr - nr) != 0:
                    breakeven = pfee + (fee - pfee) * (pnr / (pnr - nr))
            if isinstance(nr, (int, float)):
                prev = (fee, nr)
        be = f"~{breakeven:.0f} bps round-trip" if breakeven else ">30 bps (edge survives the whole grid)"
        lines += ["", f"- **Breakeven cost: {be}**", ""]

    summary = "\n".join(lines) + "\n"
    (out_dir / "SUMMARY.md").write_text(summary)
    print("\n" + "=" * 72 + "\n" + summary + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
