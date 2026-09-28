"""The queue refills, dispatches and grades itself without a session (RQ-RUN, 2026-09-28).

Three properties, each with a planted negative in the scripts' own self-tests:
determinism (the same inputs render the same bytes, which is what lets a
generated unit self-land), dedupe (a family key is generated once), and the
grader's refusal to guess (mixed / producer_failed / too few rows -> needs_review,
a first FAIL re-queues rather than kills).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.ops import schedule_keeper  # noqa: E402
from scripts.research import queue_grade, queue_replenish  # noqa: E402

WFS = {
    "replenish": REPO / ".github/workflows/research-queue-replenish.yml",
    "grade": REPO / ".github/workflows/research-queue-grade.yml",
    "dispatch": REPO / ".github/workflows/research-queue-dispatch.yml",
}


def test_self_tests_pass():
    for script in ("scripts/research/queue_replenish.py", "scripts/research/queue_grade.py",
                   "scripts/ci/check_research_queue_health.py"):
        proc = subprocess.run([sys.executable, str(REPO / script), "--self-test"],
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, script + "\n" + proc.stdout + proc.stderr


def test_every_template_generates_valid_units_deterministically():
    p1 = queue_replenish.plan(REPO, day="2031-01-01", target=200)
    p2 = queue_replenish.plan(REPO, day="2031-01-01", target=200)
    assert [u["text"] for u in p1["new_units"]] == [u["text"] for u in p2["new_units"]]
    families = {u["family"] for u in p1["new_units"]}
    templates = {t[2]["family"] for t in queue_replenish.load_templates(REPO)}
    assert families == templates, f"a family generates nothing: {templates - families}"
    keys = [u["key"] for u in p1["new_units"]]
    assert len(keys) == len(set(keys))
    from scripts.research.research_queue import validate
    for u in p1["new_units"]:
        d = yaml.safe_load(u["text"])
        assert validate(d) == [], (u["id"], validate(d))
        assert d["decision_rule"]["registered_before_run"] is True
        assert d["generated"]["key"] == u["key"]


def test_weights_follow_the_operators_priorities():
    weights = {t[2]["family"]: int(t[2]["weight"]) for t in queue_replenish.load_templates(REPO)}
    assert weights == {"prop-fit-breakout": 30, "m20-exit-lever": 25, "e35-bracket": 20,
                       "roster-walkforward": 15, "macro-valuation": 10}
    assert queue_replenish.allocate(20, weights, {k: 99 for k in weights}) == \
        {"prop-fit-breakout": 6, "m20-exit-lever": 5, "e35-bracket": 4, "roster-walkforward": 3, "macro-valuation": 2}


def test_generated_units_dedupe_against_the_committed_queue():
    units, _ = queue_replenish.existing_units(REPO)
    seen = {str((u.get("generated") or {}).get("key")) for u in units.values() if isinstance(u.get("generated"), dict)}
    p = queue_replenish.plan(REPO, day="2031-01-01", target=200)
    assert not seen & {u["key"] for u in p["new_units"]}


def test_grader_never_touches_a_hand_written_unit_without_opt_in(tmp_path):
    (tmp_path / "research/queue").mkdir(parents=True)
    (tmp_path / "research/results/RQ-20310101-001").mkdir(parents=True)
    (tmp_path / "research/results/RQ-20310101-001/1.jsonl").write_text('{"verdict":"pass","read_state":"measured"}\n')
    text = "id: RQ-20310101-001\nstatus: queued\ncadence: once\ndecision_rule:\n  id: R\nlands:\n  min_rows: 1\nlast_dispatched_at: '2031-01-01T00:00:00+00:00'\n"
    assert queue_grade.grade_unit(tmp_path, "RQ-20310101-001", text, "2031-01-02") is None
    opted = text + "grading:\n  auto: true\n"
    new = queue_grade.grade_unit(tmp_path, "RQ-20310101-001", opted, "2031-01-02")
    assert new and "status: done" in new


def test_workflows_are_keeper_targets_with_dedupe_and_commit_to_main_verified():
    for name in ("replenish", "grade"):
        wf = yaml.safe_load(WFS[name].read_text())
        assert WFS[name].name in schedule_keeper.TARGETS
        assert "dedupe" in wf["jobs"]
        job = wf["jobs"][name]
        c2m = [s for s in job["steps"] if s.get("uses") == "./.github/actions/commit-to-main"]
        assert len(c2m) == 1 and str(c2m[0]["with"]["verify-merged"]).lower() == "true"
        assert c2m[0]["with"]["paths"] == "research/queue"
        assert c2m[0]["with"]["branch-prefix"] == f"automation/research-queue-{name}"
        for c in schedule_keeper.workflow_crons(str(WFS[name])):
            schedule_keeper.parse_cron(c)


def test_dispatcher_pages_on_queue_health():
    text = WFS["dispatch"].read_text()
    assert "check_research_queue_health.py" in text
    i = text.index("check_research_queue_health.py")
    assert "send-ping" in text[i:i + 1500]


def _mini_repo(tmp_path: Path) -> Path:
    """A git repo holding the templates, the two producers and one leg."""
    import shutil
    r = tmp_path / "r"
    for sub in ("research/templates", "research/queue", "config", "comms/strategy_evidence", "scripts/research", "scripts/ci"):
        (r / sub).mkdir(parents=True)
    for f in (REPO / "research/templates").glob("*"):
        shutil.copy(f, r / "research/templates" / f.name)
    for f in ("scripts/research/queue_replenish.py", "scripts/research/queue_grade.py"):
        shutil.copy(REPO / f, r / f)
    (r / "config/strategies.yaml").write_text(yaml.safe_dump({"strategies": {
        "xrp_pullback_2h": {"symbols": ["XRPUSDT"], "timeframe": "2h", "execution": "live", "enabled": True}}}))
    (r / "config/accounts.yaml").write_text(yaml.safe_dump({"accounts": {"bybit_2": {"strategies": ["xrp_pullback_2h"]}}}))
    (r / "research/queue/README.md").write_text("x")
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(r), check=True, capture_output=True)  # noqa: E731
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    git("add", "-A"); git("commit", "-q", "-m", "base")
    return r


def test_e58_vouches_only_a_reproducible_producer_branch(tmp_path):
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    import check_pr_landing as g  # noqa: E402
    r = _mini_repo(tmp_path)
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(r), check=True, capture_output=True)  # noqa: E731
    pl = queue_replenish.plan(r, day="2031-02-02", target=2)
    for u in pl["new_units"]:
        (r / "research/queue" / f"{u['id']}.yaml").write_text(u["text"])
    git("checkout", "-q", "-b", "automation/research-queue-replenish-1-1"); git("add", "-A"); git("commit", "-q", "-m", "gen")
    changed = [f"research/queue/{u['id']}.yaml" for u in pl["new_units"]]
    vouched, notes = g.e58_generated_queue_vouch(r, "main", "automation/research-queue-replenish-1-1", changed)
    assert sorted(vouched) == sorted(changed), notes
    # same diff on a session branch: nothing vouched
    assert g.e58_generated_queue_vouch(r, "main", "claude/some-lane", changed) == ([], [])
    # a hand-edit on the producer branch: refused
    f = r / changed[0]
    f.write_text(f.read_text().replace("status: queued", "status: queued  # edited"))
    git("commit", "-q", "-am", "edit")
    vouched, notes = g.e58_generated_queue_vouch(r, "main", "automation/research-queue-replenish-1-1", changed)
    assert vouched == [] and any("REFUSED" in n for n in notes), notes
    # the grade producer never vouches an ADDED unit (it only modifies)
    vouched, notes = g.e58_generated_queue_vouch(r, "main", "automation/research-queue-grade-1-1", changed)
    assert vouched == [] and any("REFUSED" in n for n in notes), notes
