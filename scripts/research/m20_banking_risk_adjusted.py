#!/usr/bin/env python3
"""M20 — read a ladder sweep's verdicts under a RISK-ADJUSTED lens.

WHY THIS EXISTS. The M20 lever gate (both `scripts/research/m27/
ict_scalp_exit_sweep.py::beats` and `scripts/research/m20_fleet_exit_sweep.py::
beats`) requires a cell to beat baseline on net_R **AND** maxDD:

    m27:   cell.total_r      >  base.total_r      and cell.max_dd_r <  base.max_dd_r
    fleet: cell.net_total_r  >= base.net_total_r  and cell.max_drawdown_r <= ...

CORRECTED 2026-08-10 — this file originally claimed banking "necessarily"
lowers net_R and lowers maxDD, so that no banking cell could ever pass either
gate. **Both halves were measured false the same day** (memo § 10.6, GH Actions
run 31344328313, 28 ict_scalp ladder cells):

  * net_R ROSE in 6/28 in-sample and 8/28 out-of-sample cells. On a fixed 1.5R
    bracket with a 1.0R rung, banking converts a loser that first printed +1R
    from -1R into 0.25*(+1) + 0.75*(-1) = -0.5R. The original argument followed
    only the winner side of the distribution.
  * maxDD ROSE (got WORSE) in 14/28 OOS cells. Drawdown is a property of the
    equity PATH: capping the biggest winners removes the recoveries that used
    to end drawdowns, so peak-to-trough can deepen even as each loss shrinks.
  * 2/28 cells passed the gate outright AND survived the yearly walk-forward,
    which refutes "P(pass) = 0" by counterexample.

The fleet-wide result in `docs/research/M20-exit-refinement-2026-07-12.md`
§ 6.2 ("banking reduced net_R in every one of the 20 banking cells") stands as
a MEASUREMENT. What does not stand is that it had to come out that way: those
20 cells are trend-following legs whose edge genuinely IS the fat right tail,
so the prior is real but does not transfer to a capped-upside strategy.

WHAT THIS DOES. Reads the verdicts.json a sweep already wrote and reports, per
cell, the quantities the primary gate discards:

  * `mar`   = net_R / maxDD — return per unit of worst peak-to-trough pain.
              The standard way to compare a smoother book against a fatter one.
  * `dR`, `dDD`, and the TRADE RATIO `dDD / dR` — how much drawdown a cell buys
              per unit of net_R surrendered. A cell giving up 1R to remove 5R of
              drawdown is a very different object from one giving up 1R to
              remove 0.2R, and the primary gate scores both identically: FAIL.
  * `banked_pct` — the rung-fill DENOMINATOR, carried through from the harness.
              A cell whose rung almost never filled is INERT; its flat ΔR is not
              evidence about banking, and it must not be read as a negative.

This CHANGES NO GATE and ships nothing. It is a reading tool: the primary
net_R+maxDD gate stays the shipping criterion until an operator decides
otherwise, because relaxing a gate to admit a lever is exactly how a cosmetic
lever gets shipped.

The reason to read a cell this way is now STRONGER than the (wrong) original
one. `honest_negative` covers at least three different objects — a cell that
traded return for smoothness, a cell that lost on BOTH axes (measured: three
of the four ict_scalp 5m legs, every OOS cell), and a cell whose rung barely
filled and which is therefore INERT. The gate reports all three identically.
`dd_per_r` separates the first two; `banked_pct` catches the third.

Usage:
    python3 scripts/research/m20_banking_risk_adjusted.py <verdicts.json> [...]
    python3 scripts/research/m20_banking_risk_adjusted.py --glob 'runtime_logs/**/verdicts.json'

Tier-1 research tooling. Reads JSON, writes nothing.
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    """num/den, or None when it is not defined — never a fabricated 0.0.

    A maxDD of exactly 0 means the book never drew down in the window, so
    "return per unit of drawdown" is undefined, not infinite and not zero.
    Reporting 0.0 there would rank an undefined cell alongside a genuinely
    terrible one.
    """
    try:
        if num is None or den is None:
            return None
        den = float(den)
        if den == 0.0:
            return None
        return float(num) / den
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float], nd: int = 2, width: int = 8) -> str:
    return ("—".rjust(width) if v is None else f"{v:>{width}.{nd}f}")


def analyse(path: Path) -> Dict[str, Any]:
    d = json.loads(path.read_text())
    base = d.get("baseline") or {}
    rows: List[Dict[str, Any]] = []
    for tag, cell in (d.get("cells") or {}).items():
        rec: Dict[str, Any] = {"tag": tag, "lever": cell.get("lever"),
                               "gate_verdict": cell.get("verdict"),
                               "wf": cell.get("walkforward_verdict")}
        for w in ("IS", "OOS"):
            b, c = base.get(w) or {}, cell.get(w) or {}
            bR, cR = b.get("total_r"), c.get("total_r")
            bD, cD = b.get("max_dd_r"), c.get("max_dd_r")
            dR = None if (bR is None or cR is None) else float(cR) - float(bR)
            dD = None if (bD is None or cD is None) else float(cD) - float(bD)
            rec[w] = {
                "n": c.get("trades"),
                "dR": dR,
                "dDD": dD,
                "mar_base": _ratio(bR, bD),
                "mar_cell": _ratio(cR, cD),
                # Drawdown removed per unit of net_R surrendered. dD is
                # negative when the cell reduces drawdown and dR is negative
                # when it costs return, so a GOOD trade is a positive ratio.
                "dd_per_r": _ratio(-(dD if dD is not None else 0.0) or None, -dR)
                            if (dR is not None and dR < 0 and dD is not None) else None,
            }
        rows.append(rec)
    return {"path": str(path), "leg": d.get("leg"), "symbol": d.get("symbol"),
            "timeframe": d.get("timeframe"), "data": d.get("data"),
            "tp_at_r": d.get("tp_at_r"), "declared_base": d.get("declared_base"),
            "baseline": base, "rows": rows}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="verdicts.json path(s)")
    ap.add_argument("--glob", default=None, help="glob for verdicts.json files")
    a = ap.parse_args(argv[1:])

    paths = [Path(p) for p in a.paths]
    if a.glob:
        paths += [Path(p) for p in sorted(globmod.glob(a.glob, recursive=True))]
    paths = [p for p in paths if p.exists()]
    if not paths:
        # An empty input is stated, never rendered as a clean "no findings".
        print("no verdicts.json found — nothing analysed (this is NOT a result)",
              file=sys.stderr)
        return 2

    print("M20 banking — RISK-ADJUSTED read")
    print("The primary gate needs net_R AND maxDD to improve. Banking lowers")
    print("net_R by construction, so it can never pass. These are the numbers")
    print("that gate discards. Nothing here ships a lever.\n")
    for p in paths:
        r = analyse(p)
        b = r["baseline"] or {}
        print(f"=== {r['leg'] or p.parent.name}  ({r['symbol']} {r['timeframe']}, "
              f"tp_at_r={r['tp_at_r']}, declared={r['declared_base'] or 'none'})")
        print(f"    data: {r['data']}")
        for w in ("IS", "OOS"):
            m = b.get(w) or {}
            print(f"    BASE {w}: n={m.get('trades')} netR={m.get('total_r')} "
                  f"maxDD={m.get('max_dd_r')} MAR={_fmt(_ratio(m.get('total_r'), m.get('max_dd_r')))}")
        print(f"    {'cell':16s} {'IS ΔR':>8s} {'IS ΔDD':>8s} {'IS MAR':>8s} "
              f"{'OOS ΔR':>8s} {'OOS ΔDD':>8s} {'OOS MAR':>8s} {'DD/R':>7s}  gate")
        for row in r["rows"]:
            i, o = row["IS"], row["OOS"]
            print(f"    {row['tag']:16s} {_fmt(i['dR'])} {_fmt(i['dDD'])} "
                  f"{_fmt(i['mar_cell'])} {_fmt(o['dR'])} {_fmt(o['dDD'])} "
                  f"{_fmt(o['mar_cell'])} {_fmt(o['dd_per_r'], 2, 7)}  "
                  f"{row['gate_verdict']}"
                  + (f" | WF {row['wf']}" if row.get('wf') else ""))
        print()

    print("Reading guide:")
    print("  DD/R > 1 means the cell removed MORE drawdown than the net_R it cost —")
    print("  the trade a net_R-first gate is built to reject. That is a finding to")
    print("  take to the operator (and the prop-ruleset EV question,")
    print("  PB-20260712-PROP-BANKING-EV), NOT a reason to relax the shipping gate.")
    print("  Always read a cell beside its banked_pct: a rung that never filled is")
    print("  INERT, and its flat ΔR is not evidence about banking at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
