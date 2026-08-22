#!/usr/bin/env python3
# wiring: manual-only — a one-shot sizing measurement run by a review session
# before deciding whether an exit change is worth a sprint; it answers a
# question, it does not maintain a surface. Re-run it from /system-review (or
# /performance-review) when the exit stance is being re-argued.
"""How much money is actually recoverable by making a declared take-profit fire?

WHY THIS EXISTS
===============
The 2026-08-21 /system-review measured that scalp and pullback legs are held
10-100x their own bar horizon, and that only 30 of 369 closes (8.1%) exit via
the strategy's own bracket. The obvious inference — "give the scalps a
take-profit close path, it is the highest-leverage change in the exit program" —
was stated in that review's first draft and is NOT SUPPORTED BY THIS
MEASUREMENT. The operator's instinct to size the prize before spending the
sprint was the correct call, and this script is what that call produced.

WHAT IT MEASURES
================
For every closed non-backtest trade with an entry, a declared ``take_profit_1``,
a quantity and a resolved pnl: did price ever REACH the declared take-profit at
any point during the hold, and if so, what would that TP have paid on the same
quantity versus what the trade actually made?

METHOD, and its limits, stated because the number is small enough that the
limits matter:

  * 1h bars over the hold window, from /api/bot/candles (the SAME connector the
    strategies trade on — Bybit for crypto, IBKR for futures — not a third-party
    feed). A 1h bar's high/low captures intrabar movement, so a touch is a real
    touch; a resting venue TP is a market trigger, so a touch is a fill.
  * 1h CANNOT see inside an hour, so the reach rate is a LOWER BOUND. Measured
    2026-08-21: only 2 of 323 trades had a whole hold inside one of their own
    bars, so this understates little in practice — but it understates, never
    overstates.
  * Fees are ignored on the counterfactual leg, which biases the prize UP.
  * The counterfactual assumes a fill AT the TP. Slippage biases it UP too.

⚠️ THE HEADLINE IS A NET OF TWO OPPOSITE EFFECTS — DO NOT QUOTE EITHER ALONE.
A hard take-profit does not only recover give-back; it also FORFEITS the trades
that ran past the target. Measured on the live journal 2026-08-21 over the
window 2026-07-23 -> 2026-08-21:

    reached their declared TP           52 of 323  (16%)
    of those, exited WORSE              29         -20,137   (25 reconciler_filled)
    of those, exited BETTER             23         +13,899   (winners that ran)
    NET recoverable                                 +6,237

    by funding class:  paper  +6,235.41  ·  REAL MONEY  +1.81

So the mechanism is real and the sign is positive, but at a 1.45:1 ratio rather
than a free win, over 16% of trades, and essentially all of it is paper. That is
a case for a measured, reversible change — not for calling it the highest-
leverage item in the exit program, which is what it was almost called.

Usage:
    python3 scripts/research/tp_recovery_counterfactual.py --trades trades.json \\
        --candles-dir c1h/ [--json out.json]

``--trades`` is a /api/diag/journal?table=trades dump; ``--candles-dir`` holds
one <SYMBOL>.json per symbol from /api/bot/candles?interval=1h.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from collections import defaultdict

_TF = {"5m": 5, "15m": 15, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}


def _dt(v):
    """Parse the journal's THREE timestamp encodings. Returns None, never a guess."""
    if not v:
        return None
    s = str(v).strip()
    try:
        if s.replace(".", "", 1).isdigit():
            f = float(s)
            return dt.datetime.fromtimestamp(f / 1000 if f > 1e11 else f, dt.timezone.utc)
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        # Narrow on purpose. These are the four ways a REAL journal timestamp
        # fails to parse (bad literal, wrong type, epoch out of range, platform
        # limit). Anything else is a bug in this parser and must surface rather
        # than become a silently-skipped trade — a skipped trade shrinks the
        # denominator of every figure this script prints.
        return None


def _bar_minutes(strategy: str):
    for k, v in _TF.items():
        if strategy.endswith("_" + k) or ("_" + k + "_") in strategy:
            return v
    return None


def _family(s: str) -> str:
    if "scalp" in s:
        return "scalp"
    if "pullback" in s:
        return "pullback"
    if "donchian" in s or "trend" in s:
        return "trend/donchian"
    if s.startswith("pairs_"):
        return "pairs"
    return "other"


def analyse(trades, candles):
    """Returns (rows, skipped). `skipped` is a REASON HISTOGRAM, not a count —
    a bare count could not distinguish 'no candle history' (our gap) from
    'the trade declared no take-profit' (the strategy's choice)."""
    out, skipped = [], defaultdict(int)
    for r in trades:
        if r.get("status") != "closed" or r.get("is_backtest"):
            continue
        series = candles.get(r.get("symbol"))
        if not series:
            skipped["no_candle_history"] += 1
            continue
        o, c = _dt(r.get("created_at")), _dt(r.get("closed_at"))
        if not o or not c or c <= o:
            skipped["unusable_hold_window"] += 1
            continue
        entry, tp, qty, pnl = (r.get("entry_price"), r.get("take_profit_1"),
                               r.get("position_size"), r.get("pnl"))
        if not entry or not tp or not qty or pnl is None:
            skipped["no_entry_tp_qty_or_pnl"] += 1
            continue
        win = [k for k in series if o.timestamp() - 3600 <= k["time"] <= c.timestamp()]
        if not win:
            skipped["hold_outside_candle_history"] += 1
            continue
        is_long = str(r.get("direction", "")).lower()[:1] in ("l", "b")
        reached = (max(k["high"] for k in win) >= tp) if is_long \
            else (min(k["low"] for k in win) <= tp)
        tp_pnl = (tp - entry) * qty if is_long else (entry - tp) * qty
        bar = _bar_minutes(str(r.get("strategy_name", "")))
        out.append({
            "id": r.get("id"), "strategy": r.get("strategy_name"),
            "family": _family(str(r.get("strategy_name", ""))),
            "symbol": r.get("symbol"),
            "account_class": r.get("account_class")
            or ("paper" if r.get("is_demo") else "real_money"),
            "reached_tp": reached, "actual_pnl": float(pnl), "tp_pnl": float(tp_pnl),
            "delta": float(tp_pnl) - float(pnl),
            "bars_held": ((c - o).total_seconds() / 60 / bar) if bar else None,
            "exit_reason": r.get("exit_reason"),
        })
    return out, dict(skipped)


def report(rows, skipped) -> None:
    hit = [x for x in rows if x["reached_tp"]]
    print(f"POPULATION: {len(rows)} closed non-backtest trades carrying entry + "
          f"take_profit_1 + qty + pnl, with a hold window inside 1h candle history.")
    print(f"SKIPPED (reason histogram, never a bare count): {skipped}")
    if not rows:
        # The guard is right to insist on a denominator even here — arguably
        # ESPECIALLY here. "no rows" with nothing beside it is exactly the empty
        # result that reads as a clean negative; the skip histogram IS the
        # denominator, and it says whether we looked and found nothing or never
        # looked at all.
        print(f"\n0 rows entered the population, out of "
              f"{sum(skipped.values())} candidate(s) examined — every one was "
              f"skipped for the reasons above. This is an ABSENT result, NOT a "
              f"clean one: it measured nothing.")
        return
    # provenance: reached_tp — 1h bar high/low vs trades.take_profit_1 over the
    # hold window; the percentage ranges over len(rows), the population printed
    # directly above, NOT over all closed trades in the journal.
    print(f"\nReached the declared TP during the hold: {len(hit)} of {len(rows)} "
          f"({100 * len(hit) / len(rows):.0f}% of this population, not of all closed "
          f"trades)  [LOWER BOUND — 1h cannot see intrabar]")

    worse = [x for x in hit if x["delta"] > 0]
    better = [x for x in hit if x["delta"] < 0]
    print("\n⚠️ NET OF TWO OPPOSITE EFFECTS — quoting either alone is misleading:")
    print(f"  exited WORSE than TP : {len(worse):>3}   recoverable  "
          f"{sum(x['delta'] for x in worse):+12,.0f}")
    print(f"  exited BETTER than TP: {len(better):>3}   forfeited    "
          f"{sum(x['delta'] for x in better):+12,.0f}   (winners that ran)")
    print(f"  {'NET':<21}       {sum(x['delta'] for x in hit):+12,.0f}")

    for key, label in (("family", "BY FAMILY"), ("account_class", "BY FUNDING CLASS")):
        agg = defaultdict(lambda: {"n": 0, "hit": 0, "act": 0.0, "tp": 0.0})
        for x in rows:
            a = agg[x[key]]
            a["n"] += 1
            if x["reached_tp"]:
                a["hit"] += 1
                a["act"] += x["actual_pnl"]
                a["tp"] += x["tp_pnl"]
        print(f"\n{label}")
        print(f"  {'':<16}{'n':>5}{'hitTP':>7}{'%':>6}{'actual $':>13}"
              f"{'at TP $':>13}{'net $':>13}")
        for k, a in sorted(agg.items(), key=lambda kv: kv[1]["act"] - kv[1]["tp"]):
            pct = 100 * a["hit"] / a["n"] if a["n"] else 0
            print(f"  {str(k):<16}{a['n']:>5}{a['hit']:>7}{pct:>5.0f}%"
                  f"{a['act']:>13,.0f}{a['tp']:>13,.0f}{a['tp'] - a['act']:>13,.0f}")

    print("\nEXIT MECHANISM where TP was touched and the trade exited worse:")
    mech = defaultdict(lambda: [0, 0.0])
    for x in worse:
        mech[str(x["exit_reason"])][0] += 1
        mech[str(x["exit_reason"])][1] += x["delta"]
    for k, (n, d) in sorted(mech.items(), key=lambda kv: -kv[1][1]):
        print(f"  {n:>3}  {k:<34}{d:>12,.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", required=True, help="journal trades dump (JSON list)")
    ap.add_argument("--candles-dir", required=True, help="dir of <SYMBOL>.json 1h candles")
    ap.add_argument("--json", help="write the per-trade rows here")
    a = ap.parse_args()

    trades = json.loads(pathlib.Path(a.trades).read_text())
    if isinstance(trades, dict):
        trades = trades.get("rows") or []
    candles, unreadable = {}, {}
    for f in sorted(pathlib.Path(a.candles_dir).glob("*.json")):
        try:
            series = json.loads(f.read_text()).get("candles") or []
        except (ValueError, OSError, AttributeError) as exc:
            # A CORRUPT file is not an ABSENT symbol. Collapsing them would make
            # every trade on that symbol skip as `no_candle_history`, i.e. a
            # read failure of ours would read as a gap in the venue's data.
            unreadable[f.stem] = f"{type(exc).__name__}: {exc}"
            continue
        if series:
            candles[f.stem] = sorted(series, key=lambda k: k["time"])
        else:
            unreadable[f.stem] = "file present but carried no candles"
    if unreadable:
        print(f"⚠️ UNREADABLE candle series ({len(unreadable)}) — trades on these "
              f"symbols are NOT in the population below: {unreadable}")
    if not candles:
        print("ABSENT: no usable candle series — this measured nothing.")
        return 2

    rows, skipped = analyse(trades, candles)
    report(rows, skipped)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=2))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
