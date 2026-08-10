# Merge queue — UNAVAILABLE on this repo (user-owned); use the manual claim protocol

> **Status (2026-08-02): the GitHub native merge queue CANNOT be enabled on this
> repository, and BL-20260726-MERGE-QUEUE-ENABLEMENT's premise is therefore
> invalid.** `benbaichmankass/Metis-Insights` is a **user-owned** repo (owner is
> a personal account, not an organization), and GitHub's merge queue is an
> **organization-only** feature — there is **no "Require merge queue" checkbox**
> under Settings → Branches → branch-protection for a user-owned repo (operator
> confirmed 2026-08-02; consistent with GitHub's documented availability: merge
> queue ships only for repositories owned by an organization on Team/Enterprise).
> So the enablement steps that used to be here **do not exist to perform**, and
> the dormant `merge_group:` triggers on ~30 workflows **will never fire**. The
> serializer remains the **manual honour-system claim protocol** below.

## Why the merge queue was pursued (kept as record)

The honour-system merge-claim protocol (post a `🔒 MERGE SLOT CLAIM` on #6927 →
sync to `main` → merge → `🔓 RELEASE`) keeps getting skipped under load —
sessions merge stale branches and hit the `behind`/`dirty` retest churn
(BL-20260720-MERGE-PROTOCOL-LAPSE, again 2026-07-26). GitHub's native merge
queue would have removed the human-discipline dependency: it auto-syncs each PR
to the queue head, runs the required checks on that merged result, and merges in
order. #7666 (BL-20260726-MERGE-QUEUE-ENABLEMENT) shipped the **prerequisite** —
a dormant `merge_group:` trigger on all required-check workflows — on the
assumption the operator could then flip on "Require merge queue." **That
assumption was wrong for a user-owned repo** (see the status note above), so the
prerequisite is inert and the queue is not an available path here.

The dormant `merge_group:` triggers are **harmless to leave in place** (they fire
only on a `merge_group` event, which never occurs without the queue) and are
**not worth a sweep to remove** — a churny ~30-workflow PR for zero behavioural
change. Leave them; this note is the durable record of why they're dormant.

## What to do instead — the manual claim protocol (the sole serializer)

This is the binding serializer. Full contract: the `session-coordination`
skill (`.claude/skills/session-coordination/SKILL.md`) §2 and
`docs/claude/coordination-board.md`.

1. **Read the board (#6927)** — `issue_read method=get_comments`. See what other
   live sessions are touching and whether the merge slot is claimed.
2. **`🔒 MERGE SLOT — CLAIM`** on #6927 naming your PR before you merge.
3. **Sync only if you need `main`'s content.** ~~Because "Require branches to be
   up to date" is ON~~ — **that setting was unticked 2026-08-10** (`strict: false`),
   so a branch that is `behind` **merges fine** and a defensive re-sync only buys
   another full CI cycle. Sync when your change actually depends on something new
   on `main`, or when git reports a real textual conflict (a `405 merge conflicts`
   is that, and is still yours to resolve).
4. **Merge on green** — `merge_pull_request` (squash) once required checks pass on
   the synced head.
5. **`🔓 MERGE SLOT — RELEASE`** + `✅ DONE` on #6927 so the next session proceeds.
6. **One PR = one concern.** The 2026-07-26 lapse was compounded by piling
   unrelated housekeeping onto one PR, making `health-review-backlog.json` a
   conflict magnet that re-triggered CI. Keep PRs single-concern.

## The rebase-race — RESOLVED 2026-08-10 (kept as the record of why)

**Past tense throughout — this described the state BEFORE 2026-08-10.** With the
queue unavailable, concurrent sessions **used to** hit the **rebase-race**: `main`
advanced under an open PR while its checks ran, the PR went `behind`, and its
checks **had to** re-run against the new merge head before it could merge —
repeated if several sessions landed PRs in a burst. That was intrinsic to `main`'s
branch protection having **"Require branches to be up to date before merging"**
ticked, combined with no queue to auto-serialize.

Worst measured case (PR #8698, 2026-08-09/10): **four** CI cycles for one PR,
~9 minutes each, because `main` moved twice while it waited. With sessions
merging faster than one CI cycle a branch could never be simultaneously green
**and** up-to-date — a livelock, not impatience.

**RESOLVED by the first option below, taken 2026-08-10.** The section is kept
because the second option is still open and the reasoning still applies if the
setting is ever restored.

**It persisted until ONE of:**

- ✅ **DONE 2026-08-10 (operator-directed).** The operator **unticked "Require
  branches to be up to date before merging"** on
  `main`'s branch-protection rule (checks then still run per-PR, but a PR need
  not be re-synced to the current `main` tip to merge — removing the re-run
  churn; the trade-off is a PR can merge against a slightly older base, which the
  required checks still gate). This is the low-cost mitigation available today on
  a user-owned repo.
- The repo is **moved into a GitHub organization**, which unlocks the native
  merge queue (then the enablement flow — flip "Require merge queue," run a
  validation PR — becomes performable, and the dormant `merge_group:` triggers
  activate).

Today: **claim the slot, merge-on-green, release.** A PR that goes `behind`
while it waited **no longer needs a re-sync** — required checks still gate it,
they simply no longer have to have run against `main`'s exact tip.

**How to tell it is still off** (do not infer it): `branch-protection-sync.yml`
echoes `strict=` in its run notice and **fails the run** if the live protection
does not match what that file declares. A green sync run on a push to `main` is
therefore positive evidence; before 2026-08-10 the notice printed only
`contexts` and the setting could not be confirmed from CI at all.
