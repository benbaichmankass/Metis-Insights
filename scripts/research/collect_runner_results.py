#!/usr/bin/env python3
# wiring: .github/workflows/research-queue-dispatch.yml (the collect step, once per dispatcher cycle)
"""Land the token-free runner's results ONE PR PER DISPATCHER CYCLE, not one per run.

WHY (manager review, 2026-09-29 01:27Z). `research-script-run.yml` used to end
in a `land` job that opened TWO auto-merge PRs per unit (the outputs, then the
E5 record), each with a 30-minute `verify-merged` wait and a full CI fan-out
(guards + pytest-collect + pytest-run + repo-inventory). Sixteen units fired in
one cycle meant ~32 PRs and ~130 CI jobs queued behind each other, in the same
runner pool every system-action (set-env, flatten, the Breakout AUTO-REVERT)
waits in. Research must never starve a safety action.

WHAT. The runner now only UPLOADS its outputs as an artifact (with
`record-inputs.json`, the research-result inputs `script_run.py --derive-record`
derived from what the run wrote). This collector runs at the top of every
dispatcher cycle, finds every completed `research-script-run` whose artifact is
not yet on `main`, downloads it into `comms/research/<unit>/<run_id>/`, emits
the E5 record through `research_result.py --emit` (the one owner of the record
schema), and the dispatcher lands the whole batch with a SINGLE
`commit-to-main` and asserts every record landed. One PR, one CI fan-out, one
wait per cycle.

WHAT IS AND IS NOT LANDED, stated rather than assumed:
  * a run whose `comms/research/<unit>/<run_id>/run-manifest.json` is already
    in the checkout is skipped (landed by an earlier cycle, or by the old
    per-run `land` job for runs dispatched before this change);
  * a run whose artifact carries no `record-inputs.json` is an OLD-STYLE run
    (dispatched before this change): it landed itself, skipped;
  * a run whose artifact has expired or is missing is reported by id as
    `lost` -- it is not silently dropped, and it is not fabricated either;
  * a run with `conclusion != success` still lands (its manifest, logs and a
    `producer_failed` record), exactly as the old `land` job did.

Tier-1: research tooling. Reads `gh run list` / downloads artifacts with the
ambient token, writes only comms/research/** and research/results/**.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

WORKFLOW = "research-script-run.yml"
WORKFLOW_NAME = "research-script-run"
RECORD_INPUTS = "record-inputs.json"
_ARTIFACT_RE = re.compile(r"^research-script-run-(RQ-\d{8}-\d{3})-(\d+)$")
_EMIT_FIELDS = ("research_unit", "power_state", "decision_rule_id", "decision_rule_registered_at",
                "verdict", "read_state", "population", "n", "tool", "measurement_file",
                "records_file", "artifact_store", "artifact_locator", "note")


def _gh(args: List[str], *, timeout: int = 120) -> Optional[str]:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def completed_runs(limit: int = 100) -> Optional[List[Dict[str, Any]]]:
    out = _gh(["run", "list", "--workflow", WORKFLOW, "--status", "completed", "--limit", str(limit),
               "--json", "databaseId,conclusion,headSha,createdAt,url"])
    if out is None:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def run_artifacts(repo_slug: str, run_id: int) -> Optional[List[Dict[str, Any]]]:
    out = _gh(["api", f"repos/{repo_slug}/actions/runs/{run_id}/artifacts", "--jq", ".artifacts"])
    if out is None:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def download(run_id: int, name: str, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    return _gh(["run", "download", str(run_id), "-n", name, "-D", str(dest)], timeout=600) is not None


def emit_record(root: Path, rec: Dict[str, Any], *, run: Dict[str, Any], repo_slug: str) -> Optional[str]:
    """Write the E5 record through research_result.py; returns its repo path."""
    run_id = str(run["databaseId"])
    cmd = [sys.executable, str(root / "scripts/research/research_result.py"), "--emit",
           "--workflow", WORKFLOW_NAME, "--run-id", run_id, "--run-attempt", "1",
           "--run-url", str(run.get("url") or f"https://github.com/{repo_slug}/actions/runs/{run_id}"),
           "--commit-sha", str(run.get("headSha") or "")]
    for k in _EMIT_FIELDS:
        cmd += [f"--{k.replace('_', '-')}", str(rec.get(k, "") if rec.get(k) is not None else "null" if k == "n" else "")]
    if root != _REPO:
        cmd += ["--root", str(root / "research" / "results")]   # --root is the RESULTS root
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(root))
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"::error::{run_id}: research_result.py --emit could not run ({exc})", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"::error::{run_id}: research_result.py --emit refused: {(proc.stderr or proc.stdout).strip()[-400:]}",
              file=sys.stderr)
        return None
    # research_result.py prints the written path as its one stdout line
    # (`path=` goes to $GITHUB_OUTPUT only); normalise it to root-relative.
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.endswith(".jsonl"):
            continue
        cand = Path(line)
        if cand.is_absolute():
            try:
                return cand.relative_to(root).as_posix()
            except ValueError:
                return line
        return line
    return None


def collect(root: Path, *, repo_slug: str, runs: Optional[List[Dict[str, Any]]] = None,
            artifacts_for=run_artifacts, downloader=download, dry_run: bool = False) -> Dict[str, Any]:
    """The batch: {landed:[{unit,run_id,record,min_rows}], skipped:[...], lost:[...], read_error}."""
    out: Dict[str, Any] = {"landed": [], "skipped": [], "lost": [], "read_error": None}
    if runs is None:
        runs = completed_runs()
    if runs is None:
        out["read_error"] = "gh run list failed -- could not look; nothing collected, nothing claimed"
        return out
    for run in sorted(runs, key=lambda r: int(r["databaseId"])):
        run_id = int(run["databaseId"])
        arts = artifacts_for(repo_slug, run_id)
        if arts is None:
            out["lost"].append({"run_id": run_id, "why": "artifact listing failed"})
            continue
        hit = None
        for a in arts:
            m = _ARTIFACT_RE.match(str(a.get("name") or ""))
            if m and int(m.group(2)) == run_id:
                hit = (a, m.group(1))
                break
        if hit is None:
            out["skipped"].append({"run_id": run_id, "why": "no research-script-run artifact (not a unit run)"})
            continue
        art, unit = hit
        dest = root / "comms" / "research" / unit / str(run_id)
        if (dest / "run-manifest.json").exists():
            out["skipped"].append({"run_id": run_id, "unit": unit, "why": "already on main"})
            continue
        if art.get("expired"):
            out["lost"].append({"run_id": run_id, "unit": unit, "why": "artifact expired before any cycle collected it"})
            continue
        if dry_run:
            out["landed"].append({"unit": unit, "run_id": run_id, "record": None, "dry_run": True})
            continue
        if not downloader(run_id, str(art["name"]), dest):
            out["lost"].append({"run_id": run_id, "unit": unit, "why": "artifact download failed"})
            continue
        rec_path = dest / RECORD_INPUTS
        if not rec_path.exists():
            # old-style run: its own land job landed the record; leave its outputs out too
            for f in sorted(dest.rglob("*")):
                if f.is_file():
                    f.unlink()
            out["skipped"].append({"run_id": run_id, "unit": unit, "why": "old-style run (no record-inputs.json): landed itself"})
            continue
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out["lost"].append({"run_id": run_id, "unit": unit, "why": f"record-inputs.json unreadable: {exc}"})
            continue
        record = emit_record(root, rec, run=run, repo_slug=repo_slug)
        if not record:
            out["lost"].append({"run_id": run_id, "unit": unit, "why": "record emit refused (see ::error above)"})
            continue
        out["landed"].append({"unit": unit, "run_id": run_id, "record": record,
                              "min_rows": int(rec.get("min_rows") or 1), "conclusion": run.get("conclusion")})
    return out


# ── self-test ───────────────────────────────────────────────────────────────
def _self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts/research").mkdir(parents=True)
        for f in ("research_result.py",):
            (root / "scripts/research" / f).write_text((_REPO / "scripts/research" / f).read_text(encoding="utf-8"))
        (root / "research/results").mkdir(parents=True)
        (root / "research/queue").mkdir(parents=True)
        # landed already: run 1; old-style: run 2; new: run 3 (pass) and run 4 (failed script)
        (root / "comms/research/RQ-20300101-001/1").mkdir(parents=True)
        (root / "comms/research/RQ-20300101-001/1/run-manifest.json").write_text("{}")
        runs = [{"databaseId": i, "conclusion": "success" if i != 4 else "failure", "headSha": "abc", "url": f"u{i}"}
                for i in (1, 2, 3, 4)] + [{"databaseId": 5, "conclusion": "success", "headSha": "abc", "url": "u5"}]

        def arts(_slug, run_id):
            if run_id == 5:
                return [{"name": "research-script-run-RQ-20300101-005-5", "expired": True}]
            unit = {1: "RQ-20300101-001", 2: "RQ-20300101-002", 3: "RQ-20300101-003", 4: "RQ-20300101-004"}[run_id]
            return [{"name": f"research-script-run-{unit}-{run_id}", "expired": False}]

        def fake_download(run_id, name, dest):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "run-manifest.json").write_text("{}")
            if run_id == 2:
                return True   # old-style: no record-inputs.json
            unit = name.split("research-script-run-")[1].rsplit("-", 1)[0]
            rec = {"research_unit": unit, "power_state": "runnable", "decision_rule_id": "RULE-X",
                   "decision_rule_registered_at": "2030-01-01", "tool": "python3 x.py",
                   "artifact_store": f"comms/research/{unit}/{run_id}", "artifact_locator": "x", "note": "",
                   "measurement_file": "", "records_file": "", "min_rows": "1"}
            if run_id == 3:
                rec.update(verdict="pass", read_state="measured", population="7 committed trades", n="7")
            else:
                rec.update(verdict="not_applicable", read_state="producer_failed", population="script failed", n="null")
            (dest / RECORD_INPUTS).write_text(json.dumps(rec))
            return True

        res = collect(root, repo_slug="o/r", runs=runs, artifacts_for=arts, downloader=fake_download)
        assert [x["run_id"] for x in res["landed"]] == [3, 4], res
        assert [x["run_id"] for x in res["skipped"]] == [1, 2], res
        assert [x["run_id"] for x in res["lost"]] == [5], res
        for x in res["landed"]:
            assert (root / x["record"]).exists(), x
            rows = [json.loads(ln) for ln in (root / x["record"]).read_text().splitlines() if ln.strip()]
            assert rows and rows[0]["research_unit"] == x["unit"] and rows[0]["produced_by"]["run_id"] == str(x["run_id"]), rows
        # idempotent: a second collect lands nothing new
        res2 = collect(root, repo_slug="o/r", runs=runs, artifacts_for=arts, downloader=fake_download)
        assert res2["landed"] == [] and len(res2["skipped"]) == 4, res2
        # gh unreadable -> read_error, nothing claimed
        assert collect(root, repo_slug="o/r", runs=None, artifacts_for=arts, downloader=fake_download)["read_error"] \
            if not _gh(["--version"]) else True
        # dry run touches nothing
        (root / "comms/research/RQ-20300101-003").rename(root / "comms/research/RQ-20300101-003.bak")
        res3 = collect(root, repo_slug="o/r", runs=runs, artifacts_for=arts, downloader=fake_download, dry_run=True)
        assert any(x["run_id"] == 3 and x["dry_run"] for x in res3["landed"]) and not (root / "comms/research/RQ-20300101-003").exists()
    print("collect_runner_results self-test OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--repo", default="", help="owner/name (default: $GITHUB_REPOSITORY)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be collected; write nothing")
    ap.add_argument("--json-out", default="", help="write the batch manifest here (the dispatcher reads it)")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    import os
    slug = args.repo or os.environ.get("GITHUB_REPOSITORY") or ""
    if not slug:
        ap.error("--repo or $GITHUB_REPOSITORY is required")
    res = collect(_REPO, repo_slug=slug, dry_run=args.dry_run)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print(f"collect_runner_results: landed={len(res['landed'])} skipped={len(res['skipped'])} lost={len(res['lost'])}"
          + (f" READ_ERROR={res['read_error']}" if res["read_error"] else ""))
    for x in res["landed"]:
        print(f"  landed  {x['unit']:<18} run {x['run_id']}  {x.get('record') or '(dry run)'}")
    for x in res["lost"]:
        print(f"  LOST    {x.get('unit', '?'):<18} run {x['run_id']}  {x['why']}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"count={len(res['landed'])}\nlost={len(res['lost'])}\n")
    # A lost run is a red cycle: rows that cost runner minutes must never vanish silently.
    return 1 if res["lost"] or res["read_error"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
