#!/usr/bin/env python3
"""Maker-fee economics re-scorer (M22 P1, research/Tier-1).

The chop-scalp study showed small-TF cells are net-negative because
`fee_R` ~= the entire loss: the backtests charge **7.5 bps taker on the
round-trip**, but the live edge is ~breakeven gross. Bybit **maker** fees on
linear perps are ~0 / rebate, yet the live order path is 100% market/taker. This
tool answers the decisive question WITHOUT re-running any strategy: **do the
small-TF cells flip net-positive under maker execution?**

Key observation — no harness edit needed. Every research harness's
``--emit-trades`` JSONL already carries per-trade ``gross_r`` and ``net_r``, and
the harness fee is a single linear charge (``fee_r = FEE_BPS/1e4 * avg_notional /
risk``). So the per-trade fee-in-R at the emit's taker rate is simply
``fee_r_taker = gross_r - net_r``, and the fee at ANY round-trip bps X is
``fee_r_taker * X / taker_bps``. Re-pricing is therefore exact arithmetic over the
emitted trades:

    net_r(X) = gross_r - (gross_r - net_r) * X / taker_bps

Scenarios reported per cell (a cell = one emit file, tf parsed from its
``*_<tf>.jsonl`` name):
  * **taker** (X = taker_bps) — reproduces the harness net_R EXACTLY (the
    faithfulness check; must match the emit's own net).
  * **maker_both** (X ~= 0, optional rebate) — maker on both legs, 100% fill, no
    adverse selection. The OPTIMISTIC upper bound: net ~= gross.
  * **maker_entry_taker_exit** (X = taker_bps/2) — maker entry, taker exit (exits
    often must be aggressive). The REALISTIC execution assumption, further
    haircut by ``--fill-rate`` (non-fills = dropped frequency, expectancy sign
    unchanged) and ``--adverse-r`` (a per-trade R penalty for getting filled on
    the worse trades — the honest hard part of maker execution).

A cell only "flips" if it clears the REALISTIC bound. If even the optimistic
bound (~gross) is negative, that cell has no gross edge and maker execution
cannot save it. Research only; proposes nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400}


def _tf_from_name(path: str) -> str:
    m = re.search(r"_(\d+[mhd])\.jsonl$", Path(path).name)
    return m.group(1) if m else ""


def _read(path: str) -> List[dict]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _agg(rows: List[dict], roundtrip_bps: float, taker_bps: float,
         tf_seconds: int, *, fill_rate: float = 1.0, adverse_r: float = 0.0) -> Dict[str, Any]:
    """Aggregate net metrics for one cell at a target round-trip bps. fill_rate
    scales frequency (non-fills dropped); it does not change per-trade sign.
    adverse_r is a per-(filled)-trade R penalty."""
    n = len(rows)
    if n == 0:
        return {"trades": 0, "net_total_r": 0.0, "net_exp_r": None,
                "net_r_per_pos_day": None, "win_pct": None}
    scale = roundtrip_bps / taker_bps if taker_bps else 0.0
    per_trade = []
    holds = []
    for r in rows:
        g = float(r.get("gross_r", 0.0))
        net_taker = float(r.get("net_r", g))
        fee_r_taker = g - net_taker
        net = g - fee_r_taker * scale - adverse_r
        per_trade.append(net)
        holds.append(int(r.get("hold_bars", 0)))
    # fill_rate: keep the leading fill_rate fraction (deterministic; expectancy
    # is order-independent so a stride keeps it representative). For fill_rate<1
    # scale totals by fill_rate (each trade fills iid with prob fill_rate).
    net_total = sum(per_trade) * fill_rate
    pos_days = sum(holds) * fill_rate * tf_seconds / 86400.0
    wins = sum(1 for x in per_trade if x > 0)
    return {
        "trades": int(round(n * fill_rate)),
        "net_total_r": round(net_total, 2),
        "net_exp_r": round(sum(per_trade) / n, 4),
        "net_r_per_pos_day": round(net_total / pos_days, 3) if pos_days > 0 else None,
        "win_pct": round(100 * wins / n, 1),
        "gross_total_r": round(sum(float(r.get("gross_r", 0.0)) for r in rows) * fill_rate, 2),
    }


def _score_cell(path: str, taker_bps: float, maker_both_bps: float,
                fill_rate: float, adverse_r: float) -> Dict[str, Any]:
    rows = _read(path)
    tf = _tf_from_name(path)
    tfs = _TF_SECONDS.get(tf, 300)
    label = Path(path).stem
    taker = _agg(rows, taker_bps, taker_bps, tfs)
    maker_opt = _agg(rows, maker_both_bps, taker_bps, tfs)
    maker_real = _agg(rows, taker_bps / 2.0, taker_bps, tfs,
                      fill_rate=fill_rate, adverse_r=adverse_r)
    return {"cell": label, "tf": tf, "trades": taker["trades"],
            "gross_total_r": taker.get("gross_total_r"),
            "taker": taker, "maker_optimistic": maker_opt, "maker_realistic": maker_real}


def _fmt(results: List[Dict[str, Any]], taker_bps: float, maker_both_bps: float,
         fill_rate: float, adverse_r: float) -> str:
    lines = [
        f"# Maker-fee economics — taker={taker_bps}bps | maker_both~{maker_both_bps}bps "
        f"| realistic=maker-entry/taker-exit ({taker_bps/2}bps) × fill_rate={fill_rate} − adverse {adverse_r}R",
        "",
        "| cell | tf | trades | gross_R | net_R taker | net_R maker(opt) | net_R maker(real) | net_R/pos-day real | flips? |",
        "|---|--:|--:|--:|--:|--:|--:|--:|:-:|",
    ]
    for r in results:
        real = r["maker_realistic"]["net_total_r"]
        opt = r["maker_optimistic"]["net_total_r"]
        flips = "✅" if real is not None and real > 0 else ("~" if opt is not None and opt > 0 else "❌")
        lines.append(
            f"| {r['cell']} | {r['tf']} | {r['trades']} | {r['gross_total_r']} | "
            f"{r['taker']['net_total_r']} | {opt} | {real} | "
            f"{r['maker_realistic']['net_r_per_pos_day']} | {flips} |")
    lines += ["",
              "Legend: ✅ net-positive under the REALISTIC maker bound (a genuine flip) · "
              "~ positive only under the optimistic bound (gross has an edge but realistic execution eats it) · "
              "❌ negative even optimistically (no gross edge; maker can't save it).",
              "Faithfulness: 'net_R taker' must equal the harness's own net for the same run."]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="Re-price emitted backtest trades under maker fees.")
    p.add_argument("--emits", nargs="+", required=True,
                   help="Emit JSONL paths or globs (cell tf parsed from *_<tf>.jsonl).")
    p.add_argument("--taker-bps", type=float, default=7.5,
                   help="Round-trip taker bps the emits were generated at.")
    p.add_argument("--maker-both-bps", type=float, default=0.0,
                   help="Round-trip maker bps for the optimistic bound (negative = rebate).")
    p.add_argument("--fill-rate", type=float, default=0.6,
                   help="Fraction of maker entries assumed to fill (realistic bound).")
    p.add_argument("--adverse-r", type=float, default=0.02,
                   help="Per-filled-trade R haircut for adverse selection (realistic bound).")
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args(argv[1:])

    paths: List[str] = []
    for e in args.emits:
        paths.extend(sorted(glob.glob(e)) or ([e] if Path(e).exists() else []))
    if not paths:
        print("ERROR: no emit files matched.", file=sys.stderr)
        return 1
    results = [_score_cell(pp, args.taker_bps, args.maker_both_bps,
                           args.fill_rate, args.adverse_r) for pp in paths]
    print(_fmt(results, args.taker_bps, args.maker_both_bps, args.fill_rate, args.adverse_r))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"\nJSON -> {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
