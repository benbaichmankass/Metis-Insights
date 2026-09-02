# `automation/*` landing failure — disposition and cause, per branch

**Session:** MI-62 · `session_011JWFxuYAaEQKCFCmG6gnHJ` · 2026-09-02
**Tier:** 1 (investigation; the proposed fix is Tier-1 — a CI guard and an alert list)
**Status:** investigation complete; **no branch merged, no branch deleted**

---

## 0. The one-paragraph answer

The manager's measurement is **reproduced exactly** — 17 of 17 `automation/*` branches
are unreachable from `main`. But the population is **not 17 units of lost work**. Graded
by content rather than by branch name: **14 are `superseded`** (the data is on `main` by
another route, attributed commit-by-commit below), **2 are `lost` but regenerable by the
next successful run**, **1 is a genuine point-in-time gap** (and it is recoverable — it
has an open PR). **Nothing in this population is irrecoverably lost.**

The deeper finding is not about these 17 branches. It is that
`claude-run-failure-alert`'s coverage is derived from the wrong population:
**9 of the 18 workflows that call `commit-to-main` are not watched by it**, and all 9 are
exactly the ones `cron-failure-watch` cannot see, because that guard derives its required
set from `schedule:` while the property that predicts a stranded landing is
*"does this workflow call `commit-to-main`"*. Those two sets overlap by accident, not by
construction.

---

## 1. Population and method

**Population:** every branch matching `refs/heads/automation/*` on `origin`, enumerated with

```
git ls-remote --heads origin 'refs/heads/automation/*'      # -> 17 refs
```

**Ancestry test**, run per branch tip against a clone deepened to `--shallow-since=2026-06-01`
(2,396 commits of `main`, oldest 2026-06-01 — comfortably older than the oldest branch,
2026-06-23, so no branch is graded against a truncated history):

```
git merge-base --is-ancestor <sha> origin/main
```

**Result: 0 landed / 17 NOT_REACHABLE.**

**Controls**, because a test that can only return one answer measures nothing:

| control | expectation | result |
|---|---|---|
| `origin/main~5` | must test **LANDED** | LANDED ✅ — the test *can* return true |
| `automation/valuation-snapshots` | must test **NOT_REACHABLE** | NOT_REACHABLE ✅ |

**Content test.** For each branch, every path it touched relative to its own
merge-base was compared against `origin/main` three ways: path absent · blob identical ·
blob differs. For the append-only JSONL stores, that is too coarse — a file legitimately
differs while containing every line the branch added — so those were additionally graded
by **line membership** (`comm` of the branch's added lines against `main`'s file), and the
econ-calendar store by **`event_id` membership** parsed as JSON.

⚠️ **"Differs from main" is not "absent from main", and conflating them is how a
superseded branch gets reported as lost.** Every `lost` verdict below rests on the
line/id-level test, never on the blob comparison.

---

## 2. Disposition, per branch

Three states, never collapsed: `superseded` · `lost` · `unknown`.

### 2.1 `ciwatch-pr10757-*` ×8 — **`superseded`** (and they are not what they look like)

These are **not eight units of stranded work.** They are eight per-poll snapshot branches
pushed by `ci-settled.yml` while it watched **PR #10757** — the same session branch
(`claude/session-invokable-actions-ci-settled`) captured at eight successive moments, each
carrying that session's whole working tree plus its own two bookkeeping files.

**PR #10757 merged to `main` at 2026-09-02T07:52:50Z as `066bfa7f`.**

Measured on the newest tip (`...T072014Z-12666`), path by path against `main`:

| path | vs `main` |
|---|---|
| `scripts/ops/ci_settle.py` | **identical** (blob `6d1b71c5`) |
| `scripts/ops/ci_settled.sh` · `.github/workflows/ci-settled.yml` · `tests/test_ci_settle.py` | **identical** |
| `docs/audits/session-invokable-actions-inventory-2026-09-02.md` · `docs/claude/health-review-backlog.json` · `docs/github-actions-workflows.md` · `docs/claude/session-board.json` | **identical** |
| `automation/ci-results/pr10757-…json` · `automation/ci-watch/pr10757-…json` | absent — **its own watch bookkeeping** |

Every substantive path is byte-identical on `main`. The seven older tips "differ" only
because they are *earlier drafts of the same evolving files*, superseded by the version
that merged. What is genuinely absent from `main` is each branch's own ci-watch request
and result JSON — the relay's scratch state, which `ci-settled` has a
`sweep spent watch branches` commit precisely to clean up.

**Verdict: `superseded`. Nothing to re-land.** These are un-swept relay scratch branches,
and the correct disposition is deletion *by the sweep that owns them* — not by me, and
not by a merge.

### 2.2 `grade-order-packages-*` ×3 — **`superseded`**, producer retired

The manager flagged these as possibly-lost decision grades. They are not.

| branch | rows added | of those on `main` | landed via |
|---|---|---|---|
| `-28025099518` | 1,049 | **1,049** | `49bb3cdc` — *"data(grading): backfill 1049 order-package decision grades"* (**#4312**, 2026-06-23T15:28:38+03:00) |
| `-28027973367` | 18 | **18** | `fea7927a` — *"grade today's 18 order packages (fresh live-DB run)"* (**#4320**, 16:09:09) |
| `-28037490394` | 6 | **6** | `d277048b` — *"6 order-package grades for the system-report window"* (**#4349**, 18:47:02) |

**0 of 1,073 grade lines are missing from `comms/claude_strategy_scores.jsonl` on `main`.**
Each landed by a separate hand-driven PR within hours of the automation branch being cut
(the 1,049 landed **19 minutes** after its branch).

The producer no longer exists: `grade-order-packages.yml` was deleted by `f1b0b03a`,
*"ci: retire grade-order-packages.yml (grading is in-session, operator directive)"*.

**Verdict: `superseded`.** The work landed, and nothing will or should regenerate it.

### 2.3 `valuation-snapshots` — **`superseded`**

9 rows added; 0 of the 9 exact lines are on `main`. But the exact-line test is the wrong
one here, and the tuple test settles it:

- Branch rows: `as_of = 2026-07-23T15:05:00Z`, 9 `(symbol, asset_class, metric)` tuples.
- `main` holds **9 rows dated 2026-07-23** at `as_of = 2026-07-23T15:23:32Z`, and the
  tuple sets are **identical — 0 tuples on the branch that are not on `main`**.

A later run the same day, 18 minutes on, captured the same metric set and landed.

**Verdict: `superseded`.** ⚠️ Stated precisely: the *metric coverage* for that date is
complete on `main`. The *values* are a different intraday read and are not byte-identical.
For a daily valuation series that is immaterial; I am not claiming the two reads are the
same number.

### 2.4 `research-queue-stamp-*` ×2 — **one `superseded`, one `lost` (benign)**

Both stamp `last_dispatched_at` on `research/queue/RQ-20260827-001.yaml`.

- `-33340458710` (22:56:24Z) → `main` reads `last_dispatched_at: '2026-08-30T22:56:22+00:00'`.
  **That is this run's stamp. `superseded`** — it landed by another route.
- `-33342140520` (23:34:02Z) → its stamp `'2026-08-30T23:34:00+00:00'` is **not on `main`**;
  `main` still reads the earlier 22:56:22. **`lost`.**

**Harm: low, but not zero.** The stamp is idempotent bookkeeping that the next dispatch
overwrites — except **no dispatch has landed in the three days since**, so the queue's
record is currently 38 minutes stale and has stayed stale.

⚠️ **Do not re-land this branch to fix it.** The stamp workflow round-trips the YAML
through a naive load/dump: the diff against `main` is **27 insertions / 39 deletions** on a
66-line file, and it **deletes authored comments and reflows `>-` block scalars**:

```
-  # The harness emits a ledger from public Yahoo candles and scores it; the
-  # heaviest object is one pandas frame of 730 daily bars.
```

This is the `backlog_append.py` lesson (CLAUDE.md: *"it round-trips the file's exact
serialisation … a naive read-append-write reformats every non-ASCII line"*) recurring in a
second writer. Merging either branch would strip authored content from the research queue
to fix a timestamp. Filed below.

### 2.5 `probes-33615584967-1` — **`lost`, regenerable**

| | `generated_at` |
|---|---|
| branch `docs/claude/PROBES.json` | **2026-09-02T09:43:54Z** |
| `main` | **2026-09-01T10:13:20Z** |

The branch is *newer* than `main`. Note `main`'s value matches, to the minute, `probes.yml`'s
**only ever scheduled run** (#34, 2026-09-01T10:12:17Z — recorded in `CLAUDE.md`). So
`main`'s probe results came from that single run, and the 09-02 refresh is stranded.

**This one has teeth beyond the data:** the branch also carries `docs/claude/DUE.json` and
`docs/claude/DUE.md` — the generated due-list the `duty` skill reads as its single input.
A stranded probes refresh means **every duty pass since reads a day-stale due-list while
the file reads as current.** That is the failure shape the alert list's own comment names
for `probes`: *"every row it covers is quietly unwatched WHILE the last-known results file
still reads as current."*

**Verdict: `lost` for that run; regenerable by the next successful `probes` run.** The
right remedy is a green run, not a merge of this branch.

### 2.6 `work-digest-33602234950-1` — **`lost`**

One row for `docs/claude/pending-pings.jsonl`, the queue the VM drains to Telegram:

```
{"at": "2026-09-02T07:10:23.888117+00:00", "target": "claude", "event": "work_digest",
 "digest_state": "changes_observed", ...}
```

`main` holds exactly **one** `work_digest` row, at `2026-09-02T00:19:53Z` — a different
run. The 07:10 row never reached `main`, so it was **never queued, and therefore never
drained or delivered**. The digest ran, observed changes, and told nobody.

This is the manager's confirmed rate-limit case, and the mechanism is worth stating
exactly: in `commit-to-main`, `git push` of the throwaway branch happens **before**
`PR_URL="$(gh pr create …)"`. Under `set -euo pipefail`, a rate-limited `gh pr create`
kills the step **after** the branch exists and **before** any PR does. So `verify-merged`
never even runs — it is not the guard that failed here.

**Verdict: `lost`.** The next daily digest produces a *new* row for the then-current state;
the 07:10 observation is not recoverable as such, but nothing downstream depends on that
specific row.

### 2.7 `econ-calendar-33232352515-1` — **event set `superseded`, PIT layer `lost`; recoverable**

This is the only branch where the two halves genuinely disagree, and collapsing them
would be wrong in both directions.

Measured over the 444 added rows, parsed as JSON:

| test | result |
|---|---|
| added rows whose **exact row** is absent from `main` | **444** |
| added rows whose **`event_id`** is absent from `main` | **0** |

Every event is tracked on `main`. What differs, field by field:
`observed_at` ×444 · `expected` ×40 · `realized_outcome` ×26 · `status` ×17 ·
`resolved_at` ×17 · `event_name` ×14.

So the **event set is superseded** by the later captures that did land (08-30, 08-31,
09-01, 09-02). But `observed_at` differing on all 444 is the definition of a *different
point-in-time observation*, and the 40 differing `expected` values are **consensus as it
stood on 2026-08-29**, since overwritten by revised consensus. The capture file
`comms/macro/econ_calendar_captures/US-20260829T034952Z.fxstreet.json` is **absent from
`main`**, and `main`'s capture series jumps **08-28 → 08-30**.

The `macro-research` skill makes *point-in-time / no-lookahead consensus* a binding
invariant. **A PIT snapshot is by construction not regenerable**: you cannot go back and
re-observe what consensus was on 08-29.

**Verdict: `lost` at the PIT layer — but recoverable, because the branch survives and
PR #10398 is open** (verified: state `open`, head `automation/econ-calendar-33232352515-1`
@ `2726b0f6`, base `76d14af5`, `mergeable_state: unknown`, and **`updated_at` ==
`created_at` = 2026-08-29T03:49:57Z** — nothing has touched it in four days, so the
one-shot `refresh-stale-branch` never ran for it; that input only defaulted on from
2026-08-31, after this PR was opened).

**This is the one branch where merging recovers something that cannot otherwise be
recovered.** It is a data-only PR (`config/economic_calendar.yaml` explicitly untouched).
I am not merging it — per the constraint, and because a 62,524-line data merge on a
4-day-stale base is the operator's or manager's call.

### 2.8 Tally

| verdict | n | branches |
|---|---|---|
| `superseded` | **14** | ciwatch ×8, grade-order-packages ×3, valuation-snapshots, research-queue-stamp-33340458710, *(econ-calendar's event set)* |
| `lost`, regenerable by the next run | **2** | probes-33615584967, research-queue-stamp-33342140520 |
| `lost`, not regenerable — **but recoverable via open PR #10398** | **1** | econ-calendar-33232352515 (PIT layer) |
| `unknown` | **0** | — |

**`work-digest-33602234950` is counted under `lost`** (its row is gone for good; the next
digest supersedes the *function*, not the observation).

**No branch in this population is irrecoverably lost.** The oldest ones — which the brief
flagged as most at risk — turned out to be the most thoroughly superseded.

---

## 3. Why nothing notices

### 3.1 What I could establish, and what I could not

⚠️ **I could not read run history from this session, and I am not going to report that
silence as evidence.** `api.github.com` is intercepted by the sandbox and returns **403**
with a Claude-specific body for **both** unauthenticated and `GITHUB_TOKEN`-authenticated
requests (measured, both arms). `actions_list` is not in this session's MCP tool scope.

**So "did the alert fire for run 33602234950?" is `unknown` — not "no".** That distinction
matters here more than usual: if it *did* fire, the finding is normalization (a ping
arrived and was walked past), which is a different and worse problem than silence.

What *is* answerable from the repo is **coverage by construction**, and that turns out to
be the more durable finding.

### 3.2 Positive control — the alerting path can fire

Before concluding anything about coverage:

- **`cron-failure-watch` runs and passes on `main` today**:
  `20 cron'd workflows · 28 watched entries · 0 exempt — OK`. The guard is live and green.
- **Name matching is exact** for the producing workflows — `work-digest.yml` declares
  `name: work-digest`, which is the string the listener watches (GitHub matches on `name:`,
  not filename). Control: `grep` for `- work-digest` in the listener returns **1**.
- **The self-ping dedupe cannot be swallowing these.** The listener stays quiet only when a
  step named `[operator-ping]` concluded `success`. Repo-wide, **only** `oci-inventory.yml`
  and `health-snapshot.yml` carry that sentinel. **None** of `work-digest`, `probes`,
  `econ-calendar-produce`, `macro-valuation-snapshot`, `research-queue-dispatch`,
  `work-decision-commit` or `due-list` carries it (0 occurrences each).

So for the *watched* producers the path is armed and unsuppressed. Whether it fired is
`unknown`.

### 3.3 The finding: the watch population is derived from the wrong property

`cron-failure-watch` derives its required-watch set from workflows carrying a live
`schedule:`. But the property that predicts *"a failure here strands committed rows"* is
**"this workflow calls `commit-to-main`"**. Measured today:

| | n |
|---|---|
| workflows calling `.github/actions/commit-to-main` | **18** |
| of those, **watched** by `claude-run-failure-alert` | **9** |
| of those, **UNWATCHED** | **9** |
| unwatched callers that are cron'd (i.e. that the guard could have caught) | **0** |

The nine unwatched callers — every one `issues`/`workflow_dispatch`-triggered, so invisible
to a cron-derived guard:

| workflow | triggers | writes to `main` |
|---|---|---|
| `cot-positioning-backfill` | issues, dispatch | COT positioning series |
| `crypto-signals-backfill` | issues, dispatch | crypto funding/signals |
| `econ-calendar-backfill` | issues, dispatch | econ calendar history |
| `econ-calendar-survey-backfill` | issues, dispatch | survey/consensus history |
| `gld-compat-matrix` | issues, dispatch | GLD compat verdicts |
| `gpu-burst-train` | issues | **`comms/gpu_spend_ledger.json`** |
| `macro-valuation-backfill` | issues, dispatch | valuation history |
| `sysdyn-gas-calibrate` | issues, dispatch | M29 calibration |
| `e35-bracket-sweep` | dispatch | E35 bracket corpus |

Each sets `verify-merged: true`, so each **fails loudly in Actions** when its rows do not
land — and **nothing pings**. Verified by hand for three of the nine
(`gpu-burst-train`, `sysdyn-gas-calibrate`, `cot-positioning-backfill` → 0 matches each),
against the `work-digest` control (1 match).

The counter-argument — *"these are issue-triggered, so someone is waiting"* — is exactly the
one the listener's own header rejects for relays: a session that has moved on, or a
dispatch fired and forgotten, gets nothing. And `gpu-burst-train` writes the **spend
ledger** that `/api/bot/gpu/spend` reads against a $10/month cap.

**This is a recurrence, not a new class.** The listener's own comment records that its
hand-maintained list *"has now been asserted-complete and been false TWICE"*, and
`cron-failure-watch` was built so the next gap would announce itself. It did not, because
the guard was given the cron population rather than the landing population.

### 3.4 A second, narrower gap: `ci-settled`

`ci-settled` is `push`/`workflow_dispatch`-triggered, calls no `commit-to-main`, and is
**not** in the watched list — so neither mechanism covers it. Its failure does not strand
data (§2.1), but it is precisely the pain the listener was built for: a session **blocking
on a CI verdict that never arrives**. Lower priority than §3.3; recorded so it is not
rediscovered.

---

## 4. `commit-to-main` — report to the manager (MI-57 / PR #10783)

**Reported explicitly because it may change this morning's approval.**

### 4.1 The route is materially safer than the measurement implies

`commit-to-main`'s own docstring says:

> *Measured 2026-08-30: 13 workflows call this action, 12 verify nothing, and origin carried 5 stranded `automation/*` branches.*

**That figure is stale, and stale in the reassuring direction is still stale.** Measured
today by parsing each call site's `with:` block (not a file-level grep):

> **18 of 18 call sites set `verify-merged: true`.** 0 do not.

The fleet was fixed after the docstring was written and the docstring was not updated. A
session reading it today would conclude the caller layer is broken when it is not.

### 4.2 The sharp edge that remains, and it is the reconciler's

`verify-merged` **defaults to `false`**, and with it off the action **exits 0 the moment the
PR is opened**:

```
echo "::notice::verify-merged is off — this step is green because a PR was OPENED,
      which is NOT the same as the rows being on main."
```

The reconciler is a **new** call site. It inherits the default unless it opts in. A
reconciler that silently fails to land is worse than none — the record then reads as
maintained — which is exactly the manager's concern.

**Recommendation to MI-57, three lines:**
1. Set `verify-merged: true` **explicitly**. Do not inherit the default.
2. Give the calling job a `timeout-minutes` **above** `verify-timeout-minutes` (default 30)
   or the job dies mid-wait — `research-queue-dispatch` carries 35 for this reason.
3. Use `assert_rows_landed.py --pushed-ref` with the action's `branch` output. `verify-merged`
   answers *"did the PR merge"*; only that answers *"are **my** rows in the file"* — the two
   differ under a squash-merge race.

### 4.3 …and `verify-merged` would not have caught today's failure

The two confirmed rate-limit deaths did **not** fail at the verify step. In the action's
script, the branch `git push` precedes `gh pr create`; under `set -euo pipefail` a
rate-limited `gh pr create` kills the step with the branch pushed and no PR opened.
`verify-merged` is never reached.

**So `verify-merged: true` is necessary and not sufficient.** It closes the
*silently-green* mode; it does nothing for the *loudly-red-and-unwatched* mode, which is
§3.3. Both need closing, and the second is the one no caller can fix for itself.

---

## 5. What I propose

**Visibility, not a retry.** A retry on rate limit is a plaster; the fix is that a landing
failure cannot be quiet.

**P1 — extend `cron-failure-watch` to derive from the landing population (Tier-1).**
Add a second required-watch set: every workflow whose text contains
`actions/commit-to-main` must appear in `claude-run-failure-alert`'s `workflows:` list,
exactly as the cron'd set already must. The guard already has the machinery — name
extraction, the watched-name parser, the phantom-entry check, and a **verified** (not
presence-only) `# cron-watch-exempt: <name> — <reason>` override. This is one predicate and
nine list entries. It makes the next `commit-to-main` caller announce itself **on its own
commit**, which is the property `cron-failure-watch` was built for and did not deliver here
because it was pointed at the wrong population.

**P2 — correct `commit-to-main`'s docstring** to today's 18/18, dated, with the method
named. Leaving a stale measurement in the one file a future session reads before wiring a
new caller is how §4.1 recurs.

**P3 — `ci-settled` into the watched list** (§3.4), or an explicit exemption with a reason.

**P4 — file the research-queue YAML reformat** (§2.4) so nobody "fixes" the stale stamp by
merging a branch that strips authored comments.

**Deliberately NOT proposed:**
- **No mass merge.** Fourteen branches have nothing to land; two want a fresh run, not a
  merge; one (#10398) is a real decision for the manager or operator.
- **No branch deletion.** Even the eight superseded ciwatch branches stay — their sweep
  owns them, and I will not tidy up a namespace I was asked to investigate.
- **No retry-on-rate-limit.** It would convert today's two failures into silence, which is
  the wrong direction.

---

## 6. What I could not establish

Stated plainly rather than left as an implied negative:

1. **Whether `claude-run-failure-alert` actually fired** for any of the runs behind these
   branches. `api.github.com` is 403 from this session (both auth arms, measured) and
   `actions_list` is out of scope. **`unknown`, not "it did not fire."** An interactive
   session or a workflow can settle it, and it is worth settling: *fired-and-ignored* and
   *never-fired* need opposite fixes.
2. **Why PR #10398's checks never completed.** `mergeable_state: unknown` and no update in
   four days is consistent with both causes the action's own timeout message warns not to
   choose between — its checks failing on its own content, or never re-running after a
   transient red base. **Read the PR's own check runs before assuming.**
3. **Whether the 2026-08-29 consensus values differ materially** from the revised ones now
   on `main`. I established the fields differ and how many; I did not grade the economic
   significance.

---

## 7. Reproduction

Every count above is reproducible from a clone deepened past 2026-06-01:

```bash
git ls-remote --heads origin 'refs/heads/automation/*'                  # population: 17
git merge-base --is-ancestor <sha> origin/main                          # ancestry: 0/17
git merge-base --is-ancestor $(git rev-parse origin/main~5) origin/main # positive control
python3 scripts/ci/check_cron_failure_watch.py                          # 20 cron'd, 28 watched, OK
grep -rl 'actions/commit-to-main' .github/workflows/ | wc -l            # 18 callers
```

The per-call-site `verify-merged` audit and the caller-vs-watched comparison are short
Python over the workflow YAML; both are restated inline in §3.3 and §4.1 so the numbers
carry their method with them.

---

## 8. How this investigation was landed (and what that itself demonstrates)

Recorded because it is a live instance of the same class this audit is about.

`add_issue_comment` and `create_pull_request` both returned **403 Resource not
accessible by integration** from this session, while `issue_read` and
`pull_request_read` on the *same objects* succeeded in the same minutes. That is
a **write-scope boundary, not the transient hosted-MCP drop** documented in
`CLAUDE.md` — retrying with backoff would not have cleared it. Both relays were
used instead:

| step | relay | result |
|---|---|---|
| board `▶️ START` on #6927 | `board-post.yml` | [comment 5508834901](https://github.com/benbaichmankass/Metis-Insights/issues/6927#issuecomment-5508834901) |
| draft PR | `pr-opener.yml` | **#10796** |

⚠️ **The relay-writes-to-`automation/` irony is apparent, not real, and the
distinction is worth keeping straight**: `board-post` and `pr-opener` write the
*paths* `automation/board-posts/` and `automation/pr-requests/` on a `claude/**`
branch. The population under investigation is the *branch namespace*
`refs/heads/automation/*`. Nothing in this investigation added to the population
it measured.

⚠️ **Both relays commit a result file back**, and when that lands last it becomes
the PR head, which shows **zero check runs** — indistinguishable from "CI has not
started" and from "all green". `pr-opener.yml`'s own header records this. One
ordinary commit was pushed after the PR existed, to arm CI. A reader debugging a
zero-check PR here should read `mergeable_state` first: `blocked` is this,
`dirty` is a merge conflict.
