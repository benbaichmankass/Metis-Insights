#!/usr/bin/env python3
"""Pretty-print one replay_pregate_live RG4 JSON as a single scorecard line.

Split out of rg4_targeted.sh so the shell never embeds an inline ``python -c``
with quotes/parens (which the trainer-vm-diag issue relay mis-parses).

  python scripts/ml/_rg4_print.py <rg4.json> <model_id>
"""
import json
import sys


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
