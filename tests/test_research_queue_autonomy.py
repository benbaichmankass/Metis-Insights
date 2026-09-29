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
    # a family may legitimately generate nothing when EVERY point it expands to
    # is already keyed in the committed queue (macro-valuation has one point and
    # #13770 landed it); a family with unclaimed points that generates nothing
    # is the broken template this test exists to catch
    units, _ = queue_replenish.existing_units(REPO)
    seen = {str((u.get("generated") or {}).get("key")) for u in units.values() if isinstance(u.get("generated"), dict)}
    facts = queue_replenish.repo_facts(REPO)
    for _, _, tpl in queue_replenish.load_templates(REPO):
        fam = tpl["family"]
        unclaimed = [pt for pt in queue_replenish.expand(tpl, facts) if queue_replenish.point_key(fam, pt) not in seen]
        assert (fam in families) == bool(unclaimed), (fam, len(unclaimed), fam in families)
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
    for f in ("scripts/research/queue_replenish.py", "scripts/research/queue_grade.py",
              "scripts/ci/_dirty_tree.py"):  # the producers print the shared dirty-tree notice
        shutil.copy(REPO / f, r / f)
    (r / "config/strategies.yaml").write_text(yaml.safe_dump({"strategies": {
        "xrp_pullback_2h": {"symbols": ["XRPUSDT"], "timeframe": "2h", "execution": "live", "enabled": True}}}))
    (r / "config/accounts.yaml").write_text(yaml.safe_dump({"accounts": {"bybit_2": {"strategies": ["xrp_pullback_2h"]}}}))
    (r / "research/queue/README.md").write_text("x")
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(r), check=True, capture_output=True)  # noqa: E731
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    return r


def test_e58_vouches_only_a_reproducible_producer_branch(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    import check_pr_landing as g  # noqa: E402
    r = _mini_repo(tmp_path)
    # the copied producer re-expands its templates from the mini repo's rosters and
    # needs the real repo's `src.config.accounts_loader` on its path (in production
    # the checkout IS the repo, so this is the fixture's concern only)
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=str(r), check=True, capture_output=True)  # noqa: E731
    pl = queue_replenish.plan(r, day="2031-02-02", target=2)
    for u in pl["new_units"]:
        (r / "research/queue" / f"{u['id']}.yaml").write_text(u["text"])
    git("checkout", "-q", "-b", "automation/research-queue-replenish-1-1")
    git("add", "-A")
    git("commit", "-q", "-m", "gen")
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
    # PLANTED DEFECT (review 2026-09-29): a fabricated results row riding the
    # producer branch -- the grade would be "reproduced" from it -- vouches nothing
    (r / "research/results/RQ-20310202-001").mkdir(parents=True)
    (r / "research/results/RQ-20310202-001/1.jsonl").write_text('{"verdict":"pass","read_state":"measured"}\n')
    git("add", "-A")
    git("commit", "-q", "-m", "forged row")
    vouched, notes = g.e58_generated_queue_vouch(
        r, "main", "automation/research-queue-replenish-1-1", changed + ["research/results/RQ-20310202-001/1.jsonl"])
    assert vouched == [] and any("outside research/queue" in n for n in notes), notes
    # the branch's own three landing files are the only non-queue paths allowed
    own = ["research/queue/x.yaml", ".github/pr-landing/automation-research-queue-replenish-1-1.json",
           ".github/pr-automerge-requests/automation-research-queue-replenish-1-1.txt",
           ".github/merge-slots/automation-research-queue-replenish-1-1.json"]
    _, own_notes = g.e58_generated_queue_vouch(r, "main", "automation/research-queue-replenish-1-1", own)
    assert not any("outside research/queue" in n for n in own_notes), own_notes
    # the grade producer never vouches an ADDED unit (it only modifies)
    vouched, notes = g.e58_generated_queue_vouch(r, "main", "automation/research-queue-grade-1-1", changed)
    assert vouched == [] and any("REFUSED" in n for n in notes), notes


# ── runnable means "the dispatcher would fire it next cycle" (manager, 2026-09-29) ──
def test_runnable_is_what_the_dispatcher_would_fire_not_bare_queued():
    """Measured on main 2026-09-29 04:35Z: 21 units read `status: queued` while the
    dispatcher would fire 3 -- 14 once-units had already run and 8 monthly / 2 weekly
    units had not elapsed. Counting `queued` as runnable kept the refill at zero
    deficit and the alarm quiet while the runners sat idle."""
    from datetime import datetime, timedelta, timezone
    from scripts.research import dispatch_queue as dq
    from scripts.research.queue_replenish import dispatchable, runnable
    t0 = datetime(2030, 1, 10, 12, tzinfo=timezone.utc)
    ago = lambda **kw: (t0 - timedelta(**kw)).isoformat()  # noqa: E731
    wf = {"workflow": "x.yml"}
    units = {
        "once-never":    {"status": "queued", "cadence": "once", "run": wf, "last_dispatched_at": None},
        "once-ran":      {"status": "queued", "cadence": "once", "run": wf, "last_dispatched_at": ago(hours=3)},
        "monthly-fresh": {"status": "queued", "cadence": "monthly", "run": wf, "last_dispatched_at": ago(days=1)},
        "monthly-old":   {"status": "queued", "cadence": "monthly", "run": wf, "last_dispatched_at": ago(days=30)},
        "weekly-soon":   {"status": "queued", "cadence": "weekly", "run": wf, "last_dispatched_at": ago(days=6, hours=20)},
        "bad-stamp":     {"status": "queued", "cadence": "weekly", "run": wf, "last_dispatched_at": "not a date"},
        "session-bound": {"status": "queued", "cadence": "once", "run": {"workflow": "none -- session-local"}},
        "done":          {"status": "done", "cadence": "once", "run": wf, "last_dispatched_at": None},
    }
    assert sum(dispatchable(u) for u in units.values()) == 6          # the OLD, looser count
    assert runnable(units, now=t0) == ["monthly-old", "once-never", "weekly-soon"]
    assert runnable(units, now=t0, horizon_hours=0) == ["monthly-old", "once-never"]
    # and it is the dispatcher's OWN rule, not a copy of it
    for uid, u in units.items():
        if dispatchable(u):
            assert (uid in runnable(units, now=t0, horizon_hours=0)) == dq._is_due(u, t0)[0], uid


def test_live_queue_runnable_matches_dispatcher_decisions():
    """Over the committed queue: every runnable unit is one the dispatcher's dry run
    marks would_dispatch, and every would_dispatch unit with a real workflow is
    runnable (horizon 0 so the two clocks agree)."""
    from datetime import datetime, timezone
    from scripts.research import dispatch_queue as dq
    from scripts.research.queue_replenish import dispatchable, existing_units, runnable
    units, bad = existing_units(REPO)
    assert not bad, bad
    now = datetime.now(timezone.utc)
    have = set(runnable(units, now=now, horizon_hours=0))
    for uid, u in units.items():
        if dispatchable(u):
            assert (uid in have) == dq._is_due(u, now)[0], (uid, dq._is_due(u, now))
        else:
            assert uid not in have, uid
    # the alarm reads the same number
    from scripts.research.queue_grade import health
    h = health(REPO, now=now)
    assert h["runnable"] == len(runnable(units, now=now)) and h["queued"] == sum(dispatchable(u) for u in units.values())
