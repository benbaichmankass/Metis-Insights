One file per PR that wants the auto-merge relay.

**Name it `<branch-slug>.txt`, where the slug is the branch name with `claude/`
stripped and any remaining `/` replaced by `-`.** For `claude/mi69-automerge-misfire`
that is `mi69-automerge-misfire.txt`. The content is never read — the relay uses
the path only as a trigger.

⚠️ **The name is now ENFORCED, not a convention.** The relay looks for the file
named for the branch it was pushed on, and does nothing at all if it is absent.
A file named for a different branch will not arm yours.

## Why the name has to match

A per-request file is what stops two PRs conflicting on a single shared marker
(`BL-20260821-AUTOMERGE-TRIGGER-IS-A-SINGLE-SHARED-FILE`), and it is the
convention every sibling relay in this repo already follows. But a per-request
file alone was **not** enough to make the trigger mean "I asked".

GitHub computes a push's changed-file set as the diff from the branch's
pre-push head to its new head. So when a branch **merges or rebases `main`**, every
path `main` changed comes into that diff — including request files belonging to
*other* branches. Nine landed on `main` on 2026-09-02 alone, and on that day three
PRs were un-drafted and armed for auto-merge having asked for nothing
(`BL-20260902-THE-PER-REQUEST-GLOB-IS-ITSELF-DRAGGED-IN-BY-A-MERGE-OF-MAIN`).
The repo's own merge protocol is what tells branches to sync to `main`, so the
condition fired most readily on the branches doing the right thing.

**A `paths:` filter therefore cannot be the gate** — it cannot tell "I asked for
auto-merge" from "I merged somebody else's ask". The relay proves the ask in its
job body instead: your slug-named file must be **present at the pushed head and
added-or-modified relative to `main`**. A file merely inherited from `main` is
byte-identical to `main`'s copy and does not count.

⚠️ **`*.txt` only, and this README must never arm anything.** An earlier version
of the trigger globbed `**` and armed auto-merge on the PR that added this very
file (observed 2026-08-22T10:46:15Z). Prose in this directory is not a signal.

## What the relay will not do

**It will not un-draft a PR it did not itself open.** A draft is this repo's
marker for "prepared, not approved" — every Tier-2 and Tier-3 convention relies
on it, and branch protection does not help, because it gates on checks and the
checks are green. Writing a request file does **not** override a draft; the relay
refuses and says so in its run log. If you want the relay to merge a PR, open it
ready (`"draft": false` in the `pr-opener` request) or ask the manager.

The legacy shared marker `.github/pr-automerge-request` has been **deleted**. It
was inert once removed from the trigger, and an inert file whose name still reads
like a request is a trap: a future session could write to it, believe it had
asked, and be met with silence.
