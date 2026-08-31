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
    # ⚠️ `partition`, NOT `split(...)[1]`. The intent is "everything after the
    # deadline is established", and while there was exactly ONE `DEADLINE=$`
    # assignment those were the same string. The one-shot stale-branch refresh
    # (2026-08-31) added a SECOND — it resets the budget after re-triggering the
    # checks — so `[1]` silently narrowed to the span BETWEEN the two and stopped
    # before the timeout branch. The test then failed on a change that did not
    # touch the failure path at all.
    #
    # This restores the original semantics rather than relaxing them: with one
    # occurrence `partition(...)[2] == split(...)[1]` exactly, and with several it
    # keeps meaning what the docstring says. Do not "fix" a future failure here by
    # slicing narrower — the assertion below is the point, and a window that
    # shrinks whenever the script grows is a test that decays into a tripwire for
    # unrelated edits.
    tail = body.partition("DEADLINE=$")[2]
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


# ---------------------------------------------------------------------------
# The assertion must not fail a run that SUCCEEDED (2026-08-31).
#
# `git fetch` aborts ATOMICALLY on an unknown refspec. `commit-to-main` merges
# with --delete-branch, so on the SUCCESS path the landing branch is gone by the
# time the assertion runs — and `git fetch origin main "$LAND_BRANCH" || true`
# therefore updated NEITHER ref, leaving the assertion to read a checkout-time
# origin/main that predates the merge and report `absent`.
#
# Measured on run 33340468235: "Land the corpus on main" SUCCEEDED in 95s (so
# verify-merged saw MERGED, and #10535 is in main's history), then the assertion
# failed instantly. Verified the mechanism directly rather than inferring it —
# with a real deleted branch, `git fetch origin main <gone>` prints
# `fatal: couldn't find remote ref` and does NOT update origin/main.
#
# An assertion built to stop false "landed" claims was manufacturing false
# "absent" ones, which is the worse direction: it fails correct runs and trains
# the reader to ignore it.
# ---------------------------------------------------------------------------


def _assert_step_body():
    return _named("Assert the rows actually landed")["run"]


def test_the_default_branch_is_fetched_on_its_own():
    """One fetch naming both refs updates NEITHER when the second is gone."""
    body = _assert_step_body()
    assert 'git fetch origin "${DEFAULT_BRANCH:-main}"\n' in body, (
        "the default branch must be fetched by itself — a combined fetch aborts "
        "atomically when the landing branch has been deleted on merge"
    )
    assert 'git fetch origin "${DEFAULT_BRANCH:-main}" "${LAND_BRANCH}"' not in body, (
        "combined fetch reintroduced: this is the false-negative from run 33340468235"
    )


def test_the_main_fetch_is_not_swallowed():
    """`|| true` on the main fetch is what hid the fatal for a whole run."""
    for line in _assert_step_body().splitlines():
        if 'git fetch origin "${DEFAULT_BRANCH:-main}"' in line:
            assert "|| true" not in line, (
                "a swallowed main fetch means the assertion reads a stale ref and "
                "cannot tell 'we did not look' from 'the rows are absent'"
            )
            return
    raise AssertionError("no default-branch fetch found at all")


def test_pushed_ref_is_only_claimed_when_the_branch_resolves():
    """A --pushed-ref naming a deleted branch makes pending_merge unreadable again."""
    body = _assert_step_body()
    assert "PUSHED=()" in body and '"${PUSHED[@]}"' in body, (
        "--pushed-ref must be conditional on the branch actually being fetchable"
    )
    assert '--pushed-ref "origin/${LAND_BRANCH}" \\' not in body, (
        "unconditional --pushed-ref reintroduced"
    )


def test_a_deleted_landing_branch_is_reported_not_treated_as_an_error():
    """Branch gone IS the merged case — it must read as success, and say so."""
    body = _assert_step_body()
    assert "gone" in body.lower(), (
        "the deleted-branch path must state that the rows are expected on main, "
        "so a reader is not left inferring it from a missing --pushed-ref"
    )


def test_the_e35_sibling_got_the_same_fix():
    """Latent there, but 'each fix covering only the tree just proven' is the
    recurrence pattern this repo keeps paying for."""
    wf = yaml.safe_load(
        (REPO / ".github/workflows/e35-bracket-sweep.yml").read_text())
    bodies = [s.get("run", "") for j in wf["jobs"].values()
              for s in j.get("steps", [])]
    assert any('git fetch origin "${DEFAULT_BRANCH:-main}"\n' in b for b in bodies), (
        "e35 still uses the combined fetch"
    )
    assert not any('git fetch origin "${DEFAULT_BRANCH:-main}" "${TARGET}"' in b
                   for b in bodies), "e35's combined fetch reintroduced"


# ---------------------------------------------------------------------------
# e35's landing chain — the same eight hops, closed 2026-08-31.
#
# e35 pushed a side branch and printed an honest hand-off saying a human had to
# open the PR. Accurate, and still a broken pipeline: the research queue drives
# this workflow on a monthly cadence, so "a human finishes it" means the rows
# pile up unmerged and no later session reads them. Measured on run
# 33361845836 — 14/14 legs green, 2,786 rows written, 0 rows on main.
# ---------------------------------------------------------------------------
def _e35() -> dict:
    return yaml.safe_load(
        (REPO / ".github/workflows/e35-bracket-sweep.yml").read_text())


def _e35_corpus_job() -> dict:
    return _e35()["jobs"]["corpus"]


def test_e35_lands_through_commit_to_main_not_a_bare_side_branch_push():
    """A GITHUB_TOKEN push starts no workflows, so the job cannot open its own
    PR. The PAT-authenticated shared action is the only thing that can."""
    steps = _e35_corpus_job()["steps"]
    land = [s for s in steps
            if s.get("uses") == "./.github/actions/commit-to-main"]
    assert len(land) == 1, (
        "e35's corpus job must land through the shared commit-to-main action; "
        "a bare `git push` to a per-run branch leaves the merge owed to a human"
    )
    assert land[0].get("id") == "land", (
        "the landing step needs id 'land' — the assertion reads its outputs"
    )
    bodies = "\n".join(s.get("run", "") for s in steps)
    assert 'git push origin "HEAD:refs/heads/${TARGET}"' not in bodies, (
        "the side-branch push is back; that is the state that owed the merge"
    )


def test_e35_landing_waits_for_the_merge():
    """Without this the step exits 0 when the PR OPENS, so the assertion below
    reads pending_merge on every healthy run and gets switched off."""
    land = [s for s in _e35_corpus_job()["steps"]
            if s.get("uses") == "./.github/actions/commit-to-main"][0]
    assert str(land["with"].get("verify-merged")).lower() == "true", (
        "e35's landing must verify the merge, not just the push"
    )


def test_e35_corpus_job_budget_outlasts_the_merge_wait():
    """A budget shorter than the wait cancels a healthy landing mid-wait and
    reports a timeout for a PR that was merging correctly."""
    action = yaml.safe_load(
        (REPO / ".github/actions/commit-to-main/action.yml").read_text())
    wait = int(action["inputs"]["verify-timeout-minutes"]["default"])
    budget = int(_e35_corpus_job()["timeout-minutes"])
    assert budget > wait, (
        f"corpus job budget {budget}m must exceed commit-to-main's {wait}m wait"
    )


def test_e35_corpus_job_may_open_a_pull_request():
    """contents:write alone cannot open the landing PR."""
    perms = _e35_corpus_job()["permissions"]
    assert perms.get("pull-requests") == "write", (
        "commit-to-main opens a PR; the job needs pull-requests: write"
    )


def test_e35_assertion_guards_on_both_committed_and_branch():
    """`committed=false` is the legitimate no-op (corpus byte-identical);
    an empty branch beside committed=true is a broken landing. Collapsing the
    two would let a failed push read as a quiet success."""
    body = [s for s in _e35_corpus_job()["steps"]
            if s.get("name") == "assert the corpus rows landed"][0]["run"]
    assert "LAND_COMMITTED" in body and "LAND_BRANCH" in body, (
        "the assertion must read the landing step's outputs"
    )
    assert "CORPUS_TARGET" not in body, (
        "CORPUS_TARGET is gone with the push step; reading it would make the "
        "guard always fire"
    )
