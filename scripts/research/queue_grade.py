#!/usr/bin/env python3
# wiring: .github/workflows/research-queue-grade.yml (cron + dispatch); scripts/ci/check_pr_landing.py --verify
"""Grade ran units MECHANICALLY from their committed results, and say when a
result is not machine-gradable instead of guessing.

WHY THIS EXISTS (operator, 2026-09-28, relayed by the manager)
---------------------------------------------------------------
Eight units ran between 2026-09-22 and 2026-09-28 and sat `queued` with their
results on `main` until a session graded them by hand (PR #13735). A queue
that runs 24/7 without a session needs its results read without one too.

WHAT IT DOES
------------
For every `research/queue/*.yaml` with `status: queued` and a
`last_dispatched_at` stamp, it collects the committed evidence the unit's
`lands:` block points at and applies ONE of two machine graders:

  * E5 rows  -- `research/results/<unit>/*.jsonl` (the research-result
    contract). The producer already applied the unit's pre-registered rule
    per row (that is what an E5 verdict IS), so the mechanical read is
    UNANIMITY: >= lands.min_rows rows, every row `read_state: measured`, and
    every verdict the same one of pass / fail / no_action_warranted.
  * e35 corpus -- `docs/research/e35-bracket-corpus.jsonl` rows tagged with
    the unit id, for units whose rule id starts with
    `RULE-TPL-E35-BRACKET-`: PASS if any cell reads `is_oos_pass` with
    base_oos_trades >= 39; no_action_warranted if none does at >= 39;
    indeterminate below 39.

Then it lands the disposition on the unit file as TEXT EDITS (the same
line-level discipline `dispatch_queue._stamp` uses, so nothing else in the
file moves):

  * pass / no_action_warranted -> `status: done` + a `grading:` block naming
    the verdict, the rows, and the evidence paths;
  * fail, first time            -> NOT a kill: `last_dispatched_at: null`
    (re-queued for a confirmatory run) + `grading.confirmatory_run: 1`;
  * fail, second time           -> `status: done`, verdict fail, both runs
    named. The confirmatory pass reads ONLY rows landed since the
    re-dispatch (`last_dispatched_at` after the reset); with none yet it is
    not due, and a confirmatory PASS (or anything but fail) makes the two
    runs {fail, pass}: not unanimous -> `needs_review`, never closed either
    way by the grader;
  * anything else (mixed verdicts, a producer_failed row, too few rows,
    not_applicable, indeterminate) -> `grading.needs_review: true` with the
    reason; status unchanged, so a session's `needs_review` bucket is
    `grep -l "needs_review: true" research/queue/*.yaml`.
  * a recurring-cadence unit (weekly/monthly) is never closed: only
    `grading.latest` is refreshed, and a fail is recorded, not re-queued (the
    cadence re-asks anyway).

DETERMINISM. The `grading:` block is a pure function of (the unit at the
merge-base, the rows at HEAD, `graded_at`), so `check_pr_landing.py` can
re-run `--verify` at a PR's merge-base and demand the same bytes before a
grade PR self-lands. A grade nobody can reproduce holds for a human.

Tier-1 research tooling: reads research/results + corpora, writes only
research/queue/RQ-*.yaml. Never touches config/, src/ or a live path.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
QUEUE = Path("research/queue")
RESULTS = Path("research/results")
E35_CORPUS = Path("docs/research/e35-bracket-corpus.jsonl")
E35_FLOOR = 39
MECHANICAL = ("pass", "fail", "no_action_warranted")
_RECURRING = ("daily", "weekly", "monthly")


def _yaml():
    import yaml
    return yaml


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── evidence collection ─────────────────────────────────────────────────────
def e5_rows(root: Path, unit_id: str, since: Optional[datetime]) -> List[Tuple[str, Dict[str, Any]]]:
    out = []
    for f in sorted((root / RESULTS / unit_id).glob("*.jsonl")) if (root / RESULTS / unit_id).is_dir() else []:
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            ts = _parse_ts(r.get("generated_at") or (r.get("run") or {}).get("started_at"))
            # With a window, a row that cannot be dated cannot be placed in it:
            # excluded, never assumed new (review fix 2026-09-29).
            if since and (ts is None or ts < since):
                continue
            out.append((f.relative_to(root).as_posix(), r))
    return out


def e35_rows(root: Path, unit_id: str) -> List[Dict[str, Any]]:
    p = root / E35_CORPUS
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if unit_id not in ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("research_unit") == unit_id:
            out.append(r)
    return out


# ── graders (pure) ──────────────────────────────────────────────────────────
def grade_e5(rows: List[Tuple[str, Dict[str, Any]]], min_rows: int) -> Dict[str, Any]:
    n = len(rows)
    files = sorted({f for f, _ in rows})
    if n < max(1, min_rows):
        return {"verdict": None, "reason": f"{n} result row(s) on main, lands.min_rows is {min_rows}", "rows": n, "files": files}
    # ⚠️ A `producer_failed` row is "we looked and it broke", not a result, so it cannot
    # veto a `measured` row from a LATER run of the same unit. Until 2026-10-04 any mix
    # read "read_state not unanimously measured" -> needs_review forever: MEASURED, the 3
    # prop-fit units whose first run died and whose re-run measured (RQ-20260928-025/-026/
    # -029) and RQ-20260930-501 (a malformed verdict.json, then a clean pass) never closed.
    # When at least one measured row exists, grade the measured rows and NAME the failures
    # skipped; with none, every state still lands in the reason as before.
    measured = [(f, r) for f, r in rows if str(r.get("read_state")) == "measured"]
    superseded = n - len(measured) if measured else 0
    if measured:
        rows = measured
    n = len(rows)
    states = sorted({str(r.get("read_state")) for _, r in rows})
    verdicts = sorted({str(r.get("verdict")) for _, r in rows})
    if states != ["measured"]:
        return {"verdict": None, "reason": f"read_state not unanimously measured: {states}", "rows": n, "files": files}
    if len(verdicts) != 1 or verdicts[0] not in MECHANICAL + ("indeterminate",):
        return {"verdict": None, "reason": f"verdicts not unanimous-mechanical: {verdicts}", "rows": n, "files": files}
    tail = f" ({superseded} earlier producer_failed row(s) superseded)" if superseded else ""
    return {"verdict": verdicts[0], "reason": f"{n} row(s), all measured, all {verdicts[0]}{tail}", "rows": n, "files": files}


def grade_e35(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"verdict": None, "reason": "no corpus rows tagged with this unit", "rows": 0, "files": [E35_CORPUS.as_posix()]}
    n_oos = max((int(r.get("base_oos_trades") or 0) for r in rows), default=0)
    passing = sorted({f"{r.get('leg')}:{r.get('cell')}" for r in rows if r.get("gate_verdict") == "is_oos_pass"})
    if n_oos < E35_FLOOR:
        return {"verdict": "indeterminate", "reason": f"base_oos_trades {n_oos} < {E35_FLOOR} (unresolvable_at_current_n)",
                "rows": len(rows), "files": [E35_CORPUS.as_posix()], "passing_cells": passing}
    if passing:
        return {"verdict": "pass", "reason": f"{len(passing)} cell(s) is_oos_pass at base_oos_trades {n_oos}: {passing[:6]}",
                "rows": len(rows), "files": [E35_CORPUS.as_posix()], "passing_cells": passing}
    return {"verdict": "no_action_warranted", "reason": f"0 of {len(rows)} cells is_oos_pass at base_oos_trades {n_oos}",
            "rows": len(rows), "files": [E35_CORPUS.as_posix()], "passing_cells": []}


def decide(unit: Dict[str, Any], g: Dict[str, Any], graded_at: str) -> Optional[Dict[str, Any]]:
    """The disposition for one unit, or None when nothing is to be written.
    Returns {"status": new|None, "reset_stamp": bool, "grading": block}."""
    cadence = str(unit.get("cadence") or "once")
    prior = unit.get("grading") if isinstance(unit.get("grading"), dict) else {}
    base = {"auto": True, "graded_at": graded_at, "grader": "scripts/research/queue_grade.py",
            "decision_rule_id": str((unit.get("decision_rule") or {}).get("id") or ""),
            "rows": g.get("rows"), "evidence": g.get("files"), "reason": g.get("reason")}
    v = g.get("verdict")
    if v is None:
        if g.get("rows", 0) == 0 and not g.get("reason", "").startswith("read_state"):
            return None    # nothing landed yet -- not due, not a finding
        return {"status": None, "reset_stamp": False,
                "grading": {**base, "verdict": None, "needs_review": True}}
    if cadence in _RECURRING:
        return {"status": None, "reset_stamp": False,
                "grading": {**base, "verdict": v, "latest": True, "needs_review": v == "indeterminate"}}
    if int(prior.get("confirmatory_run") or 0) >= 1 and v != "fail":
        # The confirmatory run did not repeat the FAIL: {fail, v} is not
        # unanimous, so a session reads both runs (review fix 2026-09-29).
        return {"status": None, "reset_stamp": False,
                "grading": {**base, "verdict": None, "needs_review": True, "confirmatory_run": 1,
                            "first_fail": prior.get("first_fail") or prior.get("reason"),
                            "note": f"confirmatory run read {v!r} after a first FAIL: {{fail, {v}}} is not "
                                    "unanimous -> needs_review, not closed"}}
    if v in ("pass", "no_action_warranted"):
        return {"status": "done", "reset_stamp": False, "grading": {**base, "verdict": v}}
    if v == "indeterminate" and not prior.get("confirmatory_run"):
        # ⚠️ `indeterminate` (UNDERPOWERED / NULL) is a branch the unit's own rule registered
        # BEFORE the run, and a `once` unit never runs again -- so leaving it `queued` closed
        # nothing and parked it in needs_review forever (MEASURED 2026-10-04: 33 prop-fit
        # units, n=57 < the 88 floor, stayed queued for 6 days). The unit is COMPLETE; what
        # it is NOT is a pass or a kill, which `verdict: indeterminate` + `awaiting` says in
        # the record itself. Getting the missing data is the follow-up, not this unit's job.
        return {"status": "done", "reset_stamp": False,
                "grading": {**base, "verdict": "indeterminate", "awaiting": "more_data",
                            "note": "indeterminate is the pre-registered under-powered/null outcome: "
                                    "complete, but NOT a pass and NOT a kill"}}
    if v == "fail":
        if int(prior.get("confirmatory_run") or 0) >= 1:
            return {"status": "done", "reset_stamp": False,
                    "grading": {**base, "verdict": "fail", "confirmatory_run": 1,
                                "first_fail": prior.get("first_fail") or prior.get("reason"),
                                "note": "two consecutive FAIL passes; closed as fail (never on one pass)"}}
        return {"status": None, "reset_stamp": True,
                "grading": {**base, "verdict": "fail", "confirmatory_run": 1, "first_fail": g.get("reason"),
                            "note": "first FAIL: re-queued for a confirmatory run (last_dispatched_at reset), not killed"}}
    return {"status": None, "reset_stamp": False,
            "grading": {**base, "verdict": v, "needs_review": True}}


# ── text edits ──────────────────────────────────────────────────────────────
def apply_text(text: str, d: Dict[str, Any]) -> str:
    yaml = _yaml()
    lines = text.rstrip("\n").split("\n")
    # drop an existing top-level grading: block
    out: List[str] = []
    skipping = False
    for ln in lines:
        if ln.startswith("grading:"):
            skipping = True
            continue
        if skipping and (ln.startswith((" ", "\t")) or ln.strip() == ""):
            continue
        skipping = False
        out.append(ln)
    for i, ln in enumerate(out):
        if d["status"] and re.match(r"^status:\s", ln):
            out[i] = f"status: {d['status']}"
        if d["reset_stamp"] and ln.startswith("last_dispatched_at:"):
            out[i] = "last_dispatched_at: null"
    block = yaml.safe_dump({"grading": d["grading"]}, sort_keys=True, width=100, allow_unicode=True)
    return "\n".join(out).rstrip("\n") + "\n" + block


def grade_unit(root: Path, unit_id: str, text: str, graded_at: str) -> Optional[str]:
    """New text for the unit file, or None when nothing is to be written."""
    yaml = _yaml()
    unit = yaml.safe_load(text)
    if not isinstance(unit, dict) or unit.get("status") != "queued" or not unit.get("last_dispatched_at"):
        return None
    # ⚠️ ONLY units whose rule is mechanical by construction are graded here:
    # template-generated ones (`generated:`) or hand-written ones that opt in
    # with `grading: {auto: true}`. A hand-written fan-out unit (RQ-20260928-005
    # wants 3 legs over 3 dispatches with lands.min_rows 1 per run) would be
    # misread from its first row, and a session's multi-run design is not
    # this grader's to second-guess -- it stays in the session's queue.
    auto = isinstance(unit.get("generated"), dict) or \
        (isinstance(unit.get("grading"), dict) and unit["grading"].get("auto") is True)
    if not auto:
        return None
    prior = unit.get("grading") if isinstance(unit.get("grading"), dict) else {}
    confirmatory = int(prior.get("confirmatory_run") or 0) >= 1
    # A recurring unit reads only its latest cadence window. A confirmatory
    # pass (re-queued after a first FAIL) reads only rows landed since the
    # re-dispatch -- re-reading run 1's own rows would "confirm" the FAIL with
    # itself (review fix 2026-09-29).
    since = _parse_ts(unit.get("last_dispatched_at")) if (unit.get("cadence") in _RECURRING or confirmatory) else None
    rule_id = str((unit.get("decision_rule") or {}).get("id") or "")
    lands = unit.get("lands") or {}
    if rule_id.startswith("RULE-TPL-E35-BRACKET-"):
        g = grade_e35(e35_rows(root, unit_id))
    else:
        g = grade_e5(e5_rows(root, unit_id, since), int(lands.get("min_rows") or 1))
    if confirmatory and not g.get("rows"):
        return None    # the confirmatory run has not landed anything yet -- not due
    d = decide(unit, g, graded_at)
    if d is None:
        return None
    new = apply_text(text, d)
    if new == text:
        return None
    # the edit must still parse and must have changed only what it claims
    back = yaml.safe_load(new)
    assert isinstance(back, dict) and back.get("id") == unit.get("id")
    return new


def run(root: Path, *, graded_at: str, write: bool) -> Dict[str, Any]:
    changed = []
    for path in sorted((root / QUEUE).glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        new = grade_unit(root, path.stem, text, graded_at)
        if new is None:
            continue
        g = _yaml().safe_load(new).get("grading") or {}
        changed.append({"id": path.stem, "verdict": g.get("verdict"), "status": _yaml().safe_load(new).get("status"),
                        "needs_review": bool(g.get("needs_review")), "reason": g.get("reason")})
        if write:
            path.write_text(new, encoding="utf-8")
    return {"graded_at": graded_at, "changed": changed, "written": write}


# ── verify ──────────────────────────────────────────────────────────────────
def _git(root: Path, *args: str) -> Tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    return p.returncode, p.stdout


def verify(root: Path, base: str) -> List[str]:
    rc, mb = _git(root, "merge-base", base, "HEAD")
    mb = mb.strip()
    if rc != 0 or not mb:
        return [f"merge-base with {base} unreadable"]
    rc, out = _git(root, "diff", "--name-status", "--no-renames", mb, "HEAD", "--", QUEUE.as_posix())
    if rc != 0:
        return ["queue diff unreadable"]
    problems = []
    for line in out.strip().splitlines():
        status, _, path = line.partition("\t")
        if status != "M":
            problems.append(f"{path}: {status} -- the grader only MODIFIES existing units")
            continue
        rc, base_text = _git(root, "show", f"{mb}:{path}")
        if rc != 0:
            problems.append(f"{path}: base version unreadable")
            continue
        head_text = (root / path).read_text(encoding="utf-8")
        g = (_yaml().safe_load(head_text) or {}).get("grading") or {}
        graded_at = str(g.get("graded_at") or "")
        expected = grade_unit(root, Path(path).stem, base_text, graded_at)
        if expected != head_text:
            problems.append(f"{path}: does not reproduce from the merge-base unit + HEAD results at graded_at {graded_at!r}")
    return problems


def health(root: Path, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Runnable count (= what the dispatcher would fire within the next cycle,
    see `queue_replenish.runnable`), the looser `queued` count beside it, and
    hours since the last dispatch -- the alarm's inputs."""
    from scripts.research.queue_replenish import dispatchable, existing_units, runnable
    units, bad = existing_units(root)
    now = now or datetime.now(timezone.utc)
    queued = sorted(uid for uid, u in units.items() if dispatchable(u))
    stamps = [t for u in units.values() if (t := _parse_ts(u.get("last_dispatched_at")))]
    last = max(stamps) if stamps else None
    review = sorted(uid for uid, u in units.items() if isinstance(u.get("grading"), dict) and u["grading"].get("needs_review"))
    return {"runnable": len(runnable(units, now=now)), "queued": len(queued), "units": len(units), "unreadable": bad,
            "last_dispatched_at": last.isoformat() if last else None,
            "hours_since_last_dispatch": round((now - last).total_seconds() / 3600, 1) if last else None,
            "needs_review": review}


# ── self-test ───────────────────────────────────────────────────────────────
def _self_test() -> int:
    import tempfile
    row = lambda v, rs="measured": ("research/results/U/1.jsonl", {"verdict": v, "read_state": rs})  # noqa: E731
    assert grade_e5([row("pass"), row("pass")], 2)["verdict"] == "pass"
    assert grade_e5([row("pass")], 2)["verdict"] is None
    assert grade_e5([row("pass"), row("fail")], 1)["verdict"] is None
    assert grade_e5([row("not_applicable", "producer_failed")], 1)["verdict"] is None
    assert grade_e5([row("fail")], 1)["verdict"] == "fail"
    # a producer_failed row is superseded by a later measured one; alone it still is not a result
    g = grade_e5([row("not_applicable", "producer_failed"), row("pass")], 1)
    assert g["verdict"] == "pass" and "superseded" in g["reason"], g
    assert grade_e5([row("not_applicable", "producer_failed"), row("not_applicable", "producer_failed")], 1)["verdict"] is None
    assert grade_e5([row("indeterminate")], 1)["verdict"] == "indeterminate"
    assert grade_e5([row("indeterminate"), row("pass")], 1)["verdict"] is None   # measured but not unanimous
    assert grade_e35([{"gate_verdict": "is_oos_pass", "base_oos_trades": 49, "leg": "l", "cell": "c"}])["verdict"] == "pass"
    assert grade_e35([{"gate_verdict": "is_oos_fail", "base_oos_trades": 49}])["verdict"] == "no_action_warranted"
    assert grade_e35([{"gate_verdict": "is_oos_pass", "base_oos_trades": 10}])["verdict"] == "indeterminate"
    assert grade_e35([])["verdict"] is None
    u = {"cadence": "once", "decision_rule": {"id": "R"}, "generated": {"key": "k"}}
    assert decide(u, {"verdict": "pass", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")["status"] == "done"
    d = decide(u, {"verdict": "fail", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")
    assert d["status"] is None and d["reset_stamp"] and d["grading"]["confirmatory_run"] == 1
    d2 = decide({**u, "grading": {"confirmatory_run": 1}}, {"verdict": "fail", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")
    assert d2["status"] == "done" and d2["grading"]["verdict"] == "fail"
    di = decide(u, {"verdict": "indeterminate", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")
    assert di["status"] == "done" and di["grading"]["awaiting"] == "more_data" and not di["grading"].get("needs_review"), di
    dr = decide({**u, "cadence": "monthly"}, {"verdict": "indeterminate", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")
    assert dr["status"] is None and dr["grading"]["needs_review"], dr   # a recurring unit is never closed
    d3 = decide({**u, "grading": {"confirmatory_run": 1}}, {"verdict": "pass", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")
    assert d3["status"] is None and d3["grading"]["needs_review"] and "not unanimous" in d3["grading"]["note"], d3
    assert decide({**u, "cadence": "monthly"}, {"verdict": "fail", "rows": 1, "files": [], "reason": "r"}, "2030-01-01")["status"] is None
    assert decide(u, {"verdict": None, "rows": 0, "files": [], "reason": "0 result row(s)"}, "2030-01-01") is None
    assert decide(u, {"verdict": None, "rows": 2, "files": [], "reason": "verdicts not unanimous"}, "2030-01-01")["grading"]["needs_review"]

    with tempfile.TemporaryDirectory() as td:
        r = Path(td)
        (r / QUEUE).mkdir(parents=True)
        (r / RESULTS / "RQ-20300101-001").mkdir(parents=True)
        unit_text = ("id: RQ-20300101-001\ntitle: t   # keep me\nstatus: queued\ncadence: once\ngrading:\n  auto: true\n"
                     "decision_rule:\n  id: RULE-X\nlands:\n  store: research/results/\n  min_rows: 2\n"
                     "last_dispatched_at: '2030-01-01T00:00:00+00:00'\n")
        (r / QUEUE / "RQ-20300101-001.yaml").write_text(unit_text)
        assert grade_unit(r, "RQ-20300101-001", unit_text, "2030-01-02") is None          # nothing landed
        (r / RESULTS / "RQ-20300101-001" / "1.jsonl").write_text(
            json.dumps({"verdict": "pass", "read_state": "measured"}) + "\n" + json.dumps({"verdict": "pass", "read_state": "measured"}) + "\n")
        new = grade_unit(r, "RQ-20300101-001", unit_text, "2030-01-02")
        assert new and "status: done" in new and "# keep me" in new and "grading:" in new, new
        assert grade_unit(r, "RQ-20300101-001", new, "2030-01-02") is None   # done units are left alone
        # a first FAIL re-queues; the second closes
        (r / RESULTS / "RQ-20300101-001" / "1.jsonl").write_text(json.dumps({"verdict": "fail", "read_state": "measured"}) + "\n" +
                                                                json.dumps({"verdict": "fail", "read_state": "measured"}) + "\n")
        t1 = grade_unit(r, "RQ-20300101-001", unit_text, "2030-01-02")
        assert "last_dispatched_at: null" in t1 and "confirmatory_run: 1" in t1 and "status: queued" in t1, t1
        t1b = t1.replace("last_dispatched_at: null", "last_dispatched_at: '2030-01-03T00:00:00+00:00'")
        # PLANTED DEFECT (review 2026-09-29): the confirmatory pass must NOT re-read
        # run 1's rows and call them a second FAIL -- undated / older rows are not
        # the confirmatory run's, so with nothing new the unit is simply not due.
        assert grade_unit(r, "RQ-20300101-001", t1b, "2030-01-04") is None
        old1 = json.dumps({"verdict": "fail", "read_state": "measured", "generated_at": "2030-01-01T12:00:00Z"})
        (r / RESULTS / "RQ-20300101-001" / "1.jsonl").write_text(old1 + "\n" + old1 + "\n")
        assert grade_unit(r, "RQ-20300101-001", t1b, "2030-01-04") is None
        # a dated FAIL from the confirmatory run closes it; a dated PASS -> needs_review
        new2 = json.dumps({"verdict": "fail", "read_state": "measured", "generated_at": "2030-01-03T12:00:00Z"})
        (r / RESULTS / "RQ-20300101-001" / "2.jsonl").write_text(new2 + "\n" + new2 + "\n")
        t2 = grade_unit(r, "RQ-20300101-001", t1b, "2030-01-04")
        assert "status: done" in t2 and "verdict: fail" in t2 and "two consecutive" in t2, t2
        pass2 = json.dumps({"verdict": "pass", "read_state": "measured", "generated_at": "2030-01-03T12:00:00Z"})
        (r / RESULTS / "RQ-20300101-001" / "2.jsonl").write_text(pass2 + "\n" + pass2 + "\n")
        t2p = grade_unit(r, "RQ-20300101-001", t1b, "2030-01-04")
        assert "needs_review: true" in t2p and "status: queued" in t2p and "not unanimous" in t2p, t2p
        (r / RESULTS / "RQ-20300101-001" / "2.jsonl").unlink()
        # grading block replaces, never stacks
        assert t2.count("grading:") == 1
        # mixed -> needs_review, status untouched
        (r / RESULTS / "RQ-20300101-001" / "1.jsonl").write_text(json.dumps({"verdict": "pass", "read_state": "measured"}) + "\n" +
                                                                json.dumps({"verdict": "fail", "read_state": "measured"}) + "\n")
        t3 = grade_unit(r, "RQ-20300101-001", unit_text, "2030-01-02")
        assert "needs_review: true" in t3 and "status: queued" in t3
        assert health(r)["needs_review"] == []   # only what is written counts
        # verify(): reproduced passes, hand-edited fails
        subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
        git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(r), check=True, capture_output=True)  # noqa: E731
        (r / RESULTS / "RQ-20300101-001" / "1.jsonl").write_text(json.dumps({"verdict": "pass", "read_state": "measured"}) + "\n" +
                                                                json.dumps({"verdict": "pass", "read_state": "measured"}) + "\n")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        git("checkout", "-q", "-b", "g")
        res = run(r, graded_at="2030-01-02", write=True)
        assert res["changed"] and res["changed"][0]["status"] == "done"
        git("commit", "-q", "-am", "grade")
        assert verify(r, "main") == [], verify(r, "main")
        p = r / QUEUE / "RQ-20300101-001.yaml"
        p.write_text(p.read_text().replace("verdict: pass", "verdict: fail"))
        git("commit", "-q", "-am", "tamper")
        assert any("does not reproduce" in x for x in verify(r, "main"))
    print("queue_grade self-test OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--date", default=None, help="graded_at (YYYY-MM-DD, default today UTC)")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--health", action="store_true", help="print the queue-health numbers as JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    # `--verify --base` grades a COMMIT RANGE, so the verdict is about the
    # committed tree. Say so when that is not the tree you edited
    # (tests/test_dirty_tree_notice.py; notice only, no exit-code change).
    sys.path.insert(0, str(_REPO / "scripts" / "ci"))
    import _dirty_tree  # noqa: E402,PLC0415 -- path shim above
    _dirty_tree.warn()
    if args.self_test:
        return _self_test()
    if args.health:
        print(json.dumps(health(_REPO), indent=2))
        return 0
    if args.verify:
        problems = verify(_REPO, args.base)
        for p in problems:
            print(f"::error::queue_grade --verify: {p}")
        print("queue_grade --verify: " + ("OK" if not problems else f"FAIL ({len(problems)})"))
        return 1 if problems else 0
    day = args.date or datetime.now(timezone.utc).date().isoformat()
    res = run(_REPO, graded_at=day, write=args.write)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"queue_grade ({'WROTE' if args.write else 'dry run'}, graded_at {day}): {len(res['changed'])} unit(s) changed")
        for c in res["changed"]:
            print(f"  {c['id']}  status={c['status']}  verdict={c['verdict']}  needs_review={c['needs_review']}  {c['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
