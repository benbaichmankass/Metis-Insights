#!/usr/bin/env python3
# wiring: manual-only — a calibration READ a session runs against the live journal
# when it needs to answer "are our targets predictions". Nothing schedules it: the
# number only means something next to a decision about the legs it names, and a CI
# job asserting a particular reach-rate would fail every time a leg is retuned.
# Its --selftest IS the wired half (the invariants never change though the fleet does).
"""E3.6's falsifier, as a runnable report: are the fleet's targets PREDICTIONS?

`docs/design/exit-mechanism-construction-PROCESS.md` § E3.6 requires that a
predictive bracket be "graded against realised exits -- calibration first ...,
P&L second". `docs/research/exit-lever-wiring-audit-2026-09-06.md` § Q1 records
that this falsifier "is not measured anywhere ... has no instrument, no
artifact and no cell". This is the instrument.

TWO VIEWS, because they answer different questions and have different flaws:

  --exits (default)  where trades ACTUALLY ENDED vs the target they declared,
        from `trades`. Large n. ⚠️ CENSORED BY THE CURRENT GEOMETRY -- these
        trades exited at a trail, so the realised exit is a floor on how far
        the move went, not a measure of it. Good for FALSIFYING a target
        ("no trade ever got there"); bad for SETTING one.

  --mfe  how far each trade ever GOT, from `position_telemetry.peak_r`.
        Uncensored in principle and the right basis for setting a target.
        ⚠️ Small n, `peak_provenance: estimated`, and
        `peak_r_is_lower_bound: True` on every row -- so it is a LOWER BOUND,
        which is the safe direction for falsifying a too-far target and the
        unsafe one for justifying a too-near one.

Neither view is quoted without the other, and neither is pooled across exit
provenance. Everything is percent-of-entry, never R -- see
`src/runtime/bracket_calibration.py` for why (the R denominator is measurably
contaminated, and the venue clamp is itself a percent of entry).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from src.runtime import provenance as P  # noqa: E402
from src.runtime.bracket_calibration import (  # noqa: E402
    GRADE_OK, grade_trade, quantile, summarise,
)
from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT  # noqa: E402

DEFAULT_API = "https://ict-bot.duckdns.org"

#: Symbols the three Bybit accounts actually carry. The venue clamp is named
#: for a Bybit boundary and applied fleet-wide, so this split is the whole
#: point of the --mfe view: `tp_venue_cap.py` states in its own docstring that
#: whether 0.099 is right for a non-Bybit leg is an OPEN QUESTION.
BYBIT_SYMBOLS = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT",
})


def _get(api: str, path: str, token: str) -> Any:
    req = urllib.request.Request(api.rstrip("/") + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=90) as fh:
        return json.loads(fh.read().decode("utf-8"))


def _fmt(v, p=4):
    return "   n/a" if v is None else f"{v:.{p}f}"


def report_exits(rows: List[Dict[str, Any]], *, min_n: int) -> None:
    direc = [r for r in rows
             if r.get("status") == "closed" and not r.get("is_backtest")
             and not str(r.get("strategy_name") or "").startswith("pairs_")]
    dates = [d for d in (r.get("closed_at") or r.get("timestamp") for r in direc) if d]
    print(f"POPULATION: closed, non-backtest, non-pairs `trades` rows = {len(direc)} "
          f"(of {len(rows)} returned)")
    if dates:
        print(f"WINDOW: {min(dates)} -> {max(dates)}")
    print("⚠️ The pairs sleeve is EXCLUDED: it is an isolated 2-leg executor whose "
          "legs stop on the SPREAD, so a per-leg bracket is the wrong yardstick.\n")

    strata = collections.defaultdict(list)
    for r in direc:
        strata[P.classify_row(r)[0]].append(r)

    print("=== BY EXIT-PRICE PROVENANCE (never pooled) ===")
    for bucket in ("measured", "estimated", "unverified", "fabricated"):
        rs = strata.get(bucket)
        if not rs:
            continue
        s = summarise(grade_trade(r, acting_tp_producer_strategies=[]) for r in rs)
        print(f"\n--- {bucket}: n_input={s['n_input']} n_graded={s['n_graded']} "
              f"grades={s['grade_counts']}")
        if not s["n_graded"]:
            continue
        print(f"    reach_rate        {_fmt(s['reach_rate'])}   "
              f"(share that reached their declared target)")
        print(f"    clamp_bound_rate  {_fmt(s['clamp_bound_rate'])}   "
              f"(share whose target WAS the venue cap {TP_VENUE_CAP_PCT})")
        print(f"    target sits at quantile {_fmt(s['target_quantile_in_exits'])} "
              f"of realised exits")

    print("\n\n=== PER LEG (measured + estimated; both anchor the exit to a real close) ===")
    byleg = collections.defaultdict(list)
    for b in ("measured", "estimated"):
        for r in strata.get(b, []):
            byleg[str(r.get("strategy_name") or "?")].append(r)
    print(f"{'leg':<26} {'n':>4} {'reach':>7} {'clampd':>7} {'medTgt%':>8} {'tQuant':>7}")
    print("-" * 64)
    for leg, rs in sorted(byleg.items(), key=lambda kv: -len(kv[1])):
        s = summarise(grade_trade(r, acting_tp_producer_strategies=[]) for r in rs)
        if s["n_graded"] < min_n:
            continue
        print(f"{leg:<26} {s['n_graded']:>4} {_fmt(s['reach_rate']):>7} "
              f"{_fmt(s['clamp_bound_rate']):>7} "
              f"{_fmt(s['median_target_pct'], 5):>8} "
              f"{_fmt(s['target_quantile_in_exits']):>7}")


def report_mfe(rows: List[Dict[str, Any]], *, min_n: int) -> None:
    g = [r for r in rows if r.get("peak_gradeable")
         and r.get("peak_r") is not None and r.get("cap_r")]
    print(f"POPULATION: position_telemetry rows with peak_gradeable=True and a "
          f"readable peak_r/cap_r = {len(g)} of {len(rows)}")
    lb = sum(1 for r in rows if r.get("peak_r_is_lower_bound"))
    prov = collections.Counter(r.get("peak_provenance") for r in rows)
    print(f"⚠️ peak_r_is_lower_bound on {lb}/{len(rows)}; peak_provenance {dict(prov)}.")
    print("   Every MFE below is therefore a LOWER BOUND on how far the move went —")
    print("   the safe direction for falsifying a too-far target, the unsafe one")
    print("   for justifying a too-near one.\n")
    if not g:
        return

    def pct_of_entry(r):
        return float(r["peak_r"]) / float(r["cap_r"]) * TP_VENUE_CAP_PCT

    grp = collections.defaultdict(list)
    for r in g:
        key = ("crypto (Bybit-traded)" if str(r.get("symbol") or "") in BYBIT_SYMBOLS
               else "non-crypto (touches NO Bybit account)")
        grp[key].append(pct_of_entry(r))

    print("=== THE OPEN QUESTION tp_venue_cap.py NAMES: is 0.099 right off Bybit? ===")
    print(f"{'group':<40} {'n':>4} {'p50%':>7} {'p75%':>7} {'p90%':>7} {'reached cap':>12}")
    print("-" * 78)
    for k, vs in sorted(grp.items()):
        hit = sum(1 for v in vs if v >= TP_VENUE_CAP_PCT)
        print(f"{k:<40} {len(vs):>4} {quantile(vs,.5):>7.4f} {quantile(vs,.75):>7.4f} "
              f"{quantile(vs,.9):>7.4f} {hit:>5}/{len(vs):<3} {hit/len(vs):>.3f}")

    print("\n=== PER LEG ===")
    byleg = collections.defaultdict(list)
    for r in g:
        byleg[str(r.get("strategy") or "?")].append(pct_of_entry(r))
    print(f"{'leg':<26} {'n':>4} {'p50%':>8} {'p75%':>8} {'p90%':>8} {'max%':>8}")
    print("-" * 68)
    for leg, vs in sorted(byleg.items(), key=lambda kv: -len(kv[1])):
        if len(vs) < min_n:
            continue
        print(f"{leg:<26} {len(vs):>4} {quantile(vs,.5):>8.4f} {quantile(vs,.75):>8.4f} "
              f"{quantile(vs,.9):>8.4f} {max(vs):>8.4f}")


def selftest() -> int:
    """Invariants that never change though the population does."""
    # A target exactly at the cap is recognised as clamp-bound; one clearly
    # inside it is not. This is the recogniser the whole --mfe view rests on.
    at_cap = grade_trade({"entry_price": 100, "exit_price": 101,
                          "take_profit_1": 100 * (1 + TP_VENUE_CAP_PCT),
                          "direction": "long"}, acting_tp_producer_strategies=[])
    assert at_cap["clamp_bound"] is True, at_cap
    inside = grade_trade({"entry_price": 100, "exit_price": 101,
                          "take_profit_1": 102, "direction": "long"},
                         acting_tp_producer_strategies=[])
    assert inside["clamp_bound"] is False, inside
    # A short is graded on the same basis with the sign flipped.
    short = grade_trade({"entry_price": 100, "exit_price": 95,
                         "take_profit_1": 90, "direction": "short"},
                        acting_tp_producer_strategies=[])
    assert abs(short["target_pct"] - 0.10) < 1e-9, short
    assert abs(short["exit_pct"] - 0.05) < 1e-9, short
    # "We did not look" never becomes "it did not move".
    unk = grade_trade({"entry_price": 100, "exit_price": 101,
                       "take_profit_1": 102, "direction": "long"})
    assert unk["target_provenance"] == "unknown", unk
    # An empty population yields None, never a rate of 0.0.
    assert summarise([])["reach_rate"] is None
    print("bracket_calibration_report --selftest: OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default=os.environ.get("DIAG_BASE_URL") or DEFAULT_API)
    ap.add_argument("--token", default=os.environ.get("DIAG_READ_TOKEN", ""))
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--min-n", type=int, default=1,
                    help="suppress legs below this n (they are still counted in totals)")
    ap.add_argument("--mfe", action="store_true", help="the MFE view instead of exits")
    ap.add_argument("--trades-file", help="read trades JSON from a file instead of the API")
    ap.add_argument("--telemetry-file", help="read position_telemetry JSON from a file")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    if a.mfe:
        if a.telemetry_file:
            payload = json.load(open(a.telemetry_file))
        else:
            payload = _get(a.api, f"/api/diag/position_telemetry?limit={a.limit}", a.token)
        report_mfe(payload.get("rows") or [], min_n=a.min_n)
    else:
        if a.trades_file:
            rows = json.load(open(a.trades_file))
        else:
            rows = _get(a.api, f"/api/diag/journal?table=trades&limit={a.limit}", a.token)
        report_exits(rows if isinstance(rows, list) else rows.get("rows") or [],
                     min_n=a.min_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
