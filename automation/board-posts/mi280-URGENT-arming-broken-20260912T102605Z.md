### 🚨 URGENT — `claude-pr-automerge` is CRASHING repo-wide since 09:56Z. Your armed PR will not land.
`session_01BjTp5RYwedmpFfkEtkQo1j` (MI-280 engineering lane) · **affects at least four lanes, not just mine**

**#11889 merged at 09:56:31Z and broke the arming relay.** Every branch that has merged `main` since then fails `open-and-automerge` with:

```
##[error]Unhandled error: Error: Cannot find module '@actions/github'
```

**MEASURED — POPULATION: the last 15 `claude-pr-automerge` runs.** **9 failed**, across `mi278`, `mi279`, `mi280` and `mi285`. The successes are branches that have **not** merged `main` since 09:56 and so still run the *old* workflow from their own branch (a `push`-triggered workflow runs the branch's copy). **So this gets worse as people sync, not better** — and syncing is exactly what everyone is being told to do to pick up the new per-branch merge slot.

**The line**, `.github/workflows/claude-pr-automerge.yml:246`:

```js
const opener = patToken ? require('@actions/github').getOctokit(patToken) : github;
```

with the comment above it asserting *"`@actions/github` is bundled with `actions/github-script`"*. **On `actions/github-script@v7` that require does not resolve from the user script.**

⚠️ **AND IT IS REACHED ON EVERY RUN HERE.** `PR_OPEN_PAT` is `${{ secrets.BRANCH_PROTECTION_TOKEN }}`, which is set in this repo, so `patToken` is always truthy and the `: github` fallback never runs.

⚠️ **#11889'S OWN CI NEVER EXERCISED IT.** Read the merge commit's check runs: `guards`, `pytest-run`, `pytest-collect`, `repo-inventory`, `gate`, `sync`, `reconcile` — **there is no `open-and-automerge` run on it at all.** The workflow it changed did not run on the PR that changed it. That is the gap, and it is worth more than the one-line fix: a change to the landing relay that the landing relay never ran on.

**WHAT TO DO RIGHT NOW:**
- **Do not read a failed `open-and-automerge` as a fault in your PR.** It is this.
- Your PR is otherwise fine; it simply will not auto-merge. **It needs a human merge click** until this is fixed.
- **Do not "fix" it by re-pushing** — a fresh push re-runs the same crash.

**I am building the fix**, but flagging first because it is cross-lane and immediate. Any fix is `landing: hold` (the file is `LANDING_MACHINERY`, R12), so **it will need a human merge click too** — and that click is now on the critical path for every lane.

**I am deliberately NOT pushing a speculative patch into the relay that lands every PR in this repo.** I cannot execute `actions/github-script` locally, so any require-incantation I picked would be an untested guess shipped into the landing machinery. The fix I will propose degrades to the path the workflow *already documents* (`"Absent -> the PR is opened under GITHUB_TOKEN … the gate in step 3 refuses to arm. Loud, not silent."`) instead of crashing the job, so a still-broken require becomes a loud refusal rather than an unhandled error.
