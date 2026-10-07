"""The research-queue dispatcher is idempotent on RESULTS and OPEN STAMP PRs,
not only on the stamp it has to write itself (lane RQ-FIX, 2026-10-06).

MEASURED the morning #16736 made the cron hourly: RQ-20261006-005 (cadence
once) fired at 09:59 and 11:05 and RQ-20261004-659 at 10:04 and 11:09, because
the stamp PR of the first fire was still in CI when the second cycle read main.
The two stamp PRs then conflicted on the same line and the loser (#16770) went
`dirty`, so its stamps were dropped whole.

Three mechanisms, each tested in BOTH directions:
  1. `_is_due` reads research/results/<uid>/ as a stamp (`latest_result_at`).
  2. `main()` (fire path) treats a unit whose yaml an OPEN stamp PR modifies as
     already dispatched (`pending_stamp_units`), and DEFERS every fire when
     GitHub cannot be asked.
  3. A unit with results but no stamp (a dropped stamp PR) is re-stamped from
     its latest result so the grader can read it (`stamp_lost`) -- unless the
     grader itself cleared the stamp for a confirmatory run.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from scripts.research import dispatch_queue as dq

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2030, 1, 10, 12, tzinfo=timezone.utc)
UID = "RQ-20300101-001"
UID2 = "RQ-20300101-002"


def _row(generated_at: str, run_id: str = "1", verdict: str = "fail") -> str:
    return json.dumps({"research_unit": UID, "verdict": verdict, "read_state": "measured",
                       "generated_at": generated_at, "produced_by": {"run_id": run_id}})


def _results(tmp_path: Path, uid: str, rows: list[str], run_id: str = "1") -> Path:
    root = tmp_path / "results"
    (root / uid).mkdir(parents=True, exist_ok=True)
    (root / uid / f"{run_id}.jsonl").write_text("\n".join(rows) + "\n")
    return root


def _entry(**kw) -> dict:
    e = {"id": UID, "status": "queued", "cadence": "once", "run": {"workflow": "x.yml"}}
    e.update(kw)
    return e


# ── 1. result-based idempotence in _is_due ─────────────────────────────────────

def test_a_once_unit_with_a_landed_result_and_no_stamp_is_not_due(tmp_path, monkeypatch):
    monkeypatch.setattr(dq, "RESULTS_ROOT", _results(tmp_path, UID, [_row("2030-01-09T10:00:00Z")]))
    due, why = dq._is_due(_entry(last_dispatched_at=None), NOW)
    assert due is False and "result-based idempotence" in why and UID in why


def test_a_once_unit_with_no_result_and_no_stamp_is_due(tmp_path, monkeypatch):
    monkeypatch.setattr(dq, "RESULTS_ROOT", tmp_path / "results")      # absent dir
    assert dq._is_due(_entry(last_dispatched_at=None), NOW) == (True, "never dispatched")


def test_a_recurring_unit_measures_its_cadence_from_the_later_of_stamp_and_result(tmp_path, monkeypatch):
    stamp = (NOW - timedelta(days=10)).isoformat()
    monkeypatch.setattr(dq, "RESULTS_ROOT", _results(tmp_path, UID, [_row((NOW - timedelta(days=2)).isoformat())]))
    due, why = dq._is_due(_entry(cadence="weekly", last_dispatched_at=stamp), NOW)
    assert due is False and "result row" in why and "not yet elapsed" in why
    monkeypatch.setattr(dq, "RESULTS_ROOT", _results(tmp_path, UID, [_row((NOW - timedelta(days=8)).isoformat())]))
    due, why = dq._is_due(_entry(cadence="weekly", last_dispatched_at=stamp), NOW)
    assert due is True and "elapsed" in why


def test_an_undated_result_with_no_stamp_is_fail_safe_not_due(tmp_path, monkeypatch):
    monkeypatch.setattr(dq, "RESULTS_ROOT", _results(tmp_path, UID, [_row("not a date")]))
    due, why = dq._is_due(_entry(cadence="weekly", last_dispatched_at=None), NOW)
    assert due is False and "undated" in why


def test_latest_result_at_reads_rows_not_files(tmp_path):
    root = _results(tmp_path, UID, [_row("2030-01-01T00:00:00Z"), "", "not json", _row("2030-01-05T00:00:00Z")])
    at, rows = dq.latest_result_at(UID, root)
    assert rows == 2 and at == datetime(2030, 1, 5, tzinfo=timezone.utc)
    assert dq.latest_result_at("RQ-29990101-999", root) == (None, 0)


def test_the_committed_queue_is_unchanged_by_the_result_rule():
    """Positive control over the real queue: every unit the stamp already marks as
    run reads the same way, and nothing flips to due because of a result row."""
    from scripts.research.research_queue import load_queue
    jobs, err = load_queue(REPO / "research" / "queue")
    assert not err and jobs
    now = datetime.now(timezone.utc)
    flipped = []
    for j in jobs:
        e = dict(j.raw)
        with_results = dq._is_due(e, now)[0]
        e2 = dict(e)
        e2["id"] = "RQ-29990101-999"           # no results dir -> stamp-only reading
        stamp_only = dq._is_due(e2, now)[0]
        if with_results and not stamp_only:
            flipped.append(j.id)
    assert not flipped, f"result rows made these units DUE that the stamp says ran: {flipped}"


# ── 2. open stamp PRs ─────────────────────────────────────────────────────────

def _api(pulls, files_by_pr):
    def api(path):
        if path.startswith("repos/{owner}/{repo}/pulls?"):
            return pulls
        for n, files in files_by_pr.items():
            if f"/pulls/{n}/files" in path:
                return files
        return None
    return api


PULLS = [{"number": 7, "head": {"ref": f"{dq.STAMP_BRANCH_PREFIX}-1-1"}},
         {"number": 8, "head": {"ref": "automation/research-result-2-1"}},
         {"number": 9, "head": {"ref": f"{dq.STAMP_BRANCH_PREFIX}-3-1"}}]
FILES = {7: [{"filename": f"research/queue/{UID}.yaml"}, {"filename": ".github/merge-slots/x.json"}],
         8: [{"filename": f"research/queue/{UID2}.yaml"}],           # not a stamp branch: ignored
         9: [{"filename": "docs/claude/work/research-queue-dispatch-receipt.json"}]}


def test_pending_stamp_units_maps_only_stamp_branches_touching_queue_files():
    assert dq.pending_stamp_units(_api(PULLS, FILES)) == {UID: f"PR #7 ({dq.STAMP_BRANCH_PREFIX}-1-1)"}


def test_pending_stamp_units_is_none_when_github_cannot_be_asked():
    assert dq.pending_stamp_units(lambda path: None) is None
    assert dq.pending_stamp_units(_api(PULLS, {7: None, 9: []})) is None   # one PR's files unreadable
    assert dq.pending_stamp_units(_api([], {})) == {}                        # asked, nothing open


def test_the_real_gh_api_wrapper_treats_a_failed_call_as_none(monkeypatch):
    class P:
        returncode = 1
        stdout = "[]"
    monkeypatch.setattr(dq.subprocess, "run", lambda *a, **k: P())
    assert dq._gh_api_json("repos/{owner}/{repo}/pulls") is None


# ── 3. a fired cycle ──────────────────────────────────────────────────────────

def _unit_text(uid: str, **kw) -> str:
    d = yaml.safe_load((REPO / "research/queue/RQ-20260928-016.yaml").read_text())
    d.update(id=uid, status="queued", cadence="once", last_dispatched_at=None)
    d.pop("grading", None)
    d["run"] = {"workflow": "research-harness-dispatch.yml", "inputs": {"research_unit": uid}}
    d.update(kw)
    return yaml.safe_dump(d, sort_keys=False)


def _cycle(tmp_path, monkeypatch, units, pending):
    q = tmp_path / "queue"
    q.mkdir(exist_ok=True)
    for uid, kw in units:
        (q / f"{uid}.yaml").write_text(_unit_text(uid, **kw))
    fired = []

    def fake_run(cmd, *a, **k):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        if cmd[:3] == ["gh", "workflow", "run"]:
            fired.append(cmd)
        return P()

    monkeypatch.setattr(dq, "gh_runs", lambda status, limit=200: [])
    monkeypatch.setattr(dq, "declared_inputs", lambda *a, **k: {"research_unit", "power_state"})
    monkeypatch.setattr(dq, "pending_stamp_units", lambda api=None: pending)
    monkeypatch.setattr(dq.subprocess, "run", fake_run)
    monkeypatch.setattr(dq, "RESULTS_ROOT", tmp_path / "results")
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = dq.main(["--queue-dir", str(q), "--fire", "--check-open-stamp-prs", "--ref", "main",
                      "--json", "--max-research-inflight", "9"])
    assert rc == 0
    out = json.loads(buf.getvalue())
    decisions = {d["id"]: d for d in out["decisions"]}
    stamps = {p.stem: yaml.safe_load(p.read_text()).get("last_dispatched_at") for p in q.glob("*.yaml")}
    return [c[c.index("-f") + 1] for c in fired], decisions, stamps


def test_a_unit_with_an_open_stamp_pr_is_not_fired_again_and_not_restamped(tmp_path, monkeypatch):
    fired, dec, stamps = _cycle(tmp_path, monkeypatch, [(UID, {}), (UID2, {})],
                                pending={UID: "PR #7 (automation/research-queue-stamp-1-1)"})
    assert fired == [f"research_unit={UID2}"]
    assert dec[UID]["outcome"] == dq.NOT_DUE and "PR #7" in dec[UID]["reason"]
    assert stamps[UID] is None and stamps[UID2] is not None


def test_both_fire_when_no_stamp_pr_is_open(tmp_path, monkeypatch):
    fired, _dec, stamps = _cycle(tmp_path, monkeypatch, [(UID, {}), (UID2, {})], pending={})
    assert sorted(fired) == [f"research_unit={UID}", f"research_unit={UID2}"]
    assert stamps[UID] and stamps[UID2]


def test_every_fire_is_deferred_when_open_prs_cannot_be_listed(tmp_path, monkeypatch):
    fired, dec, stamps = _cycle(tmp_path, monkeypatch, [(UID, {}), (UID2, {})], pending=None)
    assert fired == []
    assert {d["outcome"] for d in dec.values()} == {dq.DEFERRED}
    assert "open stamp PRs" in dec[UID]["reason"]
    assert stamps[UID] is None and stamps[UID2] is None


def test_without_the_flag_github_is_not_asked_and_the_stamp_alone_decides(tmp_path, monkeypatch):
    """The open-stamp-PR check is OPT-IN (`--check-open-stamp-prs`). MEASURED on
    PR #16791's first CI run: asked unconditionally, the pytest runner (no gh
    auth) deferred every fire, and three pre-existing `--fire` tests that stub
    `_fire` saw nothing fire. Without the flag `pending_stamp_units` must not be
    called at all -- a planted one that raises proves the probe can see a call."""
    q = tmp_path / "queue"
    q.mkdir()
    (q / f"{UID}.yaml").write_text(_unit_text(UID))
    fired = []

    def fake_run(cmd, *a, **k):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        if cmd[:3] == ["gh", "workflow", "run"]:
            fired.append(cmd)
        return P()

    def _never(api=None):
        raise AssertionError("pending_stamp_units must not be consulted without --check-open-stamp-prs")

    monkeypatch.setattr(dq, "gh_runs", lambda status, limit=200: [])
    monkeypatch.setattr(dq, "declared_inputs", lambda *a, **k: {"research_unit", "power_state"})
    monkeypatch.setattr(dq, "pending_stamp_units", _never)
    monkeypatch.setattr(dq.subprocess, "run", fake_run)
    monkeypatch.setattr(dq, "RESULTS_ROOT", tmp_path / "results")
    import io
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        rc = dq.main(["--queue-dir", str(q), "--fire", "--ref", "main", "--json", "--max-research-inflight", "9"])
    assert rc == 0
    assert len(fired) == 1
    assert yaml.safe_load((q / f"{UID}.yaml").read_text()).get("last_dispatched_at")


def test_the_workflow_passes_the_open_stamp_pr_check_with_every_fire():
    """The flag is only worth anything if the cron's fire path carries it. Every
    line that adds --fire adds --check-open-stamp-prs on the same line, so the
    two cannot be split by a later edit without this test seeing it."""
    wf = yaml.safe_load((REPO / ".github/workflows/research-queue-dispatch.yml").read_text())
    run = "\n".join(str(s.get("run", "")) for s in wf["jobs"]["dispatch"]["steps"])
    fire_lines = [ln for ln in run.splitlines()
                  if "--fire" in ln and not ln.lstrip().startswith("#")]
    assert fire_lines, "no line adds --fire"
    for ln in fire_lines:
        assert "--check-open-stamp-prs" in ln, ln


def test_a_dropped_stamp_is_repaired_from_the_latest_result(tmp_path, monkeypatch):
    _results(tmp_path, UID, [_row("2030-01-09T10:00:00+00:00", run_id="55")], run_id="55")
    fired, dec, stamps = _cycle(tmp_path, monkeypatch, [(UID, {})], pending={})
    assert fired == []
    assert dec[UID]["outcome"] == dq.NOT_DUE and dec[UID]["stamp_repaired_to"] == "2030-01-09T10:00:00+00:00"
    assert stamps[UID] == "2030-01-09T10:00:00+00:00"


def test_a_grader_requeue_for_a_confirmatory_run_fires_and_is_not_repaired(tmp_path, monkeypatch):
    _results(tmp_path, UID, [_row("2030-01-09T10:00:00+00:00", run_id="55")], run_id="55")
    grading = {"auto": True, "confirmatory_run": 1, "graded_at": "2030-01-09T12:00:00+00:00"}
    fired, dec, stamps = _cycle(tmp_path, monkeypatch, [(UID, {"grading": grading})], pending={})
    assert fired == [f"research_unit={UID}"]
    assert "confirmatory" in dec[UID]["reason"] if "reason" in dec[UID] else True
    assert stamps[UID] is not None and stamps[UID] != "2030-01-09T10:00:00+00:00"


def test_a_result_landed_after_the_grading_means_the_confirmatory_run_already_ran():
    grading = {"confirmatory_run": 1, "graded_at": "2030-01-09T12:00:00+00:00"}
    assert dq.confirmatory_requeue({"grading": grading}, datetime(2030, 1, 9, 10, tzinfo=timezone.utc))
    assert not dq.confirmatory_requeue({"grading": grading}, datetime(2030, 1, 9, 13, tzinfo=timezone.utc))
    assert not dq.confirmatory_requeue({"grading": {"confirmatory_run": 0}}, None)
    assert not dq.confirmatory_requeue({}, None)


# ── 4. the workflow lands each throwaway branch from the checked-out base ────

def test_each_landing_returns_to_the_checked_out_base():
    """Stamp PR #16770 carried the receipt json and went `dirty` when the next
    run's receipt merged; each commit-to-main call must start from BASE_SHA."""
    wf = yaml.safe_load((REPO / ".github/workflows/research-queue-dispatch.yml").read_text())
    steps = wf["jobs"]["dispatch"]["steps"]
    names = [str(s.get("name", "")) for s in steps]
    record = [i for i, s in enumerate(steps) if "BASE_SHA=" in str(s.get("run", ""))]
    assert len(record) == 1, "exactly one step records BASE_SHA"
    lands = [i for i, s in enumerate(steps) if s.get("uses") == "./.github/actions/commit-to-main"]
    assert len(lands) == 3, names
    batch, receipt, stamp = lands
    resets = [i for i, s in enumerate(steps) if "git checkout -q --detach \"${BASE_SHA}\"" in str(s.get("run", ""))]
    assert record[0] < batch, "the base is recorded before the first landing"
    assert any(batch < r < receipt for r in resets), "the receipt landing starts from the base"
    assert any(receipt < r < stamp for r in resets), "the stamp landing starts from the base"
    stamp_reset = next(steps[r] for r in resets if receipt < r < stamp)
    assert str(stamp_reset.get("if", "")).strip() == "env.FIRE == 'true'", "gated like the stamp step itself"
