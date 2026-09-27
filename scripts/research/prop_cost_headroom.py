#!/usr/bin/env python3
"""RQ-20260922-003 — round-trip cost headroom of a leg, re-priced over its OWN committed OOS trades.

WHAT THIS ANSWERS
-----------------
How many basis points of round-trip cost drive a leg's pooled OOS net R to <= 0,
and how far that break-even sits from a venue's cost stack. It is a PAIRED
re-pricing: the SAME per-trade rows (``comms/strategy_evidence/runs/<date>/
<leg>__trades.jsonl``, the rows beside the leg's evidence record) are re-costed at
each arm. Re-pricing cannot create or destroy trades — valid because the trend
harness's entries/exits do not read the cost stack (its costs are applied after
the fact, ``scripts/backtest_trend.py`` cost block) — so n is identical across arms.

Cost in R for one trade at c bps of notional = c x 1e-4 x entry / |entry - sl|.
That is the harness's own conversion; ``--self-test`` proves it reproduces every
row's recorded ``net_r`` from ``gross_r`` and the three recorded cost terms.

OUTPUTS (per leg)
  * the unit's registered arms (fee 7.5 / 15 / 22.5 bps, slippage + funding held
    at the record's values) and ``breakeven_bps`` read off them;
  * the exact fee break-even (slippage + funding held) and the exact FLAT
    round-trip break-even (every cost term replaced by one flat bps number);
  * the holding-time distribution, and the Breakout swap in bps per trade under
    both DXTrade (one 0.033% debit per 00:00-UTC crossing) and prorated models;
  * headroom = flat break-even minus the Breakout non-swap stack, with the swap
    charged per trade from each trade's own hold (an R-weighted equivalent bps).

# wiring: manual-only - a research CLI run by a session on committed evidence; a
# cost-headroom number is read by a human before it is cited, never scheduled.

Tier-1 research tooling. Pure, deterministic. No network, no live path, no config write.

Run:
    python3 scripts/research/prop_cost_headroom.py --self-test
    python3 scripts/research/prop_cost_headroom.py --leg trend_donchian_eth_prop --leg trend_donchian_sol_prop
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
ARMS_FEE_BPS = (7.5, 15.0, 22.5)          # RULE-RQ0922-003-COST-BREAKEVEN's registered arms
BREAKOUT_SWAP_DAILY_BPS = 3.3             # config/prop_rulesets/breakout.yaml lineage; 0.033%/day
BREAKOUT_COMMISSION_BPS_RT = 8.0          # 0.04%/side, prop_ev_sim.py --costs breakout default


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_leg(leg: str) -> Dict[str, Any]:
    rec = json.loads((REPO / "comms" / "strategy_evidence" / f"{leg}.json").read_text())
    rows = [json.loads(l) for l in (REPO / rec["source_run"]).read_text().splitlines() if l.strip()]
    return {"record": rec, "rows": rows}


def per_bps_r(row: Dict[str, Any]) -> float:
    """R cost of ONE basis point of notional on this trade."""
    entry, sl = float(row["entry"]), float(row["sl"])
    return 1e-4 * entry / abs(entry - sl)


def analyse(leg: str) -> Dict[str, Any]:
    d = load_leg(leg)
    rec, rows = d["record"], d["rows"]
    cs = rec["cost_stack"]
    fee0, slip, fund = float(cs["fees"]), float(cs["slippage"]), float(cs["funding"])
    n = len(rows)
    k = [per_bps_r(r) for r in rows]
    gross = [float(r["gross_r"]) for r in rows]
    fw = [float(r.get("funding_windows") or 0.0) for r in rows]
    sum_k, sum_g = sum(k), sum(gross)
    fund_r = sum(ki * fund * w for ki, w in zip(k, fw))

    def net_at_fee(fee: float) -> float:
        return sum_g - sum_k * (fee + slip) - fund_r

    arms = [{"fee_bps_roundtrip": f, "slippage_bps": slip, "funding_bps_per_window": fund,
             "net_r_oos": round(net_at_fee(f), 4), "n": n} for f in ARMS_FEE_BPS]
    crossed = [a["fee_bps_roundtrip"] for a in arms if a["net_r_oos"] <= 0]
    breakeven_arm = min(crossed) if crossed else None
    fee_be = (sum_g - fund_r) / sum_k - slip          # exact fee level at which net = 0
    flat_be = sum_g / sum_k                            # one flat all-in bps, no funding term

    hold_h = [(_ts(r["exit_time"]) - _ts(r["entry_time"])).total_seconds() / 3600 for r in rows]
    mids = [max(0, (_ts(r["exit_time"]).date() - _ts(r["entry_time"]).date()).days) for r in rows]
    swap_dx = [BREAKOUT_SWAP_DAILY_BPS * m for m in mids]
    swap_pr = [BREAKOUT_SWAP_DAILY_BPS * h / 24 for h in hold_h]

    def eff(swaps: List[float]) -> float:           # R-weighted equivalent flat bps of the swap
        return sum(ki * s for ki, s in zip(k, swaps)) / sum_k

    def q(xs: List[float], p: float) -> float:
        s = sorted(xs)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    breakout = {}
    for model, swaps in (("dxtrade", swap_dx), ("prorated", swap_pr)):
        swap_eff = eff(swaps)
        # headroom for EVERYTHING that is not commission or swap: spread + slippage + error
        room_for_spread_slip = flat_be - BREAKOUT_COMMISSION_BPS_RT - swap_eff
        rows_out = {}
        for spread_slip in (3.0, 5.0, 10.0):
            tot = sum_g - sum(ki * (BREAKOUT_COMMISSION_BPS_RT + spread_slip + s) for ki, s in zip(k, swaps))
            rows_out[f"spread_slip_{spread_slip:g}bps"] = {"net_r_oos": round(tot, 4),
                                                           "expectancy_r": round(tot / n, 4)}
        breakout[model] = {
            "swap_bps_per_trade_mean": round(statistics.mean(swaps), 3),
            "swap_bps_per_trade_median": round(statistics.median(swaps), 3),
            "swap_bps_per_trade_p90": round(q(swaps, 0.9), 3),
            "swap_equiv_flat_bps_r_weighted": round(swap_eff, 3),
            "room_left_for_spread_plus_slippage_bps": round(room_for_spread_slip, 3),
            "net_at_spread_slip_arms": rows_out,
        }

    # reproduce the record: harness net_r per row must equal gross - (fee+slip)*k - fund*k*windows
    recon = sum(abs((g - ki * (fee0 + slip) - ki * fund * w) - float(r["net_r"]))
                for g, ki, w, r in zip(gross, k, fw, rows))
    return {
        "leg": leg,
        "evidence_record": f"comms/strategy_evidence/{leg}.json",
        "source_run": rec["source_run"],
        "record_generated_at": rec["generated_at"],
        "config_fingerprint": rec["config_fingerprint"],
        "record": {"n_trades_oos": rec["n_trades_oos"], "net_r_oos": rec["net_r_oos"],
                   "net_r_oos_fee_only": rec["net_r_oos_fee_only"], "cost_stack": cs},
        "n": n,
        "gross_r_oos": round(sum_g, 4),
        "mean_r_per_bps": round(sum_k / n, 5),
        "reproduction_abs_err_total_r": round(recon, 6),
        "arms": arms,
        "breakeven_bps_arm": breakeven_arm,
        "breakeven_fee_bps_exact": round(fee_be, 3),
        "breakeven_flat_roundtrip_bps_exact": round(flat_be, 3),
        "hold": {"hours_median": round(statistics.median(hold_h), 2),
                 "hours_mean": round(statistics.mean(hold_h), 2),
                 "hours_p90": round(q(hold_h, 0.9), 2), "hours_max": round(max(hold_h), 2),
                 "midnights_mean": round(statistics.mean(mids), 3),
                 "share_crossing_no_midnight": round(sum(1 for m in mids if m == 0) / n, 4)},
        "breakout": breakout,
    }


def _self_test() -> int:
    fails = 0
    row = {"entry": 100.0, "sl": 98.0, "gross_r": 1.0}
    if abs(per_bps_r(row) - 1e-4 * 50) > 1e-12:
        fails += 1
        print("FAIL: 1 bps on a 2% stop should be 0.005R")
    for leg in ("trend_donchian_eth_prop", "trend_donchian_sol_prop"):
        a = analyse(leg)
        if a["reproduction_abs_err_total_r"] > 0.01 * a["n"]:
            fails += 1
            print(f"FAIL: {leg} does not reproduce its recorded per-row net_r ({a['reproduction_abs_err_total_r']})")
        base = a["arms"][0]["net_r_oos"]
        if abs(base - a["record"]["net_r_oos"]) > 0.05:
            fails += 1
            print(f"FAIL: {leg} 7.5 arm {base} != record net_r_oos {a['record']['net_r_oos']}")
    print(f"self-test: {'PASS' if not fails else 'FAIL'} ({fails} failure(s))")
    return 1 if fails else 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--leg", action="append", default=[])
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.leg:
        ap.error("give --leg at least once")
    doc = {"tool": "scripts/research/prop_cost_headroom.py", "research_unit": "RQ-20260922-003",
           "decision_rule": "RULE-RQ0922-003-COST-BREAKEVEN", "legs": [analyse(l) for l in a.leg]}
    text = json.dumps(doc, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
