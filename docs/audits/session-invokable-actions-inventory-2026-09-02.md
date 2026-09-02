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
   `BL-20260902-DIAG-BASE-URL-STILL-NAMES-THE-TERMINATED-MICRO-…`.
   ⚠️ Scoped to the **live VM** only — the trainer has no HTTP diag surface and
   is genuinely relay-only.

This is why the build is on the CI side and not the diag side, even though
"batched diag pulls" was on the candidate list. The candidate was reasonable and
the measurement disagreed with it.

## 1. PR-CI polling — the anchor case (BUILT)

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

A workflow that looks armed and is not is worse than none. The dispatch path is
demonstrated end to end against this PR's own CI — the run and its payload are
recorded on the PR, not asserted here. Additionally the workflow runs
`ci_settle.py --self-test` **before** trusting the grader, because a grader that
silently stopped working would report `green` on a red PR, which is worse than
reporting nothing.
