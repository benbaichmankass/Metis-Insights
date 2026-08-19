#!/usr/bin/env python3
"""Is there a peak worth BANKING — and does cap-proximity identify it?

THE QUESTION THIS EXISTS FOR (operator, 2026-08-18). Giving back unrealised
profit IS an economic loss: at any instant the choice is bank what the position
is worth NOW or hold, and the entry price is a sunk reference point that should
not enter the decision. Treating an unbooked gain as "house money" is mental
accounting. So: can we recognise a peak worth banking rather than squeezing the
last fraction of an R?

WHY THE LEVERS ALREADY SWEPT DO NOT ANSWER IT. `stale_stop` (bars + open_r
floor), `giveback_r` (R surrendered from peak) and `rr_floor` (upside vs
give-back) are all peak-banking mechanisms and all three failed on the crypto
pullback family. But NONE of them conditions on where the trade sits relative
to its OWN structural ceiling. The venue TP clamp fixes that ceiling per fill:

    cap_R = 0.099 * entry / risk

A trade at 76% of cap has 0.9R of headroom left however long it is held; one at
20% has room to run. That is a different question from "how much has it given
back", and it is the one the operator is actually asking. `pct_of_cap` is
already in `position_telemetry` and NO lever reads it.

WHAT THIS MEASURES, AND THE ONE THING IT CANNOT
------------------------------------------------
For every trade that ever REACHED a given fraction X of its cap, it reports the
distribution of the FINAL net R. That is the base rate holding must beat.

⚠️ **It bounds a banking policy, it does not simulate one.** From summary rows
the R at the MOMENT of crossing X is unknown — `mfe_r` is an intrabar extreme,
so the close-basis R when the threshold is crossed is at most `X * cap_R`. So
`X * cap_R` is an UPPER BOUND on what banking-at-X could book. That asymmetry
makes exactly one direction conclusive:

  * upper bound BELOW the hold median  -> banking is REFUTED at that threshold,
    conclusively; it cannot beat holding even at its best case.
  * upper bound ABOVE the hold median  -> banking is NOT established. It is a
    candidate that needs a per-bar simulation to price honestly.

Reporting the second as a win would be the cosmetic-cell mistake in a new
costume, so the verdict column says `refuted` or `candidate`, never `pass`.

Usage
-----
    python3 scripts/research/peak_banking_basis.py trades1.jsonl [trades2.jsonl ...]
    python3 scripts/research/peak_banking_basis.py --self-test
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List

#: The live venue TP clamp — the same constant the reachability audit mirrors
#: from the unit modules' `_TP_SENTINEL_CAP_PCT`.
LIVE_TP_CAP_PCT = 0.099

#: Cap fractions to report. Chosen to bracket the live XRP reading (76%) and to
#: span the range where a ceiling could plausibly bind, NOT tuned to a result.
THRESHOLDS = (0.25, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.90)


def load(paths: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def enrich(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach cap_r and peak_pct_of_cap. Rows we cannot grade are DROPPED and
    counted, never defaulted — a fabricated 0 would pull every percentile."""
    out = []
    for r in rows:
        try:
            entry = float(r["entry"])
            sl = float(r["sl"])
            risk = abs(entry - sl)
            mfe = float(r["mfe_r"])
            net = float(r["net_r"])
        except (KeyError, TypeError, ValueError):
            continue
        if risk <= 0 or entry <= 0:
            continue
        cap_r = LIVE_TP_CAP_PCT * entry / risk
        if cap_r <= 0:
            continue
        out.append({**r, "risk": risk, "cap_r": cap_r,
                    "peak_pct_of_cap": mfe / cap_r,
                    "mfe_r": mfe, "final_r": net})
    return out


def conditional_hit_rate(
    rows: List[Dict[str, Any]], *, reached: float, target_frac: float = 1.0,
) -> Dict[str, Any]:
    """P(reach `target_frac` of cap | already reached `reached` of cap).

    THIS IS THE `observed_p` INPUT `src/runtime/hold_vs_cash.py` REQUIRES and
    refuses to invent. That module computes the hit rate holding *demands*
    (`p* = r_to_stop / (r_to_target + r_to_stop)`) and will not grade a position
    without a MEASURED rate to compare it against. This supplies the measured
    half, from the same population `analyse` already builds — rather than a
    second predicate free to drift from it.

    EXACT, not approximate, for the target side: a take-profit is a resting
    limit and fills on touch, so `mfe_r >= cap_r` is equivalent to the TP having
    been hit. No fill model is involved.

    ⚠️ **THE COMPLEMENT IS NOT "STOPPED OUT AT TODAY'S STOP".** `1 - p` is the
    share that never reached the target; where those trades actually ended
    depends on each one's own trailing stop, which is NOT the stop the live
    position carries now. So this is a defensible estimate of P(target) and NOT
    a simulation of the live bracket. Stated here because the number is about to
    be compared against a breakeven probability, and a reader who takes the
    complement as "P(hit my current stop)" would be reading a different trade.

    Returns `n` beside `p` always: a rate over a handful of trades is not the
    claim a rate over a hundred is, and the caller must be able to see which.
    `p` is `None` — never 0.0 — when the conditioning population is empty.
    """
    graded = enrich(rows)
    pop = [g for g in graded if g["peak_pct_of_cap"] >= reached]
    if not pop:
        return {"reached": reached, "target_frac": target_frac, "n": 0,
                "hits": 0, "p": None, "why": "empty_conditioning_population",
                "rows_graded": len(graded),
                "rows_ungradeable": len(rows) - len(graded)}
    hits = [g for g in pop if g["peak_pct_of_cap"] >= target_frac]
    return {
        "reached": reached, "target_frac": target_frac,
        "n": len(pop), "hits": len(hits), "p": len(hits) / len(pop),
        "rows_graded": len(graded),
        "rows_ungradeable": len(rows) - len(graded),
    }


def analyse(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graded = enrich(rows)
    res: Dict[str, Any] = {"rows_in": len(rows), "rows_graded": len(graded),
                           "rows_ungradeable": len(rows) - len(graded),
                           "thresholds": []}
    if not graded:
        return res
    for x in THRESHOLDS:
        hit = [g for g in graded if g["peak_pct_of_cap"] >= x]
        if not hit:
            res["thresholds"].append({"x": x, "n": 0, "verdict": "no_sample"})
            continue
        finals = sorted(g["final_r"] for g in hit)
        med = statistics.median(finals)
        # Upper bound on banking at this threshold, per trade (X * that trade's
        # own cap_R), then the median across the same population.
        bank_ub = statistics.median(sorted(x * g["cap_r"] for g in hit))
        retained = statistics.median(sorted(
            (g["final_r"] / g["mfe_r"]) for g in hit if g["mfe_r"] > 0))
        res["thresholds"].append({
            "x": x, "n": len(hit),
            "hold_median_final_r": round(med, 3),
            "hold_mean_final_r": round(statistics.fmean(finals), 3),
            "bank_upper_bound_r": round(bank_ub, 3),
            "median_retained_frac": round(retained, 3),
            # Share of this population that finished BELOW the banking upper
            # bound — i.e. how often holding actually cost something. Compared
            # in R against R; an earlier draft divided by bank_ub and compared
            # an R to a cap FRACTION, which was a units error.
            "pct_finishing_below_bank_ub": round(
                100.0 * sum(1 for f in finals if f < bank_ub) / len(finals), 1),
            # P(this population goes on to reach the cap) — the measured
            # `observed_p` hold_vs_cash needs. Exact for the target side (a TP
            # is a limit and fills on touch); see conditional_hit_rate on why
            # its complement is NOT "stopped out at today's stop".
            "p_reaches_cap": round(
                sum(1 for g in hit if g["peak_pct_of_cap"] >= 1.0) / len(hit), 4),
            # Only ONE direction is conclusive — see the module docstring.
            "verdict": "refuted" if bank_ub < med else "candidate",
        })
    return res


def _render(res: Dict[str, Any]) -> None:
    print(f"\ntrades in {res['rows_in']}  graded {res['rows_graded']}  "
          f"ungradeable {res['rows_ungradeable']} (dropped, never defaulted)")
    if not res["thresholds"]:
        print("  no gradeable rows — nothing to say")
        return
    print(f"\n  {'X=%cap':>7} {'n':>5} {'hold med':>9} {'hold mean':>10} "
          f"{'bank UB':>8} {'retained':>9} {'<UB':>8}  verdict")
    for t in res["thresholds"]:
        if t.get("n", 0) == 0:
            print(f"  {t['x']:7.0%} {0:5d}       —          —        —         —        —  no_sample")
            continue
        print(f"  {t['x']:7.0%} {t['n']:5d} {t['hold_median_final_r']:9.3f} "
              f"{t['hold_mean_final_r']:10.3f} {t['bank_upper_bound_r']:8.3f} "
              f"{t['median_retained_frac']:9.3f} {t['pct_finishing_below_bank_ub']:7.1f}%  "
              f"{t['verdict']}")
    print("\n  refuted   = banking at X cannot beat holding even at its UPPER bound")
    print("  candidate = not established; needs a per-bar simulation to price")


def _self_test() -> int:
    """Planted controls — a probe that cannot find a known positive proves nothing."""
    # entry 100, sl 90 -> risk 10, cap_r = 0.099*100/10 = 0.99
    hold_wins = [{"entry": 100, "sl": 90, "mfe_r": 0.9, "net_r": 0.95}] * 20
    bank_wins = [{"entry": 100, "sl": 90, "mfe_r": 0.9, "net_r": 0.05}] * 20
    checks = [
        ("cap_r is 0.099*entry/risk", abs(enrich(hold_wins)[0]["cap_r"] - 0.99) < 1e-9),
        ("positive: banking REFUTED when holds finish high",
         analyse(hold_wins)["thresholds"][0]["verdict"] == "refuted"),
        ("positive: banking a CANDIDATE when holds give it all back",
         analyse(bank_wins)["thresholds"][0]["verdict"] == "candidate"),
        ("ungradeable rows are dropped and counted, not defaulted",
         analyse([{"entry": 0, "sl": 0, "mfe_r": 1, "net_r": 1}])["rows_ungradeable"] == 1),
        # mfe 0.2 of a 0.99 cap = 20% — the 25% bucket and up are empty. The
        # first draft of this control used a fixture that DID reach 90%, so it
        # asserted a state it never produced.
        ("a threshold nobody reaches says no_sample, not zero",
         all(t["verdict"] == "no_sample" for t in analyse(
             [{"entry": 100, "sl": 90, "mfe_r": 0.2, "net_r": 0.1}] * 5)["thresholds"])),
    ]

    # conditional_hit_rate — the `observed_p` supplier for hold_vs_cash.
    # entry 100 / sl 99 -> risk 1 -> cap_r = 9.9, so peak_pct = mfe / 9.9.
    mk = lambda mfe, net: {"entry": 100.0, "sl": 99.0, "mfe_r": mfe, "net_r": net}
    pop = [mk(9.9, 9.9), mk(9.9, 5.0), mk(8.0, 4.0), mk(8.0, 2.0), mk(1.0, -1.0)]
    c75 = conditional_hit_rate(pop, reached=0.75)     # >= 7.425R -> four rows
    c99 = conditional_hit_rate(pop, reached=0.99)     # >= 9.801R -> the two caps
    cnone = conditional_hit_rate(pop, reached=5.0)    # nothing reaches 5x cap
    czero = conditional_hit_rate(
        pop + [{"entry": 100.0, "sl": 100.0, "mfe_r": 9.9, "net_r": 1.0}],
        reached=0.75)
    checks += [
        ("conditional population is the reached-X set", c75["n"] == 4),
        ("conditional hits are the reached-cap subset", c75["hits"] == 2),
        ("conditional p is hits/n", c75["p"] == 0.5),
        ("tighter conditioning shrinks the population", c99["n"] == 2),
        ("...and a population already at cap is all hits", c99["p"] == 1.0),
        ("empty population -> p is None, NEVER 0.0", cnone["p"] is None),
        ("empty population states why", cnone["why"] == "empty_conditioning_population"),
        ("a zero-risk row is dropped, not graded", czero["n"] == 4),
        ("...and its exclusion is COUNTED, not silent", czero["rows_ungradeable"] == 1),
        ("n rides with p so a thin rate cannot read as a thick one",
         "n" in c75 and "n" in cnone),
    ]

    ok = 0
    for label, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {label}")
        ok += bool(passed)
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.paths:
        ap.error("give at least one emit_path JSONL (or --self-test)")
    res = analyse(load(a.paths))
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        _render(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
