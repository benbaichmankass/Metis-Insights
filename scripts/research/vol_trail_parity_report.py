#!/usr/bin/env python3
# wiring: manual-only — the M20-EXITS lane / manager runs it against a copy of
# runtime_logs/vol_trail_shadow.jsonl pulled via the diag relay, to close P5.
"""Summarise the log-only vol_trail shadow (src/runtime/trail_vol_shadow.py).

Reports, over the rows supplied: n rows, n distinct packages, closed-bar fire
rate, raw-vs-closed disagreement rate (the CA-B01 forming-bar skew, measured),
and how often the shadow stop differs from the live stop. Population is always
printed; an empty file prints n=0 (not "0% disagreement").
"""
from __future__ import annotations

import json
import sys


def summarise(rows):
    n = len(rows)
    pk = {r.get("order_package_id") for r in rows}
    fire = sum(bool(r.get("would_fire_closed")) for r in rows)
    dis = sum(bool(r.get("would_fire_closed")) != bool(r.get("would_fire_raw")) for r in rows)
    diff = sum(bool(r.get("stops_differ")) for r in rows)
    return {"n_rows": n, "n_packages": len(pk), "would_fire_closed": fire,
            "closed_vs_raw_disagree": dis, "stops_differ": diff}


def main(argv):
    path = argv[1] if len(argv) > 1 else "runtime_logs/vol_trail_shadow.jsonl"
    rows = [json.loads(ln) for ln in open(path) if ln.strip()]
    print(json.dumps(summarise(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
