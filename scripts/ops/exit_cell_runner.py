#!/usr/bin/env python3
"""The runner for MD-SOAK-EXIT-CELL-PATHB (pipeline PI-20260930-39SDYWCO-0001).

    python3 scripts/ops/exit_cell_runner.py [--apply] [--json] [--session-ref REF]

WHAT IT IS. The mandate (config/mandates.yaml, granted 2026-09-30, no autoland)
authorizes declaring a passing pure-stop bracket cell on a leg that sits ONLY on
Stage-1 soak rosters. `mandate_resolver.resolve_exit_cell()` decides one
(leg, cell); nothing chose WHICH cells to ask about or acted on the answer. This
does both, once a day (.github/workflows/exit-cell-mandate.yml).

  1. For every leg with a `path_b_wf_pass`, `axis: stop` cell in
     docs/research/e35-bracket-corpus.jsonl, pick ONE cell by the rule B4 stated
     in advance and the mandate carries: highest `wf_wins_effective`, tie-break
     `d_net_r`, over that leg's pure-stop `path_b_wf_pass` cells.
  2. Ask the resolver about it. FIRE / REFUSE / NEEDS-DATA.
  3. `--apply` on a FIRE edits ONE key -- `atr_stop_mult` -- on that ONE leg in
     config/strategies.yaml, flips that cell's `bracket_geometry` to `shipped` in
     docs/research/exit-refinement-coverage.json, and writes a firing record under
     comms/mandate_firings/. NEEDS-DATA files a pipeline row (deduped on its
     rerun command). REFUSE and NO-OP are report-only.

WHAT IT NEVER DOES. It writes no roster, no `mode:`, no `execution:`, no risk
cap, and it never merges: the workflow that calls it opens a `landing: hold`
PR and pings (operator, "Grant; firing opens a PR"). The mandate has no
`autoland` field and this script does not read one.

TWO THINGS A READER SHOULD KNOW BEFORE TRUSTING A FIRE
  * If the lowest-ranked cell is REFUSEd the leg is NOT retried with its
    second-best: "one cell per leg, chosen by the stated rule" is the grant. The
    report says so, so a stranded leg is visible rather than silent.
  * The timeout-binding bound (operator, 2026-09-30) is enforced by the RESOLVER
    (clause 2b), not here: a leg the audit OR the coverage matrix calls
    CONTAMINATED never reaches FIRE. The report prints both readings.

Nothing here regenerates comms/strategy_evidence/<leg>.json: that needs the
harness and candle data, so the workflow runs build_strategy_evidence.py after
this and aborts (no PR) if it fails. Config edits can also ripple into tests that
pin the old value (#14332 had to touch tests/test_etf_intraday_rollout2b_wiring.py);
a fire's PR goes red in that case and a person fixes the pin.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import mandate_resolver as mr  # noqa: E402
import pipeline  # noqa: E402

MATRIX_REL = "docs/research/exit-refinement-coverage.json"
FIRINGS_REL = mr.FIRINGS_DIR_REL
MID = mr.EXIT_CELL_MANDATE_ID


# --------------------------------------------------------------------------
# choosing the cell
# --------------------------------------------------------------------------
def _corpus(root: Path) -> List[Dict[str, Any]]:
    p = root / mr.BRACKET_CORPUS_REL
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def pick_cells(root: Path) -> Dict[str, Dict[str, Any]]:
    """leg -> the ONE newest corpus row of its B4-selected cell."""
    newest: Dict[tuple, Dict[str, Any]] = {}
    for r in _corpus(root):
        if (r.get("gate_verdict") != mr.PATHB_VERDICT or r.get("axis") != "stop"
                or r.get("timeout") is not None or r.get("tp_r") is not None):
            continue
        k = (r.get("leg"), r.get("cell"))
        if k not in newest or str(r.get("sweep_generated_at") or "") > str(
                newest[k].get("sweep_generated_at") or ""):
            newest[k] = r
    best: Dict[str, Dict[str, Any]] = {}
    for (leg, _cell), r in newest.items():
        key = (float(r.get("wf_wins_effective") or 0), float(r.get("d_net_r") or 0))
        cur = best.get(leg)
        if cur is None or key > (float(cur.get("wf_wins_effective") or 0),
                                 float(cur.get("d_net_r") or 0)):
            best[leg] = r
    return best


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def run(root: Path = REPO) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"fire": [], "needs_data": [], "refuse": [], "noop": []}
    for leg, row in sorted(pick_cells(root).items()):
        cell = row["cell"]
        res = mr.resolve_exit_cell(leg, cell, root=root)
        entry = {"leg": leg, "cell": cell, "verdict": res["verdict"], "clause": res["clause"],
                 "detail": res["detail"], "evidence": res["evidence"], "row": row,
                 "timeout_binding": res["evidence"].get("timeout_binding"), "result": res}
        if res["verdict"] == mr.FIRE:
            out["fire"].append(entry)
        elif res["verdict"] == mr.NEEDS_DATA:
            out["needs_data"].append(entry)
        elif res["clause"] == "R-NO-OP":
            out["noop"].append(entry)
        else:
            out["refuse"].append(entry)
    return out


# --------------------------------------------------------------------------
# acting on a FIRE (writes ONLY the three things the docstring names)
# --------------------------------------------------------------------------
def edit_strategies_text(text: str, leg: str, old: Any, new: float, note: str) -> str:
    """Change `atr_stop_mult` on ONE leg's block, preserving everything else.
    Raises ValueError if the leg or the key is absent or the old value is not
    what the resolver read -- a text edit that cannot prove what it replaced
    does not run."""
    m = re.search(rf"^  {re.escape(leg)}:[ \t]*\n", text, re.M)
    if not m:
        raise ValueError(f"{leg} block not found")
    nxt = re.compile(r"^  \S", re.M).search(text, m.end())
    end = nxt.start() if nxt else len(text)
    block = text[m.end():end]
    km = re.search(r"^(    atr_stop_mult:[ \t]*)(\S+)([^\n]*)$", block, re.M)
    if not km:
        raise ValueError(f"{leg} declares no atr_stop_mult -- a new key would change the default "
                         "stop, which is not what the grant covers")
    if float(km.group(2)) != float(old):
        raise ValueError(f"{leg} atr_stop_mult is {km.group(2)}, resolver read {old}")
    new_line = f"{km.group(1)}{new:g}  # {note}"
    block = block[:km.start()] + new_line + block[km.end():]
    return text[:m.end()] + block + text[end:]


def flip_matrix(matrix_text: str, leg: str, row: Dict[str, Any], note: str) -> str:
    d = json.loads(matrix_text)
    for r in d["rows"]:
        if r.get("strategy") == leg:
            cell = r["bracket_geometry"]
            cell["status"] = "shipped"
            cell["ref"] = f"{cell.get('ref', '')} || {note}".strip(" |")
            if row.get("base_is_trades") is not None:
                cell["base_is"] = row["base_is_trades"]
            if row.get("base_oos_trades") is not None:
                cell["base_oos"] = row["base_oos_trades"]
            return json.dumps(d, indent=1, ensure_ascii=False) + "\n"
    raise ValueError(f"{leg} has no coverage-matrix row")


def apply_fire(entry: Dict[str, Any], root: Path, today: str, run_ref: str) -> List[str]:
    leg, cell, row = entry["leg"], entry["cell"], entry["row"]
    edit = entry["result"]["proposal"]["config_edit"]
    note = (f"{MID} {today}: cell {cell} ({mr.PATHB_VERDICT}, wf "
            f"{entry['evidence']['walkforward']}, d_net_r {row['d_net_r']:+.4f}); was {edit['from']}")
    sp, mp = root / mr.STRATEGIES_REL, root / MATRIX_REL
    sp.write_text(edit_strategies_text(sp.read_text(encoding="utf-8"), leg, edit["from"],
                                       edit["to"], note), encoding="utf-8")
    mp.write_text(flip_matrix(mp.read_text(encoding="utf-8"), leg, row,
                              f"SHIPPED {today} under {MID} (cell `{cell}`, run {run_ref}); "
                              f"corpus row {row.get('measurement_key')}"), encoding="utf-8")
    fp = root / FIRINGS_REL / f"{today}__{MID}__{leg}__{cell}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps({
        "mandate": MID, "leg": leg, "cell": cell, "fired_on": today, "run": run_ref,
        "config_edit": edit, "evidence": entry["evidence"], "timeout_binding": entry["timeout_binding"],
        "corpus_row": {k: row.get(k) for k in ("measurement_key", "source", "sweep_generated_at",
                                               "d_net_r", "d_max_dd", "wf_wins_effective", "wf_usable")},
    }, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return [mr.STRATEGIES_REL, MATRIX_REL, str(fp.relative_to(root))]


def file_needs_data(entries: List[Dict[str, Any]], clause: str, session_ref: str,
                    store: Path = pipeline.STORE) -> Optional[str]:
    """ONE pipeline row per NEEDS-DATA *clause* (not per leg: 9 legs sharing
    R-BASE-N is one cause), skipped when an OPEN row already names this mandate
    and that clause -- PI-20260930-39SDYWCO-0002 covers R-BASE-N today."""
    for it in pipeline.load(store).items.values():
        w = str(it.get("what") or "")
        if it.get("state") in pipeline.OPEN_STATES and MID in w and clause in w:
            return None
    legs = ", ".join(f"{e['leg']}:{e['cell']}" for e in entries)
    task = entries[0]["result"].get("data_task") or {}
    rerun = "python3 scripts/ops/exit_cell_runner.py --json"
    item = {"id": pipeline.mint_id(session_ref, store=store),
            "what": f"{MID}: {len(entries)} cell(s) are NEEDS-DATA at {clause} -- "
                    f"{task.get('what') or entries[0]['detail']} [{legs}]",
            "origin": {"kind": "session", "ref": session_ref, "rerun": rerun},
            "due_when": {"kind": "observation",
                         "clears_when": f"{rerun} lists no cell NEEDS-DATA at {clause}",
                         "check_every_days": task.get("check_every_days", 7)},
            "next_action": task.get("next_action", "dispatch_lane"), "state": "queued"}
    return pipeline.append(item, store, intent="new")["id"]


def render(out: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = [f"### {MID} runner", "",
             f"fire {len(out['fire'])} · needs-data {len(out['needs_data'])} · "
             f"refuse {len(out['refuse'])} · already-declared {len(out['noop'])}", ""]
    for k, title in (("fire", "FIRE"), ("needs_data", "NEEDS-DATA"), ("refuse", "REFUSE")):
        for e in out[k]:
            lines.append(f"- **{title}** `{e['leg']}` `{e['cell']}` [{e['clause']}] {e['detail']}")
            if e.get("timeout_binding"):
                tb = e["timeout_binding"]
                lines.append(f"  - timeout-binding: audit {tb.get('audit')} "
                             f"({tb.get('binding')}/{tb.get('graded_pairs')} binding), matrix {tb.get('matrix')}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--apply", action="store_true", help="edit files on a FIRE and file NEEDS-DATA rows")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--session-ref", default="session_exit_cell_runner")
    ap.add_argument("--run-ref", default="local")
    ap.add_argument("--today", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = run(root)
    written: List[str] = []
    filed: List[str] = []
    if a.apply:
        for e in out["fire"]:
            written += apply_fire(e, root, a.today, a.run_ref)
        by_clause: Dict[str, List[Dict[str, Any]]] = {}
        for e in out["needs_data"]:
            by_clause.setdefault(e["clause"], []).append(e)
        for clause, es in sorted(by_clause.items()):
            pid = file_needs_data(es, clause, a.session_ref)
            if pid:
                filed.append(pid)
    if a.json:
        slim = {k: [{f: e[f] for f in ("leg", "cell", "verdict", "clause", "detail", "timeout_binding")}
                    for e in v] for k, v in out.items()}
        print(json.dumps({"result": slim, "written": written, "filed": filed}, indent=2))
    else:
        print(render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
