#!/usr/bin/env python3
"""Pretty-print one replay_pregate_live RG4 JSON as a scorecard line + power verdict.

Split out of rg4_targeted.sh so the shell never embeds an inline ``python -c``
with quotes/parens (which the trainer-vm-diag issue relay mis-parses).

The POWER line implements the shadow→advisory readiness standard
(``MB-20260705-FC-ADVISORY-READINESS``): an RG4 verdict is only trustable when
the stage has ≥ MIN_POS positive-class rows spanning ≥ MIN_EPISODES distinct
volatile episodes — below that, ANY verdict (incl. ANTI_PREDICTIVE) is
noise-dominated and must be read as UNPOWERED, never as evidence. This is the
guard that stops a stale-mirror thin-sample read (the 2026-07-04 fc first-look)
from being mistaken for a signal again.

  python scripts/ml/_rg4_print.py <rg4.json> <model_id>
"""
import json
import sys

MIN_POS = 40
MIN_EPISODES = 5


def main() -> int:
    path, mid = sys.argv[1], sys.argv[2]
    with open(path) as fh:
        d = json.load(fh)
    by_stage = d.get("by_stage", {}) or {}
    stages = {
        s: (v.get("auc"), v.get("verdict"), "mf=" + str(v.get("has_market_features")))
        for s, v in by_stage.items()
    }
    print(f"  {mid} recs={d.get('n_records')} unlab={d.get('n_unlabeled')} {stages}")
    for s, v in by_stage.items():
        n_pos = v.get("n_pos")
        eps = v.get("pos_episodes")
        if n_pos is None:  # pre-power-fields JSON — say so rather than guess
            print(f"    [{s}] POWER: unknown (rg4 json predates n_pos/pos_episodes)")
            continue
        powered = (n_pos >= MIN_POS) and ((eps or 0) >= MIN_EPISODES)
        tag = "POWERED" if powered else "UNPOWERED — verdict is NOT evidence"
        print(f"    [{s}] POWER: {tag} (n_pos={n_pos}/{MIN_POS}, "
              f"episodes={eps}/{MIN_EPISODES}, n={v.get('n')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
