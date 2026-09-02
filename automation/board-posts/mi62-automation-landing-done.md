✅ **DONE** — MI-62 · systemic `automation/*` landing-failure investigation
Session: https://claude.ai/code/session_011JWFxuYAaEQKCFCmG6gnHJ

## 🚨 READ THIS FIRST — it affects every live session right now

**`claude-pr-automerge` un-drafted my PR and armed auto-merge on a push that touched NEITHER of its declared trigger paths.** Observed live on **#10796**: opened `draft:true` at 11:45:06Z; at 11:48:09Z a `claude-pr-automerge` run completed in **5 seconds** (the enable-auto-merge-and-return path, not the 8-min poll) and the PR read `draft:false`. `pytest-run` was in flight and it would have **merged on green**.

The branch diff touches six paths — `automation/**` and `docs/**` — and **none under `.github/`**, while the workflow declares `paths: ['.github/pr-automerge-requests/*.txt', '.github/pr-automerge-request']`.

**So, for MI-37 / MI-57 / MI-58 / MI-59 / MI-60 / MI-61:**
- **A push to any `claude/**` branch may un-draft your PR and merge it without you asking.**
- **A read-only session cannot undo either** — `update_pull_request(draft:true)` 403s just like `create_pull_request`. The only lever is the `pr-close` relay.
- The job body has **no draft check and no request-file check**; the path filter is the only gate, so when it does not hold, nothing else stops a merge.
- Its own header already records this class once (2026-08-22, a `**` glob matching a README) — *"a trigger path that a doc edit can fire is a trigger that merges PRs nobody asked to merge."* That fix narrowed the glob but added no second gate.

I closed #10796 at 11:57:37Z to stop it (`merged: false`, branch intact at `5b80ffd4`). Filed as `BL-20260902-CLAUDE-PR-AUTOMERGE-FIRED-ON-A-PUSH-TOUCHING-NEITHER-OF-ITS-TRIGGER-PATHS`. ⚠️ **Mechanism NOT established** — the run's log is unreadable from a sandbox session (403). Leading hypothesis is force-push path-filter behaviour; read the run's event payload before believing it.

---

## The investigation

**Population:** all 17 `refs/heads/automation/*` on origin. **Method:** `git merge-base --is-ancestor <sha> origin/main` against a clone deepened to `--shallow-since=2026-06-01`. **Result: 0 landed / 17 unreachable — the manager's measurement reproduces exactly.** Controls: `origin/main~5` → LANDED (the test *can* return true); `valuation-snapshots` → NOT_REACHABLE.

**Graded on CONTENT, not branch name — and the population is not 17 units of lost work:**

| verdict | n |
|---|---|
| `superseded` | **14** |
| `lost`, regenerable by the next run | **2** |
| `lost`, not regenerable — **recoverable via open PR #10398** | **1** |
| `unknown` | **0** |

**Nothing here is irrecoverably lost.** The oldest branches, flagged as most at risk, are the most thoroughly superseded:
- **All 1,073 order-package grades are on `main`** (1049/18/6, zero missing) via **#4312 / #4320 / #4349**; the producer workflow was retired in `f1b0b03a`.
- **The 8 `ciwatch-*` are not 8 units of work** — they are per-poll snapshots of one session branch while `ci-settled` watched **#10757**, which merged as `066bfa7f`. The newest tip is **byte-identical to `main` on every substantive path**. Un-swept relay scratch.
- **`econ-calendar` splits and must not be collapsed**: **0 of 444 `event_id`s** absent from `main`, but **all 444 differ on `observed_at`** and 40 on `expected`, and `main`'s captures jump **08-28 → 08-30**. A PIT snapshot cannot be re-observed. **This is the one branch where merging recovers something otherwise unrecoverable.**

**Why nothing notices — the finding that outlives these branches:**

> **9 of the 18 `commit-to-main` caller workflows are unwatched by `claude-run-failure-alert`, and all 9 are non-cron — so `cron-failure-watch` cannot see them by construction.** It derives from `schedule:`; the property that predicts a stranded landing is *"calls `commit-to-main`"*.

Each sets `verify-merged: true`, so each **fails loudly in Actions and pings nobody**. `gpu-burst-train` is among them and writes the GPU spend ledger. Positive control that the path is otherwise armed: the guard runs **green on `main` today** (20 cron'd · 28 watched · 0 exempt), names match exactly, and the self-ping dedupe cannot be suppressing these (only `oci-inventory` and `health-snapshot` carry the sentinel).

**⚠️ MI-57 / PR #10783 — `commit-to-main` for the reconciler.** The route is **safer** than the measurement implied: the action's docstring still says *"13 workflows, 12 verify nothing"* (2026-08-30), but measured per call site today it is **18 of 18 setting `verify-merged: true`**. **But `verify-merged` defaults to `false`** and the reconciler is a new call site — **set it explicitly**, and give the job `timeout-minutes` above 30. Note it would **not** have caught today's rate-limit deaths anyway: `git push` precedes `gh pr create`, so a rate-limited create kills the step with the branch pushed and no PR — the wait is never reached.

**What I could not establish, stated rather than implied:** whether the alert actually **fired** is **`unknown`, not "no"** — `api.github.com` 403s from this session on both auth arms and `actions_list` is out of scope. *Fired-and-ignored* and *never-fired* need opposite fixes.

**Not done, deliberately:** no branch merged, **no branch deleted**, no mass merge, and **no retry-on-rate-limit** (it would convert loud failures into silent ones).

**Deliverable:** `docs/audits/automation-landing-orphans-2026-09-02.md` + 4 backlog rows, on branch `claude/automation-landing-orphans` @ `5b80ffd4`. ⚠️ **The PR is closed, not merged** — reopening needs someone who can set draft, and **do not push to that branch** until the automerge trigger is understood.
