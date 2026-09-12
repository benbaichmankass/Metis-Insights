"""The `claude-pr-automerge` request gate, exercised as the SHIPPED ARTIFACT.

⚠️ THIS RUNS THE WORKFLOW'S OWN SCRIPT, extracted from
`.github/workflows/claude-pr-automerge.yml` and evaluated under node with a
mocked GitHub API. It deliberately does NOT re-implement the gate in Python: a
re-implementation is a second copy of the policy, free to drift from the one
that actually decides, and this defect has now recurred twice precisely because
the thing that decided was not the thing that was reasoned about.

WHY THE GATE EXISTS (measured, not reasoned). On 2026-09-02T13:19Z three PRs —
#10788 and the branches behind #10797 / #10783 — were un-drafted and armed for
auto-merge having requested nothing. Each had merged `origin/main` to resolve a
register conflict. GitHub computes a push's changed-file set as the
before-head→after-head diff, so the merge dragged in the nine
`.github/pr-automerge-requests/*.txt` files that landed on `main` that day and
the `paths:` filter matched on another branch's ask:

    $ git diff --name-only 9cf89802^1 9cf89802 -- \
        '.github/pr-automerge-requests/*.txt' '.github/pr-automerge-request'
    .github/pr-automerge-requests/claudebot-answerable.txt
    .github/pr-automerge-requests/manager-concurrency-cap.txt

⚠️ NOTE WHAT IS *NOT* IN THAT OUTPUT. The legacy shared path
`.github/pr-automerge-request` — the path both the dispatch and
`BL-20260902-A-REBASE-ARMS-AUTOMERGE-...` blamed — appears in ZERO of the three
push diffs; it had not been modified on `main` since 2026-08-21 and so could not
match a 2026-09-02 rebase. Removing it was correct housekeeping and would have
fixed NONE of the three mis-fires. That is why the load-bearing gate is here, in
the job body, and not in the `paths:` filter.

THE TWO CONTROLS ARE BOTH REQUIRED AND NEITHER IS SUFFICIENT. A test showing the
gate refuses a merge-of-main proves nothing on its own — a gate that refuses
everything passes it. The positive control (a genuine request still arms) is
what separates "fixed" from "broken".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/claude-pr-automerge.yml"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required to run the workflow's own script")


def _script() -> str:
    """The github-script body, lifted verbatim from the shipped workflow."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["open-and-automerge"]["steps"]
    bodies = [s["with"]["script"] for s in steps if "script" in s.get("with", {})]
    assert len(bodies) == 1, f"expected exactly one github-script step, got {len(bodies)}"
    return bodies[0]


# What `checks.listForRef` returns for a HEALTHY head: the repo's own required
# check, plus this workflow's self job (always present on a PR it opened). This
# is the default because it is what a genuine request looks like once the
# `pull_request` event has fired, and the positive controls are about the
# genuine case. Pass `check_runs=` explicitly to model anything else.
HEALTHY_CHECKS = [{"name": "guards"}, {"name": "open-and-automerge"}]


def run_gate(*, branch: str, head_sha: str, blobs: dict, existing_pr=None,
             get_content_raises: bool = False, check_runs=None,
             pat: str = "", pat_create_fails: bool = False,
             checks_raise: bool = False, cwd=None) -> dict:
    """Evaluate the real script against a mocked API; return every call it made.

    `blobs` maps (ref, path) -> blob sha, modelling the two `getContent` reads.
    A ref/path absent from it is a 404, i.e. genuinely not there.

    `check_runs` is what `checks.listForRef` hands the 2026-09-12 arming gate.
    It defaults to `HEALTHY_CHECKS`; pass `[]` or the self job alone to model
    the zero-check head that gate exists to refuse.

    `pat` / `pat_create_fails` model `BRANCH_PROTECTION_TOKEN` in its three
    states: absent, present-and-working, and present-but-REFUSED (the PR-create
    scope nobody has been able to verify). `@actions/github` is not installed
    here — it ships inside `actions/github-script` — so `require` is shimmed to
    hand back a stub client rather than being left to throw, which would make
    every PAT path untestable instead of merely unexercised.

    `checks_raise` models the check read FAILING, which is a third thing from
    an empty list and from a malformed payload. `cwd` runs the step from a
    directory where `scripts/ci/automerge_arming.py` is not there, which is the
    only way to reach the branch where the gate cannot be RUN at all.
    """
    harness = """
    const CALLS = [];
    const BLOBS = %(blobs)s;
    const EXISTING = %(existing)s;
    const RAISES = %(raises)s;
    const CHECKS_RAISE = %(checks_raise)s;
    const CHECK_RUNS = %(check_runs)s;
    const PAT_CREATE_FAILS = %(pat_fails)s;

    // `@actions/github` is bundled into actions/github-script and is NOT
    // installed here, so a bare require would throw and no PAT path could ever
    // be exercised. The shim is passed in as the script's own `require`.
    const realRequire = require;
    const patClient = { rest: { pulls: { create: async () => {
      CALLS.push({ call: 'pat.pulls.create' });
      if (PAT_CREATE_FAILS) {
        const e = new Error('Resource not accessible by personal access token');
        e.status = 403; throw e;
      }
      return { data: { number: 777, node_id: 'N', draft: false,
                       head: { sha: '%(sha)s' } } };
    } } } };
    const requireShim = (m) => m === '@actions/github'
      ? { getOctokit: () => { CALLS.push({ call: 'getOctokit' }); return patClient; } }
      : realRequire(m);

    const context = {
      ref: 'refs/heads/%(branch)s',
      sha: '%(sha)s',
      repo: { owner: 'o', repo: 'r' },
      payload: { head_commit: { message: 'feat: a thing\\nbody' } },
    };
    // MIRROR THE REAL `@actions/core` LOGGING SURFACE, not merely the two calls
    // the script happened to make when this harness was written. Stubbing only
    // `notice`/`setFailed` meant the first `core.warning` added to the workflow
    // threw `core.warning is not a function` INSIDE the script, which the
    // harness recorded as a generic `threw` — indistinguishable from the
    // workflow genuinely failing, and it read as a production defect.
    const core = { notice:    (m) => CALLS.push({ call: 'notice', m }),
                   warning:   (m) => CALLS.push({ call: 'warning', m }),
                   error:     (m) => CALLS.push({ call: 'error', m }),
                   info:      (m) => CALLS.push({ call: 'info', m }),
                   setFailed: (m) => CALLS.push({ call: 'setFailed', m }) };
    const github = {
      graphql: async (q, v) => {
        CALLS.push({ call: q.includes('markPullRequestReadyForReview')
          ? 'markReady' : 'enableAutoMerge' });
        return {};
      },
      rest: {
        repos: { getContent: async ({ path, ref }) => {
          CALLS.push({ call: 'getContent', path, ref });
          if (RAISES) { const e = new Error('boom'); e.status = 500; throw e; }
          const sha = BLOBS[ref + '|' + path];
          if (!sha) { const e = new Error('Not Found'); e.status = 404; throw e; }
          return { data: { sha } };
        } },
        pulls: {
          list: async () => { CALLS.push({ call: 'pulls.list' });
                              return { data: EXISTING ? [EXISTING] : [] }; },
          create: async () => { CALLS.push({ call: 'pulls.create' });
                                return { data: { number: 999, node_id: 'N', draft: false,
                                                 head: { sha: '%(sha)s' } } }; },
          merge: async () => { CALLS.push({ call: 'pulls.merge' }); return {}; },
        },
        checks: { listForRef: async () => { CALLS.push({ call: 'checks' });
                                            if (CHECKS_RAISE) { const e = new Error('checks read failed'); e.status = 500; throw e; }
                                            return { data: { check_runs: CHECK_RUNS } }; } },
      },
    };

    (async () => {
      try { await (async (require) => {
%(script)s
      })(requireShim); } catch (e) { CALLS.push({ call: 'threw', m: String(e.message) }); }
      console.log('___RESULT___' + JSON.stringify(CALLS));
    })();
    """ % {
        "blobs": json.dumps(blobs),
        "existing": json.dumps(existing_pr),
        "raises": "true" if get_content_raises else "false",
        "checks_raise": "true" if checks_raise else "false",
        "check_runs": json.dumps(HEALTHY_CHECKS if check_runs is None else check_runs),
        "pat_fails": "true" if pat_create_fails else "false",
        "branch": branch,
        "sha": head_sha,
        "script": textwrap.indent(_script(), " " * 8),
    }
    # The script writes the gate's input file and shells out to
    # `scripts/ci/automerge_arming.py`, so it needs both `ARMING_INPUT` and a cwd
    # at the repo root — exactly as the workflow gives it.
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ,
               "ARMING_INPUT": str(Path(tmp) / "automerge-arming.json"),
               "PR_OPEN_PAT": pat}
        out = subprocess.run(["node", "-e", textwrap.dedent(harness)],
                             capture_output=True, text=True, timeout=60,
                             cwd=str(cwd or REPO), env=env)
    assert out.returncode == 0, out.stderr
    marker = "___RESULT___"
    assert marker in out.stdout, out.stdout
    return {"calls": json.loads(out.stdout.split(marker, 1)[1].strip())}


def _kinds(res) -> list:
    return [c["call"] for c in res["calls"]]


ACTING = {"markReady", "enableAutoMerge", "pulls.create", "pulls.merge"}
REQ = ".github/pr-automerge-requests"


# --------------------------------------------------------------------------
# NEGATIVE CONTROL — the observed defect. A branch that merged `main`.
# --------------------------------------------------------------------------

def test_negative_control_merge_of_main_does_not_arm():
    """The exact 2026-09-02T13:19Z shape: this branch's own request file does
    not exist, and the paths filter matched only because merging `main` dragged
    in OTHER branches' request files."""
    res = run_gate(
        branch="claude/mi63-true-blocked-on-edges", head_sha="9cf89802" + "0" * 32,
        blobs={
            # what the merge dragged in — present on BOTH refs, and not ours
            f"main|{REQ}/claudebot-answerable.txt": "aaa",
            f"9cf98020000000000000000000000000|{REQ}/claudebot-answerable.txt": "aaa",
        },
    )
    kinds = _kinds(res)
    assert not (ACTING & set(kinds)), f"gate acted on a branch that asked nothing: {kinds}"
    assert "pulls.list" not in kinds, "it should not even look for a PR"
    assert any("NO REQUEST" in c.get("m", "") for c in res["calls"])


def test_negative_control_inherited_identical_file_does_not_arm():
    """The subtler half: a branch whose slug-named file exists only because it
    came from `main` unchanged. Presence alone is not an ask."""
    res = run_gate(
        branch="claude/some-branch", head_sha="b" * 40,
        blobs={f"main|{REQ}/some-branch.txt": "same",
               f"{'b'*40}|{REQ}/some-branch.txt": "same"},
    )
    assert not (ACTING & set(_kinds(res)))
    assert any("byte-identical to main" in c.get("m", "") for c in res["calls"])


# --------------------------------------------------------------------------
# POSITIVE CONTROL — without this, the negatives above prove only that the
# trigger is broken.
# --------------------------------------------------------------------------

def test_positive_control_a_genuine_request_still_arms():
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},   # absent on main == added here
    )
    kinds = _kinds(res)
    assert "pulls.create" in kinds, f"a real request must still open the PR: {kinds}"
    assert "enableAutoMerge" in kinds, f"a real request must still arm: {kinds}"


def test_positive_control_modified_request_on_an_open_nondraft_pr_arms():
    res = run_gate(
        branch="claude/some-branch", head_sha="d" * 40,
        blobs={f"main|{REQ}/some-branch.txt": "old",
               f"{'d'*40}|{REQ}/some-branch.txt": "new"},
        existing_pr={"number": 42, "node_id": "N", "draft": False,
                     "head": {"sha": "d" * 40}},
    )
    assert "enableAutoMerge" in _kinds(res)


# --------------------------------------------------------------------------
# THE ARMING GATE (2026-09-12) — the counterpart to the two positives above.
#
# ⚠️ THE TWO POSITIVE CONTROLS DID NOT CHANGE THEIR ASSERTIONS; their FIXTURE
# did. They used to run against a mock whose `checks.listForRef` always returned
# `[]` — a head with nothing attached — because when they were written arming
# did not depend on checks at all. Under the gate that head is exactly the one
# that must be REFUSED, so the old fixture asserted "a genuine request arms" of
# a PR that is no longer genuine. Changing the assertion to expect a refusal
# would have deleted the only thing standing between this change and the relay
# going silent; changing the FIXTURE to a healthy head keeps the control doing
# its job, and the test below is what stops that default from becoming a way of
# passing the gate without ever testing it.
# --------------------------------------------------------------------------

def test_a_head_with_no_checks_attached_is_not_armed():
    """The gate's whole point, exercised through the SHIPPED script.

    ⚠️ A zero-check PR does not report zero check runs — it reports ONE, this
    workflow's own job, green because it opened the PR. So the fixture is the
    self job ALONE, not `[]`: `[]` would also be refused by a naive
    `length > 0` gate and would prove nothing about the exclusion-by-name that
    does the real work.
    """
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        check_runs=[{"name": "open-and-automerge"}],
    )
    kinds = _kinds(res)
    assert "pulls.create" in kinds, f"the PR must still be OPENED: {kinds}"
    assert "enableAutoMerge" not in kinds, f"armed a head nothing is measuring: {kinds}"
    assert any("NOT ARMING" in c.get("m", "") for c in res["calls"])


def test_an_unreadable_check_list_is_not_read_as_permission_to_arm():
    """`we could not look` is not `nothing is running`, and must not arm."""
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        check_runs="not-a-list",
    )
    assert "enableAutoMerge" not in _kinds(res)


def test_a_check_read_that_THREW_is_not_filled_in_with_a_plausible_answer():
    """The malformed-payload control above does NOT cover this, and a mutation
    run is how that was established rather than argued.

    Planting `checkRuns = [{name:'guards'}]; checksReadOk = true` on the catch
    branch — a read that failed, answered with a healthy-looking fixture —
    ESCAPED the whole suite. The refusal is not enough to pin it either, since
    an empty read refuses too; what separates them is the STATE the gate
    reports, so that is what this asserts.
    """
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        checks_raise=True,
    )
    kinds = _kinds(res)
    assert "pulls.create" in kinds, f"the PR must still be OPENED: {kinds}"
    assert "enableAutoMerge" not in kinds, kinds
    assert any("arming gate: unreadable" in c.get("m", "") for c in res["calls"]), \
        f"a FAILED read must not be reported as an empty one: {res['calls']}"


def test_a_gate_that_cannot_RUN_does_not_degrade_to_arming(tmp_path):
    """The one thing a broken gate must never do is fall back to the behaviour
    it was added to prevent — the workflow says exactly that at the line, and
    until now nothing held it. Planting `catch (e) { verdict = {arm: true} }`
    ESCAPED the suite.

    Run the step from a directory where `scripts/ci/automerge_arming.py` is not
    there: the spawn fails, stdout does not parse, and the step must FAIL rather
    than arm. `setFailed` is the assertion, not just the absence of arming —
    absence alone is also what a silent swallow looks like.
    """
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        cwd=tmp_path,
    )
    kinds = _kinds(res)
    assert "enableAutoMerge" not in kinds, f"a broken gate armed anyway: {kinds}"
    assert "setFailed" in kinds, f"a broken gate must fail the step loudly: {kinds}"


# --------------------------------------------------------------------------
# WHO OPENS THE PR — the three BRANCH_PROTECTION_TOKEN states.
# --------------------------------------------------------------------------

def test_a_pat_is_used_to_open_the_pr_when_one_is_present():
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        pat="ghp_fake",
    )
    kinds = _kinds(res)
    assert "getOctokit" in kinds, f"a present PAT must be used: {kinds}"
    assert "pat.pulls.create" in kinds, f"the PR must be opened UNDER the PAT: {kinds}"
    assert "pulls.create" not in kinds, "it must not also open one under GITHUB_TOKEN"


def test_a_missing_pat_still_opens_the_pr_and_says_so_loudly():
    """The fallback must be loud AND working. A missing secret must never take
    the relay down — it degrades to `PR opened, arming refused`."""
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        pat="", check_runs=[{"name": "open-and-automerge"}],
    )
    kinds = _kinds(res)
    assert "getOctokit" not in kinds
    assert "pulls.create" in kinds, f"a missing PAT must still open the PR: {kinds}"
    assert any(c["call"] == "warning" and "BRANCH_PROTECTION_TOKEN is NOT set" in c["m"]
               for c in res["calls"]), "the fallback must announce itself"
    assert "enableAutoMerge" not in kinds, "and must NOT arm, since no check attached"


def test_an_UNUSABLE_pat_falls_back_and_still_opens_the_pr():
    """⚠️ THE OPEN QUESTION ON THIS CHANGE, MADE NON-LOAD-BEARING.

    Whether `BRANCH_PROTECTION_TOKEN` carries PR-create scope is NOT
    established and cannot be read from a session. Without a fallback the
    answer decides whether the relay works at all: a token lacking the scope
    403s, the script throws, and NO PR is opened — strictly worse than the
    GITHUB_TOKEN behaviour being replaced. With it, the unknown degrades to the
    same state a missing secret does.
    """
    res = run_gate(
        branch="claude/some-branch", head_sha="c" * 40,
        blobs={f"{'c'*40}|{REQ}/some-branch.txt": "new"},
        pat="ghp_fake", pat_create_fails=True,
        check_runs=[{"name": "open-and-automerge"}],
    )
    kinds = _kinds(res)
    assert "pat.pulls.create" in kinds, "it must have TRIED the PAT"
    assert "pulls.create" in kinds, f"an unusable PAT must still open the PR: {kinds}"
    assert "threw" not in kinds, "a 403 on the PAT must not take the relay down"
    assert any(c["call"] == "warning" and "FAILED" in c["m"] for c in res["calls"])
    assert "enableAutoMerge" not in kinds, "and must NOT arm — the checks did not attach"


# --------------------------------------------------------------------------
# The draft refusal — the second gate, independent of the first.
# --------------------------------------------------------------------------

def test_a_draft_pr_is_never_undrafted_even_on_a_genuine_request():
    """#10788 and #10764 were both armed while their own bodies said not to
    merge. A draft is this repo's "prepared, not approved" marker; a request
    file must not delete it."""
    res = run_gate(
        branch="claude/some-branch", head_sha="e" * 40,
        blobs={f"{'e'*40}|{REQ}/some-branch.txt": "new"},
        existing_pr={"number": 10788, "node_id": "N", "draft": True,
                     "head": {"sha": "e" * 40}},
    )
    kinds = _kinds(res)
    assert "markReady" not in kinds, f"un-drafted a PR it did not open: {kinds}"
    assert "enableAutoMerge" not in kinds
    assert "pulls.merge" not in kinds
    assert any("REFUSING" in c.get("m", "") for c in res["calls"])


def test_the_workflow_never_calls_markready_at_all():
    """Stronger than the behavioural test above and independent of the mock: the
    un-draft mutation must not appear in the shipped script's CODE.

    ⚠️ Comments are stripped first, on purpose. The script legitimately NAMES
    `markPullRequestReadyForReview` in the comment explaining why it no longer
    calls it, and a naive substring check over the whole body fails on that
    prose — which would train the next editor to delete the explanation in order
    to get CI green. The assertion is about what executes.
    """
    code = "\n".join(ln for ln in _script().splitlines()
                     if not ln.lstrip().startswith("//"))
    assert "markPullRequestReadyForReview" not in code, (
        "the un-draft mutation is reachable in the shipped script")


# --------------------------------------------------------------------------
# "We could not look" is not "it is not there".
# --------------------------------------------------------------------------

def test_a_failed_read_is_not_treated_as_absence():
    res = run_gate(branch="claude/some-branch", head_sha="f" * 40,
                   blobs={}, get_content_raises=True)
    kinds = _kinds(res)
    assert "threw" in kinds, "a 500 must propagate and fail the run, not read as 'no request'"
    assert not (ACTING & set(kinds))
