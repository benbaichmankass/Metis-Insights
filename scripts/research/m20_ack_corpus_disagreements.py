#!/usr/bin/env python3
"""Draft the `ref` acknowledgement for every unacknowledged corpus disagreement.

`matrix-corpus-agreement` fails CI when a live cell records a NEGATIVE while the
newest floor-clearing live-parity corpus row PASSES and the `ref` does not say
so. Its prescribed remedy is to append the measurement to the ref -- never to
flip the status, which is Tier-3.

WHY THIS IS A SCRIPT. The first two acknowledgements (2026-08-15,
`sol_pullback_2h`/giveback, `slv_pullback_1d`/stale) were hand-written, which was
fine at n=2. The very next sweep put ten more legs in the same position within
the hour, and a hand-written ref has two failure modes that scale badly: the
numbers get transcribed wrong, and the CAVEATS get dropped -- because the caveat
is the part you have to go looking for. This repo already holds that *"an
artifact whose producer is a throwaway is unreproducible"*
(`m20_consolidate_dispersion_arms.py`); a ref asserting a measurement is such an
artifact.

WHAT IT REFUSES TO DO. It writes `ref` text and nothing else. It never touches
`status`, never touches `tp_geometry`, and never decides whether a lever should
ship. A passing CELL is not a passing LEVER DISPOSITION, and on a live leg that
call is the operator's.

THE CAVEATS ARE COMPUTED, NOT BOILERPLATE. Three of them, each of which made a
headline overstate its case on the run that motivated this:

  * **inert walk-forward folds.** A fold where the lever never fired
    (`d_net_r == 0 and d_max_dd == 0`) still counts `ok`, so `wf_summary` is not
    a count of wins. On 2026-08-15 `slv_pullback_1d`'s "5/6" was 2 real wins
    against 1 real loss with THREE inert folds. Reported as
    `N/M (K real wins, J inert)` so the reader cannot take the ratio at face
    value.
  * **Path B is not Path A.** `path_b_wf_pass` says the net_R gain held across
    folds; it says nothing about `rate_ok`. Reported with `is_oos_pass` and the
    OOS gate reason beside it, because `+0.001` net_R is arithmetically nonzero
    and economically nothing.
  * **the split is per leg.** These rows are cut at a DERIVED boundary
    (`split_mode=oos-trades`), so a fresh row and the pre-cutover evidence it
    contradicts are different partitions of the same book -- not a rerun.

Dry-run by default: it prints what it would write. `--apply` writes the matrix.

Tier-1 research tooling. Reads the corpus + matrix, writes matrix `ref` prose.
Touches nothing the trader executes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
CORPUS = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"

ACK_PHRASE = "LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS"


def _guard():
    """The agreement guard, imported for its OWN definition of a disagreement.

    Deliberately imported rather than reimplemented. A second predicate for
    "does this corpus row contradict this cell" would be free to drift from the
    one CI actually fails on, and then this script would cheerfully acknowledge
    cells the guard does not care about while missing the ones it does.
    """
    path = REPO / "scripts" / "ci" / "check_matrix_corpus_agreement.py"
    spec = importlib.util.spec_from_file_location("_m20_agreement_guard", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_agreement_guard"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def fold_quality(row: dict) -> tuple[int, int, int]:
    """(counted_wins, real_wins, inert) over a row's walk-forward folds.

    `inert` is a fold the lever never fired in -- zero delta on BOTH axes. It
    still counts `ok` upstream, so a win total silently includes it.
    """
    folds = row.get("wf_folds") or []
    inert = [f for f in folds
             if f.get("d_net_r") == 0.0 and f.get("d_max_dd") == 0.0]
    wins = [f for f in folds if f.get("ok")]
    real = [f for f in wins if f not in inert]
    return len(wins), len(real), len(inert)


def caveats_for(row: dict) -> list[str]:
    """The parts of this row a reader would otherwise have to go looking for."""
    out: list[str] = []
    counted, real, inert = fold_quality(row)
    if inert:
        folds = row.get("wf_folds") or []
        names = ", ".join(str(f.get("fold")) for f in folds
                          if f.get("d_net_r") == 0.0 and f.get("d_max_dd") == 0.0)
        out.append(
            f"THE WIN TOTAL IS NOT A COUNT OF WINS: {inert} of {len(folds)} "
            f"walk-forward folds ({names}) are inert — the lever never fired, "
            f"d_net_r=0 and d_max_dd=0 — and an inert fold still counts ok. So "
            f"{counted}/{len(folds)} is really {real} real win(s)")
    if row.get("verdict") == "path_b_wf_pass":
        out.append(
            f"PATH-B, NOT PATH A: is_oos_pass={row.get('is_oos_pass')}, "
            f"rate_ok_OOS={row.get('rate_ok_OOS')}"
            + (f" (gate_reason_OOS={row.get('gate_reason_OOS')})"
               if row.get("gate_reason_OOS") else "")
            + f"; OOS net_R gain {row.get('d_net_r_OOS')}")
    if row.get("split"):
        out.append(
            f"DIFFERENT PARTITION, NOT A RERUN: split {row['split']}, derived "
            f"per leg (split_mode=oos-trades) — the pre-cutover evidence this "
            f"contradicts was cut elsewhere, so the two are different "
            f"partitions of the same book")
    return out


def draft(hit: dict, row: dict) -> str:
    """The ref segment for one disagreement. Must contain the ACK phrase."""
    counted, real, inert = fold_quality(row)
    wf = row.get("wf_summary") or "n/a"
    seg = (f" || {ACK_PHRASE} (run {str(row.get('run_id'))[:19]}Z, "
           f"docs/research/m20-sweep-corpus.jsonl). Cell {row.get('cell')} "
           f"verdict={row.get('verdict')} wf={wf} "
           f"base_trades_OOS={row.get('base_trades_OOS')} "
           f"(dnetR IS {row.get('d_net_r_IS')} / OOS {row.get('d_net_r_OOS')}; "
           f"dmaxDD IS {row.get('d_max_dd_IS')} / OOS {row.get('d_max_dd_OOS')}). "
           f"THE STATUS IS DELIBERATELY NOT FLIPPED: a passing cell is not a "
           f"passing lever disposition, and a live-leg status change is Tier-3 "
           f"— queued for the operator, not decided here.")
    cav = caveats_for(row)
    if cav:
        seg += " CAVEATS, measured from the row rather than inferred: " + \
               " ".join(f"({i}) {c}." for i, c in enumerate(cav, 1))
    return seg


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the matrix (default: print the drafts only)")
    ap.add_argument("--matrix", default=str(MATRIX))
    ap.add_argument("--corpus", default=str(CORPUS))
    a = ap.parse_args(argv[1:])

    guard = _guard()
    matrix = json.loads(Path(a.matrix).read_text())
    rows = [json.loads(x) for x in Path(a.corpus).read_text().splitlines() if x.strip()]

    hits = guard.find_disagreements(matrix, rows)
    if not hits:
        # A clean run is a real answer here, but say what it ranged over --
        # "0 findings" over an unstated population is the unasserted-denominator
        # class this repo guards for.
        print(f"no unacknowledged disagreement over {len(rows)} corpus row(s) "
              f"and {len(matrix.get('rows', []))} matrix row(s).")
        return 0

    by_cell = {r["strategy"]: r for r in matrix["rows"]}
    written = 0
    for hit in hits:
        src = guard.newest_floor_clearing_pass(rows, hit["leg"], hit["lever"])
        if src is None:
            # The guard found it and we cannot re-find it: report, never guess.
            print(f"!! {hit['leg']}/{hit['lever']}: guard flagged it but the "
                  f"source row could not be re-resolved — SKIPPED, not drafted",
                  file=sys.stderr)
            continue
        seg = draft(hit, src)
        print(f"\n=== {hit['leg']} / {hit['lever']}  "
              f"(status={hit['status']} vs {hit['verdict']})")
        print(seg.strip())
        if a.apply:
            cell = by_cell[hit["leg"]][hit["lever"]]
            cell["ref"] = (cell.get("ref") or "") + seg
            written += 1

    if a.apply:
        with open(a.matrix, "w", encoding="utf-8") as fh:
            json.dump(matrix, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"\nwrote {written} acknowledgement(s) to {a.matrix}")
    else:
        print(f"\n{len(hits)} disagreement(s) drafted; nothing written "
              f"(pass --apply). Statuses are never touched either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
