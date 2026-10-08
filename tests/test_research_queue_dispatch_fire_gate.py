"""The research-queue cron fires for real — and its two gates share ONE source.

WHY THIS TEST EXISTS
--------------------
Arming the cron is not a one-line change, and the shape of the trap is worth
stating because it is invisible in review. The workflow has TWO consumers of the
fire decision:

  1. the `--fire` flag passed to `dispatch_queue.py`
  2. the `if:` on the "Land the dispatch stamps on main" step

Both originally tested `inputs.fire`. On a `schedule` event the `inputs` context
is **empty**, so adding `--fire` to the schedule path WITHOUT fixing the stamp
step would have produced a runaway: every due job fired every day, and
`last_dispatched_at` never written, so nothing ever became `not_due`.

The dispatcher itself is not at fault — it stamps in the same code path that
fires (`dispatch_queue.py`, `--fire` drives both). Only the YAML could split them.

`oci-inventory.yml` records the same class from 2026-08-20: on that workflow
`github.event_name == 'schedule'` had **never once been evaluated by GitHub**,
and it was proven with throwaway probe crons rather than assumed. This test is
the static half; the live half is the first real scheduled run.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parents[1] / ".github/workflows/research-queue-dispatch.yml"


def _job() -> dict:
    return yaml.safe_load(WF.read_text())["jobs"]["dispatch"]


def _land() -> dict:
    """The job that holds the commit-to-main landings (split out of `dispatch`
    2026-10-07, RQ-CADENCE, so the concurrency group frees in minutes)."""
    return yaml.safe_load(WF.read_text())["jobs"]["land"]


def _stamp_step(job: dict) -> dict:
    hits = [s for s in job["steps"] if "Land the dispatch stamps" in str(s.get("name", ""))]
    assert len(hits) == 1, f"expected exactly one stamp step, found {len(hits)}"
    return hits[0]


def test_the_fire_decision_is_made_exactly_once():
    job = _job()
    assert "FIRE" in job.get("env", {}), (
        "the job must carry a single FIRE env — two independent gates is the bug"
    )


def test_a_scheduled_run_can_reach_fire():
    """Without this the cron is still a dry run, whatever the header claims."""
    assert "schedule" in _job()["env"]["FIRE"]


def test_a_hand_dispatch_still_defaults_to_dry_run():
    """Arming the CRON must not arm the button. `inputs.fire` defaults false."""
    wf = yaml.safe_load(WF.read_text())
    # PyYAML parses the `on:` key as the boolean True — a real trap in this repo.
    trig = wf.get("on", wf.get(True))
    assert trig["workflow_dispatch"]["inputs"]["fire"]["default"] is False
    assert "inputs.fire" in _job()["env"]["FIRE"], "the button must still be able to fire"


def test_both_consumers_read_the_env_and_not_inputs():
    """The load-bearing assertion: the two gates cannot disagree.

    A fired job whose stamp step was skipped re-fires on the next run, forever.
    """
    job = _job()
    land = _land()
    assert _stamp_step(land)["if"].strip() == "env.FIRE == 'true'"
    # the land job must read the dispatch job's decision, never re-derive it
    assert land["env"]["FIRE"].strip() == "${{ needs.dispatch.outputs.fire }}"
    assert "fire" in job["outputs"]

    run = "\n".join(s.get("run", "") for s in job["steps"])
    fire_lines = [ln for ln in run.splitlines() if "--fire" in ln]
    assert fire_lines, "no line adds --fire"
    for ln in fire_lines:
        assert "FIRE" in ln, ln
        assert "inputs.fire" not in ln, (
            "the flag must read the shared env, not `inputs` — on a schedule event "
            "`inputs` is empty and the gate silently never fires"
        )


def test_inputs_fire_is_consulted_in_exactly_one_place():
    """Belt and braces: one mention, inside FIRE. A second is a new gate."""
    body = WF.read_text()
    code = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    mentions = [ln for ln in code if "inputs.fire" in ln]
    assert len(mentions) == 1, f"inputs.fire read in {len(mentions)} places: {mentions}"
    assert "FIRE:" in mentions[0]


@pytest.mark.parametrize("bad_if", ["inputs.fire == true", "github.event_name == 'workflow_dispatch'"])
def test_the_assertions_are_not_vacuous(tmp_path, bad_if):
    """Plant the pre-2026-08-30 shape; the stamp assertion must reject it.

    Without this, a test that only ever sees the fixed file proves nothing about
    what it would do with the broken one.
    """
    doc = yaml.safe_load(WF.read_text())
    step = [s for s in doc["jobs"]["land"]["steps"]
            if "Land the dispatch stamps" in str(s.get("name", ""))][0]
    step["if"] = bad_if
    assert step["if"].strip() != "env.FIRE == 'true'"


def test_the_dispatch_stamp_is_verified_to_actually_merge():
    """The stamp is this workflow's ONLY idempotency, and it was unverified.

    The step's own comment warns that a fired-but-unstamped job "re-fires
    forever" — and guarded only ONE route to that state (the FIRE gate). The
    other is the stamp PR stranding: `commit-to-main` exits 0 when the PR is
    OPENED, so `last_dispatched_at` never reaches main and the dispatcher
    re-fires the same job every day indefinitely, burning a runner each time and,
    for a GPU-routed job, real money against the spend ledger.

    Measured the day this cron was armed: 5 stranded `automation/*` branches on
    origin, the oldest 10 weeks.
    """
    land = _stamp_step(_land())
    assert str(land.get("with", {}).get("verify-merged", "")).lower() == "true", (
        "the stamp must be verified to MERGE, not merely to have opened a PR — "
        "an unverified stamp makes the armed cron re-fire forever"
    )


def test_the_job_budget_outlasts_the_merge_wait():
    """A budget under the wait kills the job mid-wait and reports a false timeout."""
    import yaml as _yaml
    wf = _yaml.safe_load(WF.read_text())
    budget = wf["jobs"]["land"]["timeout-minutes"]
    action = _yaml.safe_load(
        (WF.parents[2] / ".github/actions/commit-to-main/action.yml").read_text())
    wait = int(action["inputs"]["verify-timeout-minutes"]["default"])
    assert budget > wait, (
        f"job timeout-minutes ({budget}) must exceed the merge wait ({wait}), "
        "or a stamp that was about to land is reported as a timeout"
    )


# ── the dispatch/land split (RQ-CADENCE, 2026-10-07) ─────────────────────────

def test_the_dispatch_job_holds_the_group_and_waits_on_no_merge():
    """The cadence fix: the job that holds the `research-queue-dispatch`
    concurrency group must not call commit-to-main (each call can wait 50 min
    on a saturated CI queue), or the next hourly slot cannot start."""
    wf = yaml.safe_load(WF.read_text())
    assert "concurrency" not in wf, "a workflow-level group would span the land job too"
    d = wf["jobs"]["dispatch"]
    assert d["concurrency"]["group"] == "research-queue-dispatch"
    assert d["concurrency"]["cancel-in-progress"] is False
    assert not [s for s in d["steps"] if str(s.get("uses", "")).endswith("commit-to-main")]
    assert int(d["timeout-minutes"]) <= 45, "a dispatch job budgeted for a merge wait is the old shape"


def test_the_land_job_has_no_group_that_could_drop_a_payload():
    """GitHub replaces an older PENDING run of a concurrency group with a newer
    one even with cancel-in-progress false; on `land` that would drop stamps, and
    a fired-but-unstamped unit re-fires."""
    land = yaml.safe_load(WF.read_text())["jobs"]["land"]
    assert "concurrency" not in land
    assert land["needs"] == "dispatch"
    assert "needs.dispatch.outputs.payload == 'true'" in land["if"]


def test_the_land_job_still_has_all_three_verified_landings():
    land = yaml.safe_load(WF.read_text())["jobs"]["land"]
    c2m = [s for s in land["steps"] if s.get("uses") == "./.github/actions/commit-to-main"]
    assert [s["with"]["branch-prefix"] for s in c2m] == [
        "automation/research-results-batch",
        "automation/research-queue-receipt",
        "automation/research-queue-stamp",
    ]
    assert all(str(s["with"]["verify-merged"]).lower() == "true" for s in c2m)


def test_a_receipt_pr_in_flight_skips_the_next_receipt_landing():
    """The receipt json is one file every run rewrites; two open receipt PRs
    conflict. The landing is skipped (fail open) while an earlier one is open."""
    steps = yaml.safe_load(WF.read_text())["jobs"]["land"]["steps"]
    rcpt = next(s for s in steps if s.get("id") == "rcpt")
    assert "automation/research-queue-receipt" in rcpt["run"]
    land = next(s for s in steps if s.get("name") == "Land the liveness receipt")
    assert land["if"].strip() == "steps.rcpt.outputs.skip != 'true'"
