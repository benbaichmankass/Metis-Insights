#!/usr/bin/env python3
"""Map ONE `m20-exit-lever-sweep` run onto the result contract — ONE RECORD PER LEG.

WHY PER LEG, AND NOT ONE SUMMARY ROW
-------------------------------------
The sweep fans out one job per leg. A run that landed a single summary row could
not be asserted against the fan-out width, so a run that silently dropped four of
twelve legs would look identical to one that covered all twelve — the
unasserted-denominator shape this repo files as its own bug class. One record per
leg makes `--min-rows` mean something, and the landing assertion then fails a
partial run instead of grading it green.

THE VERDICT IS READ OFF THE SWEEP'S OWN GATE, NOT RE-DERIVED
--------------------------------------------------------------
Each corpus row carries `is_oos_pass` — the IS/OOS gate the sweep already
applied. Per leg:

    pass                 at least one cell cleared the gate
    no_action_warranted  cells were swept and NONE cleared it — the outcome that
                         argues for leaving the shipped geometry alone, and the
                         wording `research-disposition-ledger.jsonl` already uses
    indeterminate        rows exist for the leg but none is a gradeable `cell`

⚠️ `no_action_warranted` IS A MEASUREMENT, NOT A SHRUG. It is `read_state:
measured` with a real n, because the sweep LOOKED and found nothing better. A leg
with no rows at all is `read_state: no_data` with `n: null` — we could not look
at that leg — and collapsing the two is exactly what `check_collapsed_states.py`
exists to prevent.

⚠️ n IS `base_trades_OOS`, THE OOS DENOMINATOR — not the cell count. The cells
are the search space; the trades are what any R figure is measured over, and
§ "Always state the population" is about the latter. It is read from the leg's
rows and is `null` when the rows do not carry it, never 0.

⚠️ WHERE THE ROWS LIVE IS NOT `main`, AND THE RECORD SAYS SO. A dispatch on the
default branch retargets the corpus to the branch `claude/m20-sweep-corpus`,
which by the workflow's own note NEVER MERGES. So `artifact_locator` names that
branch rather than implying the rows are on `main` — an honest locator is the
difference between MEASURED and a number nobody can reach.

Tier-1: reads a JSONL corpus, writes a JSONL of record parts. No live path.

Run:
    python3 scripts/research/m20_sweep_result.py --self-test
    python3 scripts/research/m20_sweep_result.py --corpus run_corpus.jsonl \\
        --out rr/records.jsonl --sweep-result success --legs "a,b"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DECISION_RULE_ID = "RULE-M20-EXIT-LEVER-IS-OOS-GATE"
DECISION_RULE_REGISTERED_AT = "2026-09-22"

CORPUS_BRANCH = "claude/m20-sweep-corpus"


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def summarize(rows: Iterable[Dict[str, Any]], *, legs: List[str],
              sweep_result: str, run_stamp: str = "") -> List[Dict[str, Any]]:
    """Return one record-part per leg in `legs`. Pure — no I/O.

    `legs` is the DISPATCHED population, so a leg that produced no rows still
    gets a record saying so. Deriving the population from the rows instead would
    make a dropped leg invisible: it would simply not appear, and nothing could
    tell that from a leg nobody asked for.
    """
    by_leg: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        leg = row.get("leg")
        if isinstance(leg, str):
            by_leg[leg].append(row)

    out: List[Dict[str, Any]] = []
    for leg in legs:
        legrows = by_leg.get(leg, [])
        cells = [r for r in legrows if r.get("kind") == "cell"]
        passed = [r for r in cells if r.get("is_oos_pass") is True]

        base_n = None
        base_net_r = None
        for r in legrows:
            if base_n is None:
                base_n = _as_int(r.get("base_trades_OOS"))
            if base_net_r is None and isinstance(r.get("base_net_r_OOS"), (int, float)):
                base_net_r = r["base_net_r_OOS"]

        if not legrows:
            # WE COULD NOT LOOK at this leg — its shard produced nothing.
            record = {
                "verdict": "not_applicable",
                "read_state": "no_data" if sweep_result == "success" else "producer_failed",
                "n": None,
                "note": (
                    f"The dispatched leg {leg!r} contributed NO rows to this "
                    f"run's corpus (sweep job result: {sweep_result}). This "
                    f"record exists so a dropped leg is visible rather than "
                    f"simply absent."),
            }
        elif not cells:
            record = {
                "verdict": "indeterminate",
                "read_state": "measured",
                "n": base_n if base_n is not None else 0,
                "note": (f"{len(legrows)} row(s) for {leg!r} but none is a "
                         f"gradeable `cell` — nothing to grade against the gate."),
            }
        else:
            record = {
                "verdict": "pass" if passed else "no_action_warranted",
                "read_state": "measured",
                "n": base_n if base_n is not None else 0,
                "note": (
                    f"{len(cells)} cell(s) swept on {leg!r}; "
                    f"{len(passed)} cleared the IS/OOS gate. "
                    + ("A cell beat the shipped geometry — this is a candidate, "
                       "not a change: the flip is Tier-3."
                       if passed else
                       "ZERO cleared the gate, which is the outcome that argues "
                       "for leaving the shipped geometry alone.")),
            }

        record["population"] = (
            f"leg {leg}; OOS side of this run's IS/OOS split over the m20 "
            f"exit-lever sweep grid ({len(cells)} cell(s) tried, "
            f"{len(legrows)} corpus row(s)); n is base_trades_OOS")
        record["measurement"] = {
            "leg": leg,
            "cells_swept": len(cells),
            "cells_passed": len(passed),
            "corpus_rows": len(legrows),
            "base_net_r_OOS": base_net_r,
            "base_trades_OOS": base_n,
            "passing_cells": sorted({str(r.get("cell")) for r in passed})[:20],
            "run_stamp": run_stamp or None,
        }
        record["artifact_store"] = "docs/research/m20-sweep-corpus.jsonl"
        record["artifact_locator"] = (
            f"rows with leg={leg!r} and sweep_generated_at={run_stamp or '<this run>'}. "
            f"⚠️ A default-branch dispatch RETARGETS the corpus to the branch "
            f"`{CORPUS_BRANCH}`, which by this workflow's own note never merges "
            f"to main — look there, not on main.")
        record["rows_landed"] = len(legrows)
        out.append(record)
    return out


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _self_test() -> int:
    failures = 0
    rows = [
        {"leg": "a", "kind": "cell", "is_oos_pass": False, "cell": "c1",
         "base_trades_OOS": 25, "base_net_r_OOS": 3.16},
        {"leg": "a", "kind": "cell", "is_oos_pass": True, "cell": "c2",
         "base_trades_OOS": 25, "base_net_r_OOS": 3.16},
        {"leg": "b", "kind": "cell", "is_oos_pass": False, "cell": "c1",
         "base_trades_OOS": 40, "base_net_r_OOS": -1.2},
        {"leg": "d", "kind": "base", "base_trades_OOS": 11},
    ]

    def case(label: str, got: Any, want: Any) -> None:
        nonlocal failures
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            print(f"        got {got!r} want {want!r}")

    recs = {r["measurement"]["leg"]: r
            for r in summarize(rows, legs=["a", "b", "c", "d"],
                               sweep_result="success", run_stamp="S")}

    case("positive: a leg with a passing cell -> pass/measured/n=25",
         (recs["a"]["verdict"], recs["a"]["read_state"], recs["a"]["n"]),
         ("pass", "measured", 25))
    case("positive: a leg swept with ZERO passes -> no_action_warranted, "
         "measured, n=40 (we LOOKED and found nothing)",
         (recs["b"]["verdict"], recs["b"]["read_state"], recs["b"]["n"]),
         ("no_action_warranted", "measured", 40))
    case("negative: a DISPATCHED leg with no rows -> no_data/n=null, never "
         "silently absent",
         (recs["c"]["verdict"], recs["c"]["read_state"], recs["c"]["n"]),
         ("not_applicable", "no_data", None))
    case("negative: rows but no gradeable cell -> indeterminate, not pass",
         (recs["d"]["verdict"], recs["d"]["read_state"]),
         ("indeterminate", "measured"))
    case("the record count equals the DISPATCHED population, not the rows'",
         len(recs), 4)

    failed = {r["measurement"]["leg"]: r
              for r in summarize([], legs=["a"], sweep_result="failure")}
    case("negative: a FAILED sweep job grades producer_failed, not no_data — "
         "'it broke' and 'it was empty' are different facts",
         (failed["a"]["read_state"], failed["a"]["n"]),
         ("producer_failed", None))

    case("cells_passed is counted from is_oos_pass, not guessed",
         (recs["a"]["measurement"]["cells_swept"],
          recs["a"]["measurement"]["cells_passed"]), (2, 1))
    case("a bool where a count belongs is not read as n=1",
         summarize([{"leg": "a", "kind": "cell", "is_oos_pass": False,
                     "base_trades_OOS": True}], legs=["a"],
                   sweep_result="success")[0]["n"], 0)

    # Every part this emits must be ADMISSIBLE once the shared fields are added.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research_result import build, validate  # noqa: E402
    bad = 0
    for part in summarize(rows, legs=["a", "b", "c", "d"], sweep_result="success"):
        rec = build(research_unit="RQ-20260922-003",
                    decision_rule_id=DECISION_RULE_ID,
                    decision_rule_registered_at=DECISION_RULE_REGISTERED_AT,
                    verdict=part["verdict"], read_state=part["read_state"],
                    population_description=part["population"], n=part["n"],
                    workflow="m20-exit-lever-sweep.yml", run_id="1",
                    commit_sha="abc", tool="t", measurement=part["measurement"],
                    artifact_store=part["artifact_store"],
                    artifact_locator=part["artifact_locator"],
                    rows_landed=part["rows_landed"], note=part["note"])
        problems = validate(rec)
        if problems:
            bad += 1
            print(f"        {part['measurement']['leg']}: {problems!r}")
    case("every emitted part is ADMISSIBLE to research_result.validate()", bad, 0)

    print(f"m20_sweep_result --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--corpus", default="run_corpus.jsonl",
                    help="A corpus holding ONLY this run's rows.")
    ap.add_argument("--out", default="rr/records.jsonl")
    ap.add_argument("--legs", default="",
                    help="CSV of the DISPATCHED legs. A leg that produced no "
                         "rows still gets a record; deriving the population "
                         "from the rows would make a dropped leg invisible.")
    ap.add_argument("--sweep-result", default="success")
    ap.add_argument("--run-stamp", default="")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    legs = [x.strip() for x in args.legs.split(",") if x.strip()]
    rows = _load(Path(args.corpus))
    if not legs:
        # Falls back to the observed legs, and SAYS SO rather than silently
        # producing a population nobody declared.
        legs = sorted({str(r["leg"]) for r in rows if isinstance(r.get("leg"), str)})
        print(f"::warning::m20_sweep_result: no --legs given; falling back to "
              f"the {len(legs)} leg(s) observed in the corpus. A leg dropped "
              f"before it wrote a row CANNOT be detected this way.")
    if not legs:
        print("::error::m20_sweep_result: no dispatched legs and no rows — "
              "there is nothing to emit, and emitting nothing silently is the "
              "defect this exists to close.", file=sys.stderr)
        return 1

    parts = summarize(rows, legs=legs, sweep_result=args.sweep_result,
                      run_stamp=args.run_stamp)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for part in parts:
            fh.write(json.dumps(part, sort_keys=True, ensure_ascii=False) + "\n")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(f"records={len(parts)}\n")
            fh.write(f"min_rows={len(parts)}\n")
    print(f"m20_sweep_result: wrote {len(parts)} record part(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
