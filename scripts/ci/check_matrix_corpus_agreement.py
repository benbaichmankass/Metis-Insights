#!/usr/bin/env python3
"""matrix-corpus-agreement — a coverage cell must not silently contradict the corpus.

THE CLASS. `docs/research/exit-refinement-coverage.json` records a DISPOSITION
per (leg, lever). `docs/research/m20-sweep-corpus.jsonl` records the MEASUREMENTS
those dispositions are supposed to rest on. Nothing checked that they agree, so a
cell could carry a July `honest_negative` while the corpus held an August
live-parity PASS for the same leg and lever — and the only thing that surfaced
was `stale_cells`, which says the evidence is OLD, not that NEWER evidence
already exists and disagrees.

Measured 2026-08-14 (BL-20260814-STALE-CELL-BACKLOG-IS-HALF-ANSWERED-BY-THE-CORPUS-ALREADY):
186 stale cells, **100** of which already had a live-parity corpus row, and
**9** whose newest floor-clearing row PASSES against a recorded negative. The
sweep had already been paid for; the join was never performed. Worked example —
`trend_donchian_xrp_4h`/`trail_decay`, `honest_negative` on a 2026-07-12/13 ref,
against two live-parity PASSes from 2026-08-10.

WHAT THIS GUARD CHECKS. For every LIVE leg's closed cell, take the NEWEST
live-parity (`tp_cap_pct == LIVE_TP_CAP`) corpus row for that (leg, lever) that
CLEARS the current OOS floor. If that row passed and the cell records a negative,
the cell's `ref` must ACKNOWLEDGE it. Silence fails; an acknowledgement passes.

THREE DELIBERATE CHOICES, each of which was wrong in a draft of the analysis
this guard comes from:

  1. NEWEST, NOT ANY. Counting "any passing live-parity row" treats a superseded
     pass as standing evidence. Two cells (`mhg_pullback_1d`/`stale_stop`,
     `slv_pullback_1d`/`stale_stop`) passed pre-floor on 6- and 7-trade windows
     and were REFUSED by a later floor-aware run — real retractions. Ignoring
     supersession MANUFACTURES contradictions; my first count did, twice.

  2. FLOOR-CLEARING ONLY. A pass on a window today's `MIN_OOS_TRADES` would
     refuse is not evidence against anything. Rows predating the floor carry
     `min_oos_trades_floor: null`, so the floor is applied to their recorded
     `base_trades_OOS` rather than trusted from the row.

  3. THE REMEDY IS AN ACKNOWLEDGEMENT, NOT A STATUS FLIP. A passing CELL is not
     automatically a passing LEVER disposition, and any status change on a live
     leg is Tier-3. So this guard never asks for a re-grade — it asks that the
     disagreement be VISIBLE where someone about to trust the status will read
     it. That keeps the guard inside Tier-1 while the judgement stays with the
     operator.

Sibling of `exit-coverage-matrix-guard` (which validates statuses and refs) and
of `provenance-consumer-guard` (a signal written and never read). This one is
the cross-artifact case: two committed files that are each internally valid and
disagree with each other.

A LEVER THE CORPUS CANNOT SPEAK TO IS NOT A LEVER THAT AGREES
=============================================================
Added 2026-08-14 (BL-20260814-CORPUS-AGREEMENT-COUNTS-141-UNCHECKABLE-CELLS-AS-CHECKED),
found by using this guard rather than reading it — the same way its own
per-cell supersession bug was found.

`find_disagreements` looks up each cell's corpus evidence and, on `hit is None`,
moves on. That branch cannot distinguish **"corpus rows exist for this lever and
none contradicts"** from **"this lever has no corpus rows AT ALL"** — and the
summary then counted every live cell as `checked`. Measured on the committed
pair: 3 of the 8 lever columns (`exit_ladder`, `exit_head_ml`,
`regime_flip_exit`) have **ZERO** corpus rows, so the reassuring
`376 live cell(s) checked against 922 corpus row(s)` covered **235**; the other
**141 (37.5%)** were unreachable by construction. 115 of those carry an explicit
`honest_negative` — a stated negative outcome resting on evidence this guard
implied it had verified.

That is sub-class **C**, an unasserted denominator (`CLAUDE.md` § "Diagnostic
provenance"), and this file already argued the principle at the INPUT-MISSING
branch — *"an empty denominator reading as OK is the failure this whole family
exists for"* — while a whole column slipped through one level down. A missing
file was guarded; a missing COLUMN was not.

The remedy is the `collapsed-state-guard` CONTRACTS shape, not a blanket fail:
the three are structural (each is measured by a pipeline that does not write the
fleet corpus), so failing on them every run would be the desensitized alarm this
repo calls a P1 in its own right. So they are DECLARED with reasons in
`CORPUS_EXEMPT_LEVERS`, reported separately from `checked` in every summary
line, and an **undeclared** lever column with no corpus rows FAILS — which is
what makes a newly-added column that quietly ships without an evidence store
distinguishable from these three.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"
CORPUS = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"

# The live take-profit clamp the production venue applies. A row measured on any
# other geometry is not comparable to a live disposition.
LIVE_TP_CAP = 0.099
# Kept in sync with m20_fleet_exit_sweep.MIN_OOS_TRADES; asserted below rather
# than imported, because that module runs harness subprocesses on import-time
# constants and this guard must stay a cheap pure read.
MIN_OOS_TRADES = 25

PASS_VERDICTS = frozenset({"PASS", "path_b_wf_pass"})
# Statuses that assert a NEGATIVE outcome. `pending`/`blocked` claim no outcome,
# so a passing row does not contradict them — it just means someone should look.
NEGATIVE_STATUSES = frozenset({"honest_negative", "is_oos_fail", "wf_fail"})

# What counts as the ref acknowledging the disagreement. Deliberately a phrase a
# human had to write, not a bare marker: `new-table-wiring-guard` taught this
# repo that the cheapest way to satisfy a presence-only marker is to lie to it.
ACK = re.compile(r"LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS", re.I)

# Lever columns the fleet corpus structurally CANNOT speak to, each with the
# reason. Being here does not make a column checked — it makes it declared, so
# its cells are reported as UNCHECKABLE instead of silently counted as verified.
# A lever column absent from both the corpus and this table is a FAILURE: that
# is a new column shipping with no evidence store, which is precisely the state
# these three were in before anyone measured it.
CORPUS_EXEMPT_LEVERS: dict[str, str] = {
    "exit_head_ml": (
        "measured by scripts/research/m20_exit_head_round.py, whose --out is a "
        "REQUIRED ephemeral directory (round_report.json / emit/*.jsonl) run on "
        "the trainer VM via research-exit-head-replay-trainer.yml. Nothing is "
        "committed back, so no (leg, lever) row can exist here. Its dispositions "
        "are backed by relay issue refs on the cell, NOT by this corpus."
    ),
    "exit_ladder": (
        "an observe-only shadow soak (runtime_logs/exit_ladder_soak.jsonl, "
        "/api/bot/exit-ladder/soak), not a fleet sweep — graduation to a real "
        "laddered exit is the backtest-gated P4. The fleet harness never emits "
        "an exit_ladder cell, so the corpus is silent by construction."
    ),
    "regime_flip_exit": (
        "has NO runtime implementation to sweep — only the offline replays "
        "scripts/research/m20_regime_flip_replay.py + m20_flip_replay_sweep.py. "
        "Building it as a YAML-declared default-off close path is operator "
        "decision (c), 2026-08-14; until then there is nothing for the fleet "
        "sweep to parameterize."
    ),
}


def _base_status(status: str | None) -> str:
    return (status or "").split(":")[0]


def lever_coverage(matrix: dict, rows: list[dict]) -> dict[str, dict]:
    """Per-lever: how many corpus rows exist, and how many live cells rest on them.

    Split out so the summary can never again report an unreachable cell as
    checked. `corpus_rows == 0` means every disposition in that column is
    unreachable by this guard, whatever its status says.
    """
    counts: dict[str, int] = {}
    for r in rows:
        lv = r.get("lever")
        if lv:
            counts[lv] = counts.get(lv, 0) + 1
    out: dict[str, dict] = {}
    for lever in matrix.get("lever_columns", []):
        live_cells = sum(
            1 for row in matrix.get("rows", [])
            if row.get("execution") == "live"
            and (row.get(lever) or {}).get("status")
        )
        out[lever] = {
            "corpus_rows": counts.get(lever, 0),
            "live_cells": live_cells,
            "declared_exempt": lever in CORPUS_EXEMPT_LEVERS,
        }
    return out


def newest_floor_clearing_pass(rows: list[dict], leg: str, lever: str) -> dict | None:
    """Does (leg, lever) have STANDING passing live-parity evidence?

    SUPERSESSION IS PER-CELL, AND THAT DISTINCTION IS THE WHOLE FUNCTION.
    A lever is swept as MANY CELLS in one run — `trail4` and `trail6`, or
    `vt_hot90` and `vt_cold10` — which are ALTERNATIVE parameterizations, not
    successive measurements of the same thing. Only the same CELL re-run later
    supersedes; a sibling cell that happens to sort last within the same
    `run_id` supersedes nothing.

    Caught 2026-08-14, in the first version of this file: taking `max(run_id)`
    across the whole (leg, lever) group returned whichever cell fell last in
    list order within one 2026-08-10 run, so five genuine standing passes read
    as "superseded" — including `gld_pullback_1h`/`vol_trail`, whose passing
    `vt_hot90_t2` was masked by a failing `vt_cold10_t2` from the SAME run. The
    resulting count would have been wrong in the SAFE-LOOKING direction (fewer
    contradictions reported), which is the kind nobody re-checks.

    So: reduce to the newest row PER CELL first — that is supersession — then
    ask whether any surviving cell passes.
    """
    cand = [
        r for r in rows
        if r.get("leg") == leg
        and r.get("lever") == lever
        and r.get("tp_cap_pct") == LIVE_TP_CAP
        and isinstance(r.get("base_trades_OOS"), (int, float))
        and r["base_trades_OOS"] >= MIN_OOS_TRADES
    ]
    newest_per_cell: dict[str, dict] = {}
    for r in cand:
        key = str(r.get("cell"))
        prev = newest_per_cell.get(key)
        if prev is None or (r.get("run_id") or "") > (prev.get("run_id") or ""):
            newest_per_cell[key] = r
    passing = [r for r in newest_per_cell.values()
               if r.get("verdict") in PASS_VERDICTS]
    if not passing:
        return None
    passing.sort(key=lambda r: r.get("run_id") or "")
    return passing[-1]


def find_disagreements(matrix: dict, rows: list[dict]) -> list[dict]:
    out = []
    for row in matrix.get("rows", []):
        if row.get("execution") != "live":
            continue
        for lever in matrix.get("lever_columns", []):
            cell = row.get(lever) or {}
            status = cell.get("status")
            if _base_status(status) not in NEGATIVE_STATUSES:
                continue
            hit = newest_floor_clearing_pass(rows, row["strategy"], lever)
            if hit is None:
                continue
            if ACK.search(cell.get("ref") or ""):
                continue
            out.append({
                "leg": row["strategy"], "lever": lever, "status": status,
                "cell": hit.get("cell"), "verdict": hit.get("verdict"),
                "run": (hit.get("run_id") or "")[:10],
                "base_oos": hit.get("base_trades_OOS"),
                "wf": hit.get("wf_summary"),
            })
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="prove the guard fails closed on a planted disagreement")
    a = ap.parse_args(argv)

    if not MATRIX.is_file() or not CORPUS.is_file():
        # Absent inputs are reported, never treated as a clean pass — an empty
        # denominator reading as OK is the failure this whole family exists for.
        print(f"matrix-corpus-agreement: INPUT MISSING "
              f"(matrix={MATRIX.is_file()}, corpus={CORPUS.is_file()}) — "
              f"cannot check, and this is NOT a pass.", file=sys.stderr)
        return 1

    matrix = json.loads(MATRIX.read_text())
    rows = [json.loads(x) for x in CORPUS.read_text().splitlines() if x.strip()]

    if a.self_test:
        # A planted negative cell whose corpus row passes MUST be caught. Without
        # this, a guard that silently stopped matching would read as "clean".
        fake_matrix = {
            "lever_columns": ["trail_decay"],
            "rows": [{"strategy": "_selftest_leg", "execution": "live",
                      "trail_decay": {"status": "honest_negative", "ref": "graded 2026-07-01"}}],
        }
        fake_rows = [{"leg": "_selftest_leg", "lever": "trail_decay", "cell": "x",
                      "tp_cap_pct": LIVE_TP_CAP, "verdict": "PASS",
                      "base_trades_OOS": 40, "run_id": "2026-08-10T00:00:00"}]
        caught = find_disagreements(fake_matrix, fake_rows)
        assert len(caught) == 1, f"guard failed to catch a planted disagreement: {caught}"
        # ...and an acknowledged one must NOT be caught, or the guard is
        # unsatisfiable and contributors will strip it rather than serve it.
        fake_matrix["rows"][0]["trail_decay"]["ref"] += (
            " || LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS: ...")
        assert not find_disagreements(fake_matrix, fake_rows), "ack did not clear the finding"
        # ...and supersession must suppress it: a NEWER refusing row means the
        # older pass is retracted, not standing.
        fake_matrix["rows"][0]["trail_decay"]["ref"] = "graded 2026-07-01"
        fake_rows.append({"leg": "_selftest_leg", "lever": "trail_decay", "cell": "x",
                          "tp_cap_pct": LIVE_TP_CAP, "verdict": "is_oos_fail",
                          "base_trades_OOS": 40, "run_id": "2026-08-13T00:00:00"})
        assert not find_disagreements(fake_matrix, fake_rows), "supersession not honoured"
        # ...but a SIBLING CELL in the SAME run must NOT suppress a pass —
        # supersession is per-cell. This is the case the first version got
        # wrong, and it failed in the safe-looking direction (under-reporting).
        sib = [{"leg": "_sib", "lever": "trail_decay", "cell": "trail4",
                "tp_cap_pct": LIVE_TP_CAP, "verdict": "PASS",
                "base_trades_OOS": 40, "run_id": "2026-08-10T00:00:00"},
               {"leg": "_sib", "lever": "trail_decay", "cell": "trail6",
                "tp_cap_pct": LIVE_TP_CAP, "verdict": "is_oos_fail",
                "base_trades_OOS": 40, "run_id": "2026-08-10T00:00:00"}]
        sib_matrix = {
            "lever_columns": ["trail_decay"],
            "rows": [{"strategy": "_sib", "execution": "live",
                      "trail_decay": {"status": "honest_negative", "ref": "old"}}],
        }
        assert len(find_disagreements(sib_matrix, sib)) == 1, (
            "a failing SIBLING cell from the same run wrongly suppressed a "
            "standing pass — supersession must be per-cell, not per-(leg,lever)")
        # ...and a lever the corpus cannot speak to must be reported as
        # UNREACHABLE, never folded into the checked count. This is the case
        # that read as clean for 141 real cells.
        cov_matrix = {
            "lever_columns": ["trail_decay", "exit_head_ml"],
            "rows": [{"strategy": "_cov", "execution": "live",
                      "trail_decay": {"status": "honest_negative", "ref": "x"},
                      "exit_head_ml": {"status": "honest_negative", "ref": "x"}}],
        }
        cov = lever_coverage(cov_matrix, fake_rows)
        assert cov["trail_decay"]["corpus_rows"] > 0, cov
        assert cov["exit_head_ml"]["corpus_rows"] == 0, (
            "a lever with no corpus rows must report zero, not be omitted")
        assert cov["exit_head_ml"]["live_cells"] == 1, cov
        assert cov["exit_head_ml"]["declared_exempt"] is True, (
            "exit_head_ml must be DECLARED, or its cells fail as undeclared")
        # ...and an UNDECLARED corpus-less column must be visible as such, which
        # is what makes a newly-added lever distinguishable from the known three.
        cov2 = lever_coverage(
            {"lever_columns": ["_brand_new_lever"],
             "rows": [{"strategy": "_cov", "execution": "live",
                       "_brand_new_lever": {"status": "honest_negative"}}]},
            fake_rows)
        assert cov2["_brand_new_lever"] == {
            "corpus_rows": 0, "live_cells": 1, "declared_exempt": False}, cov2
        # ...and every declared exemption must name a lever that still exists,
        # or the table is prose about a column nobody has.
        assert set(CORPUS_EXEMPT_LEVERS) <= set(
            json.loads(MATRIX.read_text()).get("lever_columns", [])), (
            "CORPUS_EXEMPT_LEVERS names a lever column the matrix does not have")
        print("[selftest] matrix-corpus-agreement")
        print("self-test OK — catches a planted disagreement, clears on an "
              "acknowledgement, honours supersession, and reports a lever with "
              "no corpus rows as unreachable rather than checked.")
        return 0

    bad = find_disagreements(matrix, rows)
    cov = lever_coverage(matrix, rows)

    # A lever column with no corpus rows AND no declared reason is a new column
    # that shipped without an evidence store. Fail on it — that is the whole
    # point of declaring the three known ones rather than hardcoding a skip.
    undeclared = sorted(
        lv for lv, c in cov.items()
        if c["corpus_rows"] == 0 and not c["declared_exempt"]
    )
    if undeclared:
        print(f"matrix-corpus-agreement: {len(undeclared)} lever column(s) have "
              f"ZERO corpus rows and are not declared in CORPUS_EXEMPT_LEVERS:",
              file=sys.stderr)
        for lv in undeclared:
            print(f"  {lv}: {cov[lv]['live_cells']} live cell(s) rest on evidence "
                  f"this guard cannot reach", file=sys.stderr)
        print(
            "\nEvery disposition in such a column is unverifiable here, so counting it\n"
            "as checked would overstate coverage by exactly its size. Either point the\n"
            "lever's sweep at the corpus, or declare it in CORPUS_EXEMPT_LEVERS with the\n"
            "reason the corpus cannot speak to it.", file=sys.stderr)
        return 1

    # The mirror failure: an exemption that outlived its reason. A lever that has
    # STARTED writing corpus rows is now checkable, and leaving it declared would
    # keep excusing it from a check it can pass — stale prose beside a changed
    # field, which this repo resolves in the field's favour every time.
    graduated = sorted(
        lv for lv, c in cov.items() if c["declared_exempt"] and c["corpus_rows"]
    )
    if graduated:
        print(f"matrix-corpus-agreement: {len(graduated)} lever(s) are declared in "
              f"CORPUS_EXEMPT_LEVERS but now HAVE corpus rows:", file=sys.stderr)
        for lv in graduated:
            print(f"  {lv}: {cov[lv]['corpus_rows']} corpus row(s) — the exemption's "
                  f"stated reason no longer holds", file=sys.stderr)
        print("\nRemove the entry so these cells are actually checked.", file=sys.stderr)
        return 1

    checkable = sum(c["live_cells"] for c in cov.values() if c["corpus_rows"])
    unreachable = sum(c["live_cells"] for c in cov.values() if not c["corpus_rows"])
    exempt = sorted(lv for lv, c in cov.items() if not c["corpus_rows"])

    if not bad:
        # `checked` and `unreachable` are reported as SEPARATE numbers and never
        # summed into one reassuring total: the sum is what read as coverage.
        print(f"matrix-corpus-agreement: OK — {checkable} live cell(s) checked "
              f"against {len(rows)} corpus row(s); no unacknowledged disagreement.")
        if unreachable:
            print(f"  NOT CHECKED (no corpus rows for the lever, declared): "
                  f"{unreachable} live cell(s) across {len(exempt)} column(s) — "
                  f"{', '.join(exempt)}. These are unverified here, not verified-clean.")
        return 0

    print(f"matrix-corpus-agreement: {len(bad)} cell(s) record a NEGATIVE while the "
          f"newest floor-clearing live-parity corpus row PASSES, and the ref does "
          f"not acknowledge it:\n", file=sys.stderr)
    for b in bad:
        print(f"  {b['leg']} / {b['lever']}: status={b['status']} but corpus "
              f"cell={b['cell']} verdict={b['verdict']} run={b['run']} "
              f"wf={b['wf']} base_OOS={b['base_oos']}", file=sys.stderr)
    print(
        "\nThe fix is NOT to flip the status — a passing CELL is not automatically a\n"
        "passing LEVER disposition, and a live-leg status change is Tier-3. Append the\n"
        "evidence to the cell's `ref` so a reader meeting the status also meets the\n"
        "measurement that disagrees with it, starting with the phrase\n"
        "  LIVE-PARITY COUNTER-EVIDENCE ALREADY IN THE CORPUS\n"
        "and stating the cell, verdict, run, walk-forward and base_trades_OOS.\n"
        "See BL-20260814-STALE-CELL-BACKLOG-IS-HALF-ANSWERED-BY-THE-CORPUS-ALREADY.",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
