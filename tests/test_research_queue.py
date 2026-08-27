"""The research job queue: its power gate, its routing, and its refusals.

WHY THIS EXISTS. The queue decides whether an experiment runs and where — and
once the dispatcher fires, it spends runner minutes and (for a GPU route) real
money. So the DECISION is a set of pure functions and this file is where the
policy is argued, rather than against a live dispatch. That is the direct lesson
of `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`, where
a remediation policy was reasoned about in prose and cancelled the wrong leg.

Every state in `POWER_STATES` and `ROUTE_STATES` is asserted here, because a
state nothing tests is a state nothing produces.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.research.research_queue import (  # noqa: E402
    CLEARED, GPU, KIND_DETERMINISTIC, NOT_APPLICABLE, POWER_STATES, ROUTE_STATES,
    RUNNABLE_POWER_STATES, RUNNER, TRAINER, UNDECLARED, UNDERPOWERED, UNROUTABLE,
    UNVERIFIABLE, grade_power, grade_route, load_queue, required_n, validate,
)

QUEUE_DIR = REPO / "research" / "queue"


def _entry(**kw):
    base = {
        "id": "RQ-20260827-999", "title": "t", "question": "q",
        "cadence": "once", "status": "queued",
        "kind": "experiment",
        "power": {"expected_n": 400, "min_detectable_effect": 0.3, "basis": "b"},
        "routing": {"peak_memory_gb": 2.0},
        "run": {"workflow": "w.yml"},
        "lands": {"store": "docs/research/x.jsonl"},
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# 1. The sample-size floor — arithmetic, against textbook values
# --------------------------------------------------------------------------
@pytest.mark.parametrize("d,expected", [(0.5, 31.4), (0.2, 196.2), (0.8, 12.3)])
def test_required_n_matches_the_normal_approximation(d, expected):
    """alpha=0.05 two-sided, power=0.80. (1.96+0.8416)^2 / d^2."""
    assert required_n(d) == pytest.approx(expected, abs=0.15)


def test_two_sample_needs_twice_the_n_per_group():
    assert required_n(0.5, design="two_sample") == pytest.approx(2 * required_n(0.5))


def test_required_n_refuses_an_untabulated_alpha_rather_than_interpolating():
    """Interpolating a quantile would put a made-up number under the gate."""
    with pytest.raises(KeyError):
        required_n(0.5, alpha=0.037)
    with pytest.raises(KeyError):
        required_n(0.5, power=0.855)


def test_required_n_rejects_a_non_positive_effect():
    with pytest.raises(ValueError):
        required_n(0.0)


# --------------------------------------------------------------------------
# 2. Every power state is reachable, and none collapses into another
# --------------------------------------------------------------------------
def test_power_cleared_when_n_meets_the_floor():
    v = grade_power(_entry())
    assert v.state == CLEARED and v.runnable
    assert v.required_n == pytest.approx(required_n(0.3))


def test_power_underpowered_blocks_rather_than_running_a_weak_answer():
    v = grade_power(_entry(power={"expected_n": 10, "min_detectable_effect": 0.3,
                                  "basis": "b"}))
    assert v.state == UNDERPOWERED and not v.runnable
    assert "data-acquisition" in v.reason


def test_power_undeclared_is_blocked_and_is_not_cleared():
    """The default state of today's advisory regime must NOT be runnable."""
    v = grade_power(_entry(power=None))
    assert v.state == UNDECLARED and not v.runnable


def test_power_unverifiable_when_the_basis_is_missing():
    """A number with no derivation cannot be told from a guess."""
    v = grade_power(_entry(power={"expected_n": 400, "min_detectable_effect": 0.3}))
    assert v.state == UNVERIFIABLE and not v.runnable


def test_raw_units_require_an_sd_so_an_effect_is_never_read_as_a_d():
    v = grade_power(_entry(power={"expected_n": 400, "min_detectable_effect": 2.5,
                                  "effect_units": "R", "basis": "b"}))
    assert v.state == UNVERIFIABLE
    assert "'R'" in v.reason, "the refusal must echo the author's own spelling"


def test_raw_units_with_an_sd_standardise_correctly():
    v = grade_power(_entry(power={"expected_n": 400, "min_detectable_effect": 1.0,
                                  "sd": 2.0, "effect_units": "R", "basis": "b"}))
    assert v.state == CLEARED and v.effect_size_d == pytest.approx(0.5)


def test_deterministic_is_exempt_only_with_a_written_reason():
    ok = grade_power(_entry(kind=KIND_DETERMINISTIC, why_not_inferential="re-grades a fixed ledger"))
    assert ok.state == NOT_APPLICABLE and ok.runnable
    bare = grade_power(_entry(kind=KIND_DETERMINISTIC))
    assert bare.state == UNDECLARED and not bare.runnable, \
        "an exemption nobody had to justify is a bypass"


def test_not_applicable_is_never_cleared():
    """The collapse this state exists to prevent.

    Folding them would let someone tally 'N jobs cleared the power gate' over a
    population where some never took the test.
    """
    assert NOT_APPLICABLE != CLEARED
    assert NOT_APPLICABLE in RUNNABLE_POWER_STATES and CLEARED in RUNNABLE_POWER_STATES
    v = grade_power(_entry(kind=KIND_DETERMINISTIC, why_not_inferential="x"))
    assert v.state != CLEARED


def test_an_unknown_kind_is_refused_not_defaulted():
    assert grade_power(_entry(kind="whatever")).state == UNVERIFIABLE


def test_every_declared_power_state_is_produced_by_some_input():
    produced = {
        grade_power(_entry()).state,
        grade_power(_entry(power={"expected_n": 10, "min_detectable_effect": 0.3, "basis": "b"})).state,
        grade_power(_entry(power=None)).state,
        grade_power(_entry(power={"expected_n": 400, "min_detectable_effect": 0.3})).state,
        grade_power(_entry(kind=KIND_DETERMINISTIC, why_not_inferential="x")).state,
    }
    assert produced == set(POWER_STATES)


# --------------------------------------------------------------------------
# 3. Routing — declared requirements only, never inferred
# --------------------------------------------------------------------------
def test_plain_cpu_job_goes_to_a_runner():
    assert grade_route(_entry()).state == RUNNER


def test_trainer_resident_data_goes_to_the_trainer():
    assert grade_route(_entry(routing={"needs_trainer_resident_data": True,
                                       "peak_memory_gb": 4.0})).state == TRAINER


def test_gpu_job_routes_to_gpu():
    assert grade_route(_entry(routing={"needs_gpu": True, "peak_memory_gb": 8.0})).state == GPU


def test_gpu_plus_trainer_residency_is_refused_not_guessed():
    v = grade_route(_entry(routing={"needs_gpu": True, "needs_trainer_resident_data": True,
                                    "peak_memory_gb": 2.0}))
    assert v.state == UNROUTABLE


def test_a_job_too_big_for_the_trainer_is_refused_not_sent_to_be_killed():
    v = grade_route(_entry(routing={"needs_trainer_resident_data": True,
                                    "peak_memory_gb": 9.0}))
    assert v.state == UNROUTABLE and "OOM" in v.reason


def test_a_job_too_big_for_any_destination_is_unroutable():
    assert grade_route(_entry(routing={"peak_memory_gb": 40.0})).state == UNROUTABLE


def test_undeclared_memory_is_unroutable_not_assumed_small():
    assert grade_route(_entry(routing={"needs_gpu": False})).state == UNROUTABLE


def test_missing_routing_block_is_unroutable():
    assert grade_route(_entry(routing=None)).state == UNROUTABLE


def test_every_declared_route_state_is_produced_by_some_input():
    produced = {
        grade_route(_entry()).state,
        grade_route(_entry(routing={"needs_trainer_resident_data": True, "peak_memory_gb": 4.0})).state,
        grade_route(_entry(routing={"needs_gpu": True, "peak_memory_gb": 8.0})).state,
        grade_route(_entry(routing=None)).state,
    }
    assert produced == set(ROUTE_STATES)


# --------------------------------------------------------------------------
# 4. Structural validation — a malformed job is an ERROR, never a skip
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mutation,needle", [
    ({"id": "nope"}, "RQ-YYYYMMDD-NNN"),
    ({"title": ""}, "title"),
    ({"question": "  "}, "question"),
    ({"cadence": "hourly"}, "cadence"),
    ({"status": "pending"}, "status"),
    ({"run": {}}, "run.workflow"),
    ({"lands": {}}, "lands.store"),
])
def test_validate_catches(mutation, needle):
    errs = validate(_entry(**mutation))
    assert any(needle in e for e in errs), f"expected an error mentioning {needle}: {errs}"


def test_validate_is_quiet_on_a_good_entry():
    """The negative control: the probe above must be able to return nothing."""
    assert validate(_entry()) == []


def test_filename_must_equal_the_id(tmp_path):
    p = tmp_path / "RQ-20260827-002.yaml"
    errs = validate(_entry(id="RQ-20260827-999"), path=p)
    assert any("must equal id" in e for e in errs)


# --------------------------------------------------------------------------
# 5. load_queue — "empty" and "could not look" are different answers
# --------------------------------------------------------------------------
def test_empty_dir_reads_as_empty_not_as_an_error(tmp_path):
    jobs, err = load_queue(tmp_path)
    assert jobs == [] and err is None


def test_missing_dir_reads_as_an_error_not_as_empty(tmp_path):
    jobs, err = load_queue(tmp_path / "absent")
    assert jobs == [] and err is not None


def test_an_unparseable_file_is_reported_not_dropped(tmp_path):
    (tmp_path / "RQ-20260827-003.yaml").write_text("{{{ not yaml")
    jobs, err = load_queue(tmp_path)
    assert err is None and len(jobs) == 1 and not jobs[0].valid


# --------------------------------------------------------------------------
# 6. The COMMITTED queue must stay valid — a seed that rots teaches nothing
# --------------------------------------------------------------------------
def test_the_real_queue_parses_and_every_job_is_structurally_valid():
    jobs, err = load_queue(QUEUE_DIR)
    assert err is None, f"the committed queue could not be read: {err}"
    assert jobs, "the committed queue is empty — this test would otherwise assert nothing"
    for job in jobs:
        assert job.valid, f"{job.path.name}: {job.errors}"


def test_every_committed_job_is_both_powered_and_routable():
    """A committed job that the gate would refuse is a queue nobody can run."""
    jobs, _ = load_queue(QUEUE_DIR)
    for job in jobs:
        p, r = grade_power(job.raw), grade_route(job.raw)
        assert p.runnable, f"{job.path.name}: power={p.state} — {p.reason}"
        assert r.runnable, f"{job.path.name}: route={r.state} — {r.reason}"


# --------------------------------------------------------------------------
# 7. The dispatcher — dry run by default, and it never fires by accident
# --------------------------------------------------------------------------
def _run_dispatcher(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/research/dispatch_queue.py"), *args],
        capture_output=True, text=True, timeout=120, cwd=str(REPO),
    )


def test_dispatcher_is_a_dry_run_unless_fire_is_passed():
    out = _run_dispatcher("--queue-dir", str(QUEUE_DIR), "--json")
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    outcomes = {d["outcome"] for d in payload["decisions"]}
    assert "dispatched" not in outcomes
    assert "would_dispatch" in outcomes


def test_dispatcher_exits_nonzero_when_it_could_not_read_the_queue(tmp_path):
    """A read failure must never render as a quiet, successful, empty run."""
    out = _run_dispatcher("--queue-dir", str(tmp_path / "absent"))
    assert out.returncode == 2
    assert "NOT an empty queue" in out.stderr


def test_dispatcher_reports_an_empty_queue_as_explicitly_empty(tmp_path):
    out = _run_dispatcher("--queue-dir", str(tmp_path))
    assert out.returncode == 0
    assert "EMPTY" in out.stdout and "not a read failure" in out.stdout


def test_dispatcher_flags_an_invalid_job_and_exits_nonzero(tmp_path):
    (tmp_path / "RQ-20260827-004.yaml").write_text("id: wrong\n")
    out = _run_dispatcher("--queue-dir", str(tmp_path))
    assert out.returncode == 1 and "invalid" in out.stdout


# --------------------------------------------------------------------------
# 8. Cadence — and the fail-safe direction on an undateable stamp
# --------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from scripts.research.dispatch_queue import _is_due  # noqa: E402

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def test_a_job_that_never_ran_is_due():
    due, why = _is_due({"cadence": "weekly"}, NOW)
    assert due and "never" in why


def test_a_once_job_that_ran_never_runs_again():
    due, _ = _is_due({"cadence": "once", "last_dispatched_at": "2026-08-01T00:00:00Z"}, NOW)
    assert not due


def test_a_weekly_job_is_due_once_the_gap_elapses():
    old = (NOW - timedelta(days=8)).isoformat()
    recent = (NOW - timedelta(days=2)).isoformat()
    assert _is_due({"cadence": "weekly", "last_dispatched_at": old}, NOW)[0]
    assert not _is_due({"cadence": "weekly", "last_dispatched_at": recent}, NOW)[0]


def test_an_undateable_stamp_does_not_fire():
    """We cannot show the cadence elapsed, so we do not spend.

    The opposite reading — 'unparseable, so treat it as long ago' — would fire
    on every run forever, which is the loop the per-run GPU cap exists to bound
    and which should not be reachable from a parsing failure in the first place.
    """
    due, why = _is_due({"cadence": "daily", "last_dispatched_at": "last tuesday"}, NOW)
    assert not due and "undateable" in why


def test_gpu_dispatches_are_capped_per_run(tmp_path):
    """The ledger cap is MONTHLY; it cannot bound a loop inside one run."""
    for n in (11, 12):
        (tmp_path / f"RQ-20260827-0{n}.yaml").write_text(
            f"id: RQ-20260827-0{n}\ntitle: t\nquestion: q\ncadence: once\nstatus: queued\n"
            "kind: deterministic\nwhy_not_inferential: fixed re-grade\n"
            "routing: {needs_gpu: true, peak_memory_gb: 8.0}\n"
            "run: {workflow: gpu-burst-train.yml}\n"
            "lands: {store: docs/research/x.jsonl}\n"
        )
    out = _run_dispatcher("--queue-dir", str(tmp_path), "--json",
                          "--max-gpu-dispatches-per-run", "1")
    payload = json.loads(out.stdout)
    # Dry run: both are `would_dispatch` because nothing was actually fired, so
    # the counter never advances. The cap is asserted on the FIRE path below.
    assert len(payload["decisions"]) == 2


def test_the_gpu_cap_actually_stops_the_second_burst(tmp_path, monkeypatch):
    """Exercise the cap for real, without spending anything.

    An earlier version of this test asserted on exact source strings and broke
    the moment the dispatch block was refactored — a test that pins the SHAPE of
    code rather than its BEHAVIOUR fails on correct changes and passes on
    incorrect ones. `_fire` is stubbed instead, so the cap is measured on what
    the dispatcher does, and no GPU is ever launched.
    """
    from scripts.research import dispatch_queue as dq

    for n in (21, 22, 23):
        (tmp_path / f"RQ-20260827-0{n}.yaml").write_text(
            f"id: RQ-20260827-0{n}\ntitle: t\nquestion: q\ncadence: once\nstatus: queued\n"
            "kind: deterministic\nwhy_not_inferential: fixed re-grade\n"
            "routing: {needs_gpu: true, peak_memory_gb: 8.0}\n"
            "run: {workflow: gpu-burst-train.yml}\n"
            "lands: {store: docs/research/x.jsonl}\n"
        )
    fired: list = []
    monkeypatch.setattr(dq, "_fire",
                        lambda entry, *, route, ref: (fired.append(entry["id"]), (True, "stub"))[1])
    rc = dq.main(["--queue-dir", str(tmp_path), "--fire",
                  "--max-gpu-dispatches-per-run", "2", "--json"])
    assert rc == 0
    assert len(fired) == 2, f"cap=2 must stop the third burst, fired={fired}"


def test_a_failed_fire_is_not_stamped(tmp_path, monkeypatch):
    """Stamping a failure would mark the job run and drop it for a full period."""
    import yaml

    from scripts.research import dispatch_queue as dq

    job = tmp_path / "RQ-20260827-031.yaml"
    job.write_text(
        "id: RQ-20260827-031\ntitle: t\nquestion: q\ncadence: daily\nstatus: queued\n"
        "kind: deterministic\nwhy_not_inferential: fixed re-grade\n"
        "routing: {peak_memory_gb: 2.0}\n"
        "run: {workflow: w.yml}\n"
        "lands: {store: docs/research/x.jsonl}\n"
    )
    monkeypatch.setattr(dq, "_fire", lambda entry, *, route, ref: (False, "boom"))
    dq.main(["--queue-dir", str(tmp_path), "--fire"])
    assert yaml.safe_load(job.read_text()).get("last_dispatched_at") is None


def test_a_successful_fire_is_stamped(tmp_path, monkeypatch):
    """The positive control for the test above — the probe can see a stamp."""
    import yaml

    from scripts.research import dispatch_queue as dq

    job = tmp_path / "RQ-20260827-032.yaml"
    job.write_text(
        "id: RQ-20260827-032\ntitle: t\nquestion: q\ncadence: daily\nstatus: queued\n"
        "kind: deterministic\nwhy_not_inferential: fixed re-grade\n"
        "routing: {peak_memory_gb: 2.0}\n"
        "run: {workflow: w.yml}\n"
        "lands: {store: docs/research/x.jsonl}\n"
    )
    monkeypatch.setattr(dq, "_fire", lambda entry, *, route, ref: (True, "stub"))
    dq.main(["--queue-dir", str(tmp_path), "--fire"])
    assert yaml.safe_load(job.read_text()).get("last_dispatched_at")
