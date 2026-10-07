#!/usr/bin/env python3
# wiring: manual-only - a one-off repair of the 8 result rows the PyYAML-less result job discarded (2026-10-04..06); re-run by hand if the same note ever reappears (--self-test, dry run by default)
"""Re-grade result rows that landed `wiring_only` because the RUNNER had no PyYAML.

WHAT HAPPENED (MEASURED 2026-10-06, lane RQ-FIX)
-------------------------------------------------
`research-harness-dispatch.yml`'s result job ran `harness_dispatch_result.py`
without PyYAML installed. `load_unit()` swallowed the ImportError, the mapper
stamped every unit-attached result `verdict=wiring_only` with the measurement
note "research unit named but its queue file is missing/unreadable", and the
grader parked the unit in `needs_review`. The unit file was present at every
one of those commits; the measurement block (total_trades, net_total_r, ...)
is intact on every row. 8 rows across 6 units on main.

WHAT THIS DOES
---------------
For every such row it reads the unit's OWN pre-registered `decision_rule`
(from the committed queue) and applies it to the LANDED measurement through
the same `apply_unit_rule()` the producer uses -- never a second rule
implementation -- and APPENDS a corrected record to the same JSONL file:
same `produced_by` (run_id, commit, tool, workflow), same population, same n,
same measurement, `read_state: measured`, the unit's rule id when the rule
could be applied, and a note that says exactly why the original row read as
it did. Append-only: the original rows are never edited or deleted.
`queue_grade.grade_e5` lets a later mechanical row of the same run supersede
its `wiring_only` original, so the unit can then be graded.

WHAT IT DOES NOT DO
--------------------
It does not invent a rule. A unit whose registered rule names a statistic the
harness does not produce (the roster-walkforward template's "net-of-cost OOS
R", where the harness dispatch measures in-sample `net_total_r`) still lands
`wiring_only` -- but with the TRUE reason in its note, instead of blaming an
absent file. Those units are reported by this script and need a decision
about their rule, which is not this script's to take.

Idempotent: a file that already carries a re-graded row for a run is skipped.
Dry run by default; `--write` appends.

Run:
    python3 scripts/research/regrade_wiring_only_results.py            # report
    python3 scripts/research/regrade_wiring_only_results.py --write    # append
    python3 scripts/research/regrade_wiring_only_results.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import harness_dispatch_result as hdr  # noqa: E402
from research_result import build, validate  # noqa: E402

_REPO = _HERE.parents[1]
RESULTS_ROOT = _REPO / "research" / "results"
QUEUE_DIR = _REPO / "research" / "queue"

#: The exact note the producer wrote when load_unit() returned None.
DISCARDED_NOTE = "research unit named but its queue file is missing/unreadable"
REGRADE_PREFIX = "re-graded from the landed measurement"


def discarded_rows(results_root: Path = RESULTS_ROOT) -> list[dict[str, Any]]:
    """Every (file, row) whose verdict is wiring_only with the discarded note."""
    out: list[dict[str, Any]] = []
    for f in sorted(results_root.glob("*/*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rows = []
        for ln in lines:
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    rows.append(None)
        for r in rows:
            if not isinstance(r, dict) or r.get("verdict") != hdr.WIRING_ONLY:
                continue
            note = str((r.get("measurement") or {}).get("note") or "")
            if not note.startswith(DISCARDED_NOTE):
                continue
            run_id = str((r.get("produced_by") or {}).get("run_id") or "")
            already = any(isinstance(x, dict)
                          and str((x.get("produced_by") or {}).get("run_id") or "") == run_id
                          and str(x.get("note") or "").startswith(REGRADE_PREFIX) for x in rows)
            out.append({"file": f, "row": r, "already_regraded": already})
    return out


def regrade(row: dict[str, Any], unit: dict[str, Any] | None) -> dict[str, Any]:
    """The corrected record for one discarded row. Validated by the caller."""
    meas = dict(row.get("measurement") or {})
    original_note = meas.pop("note", "")
    verdict, rule_id, reg, how = hdr.apply_unit_rule(unit, meas)
    run_id = str((row.get("produced_by") or {}).get("run_id") or "")
    pop = str((row.get("population") or {}).get("description") or "")
    if verdict != hdr.WIRING_ONLY:
        pop = (f"{pop} — re-graded against {row.get('research_unit')}'s pre-registered rule "
               f"{rule_id} (net_total_r and total_trades from the landed measurement block)")
    note = (f"{REGRADE_PREFIX}: original row was wiring_only because PyYAML was absent on the "
            f"runner (run {run_id}); the unit file was present at commit "
            f"{(row.get('produced_by') or {}).get('commit_sha')}. {how}")
    if verdict == hdr.WIRING_ONLY:
        note += (" -- still NOT a decision: the rule cannot be applied to what this harness "
                 "measures; the unit's rule needs a decision, not this script")
    meas["note"] = how
    meas["original_note"] = original_note
    produced = row.get("produced_by") or {}
    art = row.get("artifact") or {}
    n = (row.get("population") or {}).get("n")
    return build(research_unit=row.get("research_unit"), decision_rule_id=rule_id,
                 decision_rule_registered_at=reg, verdict=verdict, read_state="measured",
                 population_description=pop, n=n, workflow=str(produced.get("workflow") or ""),
                 run_id=run_id, run_attempt=str(produced.get("run_attempt") or ""),
                 run_url=str(produced.get("run_url") or ""),
                 commit_sha=str(produced.get("commit_sha") or ""),
                 tool=str(produced.get("tool") or ""),
                 power_state=str(row.get("power_state") or ""), measurement=meas,
                 artifact_store=str(art.get("store") or ""),
                 artifact_locator=str(art.get("locator") or ""),
                 rows_landed=art.get("rows_landed"), note=note)


def run(*, write: bool, results_root: Path = RESULTS_ROOT, queue_dir: Path = QUEUE_DIR,
        out=sys.stdout) -> dict[str, Any]:
    found = discarded_rows(results_root)
    report: dict[str, Any] = {"found": len(found), "appended": 0, "skipped": 0, "rows": []}
    for item in found:
        row, f = item["row"], item["file"]
        uid = str(row.get("research_unit") or "")
        run_id = str((row.get("produced_by") or {}).get("run_id") or "")
        if item["already_regraded"]:
            report["skipped"] += 1
            print(f"  {uid} run {run_id}: already re-graded in {f.name} -- skipped", file=out)
            continue
        unit = hdr.load_unit(uid, queue_dir)
        rec = regrade(row, unit)
        problems = validate(rec)
        if problems:
            raise SystemExit(f"{uid} run {run_id}: inadmissible re-grade record: {problems}")
        m = rec["measurement"]
        line = (f"  {uid} run {run_id}: {row['verdict']} -> {rec['verdict']} under "
                f"{rec['decision_rule']['id']} (net_total_r={m.get('net_total_r')} "
                f"total_trades={m.get('total_trades')})")
        print(line + ("" if write else "  [dry run]"), file=out)
        report["rows"].append({"unit": uid, "run_id": run_id, "verdict": rec["verdict"],
                               "rule_id": rec["decision_rule"]["id"],
                               "net_total_r": m.get("net_total_r"), "total_trades": m.get("total_trades")})
        if write:
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
            report["appended"] += 1
    print(f"regrade_wiring_only_results: {report['found']} discarded row(s) found, "
          f"{report['appended']} appended, {report['skipped']} already re-graded"
          + ("" if write else " (dry run: pass --write to append)"), file=out)
    return report


def _self_test() -> int:
    import io
    import tempfile
    failures = 0
    rule39 = ("PASS IF net_total_r > 0 AND n_trades >= 39. FAIL IF net_total_r <= 0 AND "
              "n_trades >= 39. indeterminate IF n_trades < 39.")
    oos = ("PASS IF net-of-cost OOS R > 0 with n >= 39. FAIL IF net-of-cost OOS R <= 0 with "
           "n >= 39. indeterminate IF n < 39.")

    def planted(uid: str, run_id: str, net: float, n: int) -> str:
        rec = build(research_unit=uid, decision_rule_id=hdr.DECISION_RULE_ID,
                    decision_rule_registered_at=hdr.DECISION_RULE_REGISTERED_AT,
                    verdict=hdr.WIRING_ONLY, read_state="measured", population_description="p",
                    n=n, workflow="research-harness-dispatch", run_id=run_id, commit_sha="abc",
                    # collapsed-state: accruing — a FIXTURE value, not a branch: this script
                    # carries `power_state` through verbatim from the original row (see
                    # regrade(): `power_state=str(row.get("power_state") or "")`) and never
                    # reads it; the planted row just needs one admissible value.
                    tool="scripts/backtest_pullback.py", power_state="accruing",
                    measurement={"total_trades": n, "net_total_r": net,
                                 "note": DISCARDED_NOTE + ", so its rule could not be applied"},
                    artifact_store="research/results/", artifact_locator="x")
        assert not validate(rec)
        return json.dumps(rec, sort_keys=True)

    with tempfile.TemporaryDirectory() as td:
        root, q = Path(td) / "results", Path(td) / "queue"
        q.mkdir()
        (root / "RQ-20300101-001").mkdir(parents=True)
        (root / "RQ-20300101-002").mkdir(parents=True)
        (root / "RQ-20300101-003").mkdir(parents=True)
        (root / "RQ-20300101-001" / "1.jsonl").write_text(planted("RQ-20300101-001", "1", -5.45, 397) + "\n")
        (root / "RQ-20300101-001" / "2.jsonl").write_text(planted("RQ-20300101-001", "2", 3.0, 10) + "\n")
        (root / "RQ-20300101-002" / "3.jsonl").write_text(planted("RQ-20300101-002", "3", 13.8, 38) + "\n")
        # a row that is wiring_only for ANOTHER reason is not touched
        other = json.loads(planted("RQ-20300101-003", "4", 1.0, 50))
        other["measurement"]["note"] = "rule is prose: wiring_only"
        (root / "RQ-20300101-003" / "4.jsonl").write_text(json.dumps(other) + "\n")
        (q / "RQ-20300101-001.yaml").write_text(
            "id: RQ-20300101-001\ndecision_rule:\n  id: RULE-T-39\n  registered_at: '2030-01-01'\n"
            f"  rule: \"{rule39}\"\n")
        (q / "RQ-20300101-002.yaml").write_text(
            "id: RQ-20300101-002\ndecision_rule:\n  id: RULE-TPL-OOS\n  registered_at: '2030-01-01'\n"
            f"  rule: \"{oos}\"\n")

        def check(label: str, ok: bool, detail: Any = "") -> None:
            nonlocal failures
            print(f"  {'PASS' if ok else 'FAIL'}  {label}{'' if ok else f'  {detail!r}'}")
            failures += 0 if ok else 1

        dry = run(write=False, results_root=root, queue_dir=q, out=io.StringIO())
        check("dry run finds the 3 discarded rows and appends none", dry["found"] == 3 and dry["appended"] == 0, dry)
        check("dry run leaves every file at one row",
              all(len(f.read_text().splitlines()) == 1 for f in root.glob("*/*.jsonl")))
        rep = run(write=True, results_root=root, queue_dir=q, out=io.StringIO())
        by = {(r["unit"], r["run_id"]): r["verdict"] for r in rep["rows"]}
        check("PLANTED net<0, n>=39 re-grades FAIL under the unit's rule",
              by.get(("RQ-20300101-001", "1")) == "fail", by)
        check("PLANTED n<39 re-grades INDETERMINATE", by.get(("RQ-20300101-001", "2")) == "indeterminate", by)
        check("an OOS-R rule stays wiring_only with the true reason",
              by.get(("RQ-20300101-002", "3")) == hdr.WIRING_ONLY, by)
        check("a wiring_only row with another note is untouched",
              ("RQ-20300101-003", "4") not in by and len((root / "RQ-20300101-003" / "4.jsonl").read_text().splitlines()) == 1)
        rows1 = [json.loads(x) for x in (root / "RQ-20300101-001" / "1.jsonl").read_text().splitlines()]
        check("the original row is kept byte-for-byte and the re-grade is appended after it",
              len(rows1) == 2 and rows1[0]["verdict"] == hdr.WIRING_ONLY and rows1[1]["verdict"] == "fail"
              and rows1[1]["produced_by"]["run_id"] == "1" and rows1[1]["decision_rule"]["id"] == "RULE-T-39"
              and rows1[1]["note"].startswith(REGRADE_PREFIX) and "run 1" in rows1[1]["note"], rows1)
        check("every appended record is admissible", all(not validate(r) for r in rows1))
        again = run(write=True, results_root=root, queue_dir=q, out=io.StringIO())
        check("a second --write appends nothing (idempotent)", again["appended"] == 0 and again["skipped"] == 3, again)
        # the grader closes on the re-graded row, not on the original
        import queue_grade as qg
        g = qg.grade_e5([("1.jsonl", rows1[0]), ("1.jsonl", rows1[1])], 1)
        check("queue_grade reads the re-graded FAIL and supersedes the wiring_only original",
              g["verdict"] == "fail", g)
    print(f"regrade_wiring_only_results --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="append the corrected rows (default: report only)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--results-root", default=str(RESULTS_ROOT))
    ap.add_argument("--queue-dir", default=str(QUEUE_DIR))
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    run(write=args.write, results_root=Path(args.results_root), queue_dir=Path(args.queue_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
