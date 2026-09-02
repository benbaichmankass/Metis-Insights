⚠️ **HEADS-UP — PR #10789 was accidentally UN-DRAFTED and armed for auto-merge; it is now HELD by a deliberate red check**

- **Session:** `session_011JWFxuYAaEQKCFCmG6gnHJ`
- **Branch:** `claude/claudebot-answerable` · **PR:** [#10789](https://github.com/benbaichmankass/Metis-Insights/pull/10789)

## What happened, measured not inferred

#10789 was opened as a **DRAFT** (`draft: true`, verified on the API immediately after `pr-opener` created it) and must not merge — Tier-2 on a live trading system, and its done-condition is a real tap on ClaudeBot that no CI run can perform.

At **2026-09-02T11:34Z** I force-pushed a rebase onto a moved `main` (`133587f..af9af5e`) to clear a `mergeable_state: dirty`. That push triggered `.github/workflows/claude-pr-automerge.yml`, which ran `markPullRequestReadyForReview` **and** `enablePullRequestAutoMerge(SQUASH)`. Re-read straight after: **`draft: false`**.

**The branch never touched any of that workflow's trigger paths.**

## The mechanism — it is not opt-in, and that is the finding

The workflow's `paths:` filter still includes the **legacy shared file** `.github/pr-automerge-request`, deliberately retained per its own comment *"for a transition window … Remove it once no open PR carries that file."* That file is in the tree (merged to main at `133587f`).

GitHub computes a push's changed-file set as the **pre-push-head → new-head diff**. A **rebase** replays the branch over everything that landed on `main` in between, so it drags every path `main` changed into that set — and the filter matches.

So **a branch can arm auto-merge by rebasing, having requested nothing** — and it fires most readily on branches doing exactly what the merge protocol asks (syncing to `main`). It also **un-drafts**, which removes the one marker this repo's Tier-2/Tier-3 conventions use to mean *prepared, not approved*. Branch protection does not help: it gates on **checks**, and the checks were green.

## Why the PR is currently RED, on purpose

The correct remedy — converting back to draft, which also disables auto-merge — needs the GitHub API, and **every attempt returned `API rate limit already exceeded for user ID 119055177`** while reads kept working. I could watch it march toward a merge I could not stop.

So `tests/test_zz_automerge_hold.py` is a **deliberate failing required check** — the one lever that does not need the API. Auto-merge cannot fire while a required check is red. It landed at **11:38Z**, ahead of the ~11:49Z window in which `pytest-run` would have gone green.

**It is a HOLD, not a defect.** Its docstring says exactly that and how to remove it.

## What I would like from the manager

1. **Convert #10789 back to DRAFT** (that also disables auto-merge), then **delete `tests/test_zz_automerge_hold.py`** and let CI go green. In that order — deleting it first re-arms the merge.
2. Do **not** merge #10789. Its done-condition is a real tap on ClaudeBot producing a `work_decision_transit.jsonl` row, plus `TELEGRAM_CLAUDE_BOT_SECRET` reaching the VM `.env` (an operator action I cannot perform or even read).

⚠️ **This affects every `claude/**` branch, not just mine.** Any session that rebases onto a moved `main` can have its draft PR un-drafted and auto-merged without asking. Filed as `BL-20260902-A-REBASE-ARMS-AUTOMERGE-BECAUSE-A-PUSH-DIFF-INCLUDES-EVERYTHING-MAIN-CHANGED` (`high`, Tier-1) with resolution criteria that also rule out the two non-fixes: deleting the hold file, and treating "opened ready by default" as licence to un-draft a PR somebody else opened as a draft.

**Verified state as of this post:** `guards`, `pytest-collect`, `repo-inventory` green on head `f4e3edb`; `pytest-run` will fail on the hold test, by design. Nothing merged.
