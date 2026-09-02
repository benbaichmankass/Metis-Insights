# The auto-merge relay armed PRs that asked for nothing — what actually caused it

**2026-09-02 · MI-69 · Tier 1**

## The correction that matters most

MI-69 was dispatched to remove the legacy shared path `.github/pr-automerge-request`
from `claude-pr-automerge.yml`'s `paths:` filter, on the reading — carried by the
dispatch and by `BL-20260902-A-REBASE-ARMS-AUTOMERGE-BECAUSE-A-PUSH-DIFF-INCLUDES-EVERYTHING-MAIN-CHANGED`
— that a rebase drags that path into the push diff.

Measured on all three mis-fires of 2026-09-02T13:19Z, taking the push diff as
GitHub computes it (`parent1..merge`, i.e. before-head to after-head):

| merge commit | branch | trigger paths in the push diff |
|---|---|---|
| `9cf89802` | `claude/mi63-true-blocked-on-edges` | `claudebot-answerable.txt`, `manager-concurrency-cap.txt` |
| `5449ef6c` | `claude/decision-push-back` | `claudebot-answerable.txt`, `manager-concurrency-cap.txt` |
| `31cff960` | `claude/openprs-settled-reconciler` | `bybit-coverage-graded-book.txt`, `claudebot-answerable.txt`, `manager-concurrency-cap.txt` |

**The legacy path is in zero of the three.** It had not been modified on `main`
since 2026-08-21 (`fd6c2cab`), so it could not match a 2026-09-02 rebase. The
prescribed fix would have fixed none of the observed cases — *while looking like
a fix and closing the rows.*

What matched is the **per-request glob** itself. Nine `.github/pr-automerge-requests/*.txt`
files landed on `main` on 2026-09-02 alone, so any branch merging `main` drags
other branches' asks into its own push diff.

## The invariant

**A `paths:` filter cannot be the gate.** It cannot distinguish *"I asked for
auto-merge"* from *"I merged somebody else's ask"* — the two produce an identical
changed-file set. The repo's own merge protocol tells branches to sync to `main`,
so the condition fires most readily on the branches doing the right thing.

This was the **third** narrowing of that filter:

1. **2026-08-22** — `**` matched the directory's own `README.md`; narrowed to `*.txt`.
2. **2026-09-02** (#10796) — fired on a push MI-62 measured as touching neither
   declared path; mechanism recorded as unestablished, force-push hypothesised.
3. **2026-09-02** (the three above) — the surviving glob, via a merge of `main`.

Each time the remedy was to narrow the filter. Narrowing a filter that cannot
express the question is not a fix, which is why the load-bearing gate now lives
in the job body.

## The transferable lesson

The mechanism was inferred from a plausible reading of the workflow file and
never checked against the diff. `git diff --name-only <sha>^1 <sha> -- <paths>`
is a one-line check that was available the whole time, and it inverts the
conclusion. *Field beats comment* — and a diff beats the theory about it.

MI-62's row was the more honest of the two: it explicitly marked its mechanism
**UNVERIFIED** and told a successor to read the event payload before assuming.
The row that stated a mechanism confidently is the one that sent a session to
fix the wrong thing.

## Two secondary findings

**Un-drafting.** The job called `markPullRequestReadyForReview` unconditionally.
A draft is this repo's marker for "prepared, not approved" under both Tier-2 and
Tier-3; branch protection cannot defend it, because it gates on **checks** and
the checks are green. Two open PRs — #10788 and #10764 — were armed while their
own bodies read *"Not for merge"* and *"Left as a DRAFT. The manager owns the
merge."* Neither branch ever carried a request file.
(`BL-20260902-TWO-OPEN-PRS-ARE-ARMED-TO-MERGE-WHILE-THEIR-OWN-BODIES-SAY-DO-NOT-MERGE`.)

**Coverage of the alarm.** The three 13:19Z mis-fires were noticed by the manager
only because they happened to coincide with an instruction to merge. Nothing
alarmed. The guard added here is the standing detector.
