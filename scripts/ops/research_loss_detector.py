#!/usr/bin/env python3
"""RESEARCH LOSS DETECTOR — a research result that did not reach `main` must be impossible to miss.

WHY THIS EXISTS (checklist row E57, 2026-09-24)
-----------------------------------------------
Operator, verbatim: *"this is a serious gap that needs to be fully resolved - not
only rerunning tests whose results were lost, but ensuring we have a mechanism
in place to prevent research from being lost"*.

MEASURED 2026-09-24 when this was written: every automated research landing PR
(`research-result`, `replay-pregate`, `research-queue-stamp`) was refused by
`pr-landing-guard` R5 and sat open with auto-merge armed and nothing watching it
— replay-pregate reports back to #12228 (2026-09-13), and `research/results/`
on `main` held **no record at all, only its README**. Every producer had
exited, `commit-to-main` had waited its 30 minutes and given up, and
`stale-automation-sweep` refreshed the branches into the same red guard every
two hours. Nothing anywhere said "research is not landing".

The R5 fix (E57, `check_pr_landing.py`) repairs the one cause found. THIS file
exists for the next cause nobody has found yet: it does not care WHY a result
did not land, only THAT it did not, and it is scheduled, so it runs whether or
not a manager session is alive to ask.

THREE QUESTIONS, EACH OVER A STATED POPULATION
----------------------------------------------
  A. STUCK — open PRs whose head is `automation/*` AND that carry research
     (branch family in RESEARCH_PREFIXES, or a changed file under
     RESEARCH_PATHS), open longer than `--stuck-hours`.
  B. UNLANDED — completed runs, inside `--lookback-days`, of every workflow that
     calls `./.github/actions/research-result` (DERIVED from the tree, never a
     hand list), whose `research_result` job actually ran, and whose record
     `research/results/**/<run_id>.jsonl` is not on the ref. A run whose
     research_result job was SKIPPED produced nothing by design and is counted,
     not flagged.
  C. EXPIRING — unexpired Actions artifacts from RESEARCH workflows that expire
     within `--expiry-hours` and whose run id appears NOWHERE on the ref (not in
     a result file name, not in a commit message, not in any file under
     RESEARCH_PATHS). The artifact is the last copy; after expiry the
     measurement is gone.

COLLAPSED STATES — "WE DID NOT LOOK" IS NEVER "NOTHING LOST"
-------------------------------------------------------------
Each question reports one of `clean` · `findings` · `could_not_read`. A failed
API read is `could_not_read` for THAT question, is printed, is written to the
receipt, and ALERTS — a detector that goes quiet when it cannot see is the
exact failure it exists to catch.

And the detector not running at all is caught one level up, on a substrate
that cannot silently stop: `--write-receipt` writes RECEIPT_PATH, the workflow
lands it on `main`, and `scripts/ci/check_cadence_liveness.py` grades that
receipt's age on EVERY PR. A detector that stops firing reds CI within three
missed windows. That is why the receipt is committed and not an artifact.

EXIT: 0 clean · 1 findings · 2 at least one question could not be read.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
RECEIPT_PATH = "docs/claude/work/RESEARCH-LOSS-RECEIPT.json"
RESULT_ACTION = "./.github/actions/research-result"

#: Branch families that carry research output. A PR outside these is still
#: caught if its diff touches RESEARCH_PATHS — this list only saves API calls.
RESEARCH_PREFIXES = (
    "automation/research-result-",
    "automation/replay-pregate-",
    "automation/research-queue-stamp-",
    "automation/e35-bracket-corpus-",
)
RESEARCH_PATHS = (
    "research/**",
    "docs/research/**",
    "runtime_logs/replay_pregate/**",
)
#: Which workflows' ARTIFACTS count as research (question C). A name heuristic,
#: stated as one: the derived list is printed in every receipt so a reader can
#: see the population rather than trust this regex.
RESEARCH_WORKFLOW_RE = re.compile(
    r"research|backtest|sweep|replay|e35|exit-head|panel|census|walkforward|"
    r"event-study|m20|m30|study", re.I)

#: Matched by the regex above but NOT research: they sweep relays, PRs and the
#: queue itself. MEASURED 2026-09-24: the bare regex matched 22 workflow files
#: and these three were the false positives.
NOT_RESEARCH_WORKFLOWS = frozenset({
    "diag-relay-sweep.yml", "stale-automation-sweep.yml", "research-queue-dispatch.yml"})


def is_research_workflow(name: str) -> bool:
    return bool(RESEARCH_WORKFLOW_RE.search(name)) and name not in NOT_RESEARCH_WORKFLOWS


CLEAN, FINDINGS, COULD_NOT_READ = "clean", "findings", "could_not_read"


class CouldNotRead(Exception):
    pass


# --------------------------------------------------------------------- IO
def _git(*args: str, root: Path = REPO) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.returncode, p.stdout


def _api(path: str, repo: str) -> Any:
    url = path if path.startswith("http") else f"https://api.github.com/repos/{repo}/{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CouldNotRead(f"GET {url}: {exc}") from exc


def _paged(path: str, repo: str, key: Optional[str], limit: int = 1000) -> list:
    out: list = []
    page = 1
    sep = "&" if "?" in path else "?"
    while len(out) < limit:
        body = _api(f"{path}{sep}per_page=100&page={page}", repo)
        items = body.get(key, []) if key else body
        if not isinstance(items, list):
            raise CouldNotRead(f"{path}: unexpected shape")
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
    return out


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --------------------------------------------------------------- derivation
def result_workflows(root: Path = REPO) -> list[str]:
    """Workflow FILES that call the research-result action — derived, not listed."""
    # A real `uses:` line, not the string anywhere: this detector's own workflow
    # MENTIONS the action in comments, and matching that made it grade itself
    # as a result producer (measured on its first live run, 2026-09-24).
    pat = re.compile(r"^\s*(?:-\s*)?uses:\s*" + re.escape(RESULT_ACTION) + r"\s*$", re.M)
    wf = root / ".github/workflows"
    return sorted(p.name for p in wf.glob("*.y*ml")
                  if pat.search(p.read_text(encoding="utf-8", errors="replace")))


def is_research_path(path: str) -> bool:
    for g in RESEARCH_PATHS:
        if fnmatch.fnmatch(path, g) or path.startswith(g[:-2]):
            return True
    return False


def landed_result_run_ids(ref: str, root: Path = REPO) -> Optional[set[str]]:
    rc, out = _git("ls-tree", "-r", "--name-only", ref, "--", "research/results", root=root)
    if rc != 0:
        return None
    return {Path(p).stem for p in out.splitlines() if p.endswith(".jsonl")}


def run_ids_mentioned(ref: str, run_ids: set[str], root: Path = REPO) -> Optional[set[str]]:
    """Which of `run_ids` appear on `ref` at all: result names, commit messages, research files."""
    if not run_ids:
        return set()
    found: set[str] = set()
    landed = landed_result_run_ids(ref, root)
    if landed is None:
        return None
    found |= run_ids & landed
    rc, log = _git("log", "--since=120 days ago", "--format=%s%n%b", ref, root=root)
    if rc != 0:
        return None
    found |= {r for r in run_ids if r in log}
    rest = run_ids - found
    if rest:
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("\n".join(sorted(rest)) + "\n")
            pat = fh.name
        rc, out = _git("grep", "-o", "-h", "-F", "-f", pat, ref, "--",
                       *[g[:-3] for g in RESEARCH_PATHS], root=root)
        os.unlink(pat)
        if rc not in (0, 1):   # 1 = no match, which is an answer
            return None
        found |= {ln.split(":")[-1].strip() for ln in out.splitlines()} & rest
    return found


# ---------------------------------------------------------------- questions
def q_stuck(repo: str, now: datetime, stuck_hours: float) -> dict:
    prs = _paged("pulls?state=open", repo, None)
    auto = [p for p in prs if str(p["head"]["ref"]).startswith("automation/")]
    research, other = [], 0
    for p in auto:
        ref = p["head"]["ref"]
        is_r = ref.startswith(RESEARCH_PREFIXES)
        if not is_r:
            files = _paged(f"pulls/{p['number']}/files", repo, None, limit=300)
            is_r = any(is_research_path(f["filename"]) for f in files)
        if not is_r:
            other += 1
            continue
        age_h = (now - _parse_ts(p["created_at"])).total_seconds() / 3600
        research.append({"pr": p["number"], "branch": ref, "age_hours": round(age_h, 1),
                         "title": p["title"]})
    stuck = sorted((r for r in research if r["age_hours"] > stuck_hours),
                   key=lambda r: -r["age_hours"])
    return {"state": FINDINGS if stuck else CLEAN,
            "population": (f"{len(prs)} open PRs; {len(auto)} automation/*; "
                           f"{len(research)} carry research; {other} automation/* "
                           f"carry none (not graded here)"),
            "threshold_hours": stuck_hours, "findings": stuck}


def q_unlanded(repo: str, now: datetime, lookback_days: int, ref: str) -> dict:
    wfs = result_workflows()
    landed = landed_result_run_ids(ref)
    if landed is None:
        raise CouldNotRead(f"could not list research/results on {ref}")
    since = now - timedelta(days=lookback_days)
    runs_seen, by_design, pending, lost = 0, 0, [], []
    open_heads = {p["head"]["ref"] for p in _paged("pulls?state=open", repo, None)}
    for wf in wfs:
        runs = _paged(f"actions/workflows/{wf}/runs?status=completed"
                      f"&created=%3E%3D{since:%Y-%m-%d}", repo, "workflow_runs", limit=500)
        for run in runs:
            if run.get("conclusion") == "skipped":
                by_design += 1
                continue
            runs_seen += 1
            rid = str(run["id"])
            if rid in landed:
                continue
            jobs = _api(f"actions/runs/{rid}/jobs?per_page=100", repo).get("jobs", [])
            rj = [j for j in jobs if j.get("name", "").startswith("research_result")]
            if not rj or all(j.get("conclusion") == "skipped" for j in rj):
                by_design += 1
                continue
            row = {"workflow": wf, "run_id": rid, "run_url": run.get("html_url"),
                   "conclusion": run.get("conclusion"), "created_at": run.get("created_at"),
                   "result_job": [j.get("conclusion") for j in rj]}
            if any(h.startswith(f"automation/research-result-{rid}-") for h in open_heads):
                pending.append(row)
            else:
                lost.append(row)
    return {"state": FINDINGS if (lost or pending) else CLEAN,
            "population": (f"{runs_seen} completed run(s) of {len(wfs)} workflow(s) "
                           f"calling {RESULT_ACTION} since {since:%Y-%m-%d} "
                           f"({', '.join(wfs)}); {by_design} produced no result by "
                           f"design (skipped); {len(landed)} result file(s) on {ref}"),
            "pending_merge": pending, "lost": lost,
            "findings": lost + pending}


def q_expiring(repo: str, now: datetime, expiry_hours: float, ref: str) -> dict:
    # Artifacts inside the window FIRST, then one run lookup per UNIQUE run id.
    # Listing every research workflow's runs instead was measured 2026-09-24 at
    # 13,293 runs in 100 days (issue-triggered workflows start a run for every
    # issue in the repo) -- far slower than looking up the few runs that matter.
    arts = _paged("actions/artifacts", repo, "artifacts", limit=5000)
    live = [a for a in arts if not a.get("expired") and a.get("expires_at")]
    horizon = now + timedelta(hours=expiry_hours)
    near = [a for a in live if _parse_ts(a["expires_at"]) <= horizon
            and (a.get("workflow_run") or {}).get("id")]
    run_wf: dict[str, str] = {}
    for a in near:
        rid = str(a["workflow_run"]["id"])
        if rid not in run_wf:
            run = _api(f"actions/runs/{rid}", repo)
            run_wf[rid] = Path(str(run.get("path", ""))).name or str(run.get("name", ""))
    research = [a for a in near if is_research_workflow(run_wf[str(a["workflow_run"]["id"])])]
    rids = {str(a["workflow_run"]["id"]) for a in research}
    mentioned = run_ids_mentioned(ref, rids)
    if mentioned is None:
        raise CouldNotRead(f"could not search {ref} for landed copies")
    orphans = [{"artifact": a["name"], "artifact_id": a["id"],
                "run_id": str(a["workflow_run"]["id"]),
                "workflow": run_wf[str(a["workflow_run"]["id"])],
                "expires_at": a["expires_at"]}
               for a in research if str(a["workflow_run"]["id"]) not in mentioned]
    return {"state": FINDINGS if orphans else CLEAN,
            "population": (f"{len(arts)} artifact(s) listed, {len(live)} unexpired, "
                           f"{len(near)} expire within {expiry_hours}h across "
                           f"{len(run_wf)} run(s); {len(research)} of those from research "
                           f"workflows ({', '.join(sorted({run_wf[r] for r in rids})) or 'none'}); "
                           f"a landed copy = run id named on {ref} in research/results, a "
                           f"commit message, or a file under {', '.join(RESEARCH_PATHS)}"),
            "findings": orphans}


# ------------------------------------------------------------------- report
def sweep(repo: str, ref: str, stuck_hours: float, lookback_days: int,
          expiry_hours: float, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "ref": ref, "repo": repo,
                           "produced_by": {
                               "workflow": os.environ.get("GITHUB_WORKFLOW") or "(local run)",
                               "run_id": os.environ.get("GITHUB_RUN_ID"),
                           },
                           "questions": {}}
    for name, fn in (("A_stuck_research_prs", lambda: q_stuck(repo, now, stuck_hours)),
                     ("B_unlanded_result_runs", lambda: q_unlanded(repo, now, lookback_days, ref)),
                     ("C_expiring_unlanded_artifacts",
                      lambda: q_expiring(repo, now, expiry_hours, ref))):
        try:
            out["questions"][name] = fn()
        except CouldNotRead as exc:
            out["questions"][name] = {"state": COULD_NOT_READ, "error": str(exc)[:400],
                                      "findings": None}
    states = [q["state"] for q in out["questions"].values()]
    out["overall"] = (COULD_NOT_READ if COULD_NOT_READ in states
                      else FINDINGS if FINDINGS in states else CLEAN)
    return out


def alert_text(rep: dict, max_rows: int = 6) -> str:
    lines = [f"🔴 RESEARCH LOSS DETECTOR — {rep['overall'].upper()} ({rep['generated_at']})"]
    for name, q in rep["questions"].items():
        if q["state"] == CLEAN:
            lines.append(f"✅ {name}: clean")
            continue
        if q["state"] == COULD_NOT_READ:
            lines.append(f"⚠️ {name}: COULD NOT READ — not 'nothing lost': {q['error'][:160]}")
            continue
        rows = q["findings"]
        lines.append(f"❌ {name}: {len(rows)}")
        for r in rows[:max_rows]:
            if "pr" in r:
                lines.append(f"   #{r['pr']} {r['branch']} open {r['age_hours']}h")
            elif "artifact" in r:
                lines.append(f"   {r['artifact']} (run {r['run_id']}) expires {r['expires_at']}")
            else:
                lines.append(f"   {r['workflow']} run {r['run_id']} ({r['conclusion']})")
        if len(rows) > max_rows:
            lines.append(f"   … +{len(rows) - max_rows} more (see {RECEIPT_PATH})")
    return "\n".join(lines)


# ---------------------------------------------------------------- self-test
def _self_test() -> int:
    bad = 0

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal bad
        if got != want:
            print(f"::error::self-test FAILED — {label}: got {got!r}, want {want!r}")
            bad += 1
        else:
            print(f"self-test: {label}")

    check("research path: research/results", is_research_path("research/results/x/1.jsonl"), True)
    check("research path: replay_pregate", is_research_path("runtime_logs/replay_pregate/a.json"), True)
    check("research path: NOT other runtime_logs", is_research_path("runtime_logs/other/a.json"), False)
    check("research path: NOT docs/claude", is_research_path("docs/claude/x.md"), False)
    check("research workflow: exit-head build", is_research_workflow("research-exit-head-build.yml"), True)
    check("research workflow: NOT the relay sweep", is_research_workflow("diag-relay-sweep.yml"), False)
    wfs = result_workflows()
    # Positive control for the derivation: the two consumers E57 measured.
    check("derivation finds research-exit-head-build.yml",
          "research-exit-head-build.yml" in wfs, True)
    check("derivation ignores a workflow that only MENTIONS the action",
          "research-loss-detector.yml" in wfs, False)

    # A could-not-read question must dominate, never read as clean.
    import unittest.mock as um  # noqa: PLC0415
    me = sys.modules[__name__]
    with um.patch.object(me, "_api", side_effect=CouldNotRead("boom")):
        rep = sweep("o/r", "HEAD", 6, 1, 72)
    check("an unreadable API is COULD_NOT_READ, not clean", rep["overall"], COULD_NOT_READ)
    check("could-not-read carries findings=None, not []",
          rep["questions"]["A_stuck_research_prs"]["findings"], None)
    check("alert text names could-not-read", "COULD NOT READ" in alert_text(rep), True)

    # Stuck: a research PR past threshold is a finding; a young one is not.
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    prs = [{"number": 1, "head": {"ref": "automation/replay-pregate-1-1"},
            "created_at": "2026-09-23T00:00:00Z", "title": "old"},
           {"number": 2, "head": {"ref": "automation/research-result-2-1"},
            "created_at": "2026-09-24T11:00:00Z", "title": "young"},
           {"number": 3, "head": {"ref": "automation/work-digest-3-1"},
            "created_at": "2026-09-01T00:00:00Z", "title": "not research"},
           {"number": 4, "head": {"ref": "claude/x"},
            "created_at": "2026-09-01T00:00:00Z", "title": "not automation"}]

    def fake(path: str, repo: str, key: Optional[str], limit: int = 1000) -> list:
        if path.startswith("pulls?"):
            return prs
        if path.startswith("pulls/3/files"):
            return [{"filename": "docs/claude/pending-pings.jsonl"}]
        raise AssertionError(path)
    with um.patch.object(me, "_paged", side_effect=fake):
        q = q_stuck("o/r", now, 6)
    check("stuck: only the >6h research PR is flagged", [r["pr"] for r in q["findings"]], [1])
    # A non-research-prefixed automation PR touching research paths IS research.
    prs[2]["head"]["ref"] = "automation/data-commit-3-1"

    def fake2(path: str, repo: str, key: Optional[str], limit: int = 1000) -> list:
        if path.startswith("pulls?"):
            return prs
        return [{"filename": "docs/research/some-corpus.jsonl"}]
    with um.patch.object(me, "_paged", side_effect=fake2):
        q = q_stuck("o/r", now, 6)
    check("stuck: research found by PATH, not only by prefix",
          sorted(r["pr"] for r in q["findings"]), [1, 3])

    print("self-test OK" if not bad else f"self-test: {bad} FAILURE(S)")
    return 1 if bad else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY",
                                                     "benbaichmankass/Metis-Insights"))
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--stuck-hours", type=float, default=6.0)
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--expiry-hours", type=float, default=72.0)
    ap.add_argument("--write-receipt", action="store_true",
                    help=f"write the full report to {RECEIPT_PATH}")
    ap.add_argument("--alert-out", default=None,
                    help="write the Telegram alert text here when not clean")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    rep = sweep(a.repo, a.ref, a.stuck_hours, a.lookback_days, a.expiry_hours)
    for name, q in rep["questions"].items():
        n = "—" if q.get("findings") is None else len(q["findings"])
        print(f"{name}: {q['state']} ({n} finding(s))")
        print(f"    population: {q.get('population') or q.get('error')}")
        for r in (q.get("findings") or [])[:40]:
            print(f"    - {json.dumps(r, sort_keys=True)}")
    print(f"OVERALL: {rep['overall']}")
    if a.write_receipt:
        p = REPO / RECEIPT_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"receipt written: {RECEIPT_PATH}")
    if a.alert_out and rep["overall"] != CLEAN:
        Path(a.alert_out).write_text(alert_text(rep) + "\n", encoding="utf-8")
    return {CLEAN: 0, FINDINGS: 1, COULD_NOT_READ: 2}[rep["overall"]]


if __name__ == "__main__":
    sys.exit(main())
