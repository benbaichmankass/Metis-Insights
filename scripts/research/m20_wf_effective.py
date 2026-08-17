#!/usr/bin/env python3
"""Re-read `wf_summary` with INERT folds separated out — the fold count that a
walk-forward record actually earned.

WHY THIS EXISTS
---------------
`wf_summary: N/M` counts a fold as a win whenever the gate returned `ok`, and
the gate returns `ok` for a fold in which the lever **changed nothing**
(`d_net_r == 0.0` and `d_max_dd == 0.0`). A no-op is not a win. It is not a
loss either — it is the lever never having been exercised on that fold, which
is a third thing the summary cannot say. So a bare `N/M` is not quotable: two
cells reading `5/6` can mean "won five folds" or "won two and sat out three".

Filed as BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS. This script is
the READ-SIDE half: it needs no re-sweep, because every corpus row that carries
a walk-forward already carries `wf_folds` with the per-fold deltas. The
degeneracy has therefore been measurable from committed data the whole time —
nothing was missing except a reader.

WHAT IT DOES NOT DO
-------------------
It does not re-grade anything and writes nothing. A verdict is the sweep's to
emit and a lever's disposition is Tier-3; this only makes the composition of an
existing number visible. `effective` is reported BESIDE `wf_summary`, never
instead of it — replacing the recorded figure would destroy the evidence that
the two differ.

INERT IS DEFINED ON BOTH DELTAS, NOT ONE
----------------------------------------
`d_net_r == 0.0 AND d_max_dd == 0.0`. A fold that moved drawdown while leaving
net R untouched DID exercise the lever and is a real (if unprofitable-looking)
observation, so keying on `d_net_r` alone would over-report inertness. The
conjunction is the honest test for "this lever did not fire in this fold".

Usage:
    python3 scripts/research/m20_wf_effective.py                # all affected rows
    python3 scripts/research/m20_wf_effective.py --shipped-only # live decisions
    python3 scripts/research/m20_wf_effective.py --leg trend_donchian_xrp_4h
    python3 scripts/research/m20_wf_effective.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "docs/research/m20-sweep-corpus.jsonl"
MATRIX = ROOT / "docs/research/exit-refinement-coverage.json"

# A live DECISION — a cell whose status says the lever is running or was judged
# ready to run. Distinguished from a negative because an inert walk-forward
# behind a SHIPPED lever costs money, while behind a negative it costs only
# knowledge.
SHIPPED_FAMILY = frozenset({"shipped", "passed_unshipped", "shipped_gate_failed"})


def is_inert(fold: dict) -> bool:
    """Did the lever change nothing at all on this fold?

    Both deltas, deliberately — see the module docstring.
    """
    return fold.get("d_net_r") == 0.0 and fold.get("d_max_dd") == 0.0


def grade_folds(folds: list) -> dict:
    """Split a wf fold list into won / inert / lost, with the denominator kept.

    `usable` is reported because a fold the sweep could not grade is a fourth
    state and must not be silently folded into `lost`.
    """
    ok = [f for f in folds if f.get("ok")]
    inert = [f for f in ok if is_inert(f)]
    return {
        "folds": len(folds),
        "ok": len(ok),
        "inert": len(inert),
        "effective": len(ok) - len(inert),
        "usable": sum(1 for f in folds if f.get("usable")),
    }


def newest_per_cell(rows: list) -> dict:
    """Newest run per (leg, lever, cell) among rows that carry a walk-forward.

    A stale run's fold detail is not evidence about the current record, and
    quoting one would be the same error as quoting a superseded verdict.
    """
    newest: dict = {}
    for r in rows:
        folds = r.get("wf_folds")
        if not isinstance(folds, list) or not folds:
            continue
        key = (r.get("leg"), r.get("lever"), r.get("cell"))
        prev = newest.get(key)
        if prev is None or (r.get("run_id") or "") > (prev.get("run_id") or ""):
            newest[key] = r
    return newest


def matrix_statuses() -> dict:
    """(leg, lever) -> status string, live rows only."""
    if not MATRIX.is_file():
        return {}
    mx = json.loads(MATRIX.read_text())
    out = {}
    for row in mx.get("rows", []):
        if row.get("execution") != "live":
            continue
        for lever in mx.get("lever_columns", []):
            cell = row.get(lever) or {}
            if cell.get("status"):
                out[(row["strategy"], lever)] = cell["status"]
    return out


def _self_test() -> int:
    # A fold that moved NEITHER delta is inert.
    assert is_inert({"d_net_r": 0.0, "d_max_dd": 0.0})
    # ...and one that moved EITHER is not — the conjunction is the point.
    assert not is_inert({"d_net_r": 0.0, "d_max_dd": -0.9})
    assert not is_inert({"d_net_r": 1.2, "d_max_dd": 0.0})
    # A missing delta must NOT read as 0.0: absent is "not recorded", which is
    # not "recorded as no change". This is the collapsed-state trap for a
    # field whose natural default happens to equal a meaningful value.
    assert not is_inert({}), "an absent delta must not be graded as inert"
    assert not is_inert({"d_net_r": 0.0}), "a half-recorded fold is not inert"

    folds = [
        {"ok": True, "usable": True, "d_net_r": 0.0, "d_max_dd": 0.0},    # inert
        {"ok": True, "usable": True, "d_net_r": 3.0, "d_max_dd": 0.0},    # win
        {"ok": False, "usable": True, "d_net_r": -0.5, "d_max_dd": 0.1},  # loss
    ]
    g = grade_folds(folds)
    assert g == {"folds": 3, "ok": 2, "inert": 1, "effective": 1, "usable": 3}, g

    # Supersession: the newest run wins, and it is picked by run_id not order.
    rows = [
        {"leg": "L", "lever": "V", "cell": "c", "run_id": "2026-08-14T00:00:00",
         "wf_folds": [{"ok": True, "d_net_r": 1.0, "d_max_dd": 0.0}]},
        {"leg": "L", "lever": "V", "cell": "c", "run_id": "2026-08-10T00:00:00",
         "wf_folds": [{"ok": True, "d_net_r": 0.0, "d_max_dd": 0.0}]},
    ]
    got = newest_per_cell(rows)[("L", "V", "c")]
    assert got["run_id"] == "2026-08-14T00:00:00", got
    # ...and a row with no walk-forward is absent entirely rather than counted
    # as a cell with zero folds — "never reached a walk-forward" is not "failed
    # one", the same distinction `wf_ran` exists to preserve in the corpus.
    assert newest_per_cell([{"leg": "L", "lever": "V", "cell": "z"}]) == {}
    print("self-test OK — inert needs BOTH deltas at zero, an absent delta is "
          "not inert, grading splits won/inert/lost, and supersession is by run_id.")
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--leg", help="restrict to one leg")
    ap.add_argument("--shipped-only", action="store_true",
                    help="only cells the matrix marks shipped/passed_unshipped")
    ap.add_argument("--all-rows", action="store_true",
                    help="include cells with zero inert folds")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    if not CORPUS.is_file():
        print(f"corpus missing: {CORPUS} — cannot check, and this is NOT a clean "
              f"result.", file=sys.stderr)
        return 1

    rows = [json.loads(x) for x in CORPUS.read_text().splitlines() if x.strip()]
    newest = newest_per_cell(rows)
    statuses = matrix_statuses()

    total_ok = total_inert = 0
    hits = []
    for (leg, lever, cell), r in newest.items():
        g = grade_folds(r["wf_folds"])
        total_ok += g["ok"]
        total_inert += g["inert"]
        status = statuses.get((leg, lever))
        if a.leg and leg != a.leg:
            continue
        if a.shipped_only and (status or "").split(":")[0] not in SHIPPED_FAMILY:
            continue
        if not g["inert"] and not a.all_rows:
            continue
        hits.append((leg, lever, cell, r, g, status))

    # STATE THE POPULATION before any finding — a count of affected cells over
    # an unstated denominator is the shape this repo has a rule about.
    print(f"POPULATION: {len(newest)} distinct (leg, lever, cell) carry a "
          f"walk-forward, newest run each, out of {len(rows)} corpus rows.")
    pct = (100.0 * total_inert / total_ok) if total_ok else 0.0
    print(f"           {total_inert} of {total_ok} `ok` folds are INERT ({pct:.1f}%) "
          f"across ALL cells (before any filter below).\n")

    if not hits:
        print("No cell matched the filter. Note this is a FILTER result, not a "
              "statement that the corpus is free of inert folds — the "
              "population line above is the denominator.")
        return 0

    hits.sort(key=lambda h: (-h[4]["inert"], h[0]))
    for leg, lever, cell, r, g, status in hits:
        eff_note = "  ⛔ EVERY win inert" if g["ok"] and g["inert"] == g["ok"] else ""
        print(f"{leg}  ·  {lever}  ·  {cell}")
        print(f"    status={status or '(not a live matrix cell)'}  "
              f"verdict={r.get('verdict')}  tp_cap_pct={r.get('tp_cap_pct')}  "
              f"run={(r.get('run_id') or '')[:19]}")
        print(f"    recorded wf_summary={r.get('wf_summary')}   "
              f"EFFECTIVE {g['effective']}/{g['folds']}   "
              f"(ok={g['ok']}, inert={g['inert']}){eff_note}")
    print(f"\n{len(hits)} cell(s) shown. `effective` is reported BESIDE the "
          f"recorded figure, never instead of it — nothing here re-grades a "
          f"verdict or moves a lever. Acting on any of these is Tier-3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
