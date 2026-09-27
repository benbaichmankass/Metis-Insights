"""schedule-keeper: the slot/dedupe logic, and that every TARGET is wired.

A target the keeper dispatches WITHOUT a dedupe job would run twice per slot
(once dispatched, once when GitHub's late cron arrives). A dedupe job whose
main job does not `needs:` it decides nothing. Both halves are pinned here.
"""
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))
import schedule_keeper as sk  # noqa: E402


def test_self_test_passes():
    r = subprocess.run([sys.executable, "scripts/ops/schedule_keeper.py", "--self-test"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_target_exists_is_scheduled_and_dedupes():
    for wf in sk.TARGETS:
        path = REPO / ".github" / "workflows" / wf
        assert path.exists(), wf
        d = yaml.safe_load(path.read_text())
        on = d.get(True) or d.get("on")
        assert on.get("schedule"), f"{wf} has no schedule"
        assert "workflow_dispatch" in on, f"{wf} cannot be dispatched by the keeper"
        for c in sk.workflow_crons(str(path)):
            sk.parse_cron(c)  # the keeper's grammar must cover every target cron
        jobs = d["jobs"]
        assert "dedupe" in jobs, f"{wf} lacks the dedupe job"
        assert jobs["dedupe"]["if"] == "github.event_name == 'schedule'"
        others = [k for k in jobs if k != "dedupe"]
        for k in others:
            needs = jobs[k].get("needs")
            needs = [needs] if isinstance(needs, str) else (needs or [])
            if "dedupe" in needs:
                cond = str(jobs[k].get("if", ""))
                assert "!cancelled()" in cond and "needs.dedupe.outputs.skip != 'true'" in cond, (
                    f"{wf}:{k} must fail OPEN (!cancelled()) and honour skip")
        assert any("dedupe" in (jobs[k].get("needs") or []) for k in others), (
            f"{wf}: no job needs dedupe, so the skip decides nothing")


def test_keeper_workflow_pages_only_on_its_paging_cron():
    d = yaml.safe_load((REPO / ".github/workflows/schedule-keeper.yml").read_text())
    crons = [s["cron"] for s in d[True]["schedule"]]
    page = [s for s in d["jobs"]["keep"]["steps"] if s.get("name", "").startswith("Page")][0]
    assert any(c in page["if"] for c in crons)
    assert "push" in d[True], "the keeper must not depend only on the scheduler it compensates for"
