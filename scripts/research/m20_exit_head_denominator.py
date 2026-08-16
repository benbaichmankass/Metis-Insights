#!/usr/bin/env python3
"""Give the ETH exit_head_ml hypothesis a denominator — and test its rival.

WHY THIS EXISTS. Two ETH legs passed the exit-head E1 gate and a symbol
hypothesis got attached to that ("the head works on ETH"). Operator decision
(a), 2026-08-14, was to test it against a real denominator instead of two
positives. This script is that test, run against the committed coverage matrix
so the numbers are reproducible rather than a one-off shell computation.

THE ANSWER, IN ORDER, BECAUSE THE FIRST TWO ATTEMPTS WERE BOTH WRONG.

  1. The SYMBOL story ("the head works on ETH") does not survive a denominator.
  2. Nor does the BOOK-SIZE story that replaced it, and that one was mine.

On the 20 mixed-geometry cells that state an `n_oos`, a single `n_oos >= 350`
split classified the E1 verdict at 90.0% against 80.0% for the symbol — so this
file originally concluded book size was the better explanation. A matched 2h
live-parity round was then run as genuine HELD-OUT data for that split (the
threshold having been chosen on the very sample it was scored on). It scores
**1 of 7 = 14.3%** there: the two largest books FAIL and the smallest pass sits
below the largest failure. The 4h stratum refutes it a SECOND time and independently: the smallest book in that set (n_oos 123, XRP) PASSES while the
largest (188, AVAX) fails. **Book size is refuted.** The 90.0% was an in-sample
artifact, which is what the caveat below always said it might be — that caveat
is the only part of the original reading that survived.

WHERE ETH LANDS — and a third stratum killed it. Over the first two strata
(n=12) ETH was 4/4 vs non-ETH 2/8, Fisher one-sided p = 0.0303, and that was
reported as arguably significant. The 4h donchian round (relays #9288/#9294)
then added five INDEPENDENT rows — that family has no `_prop` siblings — and
**ETH FAILED there** (`trend_donchian_eth_4h`, beats_hard 9/16 against an 11
bar) while ADA and XRP passed. Updated:

    n=12, prop siblings counted   ETH 4/4  vs 2/8    p = 0.0303
    n=17, all three strata        ETH 4/5  vs 4/12   p = 0.1109
    n=14, prop siblings dropped   ETH 2/3  vs 4/11   p = 0.3846   <- the honest one

So the one arguably-significant reading was an artifact of a small sample plus
double-counted prop legs, and it did not survive a third family. Both prop
figures are still printed rather than picked, because the choice is what moved
the number and hiding it would hide the reason.

WHAT IS ACTUALLY BINDING — and it is now named as unexplained rather than
misattributed. The E1 gate is a FOLD-COUNT: candidate requires
`mean_auc > 0.55` AND `beats_actual*3 >= u*2` AND `beats_hard*3 >= u*2`, i.e.
at least two-thirds of folds on the right side (`train_exit_head.py`). All
three 2h negatives fail on `beats_hard` while two of them PASS `beats_actual`,
and `sol_pullback_2h` carries that stratum's second-highest AUC and still
fails. So the separating axis is fold consistency against the CHEAP LEVER —
not the head's discrimination, and not book size. What drives it is open.

(The power-test intuition that motivated the book-size hypothesis — thin book,
fewer chances to land two-thirds of folds — remains mechanically plausible and
is simply not what separates these legs. It is recorded here as a rejected
explanation, not a standing one.)

READ THE POPULATION, NOT THE PERCENTAGE. Three limits, all load-bearing:

  1. 20 of 36 resolved cells state an `n_oos`; 16 are excluded for stating
     none. That is the denominator, and it is not the whole column.
  2. The 20 MIX GEOMETRIES. Only 5 cells are confirmed live-parity
     (tp_cap_pct 0.099); the rest do not say, and the scalp family's grading is
     recorded elsewhere as having used a NO-take-profit book. Pooling across
     geometries is the exact trap operator decision (d) exists to avoid, so the
     pooled figures here are a BOUND on what the record can say, not a result.
  3. `n_oos` is not independent of the gate — see above. That the two correlate
     is therefore partly definitional, which strengthens the "power test"
     reading and weakens any causal claim about book size per se.

The single-geometry strata are printed separately, from the committed rounds
file rather than from prose. The 1h one (relays #9206 main, #9156-58 prop) is
ETH 2/2 and non-ETH 0/3 (p = 0.10, n = 5 — underpowered by construction), and
note within it that SOL's AUC (0.6161) EXCEEDS both ETH legs': whatever
separates them is not the head's discrimination.

A FOURTH SECTION sizes the remaining work rather than the ETH question, and is
the more consequential of the two: **19 of the 29 negative cells are not known
to have been measured at live parity at all.** Every exit-head round before
2026-08-14 was built on a book with NO take-profit (the driver could not pass
the cap — `BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP`), so a negative
graded then says "the head did not help on a book the bot does not trade",
which is a different claim. Of the 10 recorded negatives re-measured so far, 4
did not survive. That rate is reported beside its denominator and its scope and
is deliberately NOT projected onto the 19 — it sizes the work, not the outcome.

Usage:  python3 scripts/research/m20_exit_head_denominator.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from math import comb
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
# The committed exit-head evidence file. It exists because this lever had NONE:
# rounds run on the trainer into an ephemeral `--out` and nothing came back, so
# every disposition was parsed out of prose in the matrix refs
# (BL-20260814-CORPUS-AGREEMENT-COUNTS-141-UNCHECKABLE-CELLS-AS-CHECKED item 3).
# Rows here carry their own geometry stamp, so a comparison across them is
# comparable BY CONSTRUCTION rather than by hoping the refs agree.
ROUNDS = REPO / "docs" / "research" / "m20-exit-head-rounds.jsonl"

# A cell whose DECIDING measurement cleared the gate. `shipped_gate_failed` is
# deliberately a FAIL here and that is not a quibble: the legend defines it as
# "LIVE in config, but a LATER re-sweep failed its gate", so for the question
# "does the head validate on this leg?" it is the re-sweep that answers, not the
# fact that the lever is still wired. Counting it as a pass — which a first pass
# at this analysis did — moves BTC and SOL into the ETH column's comparison set
# and destroys the very contrast being measured.
PASS_STATUSES = frozenset({"shipped", "passed_unshipped"})
FAIL_STATUSES = frozenset({"honest_negative", "shipped_gate_failed"})

N_OOS = re.compile(r"n_oos[=: ]+(\d+)")
GATE = re.compile(
    r"n_oos=(\d+),?\s*auc=([\d.]+),?\s*beats_actual=(\d+)/(\d+),?\s*beats_hard=(\d+)/(\d+)"
)
# The threshold is REPORTED, not tuned-then-quoted: it is chosen below as the
# best single split on this same 20-cell sample, so it is an upper bound on how
# well book size would predict out of sample. Stating that is the difference
# between a finding and an overfit.
BOOK_SPLIT = 350

LIVE_PARITY_1H = [
    "trend_donchian_eth", "trend_donchian_eth_prop",
    "trend_donchian", "trend_donchian_sol", "trend_donchian_sol_prop",
]


def fisher_one_sided(a: int, b: int, c: int, d: int) -> float:
    """P(as extreme or more, in the a-enriched direction). a+b and c+d are the groups."""
    n = a + b + c + d
    k = a + c
    tot = 0.0
    for x in range(0, min(a + b, k) + 1):
        y, z = (a + b) - x, k - x
        w = (c + d) - z
        if y < 0 or z < 0 or w < 0:
            continue
        if x >= a:
            tot += comb(a + b, x) * comb(c + d, z) / comb(n, k)
    return tot


def load_cells(matrix: dict) -> list[dict]:
    out = []
    for row in matrix.get("rows", []):
        cell = row.get("exit_head_ml") or {}
        status = (cell.get("status") or "").split(":")[0]
        if status not in PASS_STATUSES | FAIL_STATUSES:
            continue
        ref = (cell.get("ref") or "").replace("\n", " ")
        hits = N_OOS.findall(ref)
        out.append({
            "leg": row["strategy"],
            "symbol": (row.get("symbol") or "").upper(),
            "tf": row.get("tf"),
            "eth": "ETH" in (row.get("symbol") or "").upper(),
            "n_oos": int(hits[0]) if hits else None,
            "ok": status in PASS_STATUSES,
            "status": status,
            "ref": ref,
        })
    return out


def main() -> int:
    matrix = json.loads(MATRIX.read_text())
    cells = load_cells(matrix)
    sized = [c for c in cells if c["n_oos"] is not None]

    print("=" * 78)
    print("M20 — does exit_head_ml prefer ETH, or does it prefer a BIG BOOK?")
    print("=" * 78)
    clean = [c for c in sized if c["leg"] in set(LIVE_PARITY_1H)]
    print(f"resolved exit_head_ml cells      : {len(cells)}")
    print(f"  of which state an n_oos        : {len(sized)}   <- THE DENOMINATOR")
    print(f"  excluded for stating none      : {len(cells) - len(sized)}")
    print(f"  confirmed one-geometry (live parity): {len(clean)} of {len(sized)}; "
          f"the other {len(sized) - len(clean)} MIX geometries")
    if not sized:
        print("\nno cell states an n_oos — nothing to test", file=sys.stderr)
        return 1

    def acc(pred) -> float:
        return sum(1 for c in sized if pred(c) == c["ok"]) / len(sized)

    eth = [c for c in sized if c["eth"]]
    oth = [c for c in sized if not c["eth"]]
    big = [c for c in sized if c["n_oos"] >= BOOK_SPLIT]
    small = [c for c in sized if c["n_oos"] < BOOK_SPLIT]

    print("\n--- the two hypotheses, same 20 cells ---")
    print(f"  is ETH          accuracy {100*acc(lambda c: c['eth']):5.1f}%   "
          f"ETH {sum(c['ok'] for c in eth)}/{len(eth)} pass, "
          f"non-ETH {sum(c['ok'] for c in oth)}/{len(oth)} pass")
    print(f"  n_oos >= {BOOK_SPLIT}    accuracy {100*acc(lambda c: c['n_oos'] >= BOOK_SPLIT):5.1f}%   "
          f"big {sum(c['ok'] for c in big)}/{len(big)} pass, "
          f"small {sum(c['ok'] for c in small)}/{len(small)} pass")

    print("\n--- does symbol survive holding book size fixed? ---")
    for label, grp in (("n_oos >= %d" % BOOK_SPLIT, big), ("n_oos <  %d" % BOOK_SPLIT, small)):
        e = [c for c in grp if c["eth"]]
        o = [c for c in grp if not c["eth"]]
        print(f"  {label:>14}   ETH {sum(c['ok'] for c in e)}/{len(e)}   "
              f"non-ETH {sum(c['ok'] for c in o)}/{len(o)}")
    print("  => in the large-book stratum non-ETH passes 4 of 5; the symbol adds nothing.")
    print("     What is left of the ETH effect is the single small-book pass below.")

    print("\n--- the two exceptions, both worth more than the averages ---")
    for c in sized:
        if c["n_oos"] >= BOOK_SPLIT and not c["ok"]:
            print(f"  largest book that FAILS   : {c['leg']:<26} n_oos={c['n_oos']} "
                  f"({c['symbol']}) — size is necessary, not sufficient")
        if c["n_oos"] < BOOK_SPLIT and c["ok"]:
            print(f"  only small book that PASSES: {c['leg']:<25} n_oos={c['n_oos']} "
                  f"({c['symbol']}) — the whole residual ETH claim, n=1")

    p = fisher_one_sided(
        sum(1 for c in small if c["eth"] and c["ok"]),
        sum(1 for c in small if c["eth"] and not c["ok"]),
        sum(1 for c in small if not c["eth"] and c["ok"]),
        sum(1 for c in small if not c["eth"] and not c["ok"]),
    )
    print(f"  that residual, tested          : Fisher one-sided p = {p:.4f} (n={len(small)})")

    P = [c["n_oos"] for c in sized if c["ok"]]
    F = [c["n_oos"] for c in sized if not c["ok"]]
    print(f"\n  PASS median n_oos {statistics.median(P):6.0f}  (range {min(P)}-{max(P)}, n={len(P)})")
    print(f"  FAIL median n_oos {statistics.median(F):6.0f}  (range {min(F)}-{max(F)}, n={len(F)})")

    print(f"\n--- single-geometry stratum: trend_donchian 1h at LIVE PARITY "
          f"({len(clean)} of {len(sized)} sized cells) ---")
    print("    (relays #9206 main / #9156-58 prop — one geometry, one strategy, one window)")
    lp = []
    for name in LIVE_PARITY_1H:
        row = next((r for r in matrix["rows"] if r["strategy"] == name), None)
        if row is None:
            continue
        ref = ((row.get("exit_head_ml") or {}).get("ref") or "").replace("\n", " ")
        hit = GATE.search(ref)
        if not hit:
            print(f"    {name:<28} gate numbers not parseable from ref — SKIPPED, not assumed")
            continue
        n, auc, ba, u, bh, u2 = hit.groups()
        ba, u, bh = int(ba), int(u), int(bh)
        if u != int(u2):
            print(f"    {name}: fold denominators disagree ({u} vs {u2}) — skipped")
            continue
        ok = float(auc) > 0.55 and ba * 3 >= u * 2 and bh * 3 >= u * 2
        lp.append({"leg": name, "eth": "ETH" in (row.get("symbol") or "").upper(),
                   "auc": float(auc), "n": int(n), "ok": ok})
        print(f"    {name:<28} {row.get('symbol'):<9} n_oos={n:<4} auc={auc} "
              f"beats {ba}/{u} & {bh}/{u} -> {'candidate' if ok else 'honest_negative'}")
    if lp:
        e = [c for c in lp if c["eth"]]
        o = [c for c in lp if not c["eth"]]
        p2 = fisher_one_sided(sum(c["ok"] for c in e), len(e) - sum(c["ok"] for c in e),
                              sum(c["ok"] for c in o), len(o) - sum(c["ok"] for c in o))
        print(f"\n    ETH {sum(c['ok'] for c in e)}/{len(e)}   non-ETH {sum(c['ok'] for c in o)}/{len(o)}"
              f"   Fisher one-sided p = {p2:.4f} (n={len(lp)})")
        best = max(lp, key=lambda c: c["auc"])
        if not best["ok"]:
            print(f"    NOTE: the HIGHEST auc in the stratum ({best['leg']}, {best['auc']:.4f}) FAILS. "
                  f"Whatever separates these legs is not the head's discrimination —")
            print("          it is FOLD CONSISTENCY (beats_actual / beats_hard). An earlier version")
            print("          of this line called that 'the book-size axis again'; the held-out 2h")
            print("          stratum below REFUTES that reading, so fold consistency is the axis")
            print("          and what drives it is still unexplained.")
    _report_live_parity_rounds()
    _report_negative_column_vintage(cells)
    print()
    return 0


def _load_graded_rounds() -> list[dict]:
    """Rounds that GRADE a leg — `--fold-offset` dispersion arms excluded, loudly.

    Both consumers of this file assume one row per leg-measurement:
    `_report_negative_column_vintage` keys a dict on `leg` (so the LAST row for a
    leg silently wins), and `_report_live_parity_rounds` pools every row into a
    per-geometry flip rate (so repeats of one leg inflate its denominator). A
    dispersion ARM is the same leg re-measured on a shifted fold boundary — six
    legs × five offsets is thirty rows that look exactly like thirty graded
    rounds. Appending them here would change which measurement each leg is judged
    against, and nothing would say so.

    The schema has carried `fold_offset` since the flag shipped, precisely so the
    two are distinguishable — and until 2026-08-15 **no consumer read it**. A
    field written and never read is the shape that lets a contaminating row in
    unnoticed (`provenance-consumer-guard` exists for exactly this). This is the
    read. Arms belong in `docs/research/m20-fold-dispersion-arms.jsonl`.

    `null` and `0` both mean baseline — `null` is a round predating the flag, and
    conflating the two is safe here because neither shifts a boundary.
    """
    rows = [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]
    arms = [r for r in rows if r.get("fold_offset")]
    if arms:
        where = sorted({(r.get("leg"), r.get("fold_offset")) for r in arms})
        print(f"\n!! {len(arms)} row(s) in {ROUNDS.name} carry a NON-ZERO "
              f"`fold_offset` and are EXCLUDED from every count below:")
        print(f"   {where}")
        print("   These are dispersion arms, not graded rounds — see "
              "docs/research/m20-fold-dispersion-arms.jsonl. Counting them would "
              "overwrite the leg-keyed lookup and inflate the pooled denominator.")
    return [r for r in rows if not r.get("fold_offset")]


def _report_negative_column_vintage(cells: list[dict]) -> None:
    """How much of the NEGATIVE column rests on a geometry production may not run?

    Separate question from the ETH one, and the more consequential of the two.
    Every exit-head round before 2026-08-14 was built on a book with NO
    take-profit — the driver could not pass the cap
    (`BL-20260814-EXIT-HEAD-ROUNDS-CANNOT-MODEL-LIVE-TP`) — so a negative graded
    then says "the head did not help on a book the bot does not trade", which is
    not the same claim as "the head does not help".

    This sizes the WORK, deliberately not the outcome: the observed flip rate is
    reported beside its denominator and its scope, and is NOT projected onto the
    unmeasured cells.
    """
    if not ROUNDS.is_file():
        return
    rounds = {r["leg"]: r for r in _load_graded_rounds()}
    lp = re.compile(r"live[ _-]parity|tp_cap_pct\s*=?\s*0\.099|live capped TP", re.I)
    negatives = [c for c in cells if c["status"] in FAIL_STATUSES]
    measured = [c for c in negatives
                if c["leg"] in rounds or lp.search(c["ref"])]
    unmeasured = [c for c in negatives if c not in measured]

    print(f"\n=== VINTAGE OF THE NEGATIVE COLUMN ({len(negatives)} negative cells) ===")
    print(f"  measured at live parity            : {len(measured)}")
    print(f"  NOT known to be live parity        : {len(unmeasured)}  "
          f"<- graded on a geometry production may not run")
    if unmeasured:
        by_tf: dict[str, int] = {}
        for c in unmeasured:
            tf = c.get("tf") or "?"
            by_tf[tf] = by_tf.get(tf, 0) + 1
        print(f"    by timeframe: {dict(sorted(by_tf.items()))}")

    # AND THE OTHER DIRECTION, which is the one nobody had tested. Every
    # re-measurement up to 2026-08-14 was of a NEGATIVE, so the only observed
    # risk was "a negative might really be a pass". A recorded POSITIVE resting
    # on the wrong geometry is the more expensive error: a passed_unshipped cell
    # is what would justify shipping a lever onto a live leg. The 15m scalp
    # round tested it and `ict_scalp_eth_15m` -- recorded `passed_unshipped` --
    # came back `honest_negative`. So the re-measurement risk runs both ways and
    # is no longer hypothetical.
    positives = [c for c in cells if c["status"] in PASS_STATUSES]
    pos_rechecked = [c for c in positives if c["leg"] in rounds]
    if pos_rechecked:
        lost = [c["leg"] for c in pos_rechecked
                if rounds[c["leg"]]["verdict"] != "candidate"]
        kept = [c["leg"] for c in pos_rechecked
                if rounds[c["leg"]]["verdict"] == "candidate"]
        print(f"\n  recorded POSITIVES re-measured so far: {len(pos_rechecked)} "
              f"of {len(positives)}")
        print(f"    did NOT survive (now negative)    : {len(lost)} {lost}")
        print(f"    reproduced                        : {len(kept)} {kept}")
        print(f"    {len(positives) - len(pos_rechecked)} recorded positive(s) "
              f"have NEVER been re-measured -- and a positive is what would "
              f"justify shipping a lever onto a live leg.")

    # Of the recorded negatives that HAVE been re-measured, how many survived?
    rechecked = [c for c in negatives if c["leg"] in rounds]
    if rechecked:
        flipped = [c["leg"] for c in rechecked
                   if rounds[c["leg"]]["verdict"] == "candidate"]
        held = [c["leg"] for c in rechecked
                if rounds[c["leg"]]["verdict"] != "candidate"]
        n = len(flipped) + len(held)
        print(f"\n  recorded negatives re-measured so far: {n}")
        print(f"    did NOT survive (now candidate)   : {len(flipped)} {flipped}")
        print(f"    reproduced                        : {len(held)} {held}")
        if n:
            print(f"    observed flip rate {len(flipped)}/{n} = {100*len(flipped)/n:.0f}%")
        fams = sorted({rounds[c["leg"]].get("family") or "?" for c in rechecked})
        tfs = sorted({rounds[c["leg"]].get("tf") or "?" for c in rechecked})
        print(f"  ⚠️  That rate is NOT projected onto the {len(unmeasured)} unmeasured cells: "
              f"n={n}, drawn from")
        print(f"     {len(fams)} strategy famil{'y' if len(fams)==1 else 'ies'} {fams} on "
              f"{len(tfs)} timeframe(s) {tfs}. It sizes the work, not the outcome.")


def _report_live_parity_rounds() -> None:
    """The committed single-geometry evidence — and the out-of-sample verdict.

    Everything above this line is parsed from matrix PROSE across mixed
    geometries. This block reads measured rows that each carry
    `tp_geometry: live_parity`, so the comparison is comparable by construction.
    It also contains the honest test of this file's own headline: the
    `n_oos >= 350` split was fitted on the 20 mixed-geometry cells, and the 2h
    stratum is held-out data for it.
    """
    if not ROUNDS.is_file():
        print(f"\n[live-parity rounds] {ROUNDS.name} absent — NOT a clean result, "
              f"simply unmeasured here.")
        return
    all_rows = _load_graded_rounds()

    # TWO GEOMETRIES LIVE IN THIS FILE AND THEY DO NOT POOL.
    #
    # `live_parity_capped` (donchian/pullback: the live unit clamps TP at 9.9%,
    # and the round applied it) and `live_parity_uncapped` (scalp: the live unit
    # carries NO clamp, so an uncapped book is parity for it). Both are "live
    # parity" for their own family and they are NOT the same book — pooling them
    # would reintroduce, one level up, the exact cross-geometry comparison this
    # whole file exists to avoid.
    #
    # An earlier version asserted a single geometry here. That assert FIRED the
    # moment the first scalp rows landed, which is the behaviour wanted: it
    # refused to average two things rather than quietly doing it. Stratifying is
    # the fix; loosening the assert to `in {...}` would have been the bug.
    known = {"live_parity", "live_parity_capped", "live_parity_uncapped"}
    unknown = sorted({r.get("tp_geometry") for r in all_rows} - known)
    assert not unknown, f"unrecognised tp_geometry in the rounds file: {unknown}"
    strata = {}
    for r in all_rows:
        # `live_parity` is the pre-split label; every row carrying it is a
        # capped family (donchian/pullback), verified when written.
        g = "live_parity_capped" if r["tp_geometry"] == "live_parity" else r["tp_geometry"]
        strata.setdefault(g, []).append(r)

    for geom in sorted(strata):
        rows = strata[geom]
        print(f"\n=== COMMITTED ROUNDS — {geom} ({len(rows)} rows, "
              f"{len({r['tf'] for r in rows})} strata) ===")
        _report_one_geometry(rows, geom)
    return


def _report_one_geometry(rows: list[dict], geom: str) -> None:

    def split(sub, label):
        e = [r for r in sub if "ETH" in r["symbol"]]
        o = [r for r in sub if "ETH" not in r["symbol"]]
        if not e or not o:
            return
        ep = sum(r["verdict"] == "candidate" for r in e)
        op = sum(r["verdict"] == "candidate" for r in o)
        p = fisher_one_sided(ep, len(e) - ep, op, len(o) - op)
        print(f"  {label:<50} ETH {ep}/{len(e)}  non-ETH {op}/{len(o)}  p={p:.4f}")

    for tf in sorted({r["tf"] for r in rows}):
        split([r for r in rows if r["tf"] == tf], f"{tf} stratum alone (n={sum(r['tf']==tf for r in rows)})")
    n_str = len({r["tf"] for r in rows})
    if n_str > 1:
        split(rows, f"POOLED all {n_str} strata (n={len(rows)})")
    # A prop leg shares its symbol, strategy family and much of its book with the
    # main leg beside it, so counting both doubles a single observation. Whether
    # the pooled result is significant turns ENTIRELY on this, so it is reported
    # rather than chosen silently.
    main = [r for r in rows if not r.get("prop_sibling")]
    if len(main) != len(rows):
        split(main, f"POOLED, prop siblings dropped as non-independent (n={len(main)})")

    held = [r for r in rows if r["tf"] == "2h"]
    if held:
        P = sorted(r["n_oos"] for r in held if r["verdict"] == "candidate")
        F = sorted(r["n_oos"] for r in held if r["verdict"] != "candidate")
        hit = sum((r["n_oos"] >= BOOK_SPLIT) == (r["verdict"] == "candidate") for r in held)
        # `geom` qualifies the claim: the split was fitted on cells that were
        # overwhelmingly capped-family, so this is a held-out test WITHIN that
        # geometry. Saying so keeps the result from being read as a statement
        # about every book the fleet runs.
        print(f"\n  OUT-OF-SAMPLE TEST of the fitted n_oos >= {BOOK_SPLIT} split, "
              f"on the 2h stratum ({geom}):")
        print(f"    pass n_oos {P}   fail n_oos {F}")
        if P and F and min(P) < max(F):
            print(f"    the largest books FAIL and the smallest pass ({min(P)}) is below the "
                  f"largest fail ({max(F)}) — the split does not order this stratum")
        print(f"    accuracy {hit}/{len(held)} = {100*hit/len(held):.1f}%  (it was 90.0% in-sample)")
        print("    => BOOK SIZE IS REFUTED as the explanation; it was an in-sample artifact,")
        print("       which is exactly what this file warned the 90.0% could be.")


if __name__ == "__main__":
    raise SystemExit(main())
