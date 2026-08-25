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

import sys

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The live venue TP clamp — the same constant the reachability audit mirrors
#: from the unit modules' `_TP_SENTINEL_CAP_PCT`.
# The venue TP clamp -- ONE owner: src/runtime/tp_venue_cap.py. IMPORTED, not
# mirrored. This file used to carry its own `LIVE_TP_CAP_PCT = 0.099`, one of
# thirteen such literals with nothing binding them -- and this repo's own note
# on that was right: "if the live constant moves, this silently keeps measuring
# the OLD book, and the sweep will look correct while doing it". The owner
# imports only `typing`, and src/__init__.py + src/runtime/__init__.py are
# empty, so this adds no heavy dependency.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runtime.tp_venue_cap import (  # noqa: E402
    TP_VENUE_CAP_PCT as LIVE_TP_CAP_PCT)

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


#: Exit reasons that mean the take-profit filled. The harness records this
#: directly, so it is EVIDENCE rather than a reconstruction.
_TP_EXITS = frozenset({"take_profit"})


def _tp_hit(row: Dict[str, Any]) -> Optional[bool]:
    """Did this trade's take-profit fill? `None` when the row does not say."""
    why = row.get("exit_reason")
    return None if why is None else str(why).strip().lower() in _TP_EXITS


def conditional_hit_rate(
    rows: List[Dict[str, Any]], *, reached: float, target_frac: float = 1.0,
) -> Dict[str, Any]:
    """P(the take-profit fills | the trade already reached `reached` of cap).

    THIS IS THE `observed_p` INPUT `src/runtime/hold_vs_cash.py` REQUIRES and
    refuses to invent. That module computes the hit rate holding *demands*
    (`p* = r_to_stop / (r_to_target + r_to_stop)`) and will not grade a position
    without a MEASURED rate to compare against. This supplies the measured half,
    over the population `analyse` already builds.

    THE HIT IS READ FROM `exit_reason`, NOT RECONSTRUCTED FROM `mfe_r`
    -----------------------------------------------------------------
    The first version of this function asserted that a take-profit is a resting
    limit filling on touch, so `mfe_r >= cap_r` *is* the TP having been hit, and
    called that exact rather than approximate. **Measured 2026-08-19 on the
    284-trade xrp_pullback_2h corpus, it is false**: the harness's `mfe_r`
    excludes the fill bar, so on `take_profit` rows `net_r ~ cap_r` while
    `mfe_r` sits BELOW it (entry 1.083: cap_r 3.290, net_r 3.235, mfe_r 2.783).
    The proxy was therefore UNSATISFIABLE, and the function returned `p: 0.0`
    at every conditioning level over a corpus containing 85 take-profits.

    That failure is the dangerous shape, not a harmless one: `p: 0.0` is a
    confident number, it is indistinguishable from a real measurement, and fed
    to `hold_vs_cash` it would have said LIQUIDATE on every position with
    maximum edge. "My predicate cannot express this" had been rendered as "the
    market never does this".

    `hit_basis` states which evidence decided, and is never collapsed:
      ``exit_reason`` — the harness recorded the exit; authoritative.
      ``mfe_proxy``   — no `exit_reason` on these rows, so the mfe rule stood
                        in. Carries `proxy_warning`, because the failure above
                        is exactly what this basis is vulnerable to.
      ``ungradeable`` — neither available; `p` is None.

    **`proxy_agreement` is the guard that would have caught it.** When rows
    carry both, it reports the share of `exit_reason` hits the mfe proxy also
    finds. Near 0 with a non-zero numerator means the proxy is broken for this
    corpus and its `p` must not be used. A disagreement measure between two
    ways of computing the same thing is cheap; discovering the mismatch from a
    downstream verdict is not.

    `n` always rides with `p` — a rate over a handful is not the claim a rate
    over a hundred is — and an empty conditioning population returns `p: None`,
    never 0.0.
    """
    graded = enrich(rows)
    pop = [g for g in graded if g["peak_pct_of_cap"] >= reached]
    base = {"reached": reached, "target_frac": target_frac,
            "rows_graded": len(graded),
            "rows_ungradeable": len(rows) - len(graded)}
    if not pop:
        return {**base, "n": 0, "hits": 0, "p": None,
                "hit_basis": "ungradeable", "why": "empty_conditioning_population"}

    by_reason = [_tp_hit(g) for g in pop]
    have_reason = [b for b in by_reason if b is not None]
    proxy = [g["peak_pct_of_cap"] >= target_frac for g in pop]

    # Cross-check the two bases wherever both exist, whichever we end up using.
    agreement = None
    if have_reason:
        tp_idx = [i for i, b in enumerate(by_reason) if b]
        if tp_idx:
            agreement = round(
                sum(1 for i in tp_idx if proxy[i]) / len(tp_idx), 4)

    if len(have_reason) == len(pop):
        hits = sum(1 for b in by_reason if b)
        out = {**base, "n": len(pop), "hits": hits, "p": hits / len(pop),
               "hit_basis": "exit_reason", "proxy_agreement": agreement}
        if agreement is not None and agreement < 0.5:
            out["proxy_note"] = (
                f"the mfe>=cap proxy finds only {agreement:.0%} of the exits "
                "this corpus RECORDS as take_profit — the proxy is unusable "
                "here; exit_reason was used")
        return out

    hits = sum(1 for b in proxy if b)
    return {**base, "n": len(pop), "hits": hits, "p": hits / len(pop),
            "hit_basis": "mfe_proxy", "proxy_agreement": agreement,
            "proxy_warning": (
                "no exit_reason on these rows; the mfe>=cap rule stood in. It "
                "is UNSATISFIABLE on any harness whose mfe_r excludes the fill "
                "bar, which returns a confident p=0.0 — check p against the "
                "corpus's own take-profit count before using it")}


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
    def mk(mfe, net, why=None):
        r = {"entry": 100.0, "sl": 99.0, "mfe_r": mfe, "net_r": net}
        if why is not None:
            r["exit_reason"] = why
        return r

    # No exit_reason anywhere -> the proxy basis, with its warning.
    pop = [mk(9.9, 9.9), mk(9.9, 5.0), mk(8.0, 4.0), mk(8.0, 2.0), mk(1.0, -1.0)]
    c75 = conditional_hit_rate(pop, reached=0.75)     # >= 7.425R -> four rows
    c99 = conditional_hit_rate(pop, reached=0.99)     # >= 9.801R -> the two caps
    cnone = conditional_hit_rate(pop, reached=5.0)    # nothing reaches 5x cap
    czero = conditional_hit_rate(
        pop + [{"entry": 100.0, "sl": 100.0, "mfe_r": 9.9, "net_r": 1.0}],
        reached=0.75)

    # THE REGRESSION CONTROL. Reproduces the live shape measured 2026-08-19:
    # mfe_r excludes the fill bar, so every take_profit row sits BELOW cap and
    # the proxy is unsatisfiable. exit_reason must win, and the disagreement
    # must be reported rather than silently producing a confident p=0.0.
    fill_bar = [mk(9.0, 9.85, "take_profit"), mk(9.2, 9.9, "take_profit"),
                mk(8.5, 3.0, "trail_stop"), mk(8.1, -1.0, "stop")]
    creal = conditional_hit_rate(fill_bar, reached=0.75)
    proxy_only = conditional_hit_rate(
        [{k: v for k, v in r.items() if k != "exit_reason"} for r in fill_bar],
        reached=0.75)

    checks += [
        ("conditional population is the reached-X set", c75["n"] == 4),
        ("conditional p is hits/n", c75["p"] == 0.5),
        ("tighter conditioning shrinks the population", c99["n"] == 2),
        ("empty population -> p is None, NEVER 0.0", cnone["p"] is None),
        ("empty population states why", cnone["why"] == "empty_conditioning_population"),
        ("a zero-risk row is dropped, not graded", czero["n"] == 4),
        ("...and its exclusion is COUNTED, not silent", czero["rows_ungradeable"] == 1),
        ("no exit_reason -> the proxy basis", c75["hit_basis"] == "mfe_proxy"),
        ("...and the proxy carries its warning", "proxy_warning" in c75),
        # The regression the live corpus actually produced.
        ("exit_reason wins when present", creal["hit_basis"] == "exit_reason"),
        ("...and finds the take-profits the proxy cannot", creal["hits"] == 2),
        ("...and p is NOT the fabricated 0.0", creal["p"] == 0.5),
        ("the proxy disagreement is MEASURED", creal["proxy_agreement"] == 0.0),
        ("...and called out in words", "unusable" in creal.get("proxy_note", "")),
        ("the same rows WITHOUT exit_reason reproduce the p=0.0 defect",
         proxy_only["p"] == 0.0 and proxy_only["hit_basis"] == "mfe_proxy"),
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
