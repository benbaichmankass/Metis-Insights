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
import shutil
import subprocess
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


def run_gate(*, branch: str, head_sha: str, blobs: dict, existing_pr=None,
             get_content_raises: bool = False) -> dict:
    """Evaluate the real script against a mocked API; return every call it made.

    `blobs` maps (ref, path) -> blob sha, modelling the two `getContent` reads.
    A ref/path absent from it is a 404, i.e. genuinely not there.
    """
    harness = """
    const CALLS = [];
    const BLOBS = %(blobs)s;
    const EXISTING = %(existing)s;
    const RAISES = %(raises)s;

    const context = {
      ref: 'refs/heads/%(branch)s',
      sha: '%(sha)s',
      repo: { owner: 'o', repo: 'r' },
      payload: { head_commit: { message: 'feat: a thing\\nbody' } },
    };
    const core = { notice: (m) => CALLS.push({ call: 'notice', m }),
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
                                            return { data: { check_runs: [] } }; } },
      },
    };

    (async () => {
      try { await (async () => {
%(script)s
      })(); } catch (e) { CALLS.push({ call: 'threw', m: String(e.message) }); }
      console.log('___RESULT___' + JSON.stringify(CALLS));
    })();
    """ % {
        "blobs": json.dumps(blobs),
        "existing": json.dumps(existing_pr),
        "raises": "true" if get_content_raises else "false",
        "branch": branch,
        "sha": head_sha,
        "script": textwrap.indent(_script(), " " * 8),
    }
    out = subprocess.run(["node", "-e", textwrap.dedent(harness)],
                         capture_output=True, text=True, timeout=60)
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
