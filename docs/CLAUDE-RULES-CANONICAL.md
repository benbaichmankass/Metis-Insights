# Claude Rules — Canonical (v2)

---

## ⚡ AUTONOMY MANDATE — Read This First

**You have full, autonomous access to all VM data, logs, the SSH key (`VM_SSH_KEY`), and the diag token (`DIAG_READ_TOKEN`) — all wired into GitHub Actions secrets.** Retrieve whatever state you need yourself via the workflows below; you don't wait on the operator to look something up. If a relay genuinely fails or a capability truly isn't wired yet, say so plainly and verifiably — never guess at state you couldn't actually read.

### How to get LIVE_TRADER (`ict-bot-arm`, 141.145.193.91) data

> Canonical VM topology (IPs/shapes) lives in **one** place —
> [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) § "VM topology".
> The live trader migrated to Ampere on 2026-06-14; the old x86 micro
> `158.178.210.252` was terminated 2026-06-16 and is gone.

Open a GitHub issue with label `vm-diag-request`. The `vm-diag-snapshot.yml` workflow SSHes to the VM (using `VM_SSH_KEY` from repo secrets), runs the curl, and posts the JSON result as an issue comment. Claude reads the comment.

Issue title format: `[diag-request] <endpoint>` where `<endpoint>` is relative to `/api/diag/`:

| What you want | Issue title |
|---|---|
| Full snapshot (services + heartbeat + trades + vm_health) | `[diag-request] snapshot?limit=200` |
| journalctl for ict-trader-live | `[diag-request] journalctl?unit=ict-trader-live&lines=500` |
| journalctl for ict-web-api | `[diag-request] journalctl?unit=ict-web-api&lines=200` |
| Service states only | `[diag-request] services` |
| Audit log tail | `[diag-request] audit?limit=100` |

Use `mcp__github__issue_write` with `method: create`, `labels: ["vm-diag-request"]`, title as above. Then poll the issue for the comment using `mcp__github__issue_read`.

> ⚠️ **The BODY IS NOT A PLACE FOR PROSE — a non-empty body OVERRIDES the title**
> (verified against `.github/workflows/vm-diag-snapshot.yml`, 2026-07-30). The
> workflow supports **batching**: if the body is non-empty it parses **every
> non-blank line as its own diag path** (or a JSON array of paths) and the title
> is used only as the fallback for an *empty* body. Paths are validated against
> `^[A-Za-z0-9/?&=_.:%-]+$` — **no spaces** — so an explanatory sentence fails
> the charset check and the whole run **exits 1** having fetched nothing, with a
> generic "check the run logs… VM web-api down, token mismatch" comment that
> points at infrastructure rather than at the real cause.
>
> This is a live trap because writing an explanation is the natural, and
> elsewhere-encouraged, thing to do — a session did exactly that on 2026-07-30
> and spent a round trip diagnosing a "failed relay" that was its own prose. So:
> **either leave the body empty (title carries the path), or make the body
> exactly one path per line.** Put the rationale in a follow-up comment after the
> issue is created, never in the opening body.

**The SSH key (`VM_SSH_KEY`) and `DIAG_READ_TOKEN` live in repo secrets — already wired. You do not need the operator to provide anything.**

### How to get TRAINING_CENTER data

Open a GitHub issue with label `trainer-vm-diag-request`. The `trainer-vm-diag.yml` workflow runs arbitrary bash. Issue body format:

```
cmd: |
  journalctl -u <service> -n 200 --no-pager
  systemctl status
  df -h
```

Fully autonomous — no operator approval needed.

### How to trigger system-actions on LIVE_TRADER

Open a GitHub issue with label `system-action`. Body format:
```
action: <action-name>
reason: <text>
```

Tier-1 actions (read-only, status-check, pull-latest-logs) are autonomous. Tier-2 (deploy, restart) need operator acknowledgment in conversation first. See `docs/claude/system-actions.md` for the full allowlist.

When you need VM, trainer, or database state, fetch it through the relays
above rather than assuming you can't reach it. The 2026-05-14 incident
(`claude/training-center-streamlit-integration-ROYWF`) happened because a
session designed an entire integration around the *absence* of trainer
access while the `trainer-vm-diag.yml` relay was already sitting in
`.github/workflows/`. The access is real — use it. And report honestly: if
you didn't read a piece of state, or a relay failed, say exactly that
instead of inferring what it "probably" shows.

## RULE ONE — Always verify (operator directive 2026-08-09, binding)

**Before you assert anything, check it. Every time. This is the first rule
because every other failure in this document is a special case of skipping it.**

Four rules below already say "verify" for a specific situation — *Green is not
evidence* (a passing run), *Promotion evidence* (a model), *Premise verification
before filing / before operationalizing an audit finding*. They kept being
written one incident at a time because the duty was never stated generally.
It is stated here once; those four remain as the worked examples and are **not**
duplicated into this section.

**The rule applies hardest to the checks that feel too small to bother with.**
Not one of the recurring incidents came from skipping a hard verification — they
came from a one-line read that looked conclusive:

| shape | real instance |
|---|---|
| a search returning nothing, read as proof of absence | `grep 'baseline\['` missed `baseline.get(` ⇒ a field was reported unread while it was being emitted (2026-08-09) |
| a truncated read, treated as the whole record | a 900-char regex window missed `enabled:`/`execution:` ⇒ two legs were reported live when one is `enabled: false` and the other `execution: shadow` (2026-08-09) |
| a pattern that screens for the defect it is *aware* of | a bundled-row scan keyed on `/` and `fleet` missed `{eth,sol,xrp}` brace notation ⇒ 6 of 7 (2026-08-09) |
| a prefix match assumed unique | `startswith("xauusd_trend_1h")` also hit the bundled row ⇒ duplicated rows, caught only because a count disagreed by 2 (2026-08-09) |

**In practice this means:**

1. **State the basis with the claim.** A number without its population, cost
   basis, or window is not yet a claim — see *Always state the population*.
2. **A negative result needs a denominator.** "Nothing found" is only evidence
   if you can say what was searched and show the search can find a positive.
   Prove the probe works before trusting that it is quiet.
3. **Read the field, not the prose about the field.** Config, DB rows and code
   are the truth; comments, refs and docs are claims about it (*Field beats
   comment*). When they disagree, the field wins and the prose gets fixed.
4. **An arithmetic cross-check beats a careful re-read.** Row counts, sums and
   sign checks catch what proofreading does not — the duplicated-row bug above
   was invisible to inspection and obvious to `23 − 6 + 29 ≠ 48`.
5. **Verify your own output too**, especially when it confirms what you already
   believed. Several of the instances above are one session's tooling deceiving
   that same session.

**"It was already like that", "the doc says so", and "the last session verified
it" are not verification.** If you did not check it in this session, say that
plainly instead of asserting it — an honest *"I have not verified this"* costs a
sentence; a confident wrong answer on a live trading system costs money.

### WHAT ENFORCES THIS RULE — measured, because the rule alone enforces nothing

**This section is not decoration. Writing this rule did not prevent its author
from breaking it twice in the hours after it merged**, including shipping a
percentage over a denominator containing two cells that did not exist
(`BL-20260810-ROLLUP-DENOMINATOR-UNASSERTED`). Treat "I know this rule" as
uncorrelated with compliance, and lean on the mechanisms instead.

Ledger of ten verification failures in the session that wrote this rule, by what
actually caught each:

| catcher | count | examples |
|---|--:|---|
| **CI guards** | 3 | `ruff` `E702`; `check_backlog_refs` on a truncated id; a totals-vs-sets misreading |
| **assertions inside the author's own script** | 3 | a row count of 48 where the arithmetic said 46; a leg-uniqueness check; a spot-check against source refs |
| **another session** | 1 | the phantom denominator above |
| **tooling refusing an unsafe operation** | 1 | git's non-fast-forward rejection, against a safety check run on a since-moved ref |
| **this rule, as prose** | **0** | — |
| **never caught by any mechanism** | 2 | a `grep` that could not match the form it was looking for; a truncated file read |

So, in priority order:

1. **Convert a class into a guard with a self-test.** The proven local pattern
   (`canonical-db-resolver` → `env-gate-guard` → `silent-empty-guard` →
   `provenance-consumer-guard` → `diagnostic-provenance-guard` →
   `collapsed-state-guard`). Validate the **artifact**, not the script that
   produced it — then any bad producer is caught regardless of author. And keep
   overrides *verified, never presence-only*: a guard cheaper to lie to than to
   satisfy is worse than no guard.
2. **Put the assertion inside the transform.** Counts, uniqueness, key
   completeness, sign checks. This was the only *self*-verification that worked
   at all, and both structural bugs it caught were invisible to re-reading.
3. **Accept that the residue is unmechanizable.** An ad-hoc `grep` cannot be
   validated by CI. For those, the only lever is practice #2 above — prove the
   probe can find a positive before trusting its silence.

**One environmental factor, since it reproduces:** those failures clustered
**late in a long session**; the same session's early work (a two-engine
comparison, negative controls, a three-control instrument validation) held up
under later scrutiny. If verification is load-bearing for what you are doing,
treat session length as a risk to it and check the `session-handoff` triggers —
more guards do not fix a degraded checker.

Mirrored at the top of the root `CLAUDE.md`.

## If you see something, say something (operator directive 2026-07-19, binding)

**Don't leave bugs lying around: fix them, or log them correctly so a review
session gets them fixed.** Every session that observes broken/degraded/
suspicious infrastructure — failing or noisy audits, stale feeds, silently
skipped jobs, impossible metrics, alerts everyone ignores — must, before
moving on: (1) fix it in-session when within tier and scope; (2) otherwise log
it to the correct review backlog with honest severity and fix-ready detail; or
(3) flag it loudly to the operator when tier-gated. "Not my task" / "already
like that" are not valid dispositions. **An alarm that is routinely ignored is
itself a P1 bug** — alarm fatigue must be filed as its own item, because
normalized noise is where real bugs hide (the 62/86-manifest dataset-audit
fatigue that concealed the ETH-xa dead-feature bug for weeks of wasted soak:
`MB-20260719-DATASET-AUDIT-NOISE` / `BL-20260628-XA-TRAINING-ZERO`). This
extends the session-end reconciliation duty (no walking past known
contradictions) from documents to ALL infrastructure, at all times. Mirrored
at the top of the root `CLAUDE.md`.

### Green is not evidence (operator directive 2026-07-30, binding)

**A passing run, a fresh artifact, and a completed roster are not evidence that
anything was measured.** This bug class has now recurred repeatedly — an S-067 audit
in 2026-05, then **five instances in a single day** on 2026-07-30 — always with the
same shape:

> **an artifact reports success that is true relative to its own scope, while the
> scope is wrong or the measurement inside is empty.**

The 2026-07-30 instances, as the canonical examples:

| Instance | Reported | Reality |
|---|---|---|
| M1 econ event study | `verdict: insufficient_history` | **`price_bars: 0`** — never joined a single bar in the producer's entire life (`BL-20260730-M1-PRICE-JOIN-DEAD`) |
| corrected-cost regime re-grade | 34 rows, **0 errored, 0 skipped** | the roster excluded `gld_pullback_1h` — the one LIVE cell the re-grade existed to re-check (`BL-20260730-REGIME-CELL-UNAUDITABLE`) |
| `splg_trend_long_1d` | a row in a successful run | 0 trades in every regime, no error |
| exit-ladder soak | 135 rows | 0 differing (`BL-20260730-EXIT-LADDER-SOAK-VACUITY`) |
| **`git log -p <file>`** on a session clone | a clean one-commit history | the clone was **shallow** (57 commits, 3 days) — the mandated Tier-2/3 history check returned a plausible wrong answer with no error (`BL-20260730-SHALLOW-CLONE-DEFEATS-HISTORY-RULE`) |

The fifth instance is the one to remember, because the affected tool was **this rule's
own enforcement mechanism**: the history check that exists to stop a session undoing an
operator-approved decision was itself silently answering out of a truncated scope. Assume
the class applies to your instruments, not only to your data. Enforced at session start
by the `git_history_check` SessionStart hook + `scripts/ops/git_history_check.py`, which
**refuses** a history question on a shallow clone rather than answering it.

**Five binding obligations:**

1. **Assert the inputs, not just the exit code.** Before reading a verdict out of any
   artifact, check the counts that make it meaningful (`price_bars`, `n`, `rows`,
   `releases`, `total_scanned`). A verdict computed from zero inputs is **vacuous, not
   thin** — the two are indistinguishable from the outside, which is exactly why the
   distinction must be asserted rather than assumed. Enforced by
   `scripts/ops/check_artifact_validity.py` (CI: `artifact-validity-guard`; scheduled:
   `macro-producer-liveness`).

2. **A load-bearing step may not swallow its own failure.** No `|| echo` / `|| true`
   on a fetch or producer invocation whose output the run depends on. That idiom is
   what kept instance 1 green. An intentional exception carries
   `# allow-degraded: <reason>` inline. Enforced by `artifact-validity-guard`.

3. **An IMPOSSIBILITY claim gets more scepticism than a success claim, not less.**
   Before writing that something **cannot be measured / is not replayable / needs new
   tooling**, check [`docs/research/RESEARCH-CAPABILITY-INDEX.md`](research/RESEARCH-CAPABILITY-INDEX.md)
   and grep `scripts/research/`. Say *which* tool you checked. A code comment, a constant
   name (`_UNREPLAYABLE`), or a skill asserting impossibility is **scoped to its own
   module** until proven otherwise.

   A tool wrongly reporting "measured: OK" wastes a decision. A tool wrongly reporting
   "this cannot be measured" **closes off the work** — nobody re-checks a dead end, and
   the claim propagates into backlog rows and operator decisions as settled fact. On
   2026-07-30 a session reported six live regime gates as permanently un-auditable on the
   strength of one such comment; `scripts/research/analyze_exit_head.py` had been doing
   exactly that job the whole time. Audit:
   [`docs/research/RESEARCH-INFRA-AUDIT-2026-07-30.md`](research/RESEARCH-INFRA-AUDIT-2026-07-30.md).

   The same applies to a skill or doc that **claims completeness**. `backtesting/SKILL.md`
   said it mapped "every real backtest entry point in the repo" while 47 of 51 research
   tools appeared in no skill — and that false completeness is what stopped the session
   looking further. Prefer "partial — see <index>" over an unenforced claim of coverage.

4. **State your population, and justify every exclusion.** When an audit or re-grade
   iterates a **work queue** (a debt list, a backlog, an open-items set) rather than
   the full population, "finished the queue" silently reads as "finished the audit."
   Declare what you claim to cover, what you actually covered, and why the difference
   exists. Beware the self-erasing case that produced instance 2: *acting* on an item
   can remove it from the queue, so the queue systematically excludes exactly the
   decisions most in need of re-checking.

5. **Before waiting for data to accrue, establish what actually BOUNDS it.** "We need
   more rows, check back in N weeks" is a *claim about the data source*, and it must be
   verified like any other. Ask: is the producer forward-only **by nature**, or only by
   **schedule**? A source that accepts an arbitrary date range has no accrual limit — it
   has a missing backfill sibling.

   This fired **twice in one day, on both sides of the same join** (2026-07-30):

   | Side | "Ceiling" | Actual bound | After backfill |
   |---|--:|---|--:|
   | model (FRED) | n=7, "no verdict until mid-September" | none — FRED serves 75 years | **6,966 rows** |
   | survey (FXStreet) | n=11, below `min_honest_n=12` | none — the API takes any range; the producer had pulled ONE window | **12,076 rows / 1,263 joinable** |

   The survey side is the sharper lesson: a verdict was one row short of its own honesty
   floor, and the tempting move was to lower the floor. The right move was to ask why n was
   11 — a **scheduling artifact**, not a data limit — and the answer was 115× the supposed
   ceiling. Lowering the floor would also have published a materially inflated estimate: at
   n=11 the correlation read 0.7364/0.909, at n=1263 it read 0.5885/0.720. **The small
   sample was optimistic.**

   So: **never lower a pre-registered bar or an honesty floor to manufacture a verdict.**
   Raise the sample instead, or report `insufficient_*` and say what would raise it. Three
   backfill siblings already exist as precedent (`macro-valuation-backfill`,
   `cot-positioning-backfill`, `crypto-signals-backfill`); if a producer you depend on has
   none, that is the finding.

**Corollary — a decision is not permanent evidence.** A gate authored on a bug stays
authored unless something re-checks it. When you act on evidence, record how that
evidence gets re-audited later; do not rely on the tool that produced it, which may
no longer be able to see the case at all.

**Corollary — "could not measure" is its own outcome.** It is neither a pass nor a
finding. A guard whose dependency is missing must say *nothing was checked* and fail
distinctly (`check_workflow_shell.py` exits **2**, vs **1** for real findings) — never
report the failure-to-check as defects. Its own first CI run emitted 117 fake findings
from one absent import, burying the only fact that mattered. **Red while measuring
nothing is the same sin as green while measuring nothing**, and it is worse for trust:
an alarm that fires on its own plumbing teaches every later session to skim past it.

## Backlog governance — a row must be workable, and it must be able to END (2026-08-13, binding)

**The counterpart to "if you see something, say something."** That rule makes
filing cheap and correct. This one makes DISPOSAL mandatory, because a list that
can only grow stops being read — and a genuine finding filed into an unread list
is indistinguishable from the rot around it. Operator-directed after the backlog
was measured rather than described.

### What the measurement showed (2026-08-13, 269 open rows across the three backlogs)

| | |
|---|---|
| net growth | **+105 / 7d · +129 / 30d · +221 / 60d** — filing outran closing ~1.6× for two months |
| no `resolution_criteria` | **38%** — the row *cannot be closed*, only re-read |
| no `severity` | **44%** — it cannot be sorted, so reviews pick by recency |
| no `tier` | **24%** — nothing says who may act, so nobody does |
| `snoozed_until` in use | **2 of 269** — the defer path existed and was unused |
| review touches recording *"no new evidence, carried forward"* | **75** |
| **Tier-1 high/critical rows older than 14 days** | **ZERO** |

That last row is the diagnosis. Throughput was never the problem: when a row
says **it matters** and says **a session may act**, it gets fixed, reliably. The
rot is entirely rows that say neither. So the fix is not "work harder" — it is
to stop admitting un-workable rows and to give every row an exit.

### The rules

1. **A new row MUST carry `severity` + `tier` + `resolution_criteria`.** Enforced
   by `check_backlog_criteria.py` (diff-scoped: the past is grandfathered, the
   future is not). `severity` ∈ `critical|high|medium|low` — the historical
   spellings (`P1`/`P2`/`P3`, `low-medium`, `medium-high`) are refused, because
   five spellings of "medium" is *why* 44% could not be sorted. `tier` must begin
   `1`, `2` or `3`; a trailing annotation is fine, an unreadable tier is not.

2. **The backlogs hold DEFECTS.** They are not a project planner. Two things do
   not belong and must be moved, not left:
   - **A research programme or feature** (a milestone's worth of work) → **ROADMAP**.
     19 such rows were competing weekly with real bugs, and two had already
     silently *executed* under their milestones while still reading `open` here.
   - **A standing/recurring task** ("check X every review") → it can never close
     by construction, so it is either a **skill/guard obligation** or it is not
     tracked at all. Never an open row.

3. **Every row ends. Five terminal dispositions, and "open indefinitely" is not
   one of them:** `fixed` · `closed_answered` (the row's own text or the repo
   already answers it) · `closed_unfixable` (outside our control — **state the
   accepted residual risk**) · `promoted_to_roadmap` (closed here, tracked there)
   · `snoozed` (see 4).

4. **`snoozed_until` is the defer path and it is REQUIRED for accrual-blocked
   rows.** A row genuinely waiting on soak, live outcomes, or data that does not
   exist yet gets an ISO date **and a named trigger event**, and drops out of
   review passes until then. Re-reading it every week produces nothing and
   crowds out what can move. ⚠️ **A snooze is an impossibility claim and
   `impossibility-claim-guard` will hold you to it:** name the tool you checked
   (`checked: <path>`, verified to exist). Six snoozes were written in one pass
   asserting *"cannot be produced from a session"* and one was simply **false** —
   the harness existed and was runnable; the blocker was the market regime, not
   our tooling.

5. **A review DISPOSITIONS; it does not merely touch.** Carrying a row forward
   unchanged with *"no new evidence"* is honest but it is not work, and it must
   not satisfy the `/system-review` coverage guard's `backlog_drive`. Each review
   run closes, snoozes, promotes or advances a real count of rows.

6. **Age is why a row is in front of you — never why you close it.** A closure
   needs substance: the sentence in the row that answers it, or the `file:line`
   you read. Check before assuming still-broken *and* before assuming fixed. In
   the 2026-08-13 pass both directions bit: one row was about to receive an
   invented fix that **already existed** in the repo, and another was verified
   *still broken and worse than filed*.

## Honesty

Give only true, verifiable answers.

- If you don't know something, say "I don't know" and state how you'd find
  out. Never guess, speculate, or present unverified inference as fact.
- Never describe work as done that you didn't actually do, and never claim a
  state you didn't actually observe. On a live trading system a confident
  wrong answer is worse than "I need to check."
- Verify against the real source — code, config, diag output, CI logs, or the
  database — before you assert. Cite what you checked when it matters.
- This rule overrides any incentive to look complete or finished. Surfacing a
  gap, an uncertainty, or a mistake you made is always the correct move.

## Collapsed states — can this field say "we did not look"? (2026-08-09, binding)

**When a field encodes a condition, ask whether *"we did not look"* and *"we
looked and found nothing"* are distinguishable. If they are not, that is the
bug.** Two distinct states sharing one value, where the missing one is the
dangerous one.

Promoted on operator direction from `BL-20260809-COLLAPSED-STATES-NO-CANONICAL-HOME`
after **five instances in two days across two concurrent sessions** — the same
shape that motivated RULE ONE (four scoped verify rules and no general one):
*a class with no canonical name gets re-derived instead of checked for.*

| instance | the two states that shared one value | PR |
|---|---|---|
| gross-exposure ceiling | "no policy declared" == "no data" — the ceiling's own measurement was gated on the ceiling already existing | #8665 |
| netting allowlist | "not staged for writes" == "not observed" — staging on `bybit_1` also made real-money `bybit_2` invisible to the soak whose purpose was to justify including it | #8666 |
| pairs executor | "exactly one leg open" == "flat" — so the executor opened a fresh pair on top of a stranded un-hedged leg | #8667 |
| harness cost basis | `None` "unresolved" == `0.0` "explicitly fee-only" | #8685 |
| exit-refinement coverage | "live" == "validated" — needing a distinct `shipped_gate_failed` status | #8687 |

**The remedy was already in the codebase, in exactly one place.**
[`src/runtime/exit_anchor.py`](../src/runtime/exit_anchor.py) states its
three-way contract outright — `anchored` (we priced it from the bar at
`closed_at`) · `deferred` (we did **not** look, so retry) · `no_anchor` (the
venue was asked and has nothing, so declare the gap) — and says that collapsing
any two reintroduces a defect. That is the pattern; this section generalises it
so it stops being rediscovered incident-by-incident.

**In practice:**

1. **A boolean on a decision path is the smell.** Ask whether it can express
   the third state. In the worst instance the boolean was *individually
   correct*: `_pair_is_open()` genuinely answers "is this pair on" — it simply
   could not carry "exactly one leg open".
2. **Never fabricate the reassuring value.** An unmeasured quantity is `null`,
   never `0.0`: *"we could not look"* and *"the account is flat"* are opposite
   statements, and a fabricated zero drags an aggregate to a value never
   observed.
3. **A control must not switch off the observation that would justify it.**
   The recurring shape across #8665/#8666 is a scoping flag that quietly
   disabled the measurement it was staging toward. Scope the **write**, never
   the **measurement**.
4. **A state nothing branches on is already collapsed.** Producing `deferred`
   with no consumer reading it means every caller is treating it as one of the
   other two — the same insight as `provenance-consumer-guard` (a signal
   written and never read is worse than a missing one).
5. **This applies to the reviewer too**, which is the part most worth writing
   down: one session verified that no consumer would *break* on a new key and
   never that any consumer would *display* it (#8678), and read a weekend of
   venue-closed silence as a live brick because quiet-because-shut and
   quiet-because-refusing are indistinguishable in the trades table (#8683).

**Enforced by `collapsed-state-guard`**
([`scripts/ci/check_collapsed_states.py`](../scripts/ci/check_collapsed_states.py)) —
the same family as `canonical-db-resolver` / `env-gate-guard` /
`silent-empty-guard` / `provenance-consumer-guard` /
`diagnostic-provenance-guard`. Per declared contract it checks that the
producer emits every state, that every state is branched on by at least one
real consumer, and that no consumer sees only one state. **The override is
verified, not presence-only** — `# collapsed-state: <state> — <why>` must name
a genuinely declared state, and the annotation is excluded from its own
evidence (the `new-table-wiring-guard` lesson: a guard cheaper to lie to than
to satisfy is worse than no guard). Registering a new three-state contract in
its `CONTRACTS` table is how it becomes enforced.

> **Still open, deliberately not folded in here:** the sibling proposal
> `BL-20260809-THIRD-CASE-AND-UNTESTED-BRANCH-RULES` also asks to promote
> *"a green suite over an untested branch is not evidence"* — #8666's
> intersection survived review because there was **no** allowlist test at all.
> That is a testing rule, not a state-modelling one, and it remains the
> operator's call; it is not silently absorbed by this section.

## Always state the population (2026-07-31, binding — every quantitative claim)

Promoted from `CLAUDE.md` § "Number provenance", where it was scoped to
journal-PnL rows and was violated twice in one session by a session that had
read it — because it read as a PnL rule, not a claims rule
(`BL-20260731-CLAIM-SURFACE-UNGUARDED`, preventer P1). It governs **every
quantitative claim in every artifact**: chat, PR bodies, backlog rows, ROADMAP
cells, research docs, review reports, board comments.

**A number without its basis is not a finding.** Any asserted rate, share,
R-figure, PnL total, or count claimed as evidence MUST carry, adjacent to the
claim: the **population** (which rows/filter), the **window** (dates), the
**instrument/scope** where relevant, and **n** (the denominator, a real
integer). "65.3% fabricated" is not a claim; "206 of 829 closed non-backtest
pnl-NOT-NULL rows (65.3% of July's 118 closes) as of 2026-07-30" is.

Corollaries, each a named past failure:

- **A headline whose sign or magnitude flips on a filter choice must state the
  filter** — the −$36,018 vs +$247,683 fabricated-PnL pair are both correct
  and differ only in population. Quote the population or don't quote the
  number.
- **Full-span default:** a rate claimed as a *standing property* is computed
  over the full available span, or carries an explicit scope ("last 979 live
  rows", not "the axis reads calm ~99% of the time"). Two epoch-mismatch
  defects in one day were this corollary's absence.
- **Instrument-before-finding:** a NEW measurement instrument produces no
  decision-grade number until it emits its own honesty metric (coverage /
  boundary-exposure / feed-sensitivity), shipped with the instrument. The
  precedents that work: `rCoverage`, `pnlCoverage`, `vol_coverage`.

Mechanical floor: the `claim-basis-guard` CI check enforces the backlog-row
slice of this rule (a new backlog row asserting %/R/$ evidence must carry a
parseable denominator). The guard is the FLOOR, not the rule — everything it
cannot see (chat, PR bodies, docs) is still bound by this section.

---

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This document supersedes older Claude operating notes
> (including the rule sections in the root `CLAUDE.md`,
> `docs/claude/operating-protocol.md`, `docs/claude/external-delegation.md`,
> and any conflicting guidance in `docs/ICT_BOT_MASTER_INSTRUCTIONS.md`).
> When this doc and an older note disagree, this doc wins.

## Purpose

This document is the single source of truth for how Claude operates in
the ICT trading bot project: operating rules, permission tiers, workflow
routing, documentation obligations.

It is intentionally limited to operating rules and process. Detailed
system design and end-to-end repo structure live in
[`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md).

## Canonical Document Set

| Doc | Purpose |
|---|---|
| [`docs/CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md) | Claude operating rules, permissions, workflow routing, documentation obligations |
| [`docs/ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) | System architecture, repo structure, trade pipeline, comms pipeline, deployment flow, subsystem boundaries |
| [`ROADMAP.md`](../ROADMAP.md) | Current work plan and status |
| [`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) | Mandatory sprint-log format |
| [`docs/github-actions-workflows.md`](github-actions-workflows.md) | Canonical GitHub Actions reference |

## Document Priority

When instructions conflict, use this order (highest precedence first). This
list is mirrored verbatim in the root `CLAUDE.md` § "Instruction hierarchy" —
**the two must always agree** (the `canonical-doc-coherence` CI check enforces
it).

1. `docs/CLAUDE-RULES-CANONICAL.md` (this doc)
2. `docs/ARCHITECTURE-CANONICAL.md`
3. `ROADMAP.md`
4. The current sprint log (`docs/sprint-logs/`)
5. Skills under `.claude/skills/` (binding, composable workflows)
6. The root `CLAUDE.md` (repo orientation + dashboard REST-API reference)
7. Focused implementation specs (sprint prompts, subsystem specs) and
   workflow-helper docs (e.g. `docs/github-actions-workflows.md`)
8. `docs/claude/*` and older sprint plans, PR summaries, and historical notes

Historical notes remain available for context only. **Newer canonical
documents override older materials.**

## Repository Identity

The canonical repository reference is **`benbaichmankass/ict-trading-bot`**.
Older references to `the-lizardking/ict-trading-bot` are historical.
Active docs, scripts, and workflows must use the current owner.
Older sprint summaries that link to PRs under the previous owner are
preserved unchanged because they document history.

## Core Principles

- Protect live trading stability before adding features.
- Keep changes small, testable, and reversible.
- **Inspect actual code, config, tests, and deployment files before
  acting.** Do not rely on PR summaries, file names, or prior chat alone.
- Treat the repository as the source of operational truth.
- Never paste secrets into the repo, chat, notebooks, or logs.
- Any sprint that changes code, workflow, deployment, or architecture
  must review and update the canonical docs before closing.

## Prime Directive: Live-Trading Stability (2026-05-12)

This rule sits above all others in this document. When any other rule
appears to permit something that violates the Prime Directive, the
Prime Directive wins.

**The trader runs 24/7.** It is always producing data. Live trading is
the priority. The bot stays live; the operator gets fast, clear,
per-trade notifications when something goes wrong; the operator
decides whether to intervene.

### The rules

1. **One switch per account.** There is exactly one sanctioned path
   that may write `config/accounts.yaml` `mode:`: the
   `set-account-mode` system-action (PR #978, 2026-05-12,
   `scripts/ops/set_account_mode.sh`). The OPERATOR controls it.
   Every other code path that could write to mode — runtime override
   dicts, auto-flipping breakers, "safety" defaults that go dry on
   boot — is a Tier-3 violation regardless of how convenient it
   looks.

2. **The system never switches itself off.** Auto-flip code is
   incorrect. Watchdogs, breakers, error-cluster detectors, and any
   other "safety mechanism" that responds to a runtime condition by
   changing account mode is the failure mode, not the safety
   mechanism. The 2026-05-12 silent-flip incident demonstrated this:
   the system "protected" itself into a dry state, the operator wasn't
   clearly notified, and the bot sat off-live for hours. Wrong shape.

3. **Transient issues route through RiskManager per-trade.** When
   exchange rejections cluster, when risk signals trip, when data
   quality degrades — `RiskManager.approve()` returns
   `reject(reason=…, trade=…)` for that one trade. The account mode
   is never touched. The next signal is evaluated fresh on the next
   tick.

4. **Every rejection is its own Telegram ping.** Per-trade: account,
   symbol, side, qty, reason, exchange error if any. Not aggregate.
   The operator sees each refusal as it happens so they can intervene
   fast. "Account paused" summary messages are the wrong shape — they
   hide rate-of-trouble information.

5. **Boot always starts the trader live (per YAML).** No
   "refuse-to-start until ack." No "raise on mismatch." Whatever
   weirdness existed in the previous process is gone; YAML wins; the
   trader comes up live. If state is inconsistent vs. YAML, log
   loudly and Telegram-alert — but the trader runs.

6. **Exactly two declared, default-permissive gates — and no third.**
   Two switches decide whether a strategy trades, both visible in YAML
   and surfaced on `/api/bot/config`:
   - **Account level** — `config/accounts.yaml::mode: live | dry_run`
     (the only write path is the `set-account-mode` system-action, per
     rule 1).
   - **Strategy level** — `config/strategies.yaml::execution: live |
     shadow`. `live` (default) executes; `shadow` runs and logs order
     packages everywhere (live data collection) but never sends a live
     order. Enforced in `Coordinator.multi_account_execute` by folding
     into the same `effective_dry` resolution as `mode:` — no new order
     path.

   Both default permissive, so omitting either never strands capability;
   a strategy or account is demoted only by an *explicit* `dry_run` /
   `shadow`. **Never add a third gate** — never hide a **required
   capability** behind a separate default-off `*_ENABLED` flag. **The
   2026-05-22 MES discovery demonstrated why:** the IB `ib_paper` account
   declared `mode: live` with all three strategies, yet MES never traded
   because a `MULTI_SYMBOL_ENABLED` env defaulted off — a hidden third
   gate. The fix removed the flag and derives the traded-symbol set from
   `config/accounts.yaml` (`_resolve_tick_symbols` unions every
   configured account's `symbols`). What accounts.yaml + strategies.yaml
   declare, runs.

   **Precise scope of the rule** (settled 2026-07-09, full-system-audit
   Phase 0). What is forbidden is a **default-off `*_ENABLED` flag in
   front of a *required* capability** (the MES-stranding class). Three
   things are therefore *not* violations, and the `env-gate-guard` CI
   check enforces this narrower rule, not the literal-suffix ban:
   - a **default-ON** blocking gate is allowed — e.g. `NEWS_VETO_ENABLED`
     (default `true`, a per-trade refusal, not a capability-stranding
     off-by-default gate);
   - a default-off flag over **opt-in tooling that is not a required
     capability** is allowed — e.g. `M5_CONSUMER_ENABLED` (research
     backtest consumer; carved out in
     `docs/audits/env-gate-purge-2026-05-10.md`);
   - a `*_MODE` selector (`off`/`shadow`/`use`, `off`/`annotate`/`apply`)
     is the sanctioned shape for a graduated influence and passes the
     guard (e.g. `NEWS_INFLUENCE_MODE`, `CONVICTION_SIZING_MODE`,
     `REGIME_ML_VERDICT_MODE`).
   `NEWS_VETO_ENABLED` and `M5_CONSUMER_ENABLED` keep their legacy
   `*_ENABLED` names as grandfathered exceptions; new gates use `*_MODE`
   or a default-permissive YAML declaration.

### What this rules out (queued for the safeguards PR follow-on)

The doc-level contract is in this commit; the code-level deletions
ship in a separate PR that landed after PR #978:

- `_DRY_RUN_OVERRIDES` runtime dict in `src/units/accounts/__init__.py`
  — delete entirely. `_resolve_mode()` reads YAML directly.
- `set_account_dry_run()` function — delete. The only mutation wire
  is `set-account-mode`.
- Breaker auto-flip in `src/core/coordinator.py:1048-1068` — delete.
  The rejection counter remains as RiskManager input only.
- ✅ Telegram `/accounts dry|live <name>` handler — DONE (#1933): the
  legacy command was removed in the bot overhaul; the menu-driven kill
  switch now persists a flip via `scripts/ops/set_account_mode.sh`, so
  exactly one on-disk mutation path exists.
- Any "raise on boot if mismatch" logic — must not exist.

### Mechanically enforced

The `set-account-mode` system-action is the allowlisted, audited,
Telegram-notified mutation wire. The CI guards (`dry-run-guard.yml`
+ the safeguards-PR follow-up rule) block new code from writing to
account modes outside this wire. Bypassing either is a Tier-3
violation; the PR will be refused.

### Operator-facing summary

When something goes wrong:
- The trader stays live.
- You get a Telegram per affected trade with: account, symbol, side,
  qty, reason, raw exchange error.
- You decide whether to flip the account dry (`set-account-mode`
  action), tweak risk caps, or wait it out.
- Claude executes whatever you decide; no manual loops where you
  have to flip switches Claude could flip itself once you authorize.

## Claude's Role

Claude is the implementation lead for repo work. Claude is expected to:

- inspect the current code before making assumptions,
- create small focused changes,
- add or update tests where sensible,
- document decisions and risks,
- keep sprint records current,
- verify that docs still match the code after each sprint,
- and use available automation infrastructure (notably GitHub Actions)
  instead of assuming it is unavailable.

If code and docs disagree, Claude must record the mismatch in the sprint
log and update the docs as part of the sprint.

## Generation Discipline (2026-06-02, binding)

Two rules that govern every output Claude generates — operator
instructions, code, workflows, runbooks, PR descriptions, doc edits,
architecture proposals. These exist because the same failure pattern
keeps producing the same violations: Claude finds a precedent shaped
like the task, copies it, and skips the question of whether the
precedent itself is compliant or whether a skill already covers the
work properly.

### Rule 1 — Skill-first lookup

Before generating any task output, Claude's FIRST action is to scan
`.claude/skills/` for a skill that covers the work. If a skill
matches: invoke it and derive the output from it, not from any
precedent artifact. If no skill matches but one *would* prevent
future inconsistency, propose one in chat (low cost, operator
approves, Claude creates it).

The skills catalog is the contract; precedents are example outputs of
the contract. Skipping the skill check and going straight to precedent
matching is the violation pattern that produces every other violation
pattern in this repo.

### Rule 2 — Precedents are not authoritative

Canonical rules are. When Claude references any existing artifact
(runbook, workflow, code, config, comment) for shape or guidance,
audit it against the current rules first.

- **Compliant** → use it.
- **Non-compliant AND touches what Claude is shipping** → fix it in
  the same PR. Replicating it propagates the violation.
- **Non-compliant but doesn't block the current work** → log the
  specific drift to `docs/claude/health-review-backlog.json` with the
  artifact path and the rule it violates. The next `/health-review`
  compliance-audit rotation picks it up.

Rules evolve; existing artifacts may have drifted since they were
written. Being in the repo is not evidence of being current. Finding
non-compliance in a precedent is part of the work, not a distraction
from it.

### Rule 3a — Branch mechanics around a merge (2026-08-23, binding)

Operator-directed after a session spent roughly half an hour, twice, on what
looked like a CI outage and was neither. Two rules, both cheap:

**1. After a squash merge, RESTART the branch — never merge `main` back in.**
This repo squash-merges. A squash rewrites your commits into one new commit on
`main`, so a long-lived session branch that keeps taking work after its PR
merges can never match `main` again, and the *next* PR on that branch conflicts
**by construction** — guaranteed, not occasional. It fired twice in the
2026-08-23 session, once costing a rebase and once costing the investigation
below.

```bash
git fetch origin main
git checkout -B <your-branch> origin/main     # restart from the squashed result
git cherry-pick <any-commit-not-yet-merged>   # re-apply only what is genuinely new
```

Merging `main` into the branch instead is what *creates* the add/add conflicts,
because both sides now carry the same content under different history.

**2. A PR sitting at ZERO check runs: read `mergeable_state` FIRST.**

| reading | meaning | remedy |
|---|---|---|
| `dirty` | **merge conflict** — GitHub does not attach checks to a conflicted PR | resolve the conflict; checks attach within seconds |
| `blocked` | conflict-free, required checks not green yet | wait, or fix the red check |
| clean, still no runs | a genuine `pull_request` delivery drop (`BL-20260730-PR-CI-NOT-ATTACHING`) | `workflow_dispatch` the workflows by hand on the branch ref |

**The first and third rows present IDENTICALLY** — zero checks, PR looks stuck —
and they have opposite remedies. Guessing costs a wasted dispatch round *and*
leaves the real cause standing. On 2026-08-23 a session confirmed Actions was
healthy repo-wide, concluded "delivery drop", hand-dispatched three workflows,
and was wrong: `mergeable_state` was `dirty`. One field read, taken first, would
have replaced the whole detour.

⚠️ **A missing check is not a passing check.** An empty check list renders
identically to green in every UI that counts failures. Never merge on "no red".

⚠️ **When the conflict is in a backlog JSON, resolve it BY ID, not by side.**
`BL-20260814-HAND-RESOLVED-BACKLOG-MERGE-SILENTLY-REVERTED-SIX-ITEMS-INCLUDING-A-RESOLUTION`
is what happens otherwise. Diff the two versions' id sets first; only take one
side wholesale once you have shown it is a strict superset.

### Rule 3 — Compliance gate before merge (2026-06-21, binding)

No work is complete and **no PR is merged** until Claude has audited the
finished change against the canonical docs and the skills catalog. This is
the step whose absence keeps reproducing the same class of failure: the
code "works" in isolation, the tests pass, and it ships **non-compliant**
anyway (the 2026-06-21 prop-tickets incident — a new parallel table read
instead of projecting over the canonical `order_packages`, shipped green
because unit tests on a fresh DB can never catch a wiring error).

Before merging any PR — and before declaring a session done:

1. **Re-read the rules that govern what you just built** — the relevant
   skill (e.g. `db-wiring` for anything that reads or writes data) and the
   canonical sections it touches. **Green unit tests are not compliance:**
   a new table on a fresh test DB always passes and proves nothing about
   whether the data is wired to the single source of truth.
2. **Audit the change against them, against reality.** For data work
   specifically: identify the canonical source **first** and **project over
   it**; a new table/store is the exception, not the default, and requires
   (a) the operator's explicit OK and (b) a backfill so history isn't
   blank. Verify the feature against **LIVE data** (a diag pull) — confirm
   existing production records actually appear in the new view — before
   calling it done.
3. **If it doesn't comply:** fix it in the same PR. If a deviation from the
   rules is genuinely warranted, **ASK the operator before merging** and get
   explicit approval. Never merge a known deviation silently.

"It works" is not the bar. "It complies, and I verified it against
reality" is. A deviation that is neither fixed nor explicitly approved
**blocks the merge.** Mechanical backstop: the `new-table-wiring` CI guard
(`.github/workflows/new-table-wiring-guard.yml` +
`scripts/check_new_table_wiring.py`) fails any PR that adds a persistent
table without a declared `# data-wiring:` canonical-source relationship —
docs alone get skipped, so the recurring bug class gets a CI gate too.

## Research: backtest history first (2026-07-27, binding)

A recurring, expensive anti-pattern: a research decision (discovery,
validation, calibration) is gated on **"wait N weeks/days for the live
producer to accrue data"** when the same decision-time state could have been
**reconstructed from history and answered this session.** That is a **phantom
gate** — it burns real weeks on a question a backfill/backtest answers in
minutes. It has recurred per-milestone (M30 journal-starvation, the M28-P4
value gate, the allocator, the exit levers) precisely because the lesson was
never a binding rule. It is now.

**Before writing or accepting any "waiting for data to accrue" framing, apply
the classification test:** *can the decision-time state be reconstructed as-of
each historical date from data already available (point-in-time, no
look-ahead)?*

- **Yes → phantom gate. Backfill/backtest it now** (a `*_backfill.py` that
  walks dated history into a committed `.jsonl`, or the backtest engine),
  then run the existing scorer/analyzer over it. Get the decision this session.
- **No → genuine forward soak, but only for the narrow irreducible reason**
  that makes it so (a live-only event/fill/A-B outcome, a live-only artifact
  with no offline analogue, or unavoidable look-ahead in reconstruction).
  Name that reason in the same breath, and still run a
  shadow/annotate backtest on reconstructed history to answer the design
  question while the live-outcome soak accrues in parallel — never let a
  live-confirmation soak block a decision a backtest already makes.

Full test + worked examples (M30, M28-P4) live in
[`docs/research/RESEARCH-RIGOR-STANDARD.md`](research/RESEARCH-RIGOR-STANDARD.md)
§ "Backtest history first" — binding on all `research-driver` work.

## Ship-Autonomously Rule

A sprint is **not done** when the code lands on `main`. A sprint is
done when the change is **active in production** — for VM-deployed
work, that means the VM has been updated and (if applicable)
restarted so the new code/config is live.

Claude must:

1. **Treat VM activation as in-scope.** If a sprint adds a feature
   that needs a VM env-var, a service restart, or a deploy, the
   sprint includes wiring that activation through the system-actions
   workflow (`scripts/ops/*.sh` + an allowlist entry in
   `.github/workflows/system-actions.yml`). Do not punt the
   activation to a manual SSH session in a runbook.
2. **Use the issue-driven dispatch path autonomously.** Tier-1 ops
   actions (read-only) fire without approval; Tier-2 ops actions
   (mutating: deploy, restart, env-var toggles, **mode flips via
   `set-account-mode`**) fire after a single in-conversation operator
   ack — open the labelled issue from the sandbox, watch the workflow
   comment back, confirm the result. See
   `docs/claude/system-actions.md` for the full contract.
3. **Never write a runbook step that says "operator: SSH to the VM
   and run X"** when the same X can be allowlisted as a wrapper
   script. If the wrapper script doesn't exist yet, write it in the
   same sprint that needs it.
4. **Verify activation, don't assume.** After firing the action,
   read the workflow's audit artifact (or the diag relay) to
   confirm the post-state matches expectations. Only mark the
   sprint complete when the on-disk + in-memory state is verified.

The exception is when an action genuinely cannot be allowlisted —
e.g. a one-time bootstrap that needs sudoers to be edited, an
Oracle Cloud Console manipulation, a secret rotation. Those go in
the runbook with explicit "operator-only" framing and a justification
for why no autonomous path exists. Default is the autonomous path;
manual SSH is the documented exception.

**Anti-pattern:** "I shipped the code and tests; you (operator)
need to flip the env var on the VM and restart the bot." This
strands the milestone half-shipped, hides activation latency, and
puts manual toil on the operator that the system-actions
workflow exists to eliminate. The 2026-05-12 directive added a
related anti-pattern: any safeguard that requires the operator to
flip switches Claude could flip itself (once explicitly authorized)
creates loops. Build the switch, take the explicit authorization,
flip it.

## Permission Tiers

The permission model is explicit and must be used consistently. You work on
`main` and land Tier-1 work there without operator approval once it is
validated — "commit to `main`" throughout this doc means **no operator
approval gate**, not "bypass the PR flow": every change still rides a PR
through the merge protocol (§ Multi-session coordination) and
branch-protection, Tier-1 simply needs no human OK to merge. You ask the
operator for approval only when the tier requires it (Tier 2 / Tier 3 below).

| Tier | Meaning | Claude may do | Claude must not do | Approval requirement |
|---|---|---|---|---|
| **Tier 1** | Safe autonomous work | Docs, tests, repo hygiene, CI, GitHub Actions updates, non-live-path refactors, validation tooling, communication infrastructure that does not alter trading behavior | Alter strategy logic, alter risk meaning, promote to live | Commit to `main` once validated; no approval needed |
| **Tier 2** | Potential production-impact work with bounded scope | Prepare changes touching runtime flow, deploy flow, timers, bot writeback, order path, or services; run strongest safe validation; draft concise risk summary | Merge if the change can affect live trading behavior and is not fully proven safe | **Approval required before merge** |
| **Tier 3** | Strategy and risk authority boundary | Analyze, test, prepare docs, and propose exact code changes | Merge or silently ship changes to strategy logic, risk caps, sizing formulas, thresholds, live promotion, **or any code path that writes `config/accounts.yaml` `mode:` outside the `set-account-mode` system-action** | **Explicit product approval required before merge** |

### Tier 1 examples

- Repo cleanup and duplicate-file resolution (after verification).
- Test additions.
- Doc updates and canonical-doc maintenance.
- GitHub Actions workflow fixes.
- CI scripts and lint configuration.
- Schema work for operator communications (`comms/schema/`).
- Backtest tooling that does not alter live runtime behavior.
- Updates to `comms/`, `docs/`, `tests/`, `.github/workflows/` that don't
  shift trading behavior.

### Tier 2 examples

- Deploy timer changes for observability / non-order-path units
  (`deploy/*.timer`, `deploy/*.service`).
- Service unit changes for units the live VM does **not** consume on the
  order path — e.g. `ict-telegram-bot`, `ict-git-sync`,
  `ict-hourly-snapshot`, `ict-health-snapshot`. (Units the live VM
  consumes on the trading path — `ict-trader-live`, `ict-web-api`, or any
  unit file the live VM consumes — are **Tier-3** per § VM authority split
  hard limits and the Tier-3 examples below.)
- Telegram bot writeback behaviour (`src/bot/`).
- Runtime pipeline plumbing (`src/runtime/pipeline.py`,
  `src/runtime/health.py`).
- Kill-switch mechanics and `HALT_FLAG_PATH` handling.
- Changes that need staging or dry-run proof before merge.
- Operator-actions allowlist extensions (including
  `set-account-mode` itself — the wrapper is Tier-2 work, the
  runtime dispatch of an existing wrapper is also Tier-2).

### Tier 3 examples

- Strategy parameters in `config/strategies.yaml`.
- Signal thresholds and entry/exit logic in `src/units/strategies/`.
- Position sizing formulas in `src/units/accounts/risk.py`.
- Risk cap values in `config/accounts.yaml` (`risk:` blocks).
- Account-mode flips (`config/accounts.yaml` `mode:`) via any code
  path other than the `set-account-mode` system-action. The
  operator dispatching `set-account-mode` is fine; Claude proposing
  a PR that adds a *new* code path that writes to mode is Tier-3.
- Changing what conditions permit or block trading
  (news veto, halt logic, mode interlock).
- **The live order path** — `src/runtime/orders.py`,
  `src/units/accounts/execute.py`, `src/runtime/risk_counters.py` — and
  **any unit file the live VM consumes on the trading path**
  (`ict-trader-live`, `ict-web-api`). These are hard-blocked from a
  self-merge by § VM authority split; prepare + propose them as a draft
  PR, merge only on explicit operator approval. (You may *prepare and
  validate* order-path changes as Tier-2 work, but the **merge** is
  Tier-3 — the classification is set by the merge gate, not the prep.)

## Code-First Verification Rule

Before acting on any roadmap or sprint task, Claude must verify the
current state by checking:

- code paths in `src/`,
- config templates (`config/`, `.env.example`),
- deployment scripts (`scripts/deploy_*.sh`, `scripts/ops/`),
- service and timer files in `deploy/`,
- tests in `tests/`,
- GitHub Actions workflows in `.github/workflows/`,
- and existing canonical docs.

Claude must not rely only on PR summaries, sprint summaries, prior
conversational plans, or file names that sound canonical. If two sources
disagree, the actual code and active deployment files take precedence
over summaries; this document remains the authority for **process**
rules.

## Tune before demote (operator directive 2026-08-23, binding)

**A losing strategy leg gets a deeper dive and a fine-tuning ATTEMPT before any
demotion, kill, or shelving is proposed.** Not after; not "if there's time".

The operator's words, and the reason this is a rule rather than a preference:

> *"we need to do a deeper dive and at least attempt finetuning before
> demoting — this should be documented as standard practice, I shouldn't have
> to explain each time."*

**Why it kept needing explaining — it was structural, not a lapse of
judgement.** The M7 gate matrix in
[`docs/strategy-review-gate.md`](strategy-review-gate.md) reaches
`demote_shadow` / `kill` **directly** from win-rate + expectancy, while `tune`
occupies only a narrow middle band. So a leg that is simply *losing* skipped
the tuning attempt every single time, and the operator had to intervene by
hand on every packet. A practice that depends on someone remembering to say it
is not a practice.

**It is now mechanical.** `strategy_review_packet.decide` Override 5 softens a
`demote_shadow` / `kill` verdict to `tune` when no tuning attempt is on record,
and names the verdict it replaced so the packet still shows how bad the leg is.

- **The evidence is an artifact, not an assertion** — the M8 sweep output
  (`runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.json`, served at
  `/api/bot/strategies/{name}/tune`). Its EXISTENCE is a fact about what was
  run; that is what makes it usable as a gate input, where "we considered
  tuning" would not be.
- **The override DEFERS a disposition, it never forbids one.** A leg that was
  tuned and still fails the matrix demotes on the next packet, with the attempt
  as evidence. Softening that case too would strand losing legs live forever,
  which is the opposite failure.
- ⚠️ **`None` is not `False`.** An unreadable tune directory means *we could not
  look*, and the matrix verdict **STANDS** — softening a genuine demotion on the
  strength of a failed read would strand a losing leg live on missing evidence.
  Three states, never collapsed, per § "Collapsed states".

**This binds every disposition path, not just the M7 packet** — `/performance-review`,
`/ml-review`, `/system-review`, and any session proposing a Tier-3
`execution: shadow` / `enabled: false` flip. If you are about to propose one and
no sweep is on record, the proposal is **run the sweep**, and say so in those
words rather than presenting a demotion with a caveat.

**What it does NOT license.** It is not a reason to keep a leg live
indefinitely: the attempt is bounded work, and a leg that fails after it gets
its disposition. Nor does it apply to a leg failing for a MECHANICAL reason —
a broken close path, an unreachable account, a wiring gap — where the fix is
the fix and tuning would be measuring a defect.

## Promotion evidence — offline edge, live mechanics (2026-07-26, binding)

Adopted after the de-soak workplan
([`docs/research/WORKPLAN-desoak-and-milestone-closeout-2026-07-26.md`](research/WORKPLAN-desoak-and-milestone-closeout-2026-07-26.md))
found that the "weeks of soak" blocking ML promotions was mostly stale framing +
un-retired policy gates, not missing evidence. The infrastructure to prove an
edge over history already exists (purged walk-forward CV `oos_edge`; the
`replay_pregate_fleet` / `backfill-shadow-predictions` replays; the point-in-time
`valuation_snapshot_backfill`), so a gate that instead **waits on live outcome
statistics to accrue** re-proves offline evidence on a slower clock.

**Scope: this applies to STRATEGY legs, not just ML models/heads/policies.** A
new strategy cell (e.g. an `ict_scalp` alt leg) wired to a paper/demo account
(`bybit_1`, `ib_paper`) is a **paper-SOAK**, and a paper-soak is a
**mechanics check, not a performance test**. Its edge is decided by the
offline live-faithful backtest + backfill BEFORE it is wired; the soak only
confirms the live executions match the simulator, which needs **1–2 executed
trades**, not calendar time. Never frame an alt-leg paper-soak as a
calendar-time wait to see if the edge holds — that is the recurring drift this
rule kills (the operational how-to lives in the `new-strategy` skill and is
CI-guarded by `scripts/check_soak_doctrine.py`). A handful of early losing paper
trades is variance, not a demotion signal, as long as the mechanics match; if a
leg reaches soak without an adequate offline proof, the gap is the missing
backtest, not more soak time.

**The rule.** For any promotion / graduation gate:

1. **Edge is proven OFFLINE.** Whether a model/head/policy has an edge is a
   question historical data already answers — purged walk-forward CV, a
   live-faithful historical replay, or an as-of point-in-time backfill. That is
   the gate that carries the promote decision.
2. **A live soak may only prove serving-MECHANICS** — that the live pipeline
   feeds the model what it trained on (train/serve parity, label accrual) and
   that it hasn't drifted. These accrue in **hours** (bar cadence), not weeks,
   and drift is a *rolling* check, not a fixed multi-week wait.
3. **No gate may require calendar-time accrual to prove edge.** A fixed
   "N days at stage" / "wait for K live episodes" requirement is a policy
   artifact, not evidence — if the offline edge + live mechanics + rolling drift
   all pass, the model is promotable. Such a gate is demoted to advisory
   (computed + reported, non-blocking), mirroring the 2026-07-19 M25 reframe
   (`live_regime_discrimination`) and the 2026-07-26 WS-1 `shadow_soak` demotion.

This is **mechanically guarded**: `tests/ml/test_gates.py` asserts that no
required gate under the regime-classifier profile is a calendar-time edge gate
(the `shadow_soak` calendar gate stays `required=False` there). A future edit
that re-requires it fails CI. Promotion past shadow remains the operator-gated
Tier-3 switch regardless — this rule governs *what counts as evidence*, never
*who* approves the live promotion.

## Documentation Hygiene & Premise Verification (2026-05-17)

Adopted in response to the PR #1358 incident, where a Claude session
disabled a live-trading strategy (`ict_scalp_5m`) on the basis of a
stale inline comment and a downstream audit finding that had inherited
the same stale framing — bypassing the Tier-3 operator-approval rule.
Full incident: `docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` §
Addendum. Root cause: a Claude session not reading and reconciling
documentation. Fix: the loop below, every session, no exceptions.

This section is a strengthening of the existing Code-First Verification
Rule (above) and Sprint Wrap-Up Requirements (below). When they
overlap, this section is the authority because it is the more recent
and more specific.

### Field vs. comment precedence

When the live value of a YAML field, a config constant, a code symbol,
or an SQL row disagrees with a surrounding inline comment, docstring,
sibling doc, or audit finding: **the field is the truth.** The
surrounding text is stale. The fix is to update the text, never to
flip the field.

This rule has exactly one exception: a doc explicitly marked
"canonical" or "authoritative" (the documents listed in
§ Document Priority above) wins over a non-canonical field comment.
The PR #1358 stale comment was *not* canonical — it was inline
boilerplate left over from a prior version. A canonical doc would
have prevailed; ordinary inline comments do not.

### Premise verification before filing an audit finding

Before writing an audit finding that asserts a discrepancy between a
field value and a comment / doc / prior intent statement, Claude must:

1. Run `git log -p <file>` on the file in question and surface, in
   the finding itself, the most recent commit that touched the
   relevant line. Include the SHA, the PR number, the merge date, and
   any operator-approval citation present in the PR body.
2. If the most recent commit on the line is an operator-approved
   merge, the field is the truth and the surrounding text is stale.
   The finding must be filed as a documentation-hygiene fix
   (update the comment / doc), not as a "fix the field" item.
3. If the most recent commit's authorization is unclear, the finding
   must be filed in `discussion` form, not `quick fix` or `proper fix`
   form — and must be resolved by asking the operator before any
   action item is scheduled.

Audit findings filed without this context spread contamination: the
next session inherits the false premise and operationalizes it. The
chain that produced PR #1358 was exactly this: audit-doc finding H-2
→ sprint plan B-2 → unauthorized PR. Every audit finding must be
self-defensive against this chain.

### Premise verification before operationalizing an audit finding

A Claude session that takes an audit finding off the shelf and turns
it into a sprint task or PR carries the responsibility to re-verify
the finding's premise before acting. The finding's age, author, or
inclusion in a respected document does not transfer the verification
duty. Re-run step 1 of "Premise verification before filing an audit
finding" against the line you are about to change.

If the re-verification surfaces an operator-approved commit on the
line, **stop**. Do not file the PR. Update the audit finding in place
to mark it withdrawn, cite the operator-approved commit, and reframe
the work item if there is still a real (documentation-hygiene) issue
to fix.

### Session-start documentation read

At the start of every session, before touching any file:

1. Read the root `CLAUDE.md` end-to-end.
2. Read this document, `ARCHITECTURE-CANONICAL.md`, and `ROADMAP.md`.
3. For any file you plan to edit, read it whole.
4. For any Tier-2 or Tier-3 file you plan to edit, also run
   `git log -p <file> | head -200` and read it. The most recent
   operator-approved commit on the line you are about to change is
   load-bearing context, not an optional check.

**A context-compaction resume is a NEW session (2026-06-23, operator
directive).** When a session continues from a summary — even when the
resume prompt says "pick up where you left off / as if the break never
happened" — that instruction does **not** waive the read above. A fresh
context window has not read these docs; reading them is the first action,
before any tool call. The 2026-06-23 incident: a resumed session ran a
`/system-report` without reading the canonical rules or the skill, treated
"report mode" as read-only, and shipped findings instead of fixes. To make
read-first the *only* workflow rather than a discipline that can lapse, the
`SessionStart` hook in `.claude/settings.json` now emits this contract as
the first thing in every session's context (the binding read-first +
work-session + definition-of-done clauses). This is a deliberate exception
to "Why no new mechanical guardrails" below — the operator asked for the
read to be mechanically guaranteed, not left to per-session discipline.

The previous "skim the canonical doc and move on" pattern that
produced PR #1358 (the sprint log confirms canonical docs were
listed as reviewed) is not sufficient. Reading is followed by
reconciling — the next subsection.

### Session-end reconciliation pass

Before opening any PR, declaring a task done, or otherwise closing
the session, Claude must:

1. Re-read the root `CLAUDE.md`, this document, and the relevant
   subsystem docs covering the code area touched.
2. For every file edited in the session: re-read it whole and
   reconcile inline comments and docstrings against the changes
   made. If the change added a feature, removed a code path,
   enabled or disabled something, renamed or moved something —
   the inline text must reflect the new reality.
3. For every doc page covering a code area touched: re-read it and
   reconcile against the code. Drift between doc and code is the
   landmine the next session steps on.
4. Existing contradictions the session did not cause (like the
   PR #1358 stale comment, which existed before that session
   started) are not someone else's problem. Fix them in the same
   PR, or open a separate draft PR before closing the session.
   Walking past a known contradiction is the same failure mode as
   creating one.
5. Run the **`doc-freshness`** skill — it sweeps the canonical doc set
   for instructions that now contradict each other or the code. Resolve
   what it finds. Log any minor issue you noticed but did not fix this
   session to the appropriate review backlog so a future review picks
   it up rather than letting it rot. The three backlogs (split
   2026-05-26) are:
   - **System / pipeline / doc-drift issues** →
     [`docs/claude/health-review-backlog.json`](claude/health-review-backlog.json)
     (drained by `/health-review`).
   - **Strategy / trading follow-ups** →
     [`docs/claude/performance-review-backlog.json`](claude/performance-review-backlog.json)
     (drained by `/performance-review`).
   - **AI / ML experiment follow-ups** →
     [`docs/claude/ml-review-backlog.json`](claude/ml-review-backlog.json)
     (drained by `/ml-review`).

The Sprint Wrap-Up Requirements section below restates several of
these duties at the sprint scope. This subsection restates them at
the session scope because not every session is a full sprint — but
every session still touches docs that the next session will trust.

### Why no new mechanical guardrails

In the PR #1358 post-mortem the operator explicitly rejected adding
new CODEOWNERS, CI gates, or PR templates to enforce the Tier-3 rule.
The reasoning: the rule already exists in this document, and the
prior session read this document. Mechanical guardrails on top of a
discipline failure are patches on patches. The fix is for Claude
sessions to actually follow the rules they have already loaded — the
documentation hygiene loop above is the mechanism.

**Scope of this rejection** (clarified 2026-07-09, Phase 0): it applies
specifically to guarding the **Tier-3 operator-approval discipline** (the
PR #1358 class — a judgment failure no CI gate can see). It is **not** a
blanket ban on all CI guards: guards that mechanically catch a
*structural* class of bug the author cannot self-verify are explicitly
sanctioned and in force — `new-table-wiring` (Rule 3 below),
`canonical-doc-coherence`, `canonical-db-resolver`, `env-gate-guard`,
`dry-run-guard`, `silent-empty-guard`. The `SessionStart` hook and the
session-board are the two other named exceptions (documented at their call
sites). The distinction: guard structure/wiring, not judgment.

If a future incident demonstrates that this loop also fails in
practice, the right response is to reconsider this decision then,
with that incident's specifics, not to pre-empt it with infrastructure
that papers over the discipline gap.

## Multi-session coordination (2026-06-28, binding; live-board step added 2026-07-22)

Multiple Claude sessions may run concurrently. Three failure modes recur; this
section closes them. The **operational workflow is the binding
`session-coordination` skill** (`.claude/skills/session-coordination/SKILL.md`),
the **live state is `docs/claude/session-board.json`**, and the `SessionStart`
hook surfaces both at session init. Read them before acting.

0. **Read + post to the LIVE coordination board FIRST — GitHub issue
   [#6927](https://github.com/benbaichmankass/ict-trading-bot/issues/6927)
   ("🤖 Claude Coordination Board"), before your first substantive tool call.**
   This is a *different* mechanism from the `session-board.json` merge queue in
   step 2 below, and it was previously undocumented here despite being called
   "mandatory" and "the first framing" by the `SessionStart` hook and the
   `session-coordination` skill — that omission is itself the doc-drift bug a
   2026-07-22 `/system-review` session found (it skipped the board and
   collided, unnoticed, with a live concurrent session mid-trainer-VM-work).
   `session-board.json` is a **committed file** — it only propagates through a
   merge + pull, too late to prevent a collision. Issue #6927's comments are
   visible to every live session **instantly** via the API. Protocol
   (`docs/claude/coordination-board.md`): (a) `issue_read method=get_comments`
   on #6927 first — see what every other live session is touching, answer any
   open question you can; (b) post a `▶️ START` comment (session id, branch,
   **which files/subsystems/VM you're about to touch**) before that first
   change; (c) post `❓ QUESTION` / `⚠️ HEADS-UP` as things come up; (d) post
   `✅ DONE` when you wrap, to release the claim. **This applies even when a
   session's actual writes happen inside spawned sub-agents** (background
   `Agent` fan-out) rather than the top-level session's own tool calls — if a
   sub-agent you spawn will commit, push, dispatch a VM/trainer action, or open
   a PR, post the board `START` yourself, covering that sub-agent's scope,
   *before* launching it; the sub-agent has no session identity of its own to
   post with.

1. **Know your capabilities before reaching for a tool.** On PM-side / Claude
   Code on the web sessions: `run_workflow` 403s — drive workflows via labelled
   issues (the diag / system-action relays); direct VM egress is firewalled
   for a raw `http://IP:port` but **NOT** for the Caddy HTTPS hostname — measured
   2026-08-20 at default-`Trusted`: the raw IP returns `000` while
   `https://ict-bot.duckdns.org/api/diag/version` returns `200` with a bearer, so
   **try the hostname before falling back to the `vm-diag-snapshot` relay**; the GitHub MCP drops intermittently — retry with backoff, never treat
   the first failure as an expired token; there is no `create_label`. Full
   contract: root `CLAUDE.md` § "PM-side session capabilities".

2. **Serialize merges — the merge protocol (a PER-MERGE precondition, not a
   session-start ritual).** Before EVERY `merge_pull_request` call: (a) read the
   live board tail (`#6927` comments) + list open PRs (the real-time truth);
   (b) post a `🔒 MERGE SLOT CLAIM` comment on the board — **that board comment
   is the authoritative live claim** (it reaches other sessions instantly);
   mirror it into `session-board.json::merge_slot` as the durable record;
   (c) sync your branch to `main` **only if you need `main`'s content** — since
   2026-08-10 `require-up-to-date` is OFF, so being `behind` no longer blocks a
   merge and a defensive re-sync just buys another ~9-minute CI cycle; sync when
   your change depends on something newly on `main`, or when git reports a real
   textual conflict; (d) let CI go green on whatever head you merge; (e) merge;
   (f) post `🔓 MERGE SLOT RELEASE` + clear `merge_slot`. This stops two sessions
   racing a merge and forcing each other "behind" `main` → the (now-removed)
   require-up-to-date re-run churn
   (observed twice on 2026-06-28; and the 2026-07-20 lapse where 3 claim-less
   merges raced `behind` in one day because the claim was treated as a
   session-start ritual and skipped under load — BL-20260720-MERGE-PROTOCOL-LAPSE).
   **Hard-enforced since 2026-07-27** (BL-20260727-MERGE-PROTOCOL-LAPSE-2, after a
   full M36 Track-D session auto-merged its whole PR chain claim-less and raced a
   concurrent Track-C session into behind-rebase churn): a `PreToolUse` guard in
   `.claude/settings.json` **denies** `merge_pull_request` **and**
   `enable_pr_auto_merge` until the session has run steps (a)–(d) for the specific
   PR and set a fresh (< 20 min) per-PR marker
   `/tmp/.claude-merge-claim-<session_id>-<pr>`. The marker is a speed-bump proving
   the protocol ran for *that* PR — not the claim itself; the `🔒 CLAIM` board
   comment is still what other sessions see. Full rationale:
   `docs/claude/coordination-board.md` § "Enforcement: the hard merge-guard".

3. **One PR = one concern.** Never add unrelated work to a branch that already
   has an open PR — it pollutes the PR and invalidates its CI run (and a new
   head SHA strands any merge-gate watcher). Start a fresh branch off `main` for
   a distinct deliverable, even mid-session.

4. **Cross-session resource optimization (2026-07-28, binding).** Full contract:
   [`docs/claude/vm-resource-management.md`](claude/vm-resource-management.md).
   (a) **Route to the cheapest sufficient resource** — GitHub-hosted runners are
   free ($0 on this public repo), abundant, and parallel across sessions (4 vCPU,
   ~5.5h); the trainer VM is a **single core, scarce and serialized**. CPU-heavy
   work needing **no VM-resident state** (a public-feed fetch + a backtest over it)
   runs on a **runner** (`research-exit-head-build.yml` pattern), NOT the
   `trainer-vm-diag` SSH relay. (b) **Serialize the scarce VM with a board FIFO
   lane** — before a heavy/exclusive VM job, claim `🔒 VM-LANE CLAIM` on #6927;
   if held, `🕓 QUEUED` and wait (FIFO, running never preempted, one `⚡ OVERRIDE`
   escape hatch); `🔓 RELEASE` when done. Quick read-only pulls need no claim. This
   is a **board** FIFO, not a GitHub concurrency group (which can't queue depth > 1).
   (c) **A dead run flags loudly + immediately** — `claude-run-failure-alert.yml`
   pings the operator the instant a watched VM/relay/research run fails/cancels/
   times-out, and every such workflow posts an **honest** failure comment (a
   `cancelled` relay run is the job time budget or a manual cancel — NEVER a sibling
   preemption). Never block indefinitely on a dispatched run; treat a failure comment
   as terminal. The whole reason: sessions were disrupting each other by piling heavy
   work onto the one scarce VM, then waiting hours on runs that had already died.

This is discipline + a shared board + the hook surfacing it, **not** a new CI
gate (operator decision, 2026-06-28). The 2026-07-27 merge-guard is a
**client-side `PreToolUse` speed-bump** on the merge tools — it does not gate CI
or branch-protection, it just forces the session to run the claim+sync protocol
before the merge tool fires (operator-directed after the claim-less-auto-merge
lapse). The hard safety net is GitHub branch-protection — **required status
checks** (`pytest-collect` / `pytest-run` / `guards`) with `enforce_admins`, so
no merge lands red; the board coordinates intent + the one merge slot, and the
guard makes the per-merge claim a physical precondition rather than an
honour-system step.

**Correction 2026-08-10:** this previously read *"the hard safety net remains
GitHub branch-protection (require-up-to-date)"*. **`require-up-to-date` is now
OFF** (`strict: false`, operator-directed — `.github/workflows/branch-protection-sync.yml`).
It was removed because it did not serialize anything; it only forced a PR that
had gone `behind` to re-run ~9 minutes of CI, so with sessions merging faster
than one CI cycle a branch could never be simultaneously green AND up-to-date.
**Required checks still gate every merge** — that is the safety net, and it is
unchanged. What is gone is the coupling to `main`'s tip. The accepted exposure
is a *semantic* conflict (two PRs clean textually, green alone, broken
together); `guards` + `pytest-run` also run on every push to `main`, so such a
break surfaces post-merge rather than silently.

## Session-length discipline & handoff (2026-07-23, binding)

A session that grinds through many unrelated tasks in one context window
wastes compute: every context-compaction is a lossy, costly re-derivation,
and a session that never closes the books leaves the next session to
rediscover state from chat scrollback instead of the repo. The binding
workflow is **`session-handoff`** (`.claude/skills/session-handoff/SKILL.md`)
— check it at every natural checkpoint (a PR merged, an investigation
resolved, before starting a new unrelated item), most relevant to
open-ended/long-running sessions (`research-driver`, `full-system-audit`,
any multi-hour ad hoc session).

The essentials (full detail in the skill): (1) recognize the cut point — a
compaction has already fired this session, or the next candidate work item
shares no context with what's loaded, or the operator says to wrap up; (2)
never hand off mid-flight on unfinished work — finish or checkpoint the
current unit first (commit, push, PR open at minimum), verify no loose ends
(clean `git status`, every branch has a PR, coordination-board `✅ DONE`
posted, any pending Tier-3 proposal logged to its backlog rather than
dropped); (3) end the session by handing the operator a **concrete,
paste-ready prompt** for a fresh session to continue — what was verified,
where the durable record lives (sprint log / roadmap row / backlog id), what
the specific next item is, and anything still outstanding. This is the
**serial/time-axis** counterpart to `delegate-work`'s **parallel/space-axis**
big-task decomposition — the same discipline of splitting work into
independently-resumable units, applied across a session boundary instead of
across concurrent agents.

This is discipline + a skill, not a *self-introspected* token-count gate —
this harness gives the main loop no reliable read of its own 5-hour or weekly
usage, so the skill's **default** triggers are structural/observable signals
(a compaction having already fired, a context-sharing test), never a numeric
threshold a session invents from nothing.

**Token/time-budget wrap-up gate (operator-directed 2026-07-27, binding on
every session).** Two things extend the above:

1. **Scope is every session**, not only long/autonomous ones. A short
   interactive session that is near a limit still finishes its current unit
   and closes the books cleanly rather than dying mid-edit and stranding
   half-done work for the next session to rediscover.
2. **When the operator surfaces a budget** — an explicit `+500k`-style
   target, a "you've got ~X left", or the Workflow `budget` global — that is
   a *real* number, not an invented one, so the no-invented-threshold rule
   does not forbid acting on it. Apply a **two-stage gate against that
   operator-given budget**:
   - **At ~85% spent** — STOP starting any *new* unit of work; finish only
     the one already in flight.
   - **At ~95% spent** — hard stop and run the **wrap-up sequence**:
     finish/checkpoint the current unit (commit → push → PR open at
     minimum), run the `doc-freshness` sweep, land the durable record
     (roadmap row / sprint-log / backlog id), post the coordination-board
     `✅ DONE`, and **ping the operator that work is pausing near the budget**
     with a paste-ready continuation prompt (the `session-handoff` handoff).

   The gap between 95% and the true ceiling **plus** the reserved cost of the
   wrap-up sequence itself is the deliberate **emergency reserve** — so a
   session never has to buy fresh tokens just to close its own books.

Absent an operator-surfaced budget, the structural proxies above remain the
only trigger (the no-invented-threshold rule stands — a session must not
fabricate a usage percentage it cannot actually measure). An explicit
operator instruction to keep going overrides the wrap-up for that session.
Mechanics live in the `session-handoff` skill.

## GitHub Actions Rule

Claude is allowed to inspect, create, modify, and use GitHub Actions
workflow files when relevant to CI, staging, validation, data
publishing, or release automation, **as long as the change stays within
the active permission tier.**

Claude must not claim that GitHub Actions are unavailable by default —
they are part of this project's automation surface. Inspect the repo
for existing workflow files and read
[`docs/github-actions-workflows.md`](github-actions-workflows.md) before
deciding what is or is not possible.

## Skills (composable workflows)

Repeatable workflows live as skills under `.claude/skills/`, written at a
granular level so you can chain them to accomplish larger tasks (e.g. retrieve
runtime data → inspect a VM → dispatch a system-action → review the result).

- **Prefer a skill over improvising.** If a skill covers the task, use it.
- **Propose new skills.** When you hit a mistake a clear workflow would have
  prevented, draft a new skill for it before closing the session — that is how
  this library grows and how recurring errors get designed out.
- **Keep them granular and composable.** One skill, one well-scoped job, so
  they can be patched together rather than duplicated.

Every session ends by running the `doc-freshness` skill (see § Session-end
reconciliation pass).

## Workflow Map

| Need | Canonical place to start |
|---|---|
| Claude operating rules and permissions | This document |
| System architecture and trade pipeline | [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) |
| Current work status and next work | [`../ROADMAP.md`](../ROADMAP.md) |
| Active sprint execution record | current sprint log under `docs/sprint-logs/` |
| Sprint log format | [`SPRINT-LOG-TEMPLATE-CANONICAL.md`](SPRINT-LOG-TEMPLATE-CANONICAL.md) |
| GitHub Actions usage and workflow automation | [`github-actions-workflows.md`](github-actions-workflows.md) |
| Telegram comms architecture | [`claude/comms-architecture.md`](claude/comms-architecture.md) |
| Operator-actions / VM dispatch | [`claude/system-actions.md`](claude/system-actions.md) |
| Mode mutation contract | [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) § Mode Mutation Contract |
| Deployment & ops | [`claude/deployment-ops.md`](claude/deployment-ops.md), [`DEPLOYMENT_LIVE_TRADING.md`](../DEPLOYMENT_LIVE_TRADING.md) |
| API tier policy | [`api-tier-policy.md`](api-tier-policy.md) |
| Trading mode flags | [`claude/trading-mode-flags.md`](claude/trading-mode-flags.md) |
| Cleanup policy | [`claude/cleanup-policy.md`](claude/cleanup-policy.md) |

If a workflow doc conflicts with this document on **process or
authority**, this document wins.

## Sprint Execution Standard

Every sprint should follow this structure:

1. Read the canonical rules, architecture, roadmap, and the active
   sprint log.
2. Inspect real code before planning changes.
3. Record scope, assumptions, tier, and verification targets.
4. Execute small changes in reviewable batches.
5. Verify with tests, dry-runs, staging checks, code inspection, or CI
   as appropriate.
6. Update affected docs.
7. Write a wrap-up entry that includes actual verification, not just
   intent.

## Sprint Wrap-Up Requirements

A sprint is not complete until Claude has:

- reviewed whether the canonical rules doc needs updates,
- reviewed whether the canonical architecture doc needs updates,
- reviewed whether the roadmap status needs updates,
- reviewed whether subsystem docs (e.g. GitHub Actions doc) need updates,
- recorded what code was actually checked,
- recorded what remains uncertain,
- and linked the next recommended work.

**Documentation review is part of the definition of done, not an
optional extra.**

## Sprint Log Standard

Sprint logs must be uniform and must use the canonical sprint log
template. Logs describe verified reality, not just PR intent.
New sprint logs live under `docs/sprint-logs/`.

## Strategy-improvement program — branching convention (2026-05-24)

The strategy-improvement program is a **continuous, multi-session**
effort (find/validate complementary strategies, build the decider, add
cross-asset members). It uses two kinds of branch — keep them separate:

1. **Persistent program branch** — `claude/strategy-improvement-program-EZi1X`
   (PR #1787 is its living research ledger: kept open, **not** a merge
   candidate). This is where research **tooling and artifacts** accumulate
   across sessions — backtest/validation harnesses (`scripts/research/*.py`,
   `scripts/ops/fetch_dukascopy_ohlcv.py`), audit docs, sprint logs, and
   design docs. **Future research sessions
   continue on this branch** so the harnesses are not re-derived each
   session.
2. **Fresh, focused branches cut from current `main`** — for anything that
   LANDS on `main`: strategy wiring, `config/*` changes, doc reconciliation.
   Cut these from `main`, **never from the program branch.**

**Why the split (the hazard it prevents):** the program branch carries
in-flight, research-only edits that are not meant for `main` (the
2026-05-24 session found it held unrelated `ict_scalp` signal-builder
deletions). If a main-bound PR were branched off the program branch, those
edits would leak into `main`. So every main-bound deliverable is
re-implemented or cherry-picked onto a clean branch off `main` — exactly
how the S9 trend/fade/squeeze wiring (#1875/#1884/#1885/#1907/#1908) and the
close-out docs (#1915) reached `main` while the harnesses stayed on the
program branch.

**Hygiene:** periodically land stable harnesses to `main` via clean PRs and
rebase the program branch on `main`, so it does not accumulate unbounded
divergence. The session-config "develop on
`claude/strategy-improvement-program-EZi1X`" directive points research
sessions at the persistent branch by default; the operator repoints it only
to start a new program line.

## Handling Contradictions

When Claude finds contradictory instructions:

1. Check this document first.
2. Check architecture and roadmap second.
3. Check the active code and deployment files.
4. Mark the contradiction in the sprint log.
5. Update the affected docs during the sprint, or propose the exact doc
   change if blocked.

## Historical Notes Policy

Old sprint plans, prompts, and PR notes are preserved for history. They
are useful for context, but they are not authoritative once replaced by
newer canonical docs. When a historical doc directly contradicts a
canonical doc, link to it from the canonical doc with a "superseded by"
note rather than silently editing it.

## Open Items to Finalize

- The sprint-log directory (`docs/sprint-logs/`) replaces the older
  `docs/sprint-summaries/` and `docs/sprint-plans/` formats. Older
  files in those folders are kept as historical record.
- This rules doc and `ARCHITECTURE-CANONICAL.md` should be reviewed at
  the start of every sprint until the milestone roadmap (M0..M10) is
  closed.
- Safeguards follow-on to PR #978: **DONE.** The *live* auto-flip vectors under
  § Prime Directive · "What this rules out" are behaviourally removed — the
  breaker auto-flip in `src/core/coordinator.py` is gone (now alert-only) and
  the legacy Telegram `/accounts dry|live` writer was removed in #1933. The
  orphaned dead-code cleanup is also complete: the `_DRY_RUN_OVERRIDES` dict +
  `set_account_dry_run()` (+ the `Coordinator.set_account_dry_run()` wrapper)
  were **deleted** in the 2026-06-10 dead-code cleanup; `_resolve_mode()` reads
  YAML directly and a regression test
  (`tests/test_exchange_rejection_circuit_breaker.py`) asserts their absence —
  see [`ARCHITECTURE-CANONICAL.md`](ARCHITECTURE-CANONICAL.md) § Mode Mutation
  Contract item 3.
