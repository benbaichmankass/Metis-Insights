#!/usr/bin/env python3
"""Give the ETH exit_head_ml hypothesis a denominator — and test its rival.

WHY THIS EXISTS. Two ETH legs passed the exit-head E1 gate and a symbol
hypothesis got attached to that ("the head works on ETH"). Operator decision
(a), 2026-08-14, was to test it against a real denominator instead of two
positives. This script is that test, run against the committed coverage matrix
so the numbers are reproducible rather than a one-off shell computation.

WHAT IT FOUND, and it inverts the hypothesis. Across the 20 resolved cells
whose ref states an `n_oos`, a single threshold on OOS BOOK SIZE predicts the
E1 verdict better than the symbol does (90.0% vs 80.0%), and once book size is
held fixed the symbol adds essentially nothing: in the large-book stratum ETH
is 2/2 and non-ETH is 4/5. Every remaining trace of the "ETH effect" is ONE
cell — `ict_scalp_eth_15m`, the only pass in a 13-cell small-book stratum.

WHY THAT IS MECHANISTICALLY UNSURPRISING, which is the part that matters more
than the ETH question. The E1 gate is a FOLD-COUNT: candidate requires
`mean_auc > 0.55` AND `beats_actual*3 >= u*2` AND `beats_hard*3 >= u*2`, i.e.
at least two-thirds of folds on the right side of the comparison
(`train_exit_head.py`). Per-fold noise falls as trades-per-fold rises, so a leg
with a thin book has fewer chances to land two-thirds of its folds correctly
whether or not the head helps it. A gate whose pass rate is 90% predicted by
book size is substantially a POWER TEST, and a small-book `honest_negative` is
then closer to "underpowered" than to "the head does not work here" — a
distinction the status vocabulary cannot currently express.

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

The one stratum that IS internally clean is printed separately: the
trend_donchian 1h family at live parity (relays #9206 main, #9156-58 prop), one
geometry, one strategy, one calendar window. There ETH is 2/2 and non-ETH 0/3
(Fisher one-sided p = 0.10, n = 5 — underpowered by construction). Note within
it that SOL's AUC (0.6161) EXCEEDS both ETH legs': whatever separates them is
not the head's discrimination, it is fold consistency, which is the book-size
axis again.

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
    print()
    return 0


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
    rows = [json.loads(x) for x in ROUNDS.read_text().splitlines() if x.strip()]
    assert all(r.get("tp_geometry") == "live_parity" for r in rows), (
        "a non-live-parity row is in the rounds file; the whole point is one geometry"
    )
    print(f"\n=== COMMITTED LIVE-PARITY ROUNDS ({len(rows)} rows, "
          f"{len({r['tf'] for r in rows})} strata) ===")

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
    split(rows, f"POOLED both strata (n={len(rows)})")
    # A prop leg shares its symbol, strategy family and much of its book with the
    # main leg beside it, so counting both doubles a single observation. Whether
    # the pooled result is significant turns ENTIRELY on this, so it is reported
    # rather than chosen silently.
    main = [r for r in rows if not r.get("prop_sibling")]
    split(main, f"POOLED, prop siblings dropped as non-independent (n={len(main)})")

    held = [r for r in rows if r["tf"] == "2h"]
    if held:
        P = sorted(r["n_oos"] for r in held if r["verdict"] == "candidate")
        F = sorted(r["n_oos"] for r in held if r["verdict"] != "candidate")
        hit = sum((r["n_oos"] >= BOOK_SPLIT) == (r["verdict"] == "candidate") for r in held)
        print(f"\n  OUT-OF-SAMPLE TEST of the fitted n_oos >= {BOOK_SPLIT} split, on the 2h stratum:")
        print(f"    pass n_oos {P}   fail n_oos {F}")
        if P and F and min(P) < max(F):
            print(f"    the largest books FAIL and the smallest pass ({min(P)}) is below the "
                  f"largest fail ({max(F)}) — the split does not order this stratum")
        print(f"    accuracy {hit}/{len(held)} = {100*hit/len(held):.1f}%  (it was 90.0% in-sample)")
        print("    => BOOK SIZE IS REFUTED as the explanation; it was an in-sample artifact,")
        print("       which is exactly what this file warned the 90.0% could be.")


if __name__ == "__main__":
    raise SystemExit(main())
