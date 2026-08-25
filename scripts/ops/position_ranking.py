#!/usr/bin/env python3
# wiring: manual-only - an operator DECISION-SUPPORT tool for the standing
#     'value of liquidating vs holding' ask. It ranks open positions and
#     recommends nothing; the decision is Tier-3 and human. Wiring it to a
#     timer would produce a recommendation feed nobody asked for, which is the
#     desensitized-alarm shape.
"""Rank open positions by whether they still earn the capital they occupy.

`portfolio_conflict_audit.py` says WHICH positions conflict. It does not say
WHICH to drop, and that gap is the operator's standing ask (2026-08-18, twice):

    "I need to come up with some sort of proxy for the value of liquidating
    versus the value of holding."

This is that ranking. It grades each open position with
`src/runtime/hold_vs_cash.py` and orders the results worst-first, so the
question stops being "is this trade bad?" (which every exit lever answers
"no") and becomes "of everything we hold, which is paying least for the risk
it carries?"

THREE INPUTS, AND TWO OF THEM ARE REFUSED RATHER THAN GUESSED
--------------------------------------------------------------
* ``positions`` — `/api/bot/positions` rows.
* ``--telemetry`` — `/api/diag/position_telemetry` rows, supplying each trade's
  `rToTarget` / `rToStop`. **Not recomputed here from entry/SL/TP.** A locally
  re-derived level is a second definition free to drift from the one the
  monitor acts on, and that exact shortcut produced the ATR reconstruction
  error on trade 4163 (a recomputed ATR vs the frozen `meta["atr"]` the monitor
  actually uses). A position with no telemetry row grades `unmeasured`.
* ``--hit-rates`` — `{"<strategy>": p}`, the MEASURED historical hit rate per
  leg, from `scripts/research/peak_banking_basis.py::conditional_hit_rate`.
  **Never defaulted and never estimated.** A leg absent from the table grades
  `unmeasured`.

`unmeasured` is reported as its own rank bucket, never folded into `hold`.
Being unable to grade a position is otherwise the cheapest way to keep it open,
and that bias is precisely why every prior exit lever came back "hold".

WHAT IT DOES NOT DO
-------------------
It ranks; it does not close anything, and it is not a queue to work down. A
`liquidate` verdict is a position whose measured hit rate is below the rate its
own geometry requires — that is a reason to LOOK, and acting on it is Tier-3.

Observe-only. Reads JSON on stdin/disk. No DB, no socket, no order path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.runtime import hold_vs_cash as hvc  # noqa: E402

#: Worst first. `unmeasured`/`ungradeable` sort AFTER decided verdicts but are
#: never hidden — they are the work list for the next measurement round.
_ORDER = {hvc.STATE_LIQUIDATE: 0, hvc.STATE_HOLD: 1, hvc.STATE_UNGRADEABLE: 2,
          hvc.STATE_UNMEASURED: 3, hvc.STATE_NOT_APPLICABLE: 4}


def _load(path: str) -> Any:
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text())


def _rows(doc: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(doc, list):
        return doc
    for k in keys:
        v = doc.get(k)
        if isinstance(v, list):
            return v
    return []


def telemetry_index(doc: Any) -> Dict[str, Dict[str, Any]]:
    """`{trade_id: row}`. Ids are stringified so a JSON int and a DB TEXT id
    cannot silently miss each other — a miss here would read as `unmeasured`,
    which is a safe direction but a misleading one."""
    out = {}
    for r in _rows(doc, "rows", "telemetry"):
        tid = r.get("trade_id", r.get("tradeId", r.get("id")))
        if tid is not None:
            out[str(tid)] = r
    return out


def grade(
    positions: List[Dict[str, Any]],
    telemetry: Dict[str, Dict[str, Any]],
    hit_rates: Dict[str, float],
    *,
    bars_per_day: Dict[str, float] | None = None,
    redeploy: Dict[str, float] | None = None,
) -> List[Dict[str, Any]]:
    bars_per_day = bars_per_day or {}
    redeploy = redeploy or {}
    out = []
    for p in positions:
        pid = str(p.get("id"))
        strat = str(p.get("pattern") or p.get("strategy") or "")
        t = telemetry.get(pid) or {}
        v = hvc.evaluate(
            r_to_target=t.get("rToTarget", t.get("r_to_target")),
            r_to_stop=t.get("rToStop", t.get("r_to_stop")),
            observed_p=hit_rates.get(strat),
            open_r=t.get("openR", t.get("open_r")),
            bars_held=t.get("barsHeld", t.get("bars_held")),
            bars_per_day=bars_per_day.get(strat),
            redeploy_r_per_day=redeploy.get(strat),
            qty=p.get("qty"),
        )
        out.append({
            "id": pid, "symbol": p.get("symbol"), "side": p.get("side"),
            "account": p.get("account"), "strategy": strat,
            "state": v.state, "reason": v.reason,
            "required_p": v.breakeven_p, "observed_p": v.observed_p,
            "edge_p": v.edge_p, "rr_from_here": v.rr_from_here,
            "r_per_day": v.r_per_day,
            "has_telemetry": bool(t),
            "has_hit_rate": strat in hit_rates,
        })
    # Within `liquidate`, the most negative edge first — that is the ordering
    # the question asks for. Rows without an edge sort last inside their bucket
    # rather than being treated as a zero edge.
    out.sort(key=lambda r: (_ORDER.get(r["state"], 9),
                            r["edge_p"] if r["edge_p"] is not None else 1e9))
    return out


def render(rows: List[Dict[str, Any]]) -> str:
    from collections import Counter
    counts = Counter(r["state"] for r in rows)
    out = ["POSITION RANKING — does this still earn the capital it occupies?",
           "=" * 78,
           f"positions: {len(rows)}"]
    for state in (hvc.STATE_LIQUIDATE, hvc.STATE_HOLD, hvc.STATE_UNGRADEABLE,
                  hvc.STATE_UNMEASURED, hvc.STATE_NOT_APPLICABLE):
        out.append(f"  {state:<16}{counts.get(state, 0)}")
    # The two denominators. Printed even at zero: their absence is what would
    # make an all-`hold` report unfalsifiable.
    out += ["",
            f"missing telemetry : {sum(1 for r in rows if not r['has_telemetry'])}",
            f"missing hit rate  : {sum(1 for r in rows if not r['has_hit_rate'])}",
            "  ^ these are why a row is `unmeasured`. `unmeasured` is NOT `hold`.",
            ""]
    hdr = (f"{'id':>6} {'symbol':<10} {'side':<5} {'strategy':<24} "
           f"{'state':<13} {'req_p':>7} {'obs_p':>7} {'edge':>7} {'rr':>6}")
    out += [hdr, "-" * len(hdr)]

    def fmt(v, n=3):
        """An em-dash for a value we do not have. Never 0.000, which in a
        probability column reads as a measured certainty."""
        return "—" if v is None else f"{v:.{n}f}"

    for r in rows:
        out.append(f"{r['id']:>6} {str(r['symbol']):<10} {str(r['side']):<5} "
                   f"{r['strategy'][:24]:<24} {r['state']:<13} "
                   f"{fmt(r['required_p']):>7} {fmt(r['observed_p']):>7} "
                   f"{fmt(r['edge_p']):>7} {fmt(r['rr_from_here'],2):>6}")
    return "\n".join(out)


def _self_test() -> int:
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    pos = [
        {"id": 1, "symbol": "A", "side": "sell", "pattern": "leg_a", "qty": 1},
        {"id": 2, "symbol": "B", "side": "buy", "pattern": "leg_b", "qty": 1},
        {"id": 3, "symbol": "C", "side": "buy", "pattern": "leg_c", "qty": 1},
    ]
    tel = telemetry_index({"rows": [
        {"trade_id": 1, "rToTarget": 0.5, "rToStop": 1.0, "openR": 3.0, "barsHeld": 240},
        {"trade_id": "2", "rToTarget": 3.0, "rToStop": 1.0, "openR": 1.0, "barsHeld": 24},
    ]})
    check("telemetry ids are stringified", sorted(tel), ["1", "2"])

    rows = grade(pos, tel, {"leg_a": 0.40, "leg_b": 0.40})
    by = {r["id"]: r for r in rows}
    # leg_a needs 1.0/1.5 = 0.667 and has 0.40 -> liquidate.
    check("under-earning position liquidates", by["1"]["state"], hvc.STATE_LIQUIDATE)
    # leg_b needs 1.0/4.0 = 0.25 and has 0.40 -> hold.
    check("over-earning position holds", by["2"]["state"], hvc.STATE_HOLD)
    # leg_c has neither telemetry nor a hit rate.
    check("no telemetry -> unmeasured", by["3"]["state"], hvc.STATE_UNMEASURED)
    check("...and is NOT hold", by["3"]["state"] != hvc.STATE_HOLD, True)
    check("worst-first ordering", [r["id"] for r in rows], ["1", "2", "3"])

    # A position with telemetry but no measured hit rate must ALSO be unmeasured
    # -- the geometry alone cannot decide, and defaulting the rate is the bias.
    only_geo = grade(pos[:1], tel, {})
    check("geometry without a measured rate is unmeasured",
          only_geo[0]["state"], hvc.STATE_UNMEASURED)
    check("...and the required rate is still reported",
          round(only_geo[0]["required_p"], 3), 0.667)

    text = render(rows)
    check("denominators are printed", "missing telemetry" in text, True)
    check("the unmeasured caveat is printed", "NOT `hold`" in text, True)

    for f in fails:
        print("FAIL", f)
    print(f"self-test: {10 - len(fails)}/10 passed")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("positions", nargs="?", default="-")
    ap.add_argument("--telemetry", help="/api/diag/position_telemetry JSON")
    ap.add_argument("--hit-rates", help='{"<strategy>": p} measured, never defaulted')
    ap.add_argument("--bars-per-day", help='{"<strategy>": bars}')
    ap.add_argument("--redeploy", help='{"<strategy>": r_per_day}')
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    try:
        positions = _rows(_load(a.positions), "positions", "rows")
        tel = telemetry_index(_load(a.telemetry)) if a.telemetry else {}
        rates = _load(a.hit_rates) if a.hit_rates else {}
        bpd = _load(a.bars_per_day) if a.bars_per_day else {}
        redep = _load(a.redeploy) if a.redeploy else {}
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = grade(positions, tel, rates, bars_per_day=bpd, redeploy=redep)
    print(json.dumps(rows, indent=2, default=str) if a.json else render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
