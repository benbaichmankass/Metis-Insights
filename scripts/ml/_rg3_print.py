#!/usr/bin/env python3
"""Pretty-print a replay_pregate_fleet RG3 JSON as a per-head scorecard.

Split out so the trainer-vm-diag relay never embeds an inline ``python -c``
(the issue relay mis-parses quoted -c). Prints overall AUC + verdict + n, and
the per-fold AUCs — the LAST fold is the most-recent window, the closest
in-session proxy for the live RG4 sample a brand-new head can't have yet.

  python scripts/ml/_rg3_print.py /tmp/rg3_eth.json
"""
import json
import sys


def main() -> int:
    with open(sys.argv[1]) as fh:
        d = json.load(fh)
    print(f"RG3 heads scored: {d.get('n_scored')} / {d.get('n_models')}")
    for r in d.get("results", []):
        ov = r.get("overall") or {}
        folds = r.get("folds") or []
        fold_aucs = [round(f.get("auc"), 3) if f.get("auc") is not None else None
                     for f in folds]
        recent = fold_aucs[-1] if fold_aucs else None
        print(f"  {r.get('model_id')} {r.get('symbol')}/{r.get('timeframe')} "
              f"overall_auc={ov.get('auc')} {r.get('auc_verdict')} "
              f"n={r.get('n_scored')}")
        print(f"      folds(auc)={fold_aucs}  recent_fold={recent}")
    for e in d.get("errors", []):
        print(f"  ERROR {e.get('model_id')}: {e.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
