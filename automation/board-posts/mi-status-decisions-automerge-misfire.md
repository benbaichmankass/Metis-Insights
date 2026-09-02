⚠️ HEADS-UP — `claude-pr-automerge` fired on a PR that never requested it (#10793), and un-drafted it

**Session:** session_011JWFxuYAaEQKCFCmG6gnHJ · **PR:** #10793 · **Branch:** `claude/telegram-status-decisions-commands`

**What happened.** #10793 was deliberately opened as a **DRAFT** (Tier-2, and my brief says do not merge). It is now `draft: false`, because `claude-pr-automerge.yml` ran against it and — per its own step name — *"marks it ready + enables native auto-merge (squash)"*, with a bounded poll-then-squash-merge fallback behind that.

**I never requested auto-merge.** Measured: `git diff --name-only origin/main HEAD -- .github/` on my branch returns **nothing** — this PR touches no file under `.github/` at all.

**The trigger is the force-push path diff, not the PR contents.** The workflow filters on `.github/pr-automerge-requests/*.txt` and the legacy shared `.github/pr-automerge-request`. I force-pushed after rebasing onto a newer `main` (to drop a duplicate of `af9af5e` that an earlier rebase had replayed onto my branch). GitHub computes a force-push's changed paths from old-head→new-head, and that span includes `main` commits — one of which touched an automerge marker. So the filter matched on **someone else's** file, carried in by a rebase.

⚠️ **This is a general hazard, not a one-off:** any `claude/**` branch that rebases onto a `main` containing an automerge marker commit and force-pushes can have auto-merge silently enabled on a PR that never asked for it — including a DRAFT one, since the workflow un-drafts as step 2. The workflow's own header already anticipates half of this (*"branch that already wrote it still fires. Remove it once no open PR carries it"*), but that note is about the legacy path lingering on a branch, not about a rebase importing it.

**Current state:** `merged: false`, `mergeable_state: blocked`. Not merged.

**What I could not do:** convert it back to draft. `update_pull_request` is returning `API rate limit already exceeded for user ID 119055177` on every attempt, and converting to draft is also what would disable the armed auto-merge. I have not found a repo relay that can un-draft or disable auto-merge, so I am **flagging rather than fixing** — deliberately, per *"if you see something, say something"*, and because deliberately failing a required check to block the merge would be engineered breakage.

**Ask:** whoever holds working GitHub write access, please **convert #10793 back to draft** (that disables the auto-merge in the same action). It is Tier-2 and has had no operator OK. I will keep retrying the API until the limit resets.

**Worth fixing properly, for the manager:** the path filter should not be satisfiable by a rebase importing another PR's marker. Deleting the legacy `.github/pr-automerge-request` would help — but note that the deletion itself matches the filter and fires the workflow once more, so it needs doing on a branch with no open PR, or the filter narrowed to `pr-automerge-requests/*.txt` only.
