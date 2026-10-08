#!/usr/bin/env python3
"""Map ONE `ict-scalp-exit-sweep` run onto the research-result contract -- ONE RECORD PER LEG.

WHY THIS EXISTS. `ict-scalp-exit-sweep.yml` ended in a PR comment and an expiring
artifact, so a queue unit routed to it (RQ-20261007-004) could never satisfy its own
`lands:` clause: nothing queryable came out of the run. This is the scalp twin of
`scripts/research/m20_sweep_result.py` -- the same one-record-per-DISPATCHED-leg
rule, so a leg whose shard died is a visible `no_data` / `producer_failed` record
rather than an absence nobody can tell from a leg nobody asked for.

THE VERDICT IS READ OFF THE SWEEP'S OWN GATE PLUS THE DOCTRINE'S CALIBRATION CLAUSE.
RQ-20261007-004 registers (before any run): PASS iff a `tp_extend` / `tp_retarget`
cell (a) IMPROVES the calibration share vs the config-exact base in BOTH IS and OOS
AND (b) clears the P2 gate (IS/OOS Path A, walk-forward). `calibration_share` is read
from each cell's `tp_geometry` block, never re-derived; a cell that clears (b) while
failing (a) is "P&L without prediction" and does not pass. An unreadable calibration
side is `None`, and `None` never passes.

    pass                 a swept cell improved calibration in both windows AND survived
                         the walk-forward gate
    no_action_warranted  cells were swept, none did both -- a MEASUREMENT (n = base
                         OOS trades), not a shrug
    indeterminate        base OOS trades below the 25 floor (`insufficient_base`), or
                         no tp cell in the verdicts
    not_applicable       the leg's shard produced no verdicts.json: we could not look

Each leg's full `verdicts.json` (the per-cell calibration the unit's rule reads) is
copied under `--measurements-dir` so the record POINTS at a committed measurement
instead of an expiring artifact.

Tier-1: reads JSON, writes JSONL. No live path.

    python3 scripts/research/m27/scalp_sweep_result.py --self-test
    python3 scripts/research/m27/scalp_sweep_result.py --verdicts-dir sweep_out \\
        --legs ict_scalp_5m,ict_scalp_xrp_5m --out rr/records.jsonl \\
        --measurements-dir research/results/RQ-20261007-004/measurements
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

MIN_OOS_TRADES = 25
TP_LEVERS = ("tp_extend", "tp_retarget")


def _num(v: Any) -> Optional[float]:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)


def _cell_summary(tag: str, c: Dict[str, Any], base: Dict[str, Any],
                  wf: Dict[str, Any]) -> Dict[str, Any]:
    """One tp cell: the gate half (b) and the calibration half (a), never merged."""
    tg = c.get("tp_geometry") or {}
    cal = {}
    improves = []
    for w in ("IS", "OOS"):
        b = tg.get(w) or {}
        cs, bs = _num(b.get("calibration_share_cell")), _num(b.get("calibration_share_base"))
        cal[w] = {"cell": cs, "base": bs}
        improves.append(None if cs is None or bs is None else cs > bs)
    cal_improves = None if any(x is None for x in improves) else all(improves)
    wf_ok = (wf.get(tag) or {}).get("verdict") == "PASS"
    gate = c.get("verdict") == "CANDIDATE" and wf_ok
    return {
        "cell": tag, "lever": c.get("lever"),
        "d_total_r_IS": _d(c, base, "IS"), "d_total_r_OOS": _d(c, base, "OOS"),
        "gate_cleared": gate, "calibration": cal, "calibration_improves_both": cal_improves,
        "passes": bool(gate and cal_improves is True),
        "p_and_l_without_prediction": bool(gate and cal_improves is False),
        "total_extends_OOS": ((tg.get("OOS") or {}).get("cell") or {}).get("total_extends"),
    }


def _d(c: Dict[str, Any], base: Dict[str, Any], w: str) -> Optional[float]:
    try:
        return round(float(c[w]["total_r"]) - float(base[w]["total_r"]), 4)
    except (KeyError, TypeError, ValueError):
        return None


def summarize(verdicts: Dict[str, Optional[Dict[str, Any]]], *, legs: List[str],
              sweep_result: str) -> List[Dict[str, Any]]:
    """One record-part per dispatched leg. Pure."""
    out: List[Dict[str, Any]] = []
    for leg in legs:
        v = verdicts.get(leg)
        if not v:
            out.append({
                "verdict": "not_applicable",
                "read_state": "no_data" if sweep_result == "success" else "producer_failed",
                "n": None,
                "population": f"leg {leg}; the shard produced no verdicts.json",
                "note": (f"The dispatched leg {leg!r} contributed no verdicts "
                         f"(sweep job result: {sweep_result}); this record exists so a "
                         f"dropped leg is visible rather than absent."),
                "measurement": {"leg": leg}})
            continue
        base = v.get("baseline") or {}
        wf = v.get("walkforward") or {}
        cells = [_cell_summary(t, c, base, wf) for t, c in (v.get("cells") or {}).items()
                 if c.get("lever") in TP_LEVERS]
        base_oos = base.get("OOS") or {}
        n = base_oos.get("trades")
        n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else None
        passing = [c["cell"] for c in cells if c["passes"]]
        no_pred = [c["cell"] for c in cells if c["p_and_l_without_prediction"]]
        if not cells:
            verdict, note = "indeterminate", "no tp_extend / tp_retarget cell in this leg's verdicts"
        elif n is None or n < MIN_OOS_TRADES:
            verdict = "indeterminate"
            note = (f"insufficient_base: base OOS trades {n} < the {MIN_OOS_TRADES} floor; "
                    f"nothing is graded, and a thin base is not a failed lever")
        elif passing:
            verdict = "pass"
            note = (f"{len(passing)} cell(s) improved calibration in BOTH windows and survived "
                    f"the walk-forward gate: {passing[:6]}. A candidate, not a change: the "
                    f"declaration is Tier-3.")
        else:
            verdict = "no_action_warranted"
            note = (f"{len(cells)} cell(s) swept; none both improved calibration in IS+OOS and "
                    f"cleared the gate. {len(no_pred)} cleared the gate while NOT improving "
                    f"calibration ('P&L without prediction', not a pass): {no_pred[:6]}.")
        out.append({
            "verdict": verdict, "read_state": "measured", "n": n if n is not None else 0,
            "population": (f"leg {leg}; OOS side of the IS/OOS split ({v.get('split')}) over the "
                           f"tp_revision grid ({len(cells)} cell(s)); n is base OOS trades"),
            "note": note,
            "measurement": {"leg": leg, "split": v.get("split"), "tp_at_r": v.get("tp_at_r"),
                            "cells_swept": len(cells), "cells_passed": len(passing),
                            "passing_cells": passing[:20],
                            "p_and_l_without_prediction": no_pred[:20],
                            "base_trades_OOS": n, "cells": cells}})
    return out


def _self_test() -> int:
    def cell(lever, verdict, cs_is, bs_is, cs_oos, bs_oos):
        mk = lambda cs, bs: {"calibration_share_cell": cs, "calibration_share_base": bs}  # noqa: E731
        return {"lever": lever, "verdict": verdict,
                "IS": {"total_r": 2.0}, "OOS": {"total_r": 2.0},
                "tp_geometry": {"IS": mk(cs_is, bs_is), "OOS": mk(cs_oos, bs_oos)}}
    base = {"IS": {"total_r": 1.0}, "OOS": {"total_r": 1.0, "trades": 80}}
    v = {"split": "2025-07-01", "baseline": base,
         "cells": {"good": cell("tp_extend", "CANDIDATE", .5, .3, .6, .4),
                   "pnl_only": cell("tp_extend", "CANDIDATE", .2, .3, .2, .4),
                   "unread": cell("tp_retarget", "CANDIDATE", None, .3, .5, .4),
                   "gate_fail": cell("tp_retarget", "honest_negative", .9, .3, .9, .4),
                   "other": cell("stale_stop", "CANDIDATE", .9, .3, .9, .4)},
         "walkforward": {"good": {"verdict": "PASS"}, "pnl_only": {"verdict": "PASS"},
                         "unread": {"verdict": "PASS"}}}
    thin = {"split": "s", "baseline": {"IS": {"total_r": 0}, "OOS": {"total_r": 0, "trades": 10}},
            "cells": {"good": cell("tp_extend", "CANDIDATE", .5, .3, .6, .4)},
            "walkforward": {"good": {"verdict": "PASS"}}}
    nothing = {"split": "s", "baseline": base,
               "cells": {"x": cell("tp_extend", "honest_negative", .1, .3, .1, .4)}}
    recs = summarize({"a": v, "thin": thin, "none": nothing, "empty": None},
                     legs=["a", "thin", "none", "empty", "missing"], sweep_result="success")
    by = {r["measurement"]["leg"]: r for r in recs}
    fails = 0

    def case(label, got, want):
        nonlocal fails
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            fails += 1
            print(f"        got {got!r} want {want!r}")
    case("positive: calibration improved in IS+OOS AND walk-forward survived -> pass",
         (by["a"]["verdict"], by["a"]["measurement"]["passing_cells"]), ("pass", ["good"]))
    case("negative: gate cleared but calibration WORSE is 'P&L without prediction', not a pass",
         by["a"]["measurement"]["p_and_l_without_prediction"], ["pnl_only"])
    case("negative: an unreadable calibration side (None) never passes",
         "unread" in by["a"]["measurement"]["passing_cells"], False)
    case("a non-tp lever's cell is not graded here", by["a"]["measurement"]["cells_swept"], 4)
    case("base OOS trades below the floor -> indeterminate, never pass",
         by["thin"]["verdict"], "indeterminate")
    case("swept, nothing improved -> no_action_warranted over n=80 (a measurement)",
         (by["none"]["verdict"], by["none"]["read_state"], by["none"]["n"]),
         ("no_action_warranted", "measured", 80))
    case("a dispatched leg with no verdicts -> no_data / n=null, never silently absent",
         (by["empty"]["read_state"], by["empty"]["n"]), ("no_data", None))
    case("record count = the DISPATCHED population", len(recs), 5)
    case("a failed sweep job grades producer_failed, not no_data",
         summarize({}, legs=["a"], sweep_result="failure")[0]["read_state"], "producer_failed")
    print(f"scalp_sweep_result --self-test: {fails} failure(s)")
    return 1 if fails else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verdicts-dir", default="sweep_out",
                    help="dir holding one `scalp-sweep-<leg>/verdicts.json` per leg")
    ap.add_argument("--legs", default="", help="CSV of the DISPATCHED legs (required)")
    ap.add_argument("--out", default="rr/records.jsonl")
    ap.add_argument("--measurements-dir", default="")
    ap.add_argument("--sweep-result", default="success")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    legs = [x.strip() for x in a.legs.split(",") if x.strip()]
    if not legs:
        print("::error::scalp_sweep_result: no --legs. The dispatched population must be "
              "declared, or a dropped leg is invisible.", file=sys.stderr)
        return 1
    if not a.measurements_dir:
        print("::error::scalp_sweep_result: --measurements-dir is required. A measured "
              "result must say WHERE THE MEASUREMENT LIVES (research_result.validate "
              "refuses it otherwise).", file=sys.stderr)
        return 1
    verdicts: Dict[str, Optional[Dict[str, Any]]] = {}
    for leg in legs:
        p = Path(a.verdicts_dir) / f"scalp-sweep-{leg}" / "verdicts.json"
        try:
            verdicts[leg] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            verdicts[leg] = None
    parts = summarize(verdicts, legs=legs, sweep_result=a.sweep_result)
    extra: List[str] = []
    if a.measurements_dir:
        md = Path(a.measurements_dir)
        md.mkdir(parents=True, exist_ok=True)
        for part in parts:
            leg = part["measurement"]["leg"]
            src = Path(a.verdicts_dir) / f"scalp-sweep-{leg}" / "verdicts.json"
            if verdicts.get(leg) and src.exists():
                dst = md / f"{leg}.verdicts.json"
                shutil.copyfile(src, dst)
                part["artifact_store"] = dst.as_posix()
                part["artifact_locator"] = (f"the whole file: per-cell IS/OOS metrics, the "
                                            f"`tp_geometry` calibration block and the walk-forward "
                                            f"for leg {leg!r}")
                part["rows_landed"] = len(verdicts[leg].get("cells") or {})
                extra.append(dst.as_posix())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for part in parts:
            fh.write(json.dumps(part, sort_keys=True, ensure_ascii=False) + "\n")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(f"min_rows={len(parts)}\n")
            fh.write(f"extra_paths={' '.join(extra)}\n")
    print(f"scalp_sweep_result: wrote {len(parts)} record part(s) to {out}; "
          f"{len(extra)} measurement file(s) staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
