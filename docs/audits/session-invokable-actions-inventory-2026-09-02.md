# What a session repeatedly does by hand — measured inventory (2026-09-02)

Dispatched to build **actions Claude can invoke** so a session stops spending
context on work a runner could do. This is the evidence half; the build half is
`.github/workflows/ci-settled.yml` + `scripts/ops/ci_settle{,d}.py|sh`.

⚠️ **Read the population line on every row.** Several of these are single
observations made inside one session. They are enough to justify what was built
and not enough to support a rate.

## 0. The capability boundary, re-measured rather than assumed

Four probes, one shell, this session, 2026-09-02:

| probe | result |
|---|---|
| `curl https://api.github.com/repos/benbaichmankass/Metis-Insights` | **403** |
| `curl https://ict-bot.duckdns.org/api/health` | **200** |
| `curl -H 'Authorization: Bearer $DIAG_READ_TOKEN' …/api/diag/version` | **200**, real JSON |
| `echo $DIAG_BASE_URL` | `http://141.145.193.91:8001` — plain HTTP, a scheme the proxy drops |

Two consequences, and they point in opposite directions:

1. **The GitHub API is the binding constraint, and only it.** Egress is not
   broadly blocked — the same shell reached the trading VM over HTTPS. So a
   session genuinely cannot script a poller for *GitHub* state, and that is
   where a relay earns its keep.
2. **The diag relay is largely superseded and the docs have not caught up.**
   A credentialed `/api/diag/*` read is a Bash call from inside the sandbox. It
   does not need an issue, a workflow, or an MCP read-back. Filed as
   `BL-20260902-DIAG-BASE-URL-STILL-NAMES-THE-TERMINATED-MICRO-AND-CLAUDE-MD-STILL-ROUTES-TO-THE-RELAY`.
   ⚠️ Scoped to the **live VM** only — the trainer has no HTTP diag surface and
   is genuinely relay-only.

This is why the build is on the CI side and not the diag side, even though
"batched diag pulls" was on the candidate list. The candidate was reasonable and
the measurement disagreed with it.

### A capability limit found by building, not by reading

`git push origin --delete <branch>` is **HTTP 403** at the sandbox's git proxy —
measured 2026-09-02, in the same shell where *creating* a ref succeeded moments
earlier. So a session can make a throwaway branch and cannot remove it. This is
not documented in `CLAUDE.md` § "PM-side session capabilities", which describes
git push as working without qualification.

The relay therefore sweeps its own spent `automation/ciwatch-*` branches (age
> 6h, well beyond the 45-minute watch cap) rather than asking the caller to.

## 1. PR-CI polling — the anchor case, and a mid-flight correction

⚠️ **THE BRIEF I WAS GIVEN WAS CORRECTED WHILE I WAS BUILDING, AND THE
CORRECTION WAS RIGHT.** A CI-settled *wake already exists*:
`mcp__github__subscribe_pr_activity` delivers `check_suite.completed` into a
subscribed session's turn, at zero polling cost. For a subscribed PR that is
strictly better than any poll loop, and building a second waiter would be
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED`. **The waiting mode here is
therefore a fallback, not the headline** — for a PR nobody subscribed to, or a
session that cannot end its turn and be woken.

**But the correction is incomplete in the direction that matters, and the repo
already knew it.** `BL-20260821-CHECK-SUITE-EVENT-IS-PER-SUITE-NOT-PER-PR` is
**OPEN at severity HIGH**. This repo's four required checks — `guards`,
`pytest-run`, `pytest-collect`, `repo-inventory` — come from **four separate
check suites**, so a `check_suite.completed` success says *one suite finished*,
never that the PR is green. It was observed twice on two PRs within one hour,
and the event's own footer tells the reader to verify overall state first.

**Run 3 of this relay is a third, independent instance.** On PR #10757,
`guards`, `pytest-collect` and `repo-inventory` were all passing while
`pytest-run` was still in flight — measured, not reconstructed. A session acting
on a success event arriving in that window would have merged on a partial
required set.

So the honest division of labour, and what this PR is actually for:

> **The wake is the TRIGGER. This is the READER.**

`ci_settled.sh <PR> once` grades the *whole head* in a single observation with
no waiting — the mode meant to run **when the wake arrives**. It is what turns
"a suite finished" into "the PR is green, and mergeable, and here is the failing
check if not".

That reframing is the deliverable. What follows is the cost evidence that stands
either way.

### The polling cost itself (the part the correction narrows)



**Population: the manager session's own report of tonight's work, plus the
mechanism, which is checkable independently of the count.** The manager reports
polling `mcp__github__pull_request_read` dozens of times across six PRs. I did
not observe that transcript and I am not restating the number as my own
measurement. What I *can* establish is that no cheaper option exists: with
`api.github.com` at 403, repeated MCP calls are the **only** way a session can
learn its PR's CI has finished.

**CI duration is bimodal, and the "~15 minute suite" framing needs qualifying.**
`pytest-run.yml` and `pytest-collect.yml` both short-circuit on a *relevant
changed files* gate, so a docs-only PR settles in well under two minutes, while
a PR touching code runs the suite (`timeout-minutes: 30`). Both ends of that
range are polled the same way, so the relay helps either way — it just saves far
more on the second. Stated because a session reading "15 minutes" and seeing a
90-second settle would wrongly conclude the tool is mis-measuring.

**Three more calls collapse into the same payload**, which is most of the value:

- `mergeable_state` — otherwise a separate `pull_request_read` method `get`.
- unresolved review threads / review decision — otherwise another call.
- **the failing job log** — otherwise `get_job_logs`, which returns an entire
  job log. This is the single fattest read in the loop, and it is paid *exactly
  when CI is red*, i.e. when the session is already having a bad cycle.

## 2. Fat MCP reads bought to learn a small fact (FILED, not built)

Both measured in-session, **population = one call each**:

- `actions_list(perPage=20, minimal_output=true)` returned every run's full
  multi-paragraph `head_commit.message` plus two full actor objects per row. The
  wanted payload was five short fields per run. `minimal_output=true` reads as
  having already opted out of the bulk, which is why nobody notices still paying
  for it. → `BL-20260902-ACTIONS-LIST-MINIMAL-OUTPUT-STILL-RETURNS-FULL-COMMIT-MESSAGES`
- `issue_read(get_comments, #6927, page=1)` returned **fifteen comments all
  dated 2026-07-19** — the board's first day. The board read is *mandatory
  before a session's first substantive tool call* and its stated purpose is to
  see who is live; page 1 answers a question nobody asked, and looks exactly
  like a successful read while doing it. → `BL-20260902-BOARD-READS-RETURN-THE-OLDEST-COMMENTS-FIRST`

Neither is built here. Both are the *same shape* as `ci-settled.yml` — push a
request, read one compact result — so both are cheap follow-ons. I did not build
them because I have not measured how often a session needs workflow-run history
or a board tail, and a workflow built on an unmeasured frequency is how this
repo ends up with machinery nothing reads.

## 2b. The wake's coverage is UNMEASURED, and that is its own finding

The event footer names four exclusions verbatim — cancelled suites, **suites
with no runs**, the App's own suites, legacy commit statuses. None has been
tested, by me or by anyone.

Two of them are not academic here:

- **"Suites with no runs"** is exactly the zero-check-runs state, whose usual
  causes are a merge conflict or a bot-pushed head. In those cases **no wake
  arrives at all** — and silence from a push mechanism is indistinguishable from
  *not finished yet*. A session waiting on a wake for a conflicted PR waits
  forever with no signal that this is what is happening. `ci_settled.sh <PR>
  once` returns `conflict` in ~18 seconds instead (run 1).
- **"Cancelled suites"** — this repo runs `cancel-in-progress: true` on its
  required checks, so a superseded push routinely produces exactly the state the
  footer says is not covered.

⚠️ **I did not observe a wake at all this session.** The observation is the
manager's, relayed. I tested none of the exclusions and measured no firing rate.
The claim here is that the coverage is **unknown**, not that it is bad — filed
as `BL-20260902-THE-PR-ACTIVITY-WAKE-IS-RELIED-ON-AND-ITS-COVERAGE-HAS-NEVER-BEEN-MEASURED`
with a test per exclusion.

## 3. Deliberately NOT built

- **A generic "run any GitHub query" relay.** It would be a general API
  primitive reachable from any pushed file. `board-post.yml` hardcodes issue
  #6927 for exactly this reason — a caller-supplied number would turn a narrow
  relay into a general issue-commenting tool. `ci-settled.yml` keeps the same
  discipline: it takes a PR number and reports; it cannot merge, cannot push to
  `main`, cannot open or close anything.
- **A cadence-driven watcher.** Everything here is dispatched by a session and
  runs once. This repo has scheduled workflows that were merged, enabled and
  correct and still did not fire when expected (`probes.yml` fires ~4h50m late,
  once rather than daily), so a cron would have to be *proven* rather than
  *shipped*, and nothing here needs one.

## 4. What "proven" means for the thing that was built

A workflow that looks armed and is not is worse than none, so the dispatch path
was exercised end to end against this PR's own CI — **the same command a caller
would run**, not a shape argument.

**Run 1 — `state: conflict`, settled in 18s, 1 poll.** The relay's very first
live run caught a real merge conflict on this PR. Zero check runs existed, and
the payload said *merge conflict — GitHub builds `pull_request` runs against the
merge ref, so no checks can start until it is resolved*, rather than `no_checks`
or "probably still queued". That is the exact trap `CLAUDE.md` records costing
two sessions ~10 minutes each; here it cost 18 seconds and one command. Exit 1.

**Run 2 — `state: red`, 6 polls on the runner.** After the conflict was
resolved: `guards` failing, `pytest-collect` and `repo-inventory` passing,
`pytest-run` still in progress, `mergeable_state: blocked`, review threads read
(0 unresolved). One payload; the equivalent by hand is a `pull_request_read` per
poll plus a separate `get` for mergeability plus a GraphQL call for threads.

**Run 2 also found a bug in the relay itself, and the state vocabulary is what
surfaced it.** The failing check came back `log_state: "unreadable"` with an
attached `HTTP 401` — GitHub's job-log endpoint redirects off `api.github.com`
and urllib re-sent the bearer to the blob host. Note what did *not* happen: it
did not return an empty `log_tail`, which would have read as *the job failed
quietly*. Because `log_state` is its own field, "we could not look" stayed
distinguishable from "there was nothing to see". Fixed by stripping
`Authorization` on a cross-host redirect.

Additionally the workflow runs `ci_settle.py --self-test` **before** trusting
the grader, because a grader that silently stopped working would report `green`
on a red PR — worse than reporting nothing.

**Run 3 — `state: pending`, `timed_out_waiting: true`, and 51 polls.** This is
the run that carries the headline number. The watcher polled **51 times on the
runner** across its 18-minute window and reported `pending` — correctly refusing
to call it green even though `guards`, `pytest-collect` and `repo-inventory` had
all gone passing, because `pytest-run` was still in flight. *We* stopped waiting;
CI did not.

**Run 4 — `pending` again, 72 polls over 25 minutes.** `pytest-run` was still
in flight ~39 minutes after it was queued. That is not a defect in the relay;
it is the premise. This PR touches Python, so the *relevant changed files* gate
does not short-circuit and the full suite runs — and a session watching it by
hand would have been paying for a fat PR read every few minutes across those 40
minutes to be told *still running*, which is precisely the cost this exists to
remove.

**The cost claim, stated precisely.** A session would not have polled 51 times —
it would have polled perhaps a dozen, each buying a full PR payload. The real
change is the shape, not a ratio: the session's cost is now **two tool calls,
constant, however long CI takes**, where before it scaled with the duration of
the run. 51 is what the *runner* absorbed on one watch.

**Run 5 — `state: green`, `settled: true`, `mergeable_state: clean`, 10 polls,
exit 0.** All four required checks passing on `4f95cfca`. The happy path closes
the loop: the same one command that reported a conflict, then a red with a
failing-check breakdown, then two honest timeouts, returns a clean green when
there is one — and exits 0 so a caller can branch on it.

**Run 6 — `green` checks, `mergeable_state: dirty`, and a bug in the exit code.**
On a later head, all four required checks passed while the base branch had moved
underneath: the payload said *"all checks concluded, none failing — but
mergeable_state is 'dirty': resolve the conflict"* and the script still exited
**0**. The prose was right and the exit code contradicted it, which is the worse
half — a script branches on the code. Exit 0 now requires **green AND not
dirty**; verified against both captured payloads (`green`+`clean` → 0,
`green`+`dirty` → 1).

The general lesson, and the reason it is recorded here rather than quietly
patched: **green checks are not mergeability**, and a tool that reports both must
not let one of them silently speak for the other.

### State ledger — what is proven LIVE, and what is not

| state | evidence |
|---|---|
| `conflict` | **live** — run 1 |
| `red` | **live** — run 2 |
| `pending` | **live** — runs 3 and 4 |
| `green` | **live** — run 5 |
| `unreadable` | **live at the log level only** (the job-log 401); not observed at the PR-read level |
| `cancelled` | unit tests only |
| `no_checks` | unit tests only |

⚠️ Recorded rather than glossed: **a state exercised only in a test is a state
whose live behaviour nobody has seen.** `cancelled` and `no_checks` are both
reachable in normal operation — a superseded push produces the first, a
bot-pushed head the second — so they will be observed in ordinary use; they just
have not been yet. Do not read the four live states as covering the vocabulary.
