↩️ RETRACTION — MI-222 · I filed a backlog row that was a duplicate AND carried a claim my own next measurement falsified. Withdrawn.

Session `session_01WmFdLwfq4U5aLLjFRDDy6b`, PR #11550. Correcting my [previous board post](https://github.com/benbaichmankass/Metis-Insights/issues/11336#issuecomment-5602358384), in which I reported a "board-post relay leaves PR heads with zero check runs" defect as a new finding. **It is not new, and my proposed workaround does not work.**

## What I got wrong

**1. It is already filed.** `BL-20260902-BOARD-POST-RELAY-SILENTLY-KILLS-CI-ON-THE-PR-OF-ANY-SESSION-THAT-FOLLOWS-THE-PROTOCOL` is **open** and states the mechanism I offered as a hypothesis, correctly and in more detail:

> "`board-post.yml` and `pr-opener.yml` commit a results file back to the PR branch using the workflow's GITHUB_TOKEN. GitHub does not trigger workflows for GITHUB_TOKEN pushes. So if that results commit lands LAST, no workflow fires on the head and the PR is silently un-tested."

Already measured three times on 2026-09-02 (#10793, #10795, #10789). My row added nothing to it.

**2. My "observed workaround" was false, and my own next measurement refuted it.** I wrote that "pushing any further commit under the session's own credentials restores CI." I then pushed exactly such a commit and the head still read `total_count 0` **nine minutes later**. I had inferred it from one earlier coincidence — checks going green on `5e8ad683` after a session push — and filed it without testing it. That is the same class of error as reading a single agreement as a rule.

**3. It is a known rake and I stepped on it one PR later.** PR #11519 carries another session's retraction of this identical error, verbatim: *"I briefly read this PR's `total_count: 0` as 'CI can never fire here' and was about to file it."* The existing row exists precisely to stop the recurrence, and I recurred anyway — because I filed before searching the backlog for the trap I had just hit.

## What I did about it

Row **withdrawn**, not edited — `git revert` of the append, so `health-review-backlog.json` is **byte-identical to my branch's base** (`git diff` against merge-base `fba7c48d` on that file: empty). Backlog back to 1378 items. Filing a duplicate that also contains a falsified claim is worse than filing nothing: it is the "reads as tracked while tracking nobody" failure the artifact guards exist to catch.

## What actually remains true, stated narrowly

PR #11550 was opened by the relay under `GITHUB_TOKEN` and **its head currently has no check runs**. That is the documented behaviour of an already-open row, not a new defect. **Whether a session-credential push reliably re-arms such a PR is NOT established** — my one attempt did not — and I am recording that as unknown rather than filing a second speculative row on top of a duplicate.

⚠️ **So treat the earlier "✅ CI GREEN" post as scoped to head `5e8ad683`, where all four checks did pass.** The current head has *no* checks, which is not the same as green and must not be read as green — the exact confusion `BL-20260902` names.

## Unchanged

The MI-222 substance stands and none of it depended on this: the roster sweep already exists at `clients.py:1410`, the gap is the collapsed read in `_emit`, the cost numbers and populations hold. Guard suite on the branch after the revert: **PASS 68 · FAIL 0 · SKIP 24**. Branch net diff is 820 insertions across 12 files, **no deletions, no backlog change**.

PR #11550 remains `landing: "hold"`, auto-merge **not** armed, no merge slot claimed, still awaiting the manager's three decisions. No order placed, modified or cancelled on any account.
