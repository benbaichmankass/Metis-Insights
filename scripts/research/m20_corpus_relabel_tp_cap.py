#!/usr/bin/env python3
"""Relabel the M20 corpus rows that were measured WITH the 0.099 TP cap but
recorded `tp_cap_pct: null`, then collapse the duplicates that mislabel created.

WHY THIS EXISTS
---------------
`tp_cap_pct` is part of `measurement_key`, deliberately: the legacy no-TP
geometry and live parity are two different books
(BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP). So a row carrying the
WRONG cap label does not merely read wrong — it keys as a separate measurement
and never supersedes its true counterpart. The corpus then holds two identities
for one book and invites a capped-vs-uncapped A/B where both arms are capped.

THE EVIDENCE, because a relabel must never rest on inference
------------------------------------------------------------
Two `m20-exit-lever-sweep` runs bracket the affected window, both on
`claude/m20-exit-refinement-continue-g1qv46`, both `event: push`:

  run 31438120910  created 2026-08-10T22:23:54Z  head d76643b9
  run 31438683081  created 2026-08-10T22:31:57Z  head e6e519d1

On a `push` event the `inputs` context is EMPTY, so the workflow's
`TP_CAP_PCT: ${{ inputs.tp_cap_pct || '0.099' }}` resolves to **0.099** and the
sweep is invoked `--tp-cap-pct "0.099"`. Both runs were therefore capped.

They differ only in whether their sweep script WROTE the provenance field:

  git show d76643b9:scripts/research/m20_fleet_exit_sweep.py \
    | grep -c '"tp_cap_pct": a.tp_cap_pct'   ->  0     (field absent)
  git show e6e519d1:scripts/research/m20_fleet_exit_sweep.py \
    | grep -c '"tp_cap_pct": a.tp_cap_pct'   ->  1     (field written)

The first run's legs finished 22:26:28-22:28:42Z -> the 140 `null` rows.
The second run's legs finished 22:34:13-22:42:40Z -> `0.099` rows.

DATE THE FIELD BY THE REF THAT RAN, NOT BY `main`. An earlier pass refuted this
same conclusion by timing the field from its merge to `main` (`2a289ddc`,
2026-08-11 14:10:55Z) and then finding 464 rows carrying 0.099 with EARLIER
timestamps — an apparent contradiction that dissolves entirely once you use the
feature branch the runs actually executed on, where the two commits sit about
eight minutes apart on 08-10. `sweep_generated_at` cannot date a row's
provenance on its own; the run's head SHA can.

A CORROBORATING CHECK, not the basis: 7 legs across 2 families carry both labels
with identical `base_trades_IS`, and 34 of 35 shared (leg,lever,cell,split)
pairs agree on all six delta/verdict fields. A genuine cap/no-cap difference
would not produce that — the cap demonstrably moves a book (a direct control on
a synthetic series went 57 trades -> 127, net_R 466.37 -> 347.55).

THE CAP IS NOT THE ONLY FIELD THOSE ROWS ARE MISSING — AND THE OTHERS MUST NOT
BE FILLED IN THE SAME SWEEP
-----------------------------------------------------------------------------
Measured over the 35 (leg,lever,cell,split) pairs that carry both labels, the
null rows differ from their 0.099 counterparts on THREE identity fields, not
one: `tp_cap_pct` (35/35), `regime_router` None->'off' (35/35),
`min_oos_trades_floor` None->25 (35/35), and `declared_levers_dropped` None->[]
(8/35). They predate the whole provenance block, not just the cap.

Those fields are NOT the same kind of missing, and treating them alike would
corrupt the corpus:

  * `tp_cap_pct` — the run HAD a value (0.099, resolved from the workflow
    default on a push event) and failed to record it. Backfillable from run
    evidence. THIS is what the script fixes.
  * `min_oos_trades_floor` — the run genuinely had NO floor. The 25-trade floor
    is an operator decision dated 2026-08-11, a day AFTER these runs. `None`
    here means "ungraded by any floor", which is the TRUTH about that run and
    is emphatically not floor 25. Setting it would manufacture a grading that
    never happened.
  * `regime_router` — the run did run ungated, so 'off' would be accurate, but
    it is left alone: it is not what the evidence in this file was gathered to
    establish, and a correction should not quietly widen past its basis.

CONSEQUENTLY THE RELABEL COLLAPSES NOTHING, and that is correct rather than a
shortfall. An earlier draft of this reasoning predicted the 35 pairs would merge
once the caps matched; they do not, because `min_oos_trades_floor` still
separates them — and it SHOULD, since the two rows really are one cell graded
under two different floors. Two rows for that is the corpus working as designed.
The defect being fixed is narrower than "duplicate measurements under two
identities": it is one axis, mislabelled, on rows a reader is likely to trust.

WHAT THIS DOES NOT DO
---------------------
It does not touch rows outside the measured window, does not fill any field but
the cap, and refuses to run if the window's rows do not look the way the
evidence says they should. It is a data correction with a stated basis, not a
sweep-and-hope.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# The window whose rows run 31438120910 produced. Closed interval, and
# deliberately NARROW: it is the observed span of that run's leg completions,
# not a guess at the run's duration.
WINDOW_START = "2026-08-10T22:26:00"
WINDOW_END = "2026-08-10T22:29:00"

# What those rows were actually measured at, per the run's own resolved env.
TRUE_CAP = 0.099

# The mislabel we are correcting. A row already carrying a cap is left alone —
# this script only ever fills in a value that was never recorded, and never
# overwrites one that was.
MISLABEL = None


def _gen(row: dict) -> str:
    return (row.get("sweep_generated_at") or "")[:19]


def in_window(row: dict) -> bool:
    g = _gen(row)
    return bool(g) and WINDOW_START <= g <= WINDOW_END


def measurement_key(row: dict) -> tuple:
    """Mirrors m20_corpus_extract.measurement_key.

    Imported rather than duplicated where possible; duplicated here only so the
    script stays runnable against a corpus checked out without the extractor.
    Keep in sync — a divergence would silently collapse rows that are NOT the
    same measurement, which is worse than the defect being fixed.
    """
    dropped = row.get("declared_levers_dropped")
    return (row.get("kind"), row.get("leg"), row.get("cell"),
            row.get("split"), row.get("tp_cap_pct"), row.get("regime_router"),
            row.get("min_oos_trades_floor"), row.get("fee_bps_roundtrip"),
            row.get("min_confidence_override"),
            tuple(sorted(dropped)) if isinstance(dropped, list) else ())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/research/m20-sweep-corpus.jsonl")
    ap.add_argument("--apply", action="store_true",
                    help="Write the file. Default is a dry run that reports "
                         "what would change and exits without touching it.")
    a = ap.parse_args()

    path = Path(a.corpus)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    print(f"corpus: {len(rows)} rows  ({path})")

    targets = [r for r in rows if in_window(r) and r.get("tp_cap_pct") is MISLABEL]
    others_in_window = [r for r in rows if in_window(r) and r.get("tp_cap_pct") is not MISLABEL]

    # PRECONDITIONS. Each one is a claim the evidence makes; if the corpus does
    # not match, the evidence does not describe THIS file and relabelling it
    # would be exactly the inference the gate forbids.
    if not targets:
        print("REFUSING: no null-cap rows in the window — already relabelled, "
              "or this is not the corpus the evidence was gathered against.")
        return 1
    if others_in_window:
        print(f"REFUSING: {len(others_in_window)} rows in the window already "
              "carry a cap value. The window is supposed to be exactly one "
              "run's output; a mixed window means the window is wrong.")
        return 1

    nulls_outside = [r for r in rows
                     if not in_window(r) and r.get("tp_cap_pct") is MISLABEL]
    if nulls_outside:
        # NOT fatal, but it must be stated: these are null rows the evidence
        # says nothing about, and they are NOT being relabelled.
        spans = sorted({_gen(r)[:16] for r in nulls_outside})
        print(f"NOTE: {len(nulls_outside)} null-cap rows lie OUTSIDE the "
              f"evidenced window and are left untouched ({len(spans)} distinct "
              f"timestamps, {spans[0]} .. {spans[-1]}). They need their own "
              "run-input check before anyone relabels them.")

    print(f"would relabel: {len(targets)} rows  null -> {TRUE_CAP}")
    print(f"  legs: {len(set(r.get('leg') for r in targets))}")

    # Whether the relabel collapses anything. It is EXPECTED to collapse
    # nothing: `min_oos_trades_floor` still separates a pre-floor row from its
    # floor-25 counterpart, correctly, because those are one cell graded under
    # two different floors. A non-zero count here would mean the cap really was
    # the only thing keeping two identical measurements apart — report it either
    # way rather than assuming, since the assumption was wrong once already.
    keyed: dict[tuple, list[dict]] = {}
    for r in rows:
        rr = dict(r)
        if in_window(r) and r.get("tp_cap_pct") is MISLABEL:
            rr["tp_cap_pct"] = TRUE_CAP
        keyed.setdefault(measurement_key(rr), []).append(rr)
    dupes = {k: v for k, v in keyed.items() if len(v) > 1}
    print(f"  duplicate measurement keys after relabel: {len(dupes)} "
          f"(collapsing to newest by sweep_generated_at)")

    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write.")
        return 0

    out = []
    for _, group in keyed.items():
        out.append(max(group, key=lambda r: r.get("sweep_generated_at") or ""))
    out.sort(key=lambda r: (r.get("sweep_generated_at") or "", str(r.get("leg")),
                            str(r.get("lever")), str(r.get("cell"))))
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in out))
    print(f"\nwrote {len(out)} rows (was {len(rows)}; "
          f"{len(rows) - len(out)} superseded by the collapse)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
