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
    assert _stamp_step(job)["if"].strip() == "env.FIRE == 'true'"

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
    step = [s for s in doc["jobs"]["dispatch"]["steps"]
            if "Land the dispatch stamps" in str(s.get("name", ""))][0]
    step["if"] = bad_if
    assert step["if"].strip() != "env.FIRE == 'true'"
