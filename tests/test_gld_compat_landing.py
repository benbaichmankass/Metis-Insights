"""The compat-matrix run lands its verdicts, and the assertion can FAIL.

`gld-compat-matrix.yml` ended at `upload-artifact`, which a PM-side session
cannot download — one of the eighteen in
`BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`. It is also
the first job an armed research-queue cron fires, and `RQ-20260827-001` declares
a `lands.store` that had never existed on `main`.

The load-bearing test is `test_the_landing_assertion_is_run_scoped`: on a
CUMULATIVE store an assertion keyed on a field the store already holds is
satisfied by history and can never fail — the e35 vacuity fixed in #10487.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
WF = REPO / ".github/workflows/gld-compat-matrix.yml"

from scripts.research.gld_compat_extract import rows_from  # noqa: E402


def _steps() -> list[dict]:
    return yaml.safe_load(WF.read_text())["jobs"]["compat"]["steps"]


def _named(frag: str) -> dict:
    hits = [s for s in _steps() if frag.lower() in str(s.get("name", "")).lower()]
    assert len(hits) == 1, f"expected one step matching {frag!r}, got {len(hits)}"
    return hits[0]


def test_extractor_selftest_passes():
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--selftest"], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_producer_key_is_renamed_at_the_boundary():
    """`account_compat_matrix.py` writes `account`; the corpus keys `account_id`.

    The queue job's `assert_field` is `account_id`, and every other store in the
    repo keys accounts that way. The rename happens once, here.
    """
    row = rows_from({"generated_at": "t", "rows": [{"account": "bybit_2", "verdict": "ROUTE"}]})[0]
    assert row["account_id"] == "bybit_2"
    assert "account" not in row


def test_an_undateable_payload_is_refused():
    """A row that cannot be dated cannot be scoped to its run."""
    with pytest.raises(ValueError):
        rows_from({"rows": [{"account": "x"}]})


def test_an_empty_scan_exits_2_rather_than_reporting_success(tmp_path):
    """A silent zero-row success is how a broken producer reads as a quiet one."""
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--in-dir", str(tmp_path), "--store", str(tmp_path / "s.jsonl")],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_workflow_lands_and_asserts():
    names = " | ".join(str(s.get("name", "")) for s in _steps())
    assert "Land the corpus on main" in names
    assert _named("Land the corpus on main")["uses"] == "./.github/actions/commit-to-main"
    assert "Assert the rows actually landed" in names


def test_the_landing_assertion_is_run_scoped():
    """THE load-bearing one. Scoped on this run's stamp, not on stored history."""
    body = _named("Assert the rows actually landed")["run"]
    assert "--field run_generated_at" in body
    assert 'run-stamp: //p' in body, "the stamp must come from the extractor's output"
    for stored in ("--field account_id", "--field verdict", "--field strategy"):
        assert stored not in body, (
            f"{stored} is satisfied by rows already in the cumulative store — "
            "the assertion could never fail"
        )


def test_the_job_checks_out_with_a_pat():
    """commit-to-main's own contract: a GITHUB_TOKEN PR never triggers checks."""
    co = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout")][0]
    assert "BRANCH_PROTECTION_TOKEN" in str(co.get("with", {}).get("token", ""))


def test_round_trip_through_the_store(tmp_path):
    """Positive control for the refusals above: a real payload IS written."""
    d = tmp_path / "in"
    d.mkdir()
    (d / "compat_gld.json").write_text(json.dumps({
        "generated_at": "2026-08-30T10:00:00+00:00", "strategy": "gld_pullback_1h",
        "rows": [{"account": "alpaca_portfolio", "verdict": "ROUTE"}]}))
    store = tmp_path / "s.jsonl"
    r = subprocess.run([sys.executable, "scripts/research/gld_compat_extract.py",
                        "--in-dir", str(d), "--store", str(store)],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "run-stamp: 2026-08-30T10:00:00+00:00" in r.stdout
    assert json.loads(store.read_text().strip())["account_id"] == "alpaca_portfolio"


# ---------------------------------------------------------------------------
# Regression pins from run 33331071521 — the FIRST real execution of this path.
#
# Everything above passed while the workflow was broken in production, which is
# the point worth recording: they pinned that the assertion is run-SCOPED (the
# vacuous-predicate defect) and never that it can name the state it found. The
# run emitted 124 trades, scored every account, pushed a good commit, and then
# reported `absent — 0 rows among 0 total rows`, because `--pushed-ref` was
# missing and `pending_merge` was therefore unreachable. A job that did its work
# was told it had produced nothing.
# ---------------------------------------------------------------------------


def test_the_assertion_can_distinguish_pending_merge_from_absent():
    """Without --pushed-ref the two states collapse, and they mean opposite things.

    `absent` = the run produced nothing. `pending_merge` = the run produced rows
    and the merge is owed. Reporting the first when the second is true blames the
    job for work it actually did — and this workflow's OWN comment block called
    `pending_merge` "expected-and-actionable" while making it impossible to emit.
    """
    body = _named("Assert the rows actually landed")["run"]
    assert "--pushed-ref" in body, (
        "the assertion must name the ref the rows were PUSHED to; without it "
        "`pending_merge` is unreachable and every owed merge reports as `absent`"
    )


def test_the_pushed_ref_comes_from_the_landing_step_not_a_rederived_name():
    """The branch name is commit-to-main's to own; a second copy would drift."""
    step = _named("Assert the rows actually landed")
    env = step.get("env", {})
    assert "steps.land.outputs.branch" in str(env.get("LAND_BRANCH", "")), (
        "read the branch from the landing step's output, do not rebuild "
        "'<prefix>-<run_id>-<attempt>' here"
    )
    body = step["run"]
    assert "automation/gld-compat-verdicts-" not in body, (
        "the branch name is reconstructed in the assertion — that is the second "
        "copy this output exists to prevent"
    )


def test_a_failed_landing_does_not_also_destroy_the_evidence():
    """Run 33331071521 skipped its artifact upload because the assertion failed.

    The one run that most needed its compat_*.json kept none of it.
    """
    for name in ("Assert the rows actually landed", "Upload artifacts"):
        assert _named(name).get("if") == "always()", (
            f"{name!r} must run with if: always() — otherwise a red assertion "
            "takes the diagnostic evidence down with it"
        )


def test_the_landing_waits_for_the_owed_automerge():
    """commit-to-main enables AUTO-merge, so at T+0 the rows are never on main.

    Landing without waiting would leave `pending_merge` as the verdict on every
    healthy run — a workflow that is red whenever it succeeds trains everyone to
    ignore it, and the research queue would grade this job permanently failed.

    The wait lives in the shared action, not inline here: this workflow and
    research-queue-dispatch.yml both need it, and two copies of a wait keyed on
    the action's own PR url is how they drift.
    """
    land = [s for s in _steps()
            if str(s.get("uses", "")).endswith("commit-to-main")][0]
    assert str(land.get("with", {}).get("verify-merged", "")).lower() == "true", (
        "the landing step must wait for its own auto-merge"
    )


def test_the_shared_action_actually_waits_and_fails_rather_than_giving_up():
    """A timeout must FAIL, never exit 0 having given up.

    Passing on an unmerged branch is precisely the over-reporting that
    `--min-rows 1` over a cumulative store already permitted once.
    """
    body = (REPO / ".github/actions/commit-to-main/action.yml").read_text()
    assert "VERIFY_MERGED" in body and "DEADLINE" in body, "no bounded wait"
    assert "gh pr view" in body, "the wait must observe the PR, not sleep blind"
    tail = body.split("DEADLINE=$")[1]
    assert "did not merge within" in tail and "exit 1" in tail, (
        "the wait must exit NON-ZERO on timeout"
    )
    # `UNKNOWN` is 'we could not read the PR', not 'not merged'. Concluding
    # either way from an unreadable state is the collapse this repo files as a bug.
    assert "UNKNOWN" in tail, "an unreadable PR state must not be read as a verdict"


def test_verify_merged_defaults_off_so_existing_callers_are_unchanged():
    action = yaml.safe_load(
        (REPO / ".github/actions/commit-to-main/action.yml").read_text())
    assert action["inputs"]["verify-merged"]["default"] == "false"


def test_the_landing_step_is_addressable():
    """The env wiring above is dead unless the landing step carries an id."""
    land = [s for s in _steps()
            if str(s.get("uses", "")).endswith("commit-to-main")][0]
    assert land.get("id") == "land"


def test_commit_to_main_publishes_the_branch_it_pushed():
    """The fix belongs in the shared action, not in each caller."""
    action = yaml.safe_load(
        (REPO / ".github/actions/commit-to-main/action.yml").read_text())
    assert "branch" in action["outputs"], (
        "callers cannot report `pending_merge` without the branch, and "
        "re-deriving it per caller is a naming scheme this action owns"
    )
