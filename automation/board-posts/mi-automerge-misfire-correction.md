🔧 CORRECTION to my earlier automerge-misfire post on #10793 — two things I got wrong

**Session:** session_011JWFxuYAaEQKCFCmG6gnHJ · **PR:** #10793 · Posted from `claude/board-automerge-correction`, **deliberately not from #10793's own branch** — see (2).

My earlier post said *"I will keep retrying the API until the limit resets."* **Do not rely on that.** Two corrections, and the second changes what the next person should actually do.

**(1) It is NOT a rate limit. It is a permanent permissions boundary.**
`update_pull_request(draft: true)` returned `API rate limit already exceeded for user ID 119055177` on eight attempts across ~40 minutes. Once the window actually cleared, the SAME call returned **`Resource not accessible by integration`** — the identical 403 write-scope boundary that already blocks `add_issue_comment` and `create_pull_request` from this session, and the reason the `board-post` and `pr-opener` relays exist at all. So the rate-limit message was masking a wall, and no amount of waiting fixes it. **This session cannot un-draft a PR, ever.** A human or a relay must. I have found no repo relay that can un-draft a PR or disable auto-merge — if that gap is worth closing, it is a sibling of `pr-opener.yml`, and I am naming it rather than building it unasked.

**(2) #10793 is currently FROZEN and CANNOT auto-merge — and the reason is fragile.**
Measured just now: head `069ff90`, `get_status` → `state: pending`, **`total_count: 0`**, zero check runs. That head is the `board-post` relay's own results commit, pushed by `github-actions[bot]`, and GitHub fires no workflows for `GITHUB_TOKEN` pushes — the trap `pr-opener.yml`'s header documents. With zero checks, required-status-checks can never be satisfied, so the armed auto-merge has nothing to fire on.

⚠️ **THE FREEZE ENDS THE INSTANT ANYONE PUSHES AN ORDINARY COMMIT TO THAT BRANCH.** CI arms, the checks go green (they pass locally: 175 tests on the touched files, full suite 14,617 passed / 0 failed, guards 51/0, ruff clean on the pinned `<0.16`), and auto-merge squashes a **Tier-2 PR that has had no operator OK**.

**So the ORDER matters, and it is the opposite of the usual instinct:**
> **Convert #10793 to draft FIRST (that also disables the auto-merge). Arm CI only afterwards.**

Arming CI first — the normal remedy for a relay-flattened PR, and what I would otherwise have done — is precisely what releases the merge. I have therefore pushed **nothing** further to `claude/telegram-status-decisions-commands` and posted this from a separate branch, so the PR stays frozen until someone with write access decides.

**Unchanged from the first post:** the misfire trigger is a force-push path diff. `git diff --name-only origin/main HEAD -- .github/` on that branch returns nothing — #10793 touches no `.github/` file. Rebasing onto a newer `main` and force-pushing made GitHub compute changed paths across `main` commits, one of which touched an automerge marker, satisfying `claude-pr-automerge.yml`'s filter. Any `claude/**` branch that rebases and force-pushes can hit this, draft or not, since the workflow un-drafts as its step 2.
