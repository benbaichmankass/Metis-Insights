# System audit — a proposal, not a dispatch

> **Doc status:** `unknown` · category `plan` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**
> **This document proposes work. It does not authorize any of it.** Nothing here
> runs until the operator picks a scope and says go. Filed per the wave-6 OPS
> lane's third assignment (`docs/claude/work/MANAGER-CHECKLIST.json` row R4, "OPS
> LANE" note) — draft only.

---

## 1. Why this is being proposed now, not why an audit is abstractly a good idea

Two concrete, unrelated findings landed in the same 30 hours this document was
written, both from *routine review sessions*, neither from an audit:

- **The ml-review W6-M1 lane (2026-09-27) found that the shadow-drift gate has
  been structurally blind for roughly the first 1–2 weeks after every log
  rotation** (`PI-20260927-YZRZQ725-0001`) — meaning no drift-based Tier-3
  demote/promote decision this repo has ever made, including the one that
  seeded the original E72 btc/sol regime demote proposal, was computed on the
  endpoint's own intended 30-day reference window. The instrument had been
  silently degraded since whenever rotation was last enabled, and nothing
  noticed until a session happened to diff two consecutive days' reads.
- **The archived `full-system-audit` skill's own case study** (`IBClient.protection_coverage`,
  landed 2026-07-26, missed by two full audits on 2026-07-31 and 2026-08-04,
  caught 2026-08-16 only because a *new* read surface was built) is the
  canonical instance of the same shape: every individual component reported
  healthy, and the audit's own instruments summarized the system rather than
  contradicting it.

**The pattern is the same both times: a measurement that looks like a clean
verdict is actually a silent capability loss, and nothing short of an
independent cross-check surfaces it.** That is what an audit is *for* — not
finding typos, finding the seams where individually-correct components combine
into a systemic gap. This proposal exists because that class of finding is
still happening on an ad-hoc basis (once every few weeks, whenever a session
happens to look sideways at its own data) rather than on a standing cadence.

## 2. What this is NOT proposing

- **Not a resurrection of `full-system-audit` or `system-review`** — both are
  retired because they were built on the 2026-09-21-superseded operating model
  (a coordination board, a session registry, `docs/claude/session-board.json`
  merge slots, four review backlogs). Their *content* — the seven axes, the
  finding schema, the "the audited system's own summarizer is not an
  independent check" principle — is sound and is reused below. Their
  *orchestration* is not: this proposal re-hosts the same methodology on the
  live one-manager-plus-lanes model.
- **Not a code-quality review.** `/code-review` and `/review` exist for that
  and are cheap; this is not a substitute for either.
- **Not a replacement for the three standing reviews.** `/health-review`,
  `/performance-review`, `/ml-review` keep running on their own cadence
  regardless of whether this audit runs. The audit's job is to catch what a
  *routine* review structurally cannot: cross-cutting seams no single review's
  scope covers, and instruments whose own self-report cannot be trusted
  without an outside check.
- **Not self-authorizing.** Nothing in this doc is a decision. Section 6 asks
  four explicit questions the operator answers before any lane is dispatched.

## 3. Lessons carried forward from the retired skill, stated as constraints on this one

1. **Never grade a claim using the same system that produced it.** A pass on
   `/api/diag/services` reading `active` is not independent evidence the
   service is doing its job; an audit must reach for a surface the audited
   component does not control (a broker-side balance vs. the journal's own
   PnL; a rotated shadow-log archive vs. the live drift endpoint's own
   reference window; an `EXPLAIN QUERY PLAN` vs. a docstring's claimed index).
2. **A finding is not closed by a fix. It is closed by a fix plus a permanent
   detector** — a CI guard, a self-test, a soak alarm — or an explicit,
   named reason no detector is possible. A fix with no detector is a fix that
   will silently regress and be "discovered" again in three months, which is
   this repo's own measured history (`docs/CLAUDE-RULES-CANONICAL.md`'s
   `RC-STORED-FIELD-READ-AS-ITS-NAME` recurrence class, now at 10 occurrences).
3. **State the population and the coverage honestly, both ways.** "Every line
   was read" has never once been true across five prior audit attempts and is
   not a target worth setting again. Report *behavioral* coverage (of the
   system's declared capabilities, how many were exercised end-to-end against
   real data this pass) as primary, and *reading* coverage (which files were
   read, which explicitly were not) as secondary — never silently.
4. **A finding schema, not prose.** Every finding — from a lane or the
   coordinating session — carries a falsifiable claim, the exact command/query
   and its output (not a description of one), expected vs. actual, the
   population with n, blast radius, tier, and a named detector or a named
   reason none exists. Prose findings get rejected and re-tasked; consolidating
   prose is how a lead session ends up writing "everything looks fine" without
   having checked anything.
5. **One lane, one question.** Per the live manager model (`.claude/skills/manager/SKILL.md`
   § "One question per lane"), each sub-lane below is scoped to answer exactly
   one falsifiable question, not "audit area X" open-endedly — that is what
   keeps a lane's cost bounded and its output checkable.

## 4. Scope — eight areas, each with its method and what "found something" looks like

Ordered by blast radius (money-at-risk first), matching the retired skill's own
ordering principle, not by convenience.

### 4.1 Order-path safety
**Question:** for a real-money position opened in the review window, does the
broker's own resting-order state match what the journal believes is protecting
it, at every point across its life — not just at open and close?
**Method:** pull the full resting-order snapshot from each live venue
(`/api/diag/ib_open_orders`-class endpoints, Bybit's open-orders REST, Alpaca's
order list) for every real-money position open at ANY point in the window;
independently reconstruct what SL/TP *should* exist from the journal's own
bracket record; diff. This is exactly the axis that missed
`protection_coverage`'s single-membership-test bug for 21 days — the check
must be against the venue, never against the app's own "am I covered" field.
**A finding looks like:** a position with a resting stop and no resting target
(or the reverse) for more than one tick interval; a bracket that references a
price no longer valid for the position's current size after a partial fill.

### 4.2 Trading correctness (signal → order → fill → exit → grade)
**Question:** for a sample of closed trades, does every hop in the pipeline
(`docs/ARCHITECTURE-CANONICAL.md`'s trade pipeline) match its declared
contract, end to end, on live data — not "is each hop individually reachable."
**Method:** the retired skill's "FULL PIPELINE VERIFICATION" pass, narrowed to
a stratified sample (one per account × one per exit-mechanism family) rather
than the full trade population; trace each sampled trade from its
`order_packages` row through fill, monitor decisions, exit, and
`claude_strategy_scores.jsonl` grading, asserting the contract at each hop
rather than reading the summary field.
**A finding looks like:** an exit stamped `sl` whose exit price sits outside
any bracket level ever recorded for the position (the class `BL-20260826`
already found once, at 22/28 mismatch, on a small sample — this checks whether
that class recurred or was structural).

### 4.3 Data / provenance integrity
**Question:** for every numeric field this repo's reviews and dashboards treat
as MEASURED, is its provenance chain actually intact — or does a `MEASURED`
label paper over an `ESTIMATED`/`UNVERIFIED` upstream value?
**Method:** `scripts/ops/column_provenance.py` over every column a review skill
cites as MEASURED in its last three runs; cross-check the collapsed-state
guard's `CONTRACTS` table against every three-state field actually in
production (not just the ones already registered) — the registration gap
itself (§ "Collapsed states" in `docs/CLAUDE-RULES-CANONICAL.md`: "a state
nothing branches on is already collapsed") is testable directly.
**A finding looks like:** a field with more than one writer that a review
skill's SKILL.md cites as if singly-owned (the `trades.stop_loss`, 119-write-site
precedent); a three-state contract in production code with no
`collapsed-state-guard` registration.

### 4.4 ML serving fidelity
**Question:** does every `advisory`/`shadow` model's LIVE feature-serving path
actually match its TRAINING-time feature computation, continuously — not just
at the moment a review happens to check?
**Method:** the ml-review skill's own "Soak Integrity Audit" (train/serve
parity, label-accrual coverage, evidence-destination check), but run against
EVERY model at `shadow`+ in one pass rather than the 3–4 a routine ml-review
samples, and cross-checked against the archived shadow-log windows
(`runtime_logs/shadow_predictions.*.jsonl.gz`) to see whether a rotation-driven
blind spot (§1's finding) is a one-off or a standing property of every gate
that reads the active log alone.
**A finding looks like:** the ETH-xa class (a feature that is dead at serving
time but live at training time) recurring on a model no routine review has
sampled yet; a second gate (beyond `drift_clean`) with the same rotation-window
blind spot.

### 4.5 Infra / VMs
**Question:** does every declared systemd unit's state match what the fleet
actually depends on it doing, and does `ict-git-sync` genuinely keep the live
VM within its documented ~5-minute staleness of `main`?
**Method:** full `systemctl` unit inventory on both VMs, cross-referenced
against every unit a live workflow, skill, or doc names; independently confirm
`ict-git-sync`'s claimed freshness by diffing the live VM's checked-out HEAD
against `origin/main` at a sampled instant, not by trusting the timer's
`active` state (this review's own incidental finding —
`ict-git-sync.service` reading `failed` on 2026-09-27, `PI-20260927-YZRZQ725-0002`
— is exactly the kind of thing this area should have caught on its own cadence
rather than by accident).
**A finding looks like:** a `failed` unit whose timer masks the failure by
staying `active`; a live-VM HEAD measurably behind `main` by more than one
sync interval.

### 4.6 CI guard health
**Question:** of the ~89 registered guards (`scripts/ci/run_guards.py --list`),
how many have a **failure-path self-test** (a planted defect the guard is
proven to catch), and how many merely assert "ran without error"?
**Method:** `scripts/ci/guard_selftests.py`'s own coverage report, extended to
enumerate every guard NOT covered by it; for each uncovered guard, plant one
synthetic defect it claims to catch and confirm it actually fails red. The
retired skill measured 10 of 41 guards proven this way in 2026-08-20; the
guard count has since roughly doubled, so the proven fraction is unmeasured and
could have gone either direction.
**A finding looks like:** a guard whose only "test" is that it exits 0 on the
current (already-clean) tree — which proves nothing about whether it would
catch the defect it exists for.

### 4.7 Security
**Question:** does every credential-bearing path (API keys, the diag token,
the VM SSH key, the GitHub token scopes granted to Actions workflows) have the
minimum privilege it needs, and is any secret material reachable from a
surface that shouldn't have it (a log line, a diag response, a committed
fixture)?
**Method:** grep every workflow's `permissions:` block against what its steps
actually use; grep committed history (not just HEAD) for credential-shaped
strings using a standard secret-scanner, since `git log` on this container is
shallow and a full unshallow fetch would be needed first; confirm the diag
relay's allowlists (`vm-diag-snapshot.yml`) cannot be walked to a write path.
**A finding looks like:** a workflow requesting `contents: write` it does not
use; a fixture file carrying a real-looking (even if inert) API key shape.

### 4.8 The operating model itself
**Question:** is the 2026-09-21 reset's own promise — "an item comes due on
the operator's own page, and stays there until routed" — actually true today,
measured, not asserted?
**Method:** `scripts/ops/pipeline.py --check --due` against the live
`docs/claude/work/pipeline/` store; independently verify `render_section_0()`
is actually called by whatever renders the operator's daily brief (§ "the pull
is BUILT but NOT YET CONNECTED" — `CLAUDE.md` already flags this as
unfinished, A3/A7); measure whether skills, workflows and docs still reference
retired registers (the archived-and-still-referenced class E29 found at 40
paths across ~60 files on 2026-09-22) and whether that count has grown or
shrunk since.
**A finding looks like:** a pipeline item that is `due` today and does not
appear anywhere the operator actually looks; a fresh (post-2026-09-22) skill
edit that reintroduces a path to an archived register.

## 5. Method, in general: how a sub-lane is run and how a finding is trusted

Per area above, one lane, one question, the finding schema from §3.4 for every
claim it produces:

```
{
  "id": "AUD-2026-XX-XX-<slug>",
  "axis": "order-path | trading-correctness | provenance | ml-serving | infra | ci-guards | security | operating-model",
  "claim": "<one falsifiable sentence>",
  "evidence": "<the exact command/query run, and its actual output>",
  "expected": "...", "actual": "...",
  "population": "<what was measured over, with n>",
  "blast_radius": "money-at-risk | accounting | observability | docs",
  "tier": 1 | 2 | 3,
  "detector": "<the CI guard / self-test / soak alarm that fails if this recurs, OR a stated reason none is possible>",
  "disposition": "fixed | draft-PR | filed (docs/claude/work/pipeline/<id>) | verified-non-issue"
}
```

A finding lands in **`docs/claude/work/pipeline/`** (never a new register — the
2026-09-21 reset's whole point), tagged so a future review can find it, and a
Tier-3 finding goes to the operator with the exact proposed fix, never enacted
by the lane itself. **Never enact a Tier-2/3 fix inside the audit lane that
found it** — same separation of finding from fixing that governs every other
review in this repo, so a lane under schedule pressure cannot grade its own
fix.

## 6. Four questions for the operator, because this proposal cannot answer them for itself

1. **Scope: all eight areas, or a subset first?** §4.1–4.2 (order-path,
   trading correctness) carry the largest blast radius and are the cheapest
   to justify running alone; §4.6–4.8 (guards, security, operating model) are
   cheaper to run and lower urgency. A first pass on 4.1+4.2+4.5 (the three
   areas with a concrete recent finding motivating them) is one reasonable
   subset if the operator wants to bound cost before committing to all eight.
2. **Cadence: one-off, or standing?** The retired skill ran roughly monthly
   and still missed a 21-day-live order-path bug — a standing cadence is not
   sufficient by itself; the finding-schema + detector discipline in §3.2/§5
   is what actually changes the outcome, cadence is secondary.
3. **Model budget: which class gets Fable?** Per `.claude/skills/manager/SKILL.md`
   §E11 (undecided), this is a candidate use: several of the eight lanes below
   (§4.6 in particular — mechanical guard-coverage enumeration) are cheaply
   verifiable against a sonnet lane's output, which is exactly E11's stated
   bar for widening to Fable.
4. **Who reviews the findings before they're actioned?** The manager relays
   Tier-3 findings to the operator per the standing model; this proposal does
   not ask for anything different, but flags that eight lanes running roughly
   in parallel could produce Tier-3 findings faster than a single daily sync
   can process them — worth deciding whether this justifies a dedicated
   findings-review session rather than folding into the regular daily brief.

## 7. Proposed lanes, model, and cost estimate

Costed against the manager skill's own model table and the measured cost
shape of comparable lanes already run this cycle (a live-data-pulling review
lane like this session's own ml-review ran multiple trainer-vm-diag +
vm-diag-snapshot round trips plus a merge-conflict resolution; a bounded
research/backtest lane in the $10–20 range is the repo's typical unit, per
several `ceiling_usd` values already on the checklist, e.g. E67 at $15–35).
**These are estimates, not quotes** — actual cost is read off `get_session`
after archiving, same as every other lane.

| Lane | Area | Model | Est. ceiling | Parallelizable with |
|---|---|---|---|---|
| AUD-1 | 4.1 Order-path safety | `claude-opus-5` (order-path work is always opus per the model table) | $35–50 | all others |
| AUD-2 | 4.2 Trading correctness (pipeline trace) | `claude-sonnet-5` | $25–40 | all others |
| AUD-3 | 4.3 Data/provenance integrity | `claude-sonnet-5` | $20–30 | all others |
| AUD-4 | 4.4 ML serving fidelity | `claude-sonnet-5` | $20–35 (trainer-VM diag round trips are the cost driver, per this session's own ml-review) | all others |
| AUD-5 | 4.5 Infra/VMs | `claude-sonnet-5` | $15–25 | all others |
| AUD-6 | 4.6 CI guard health | `claude-haiku-4-5-20251001` candidate (mechanical enumeration + planted-defect tests — E11 candidate, §6.3) | $10–20 | all others |
| AUD-7 | 4.7 Security | `claude-sonnet-5` (credential-adjacent; not order-path, so not mandatory-opus) | $15–25 | all others |
| AUD-8 | 4.8 Operating model | `claude-sonnet-5` | $10–20 | all others |
| Lead (this session or a fresh manager delegate) | consolidate findings, dedupe, file pipeline records, run the daily-sync-facing summary | `claude-opus-5` | $15–25 | runs after the 8 lanes land, or streams as they land |

**Total estimated range: $165–270** for all eight areas in one wave, one
question per lane, run in parallel (per §3.5's one-question rule, no lane
needs another lane's output mid-flight — the lead session is the only
serialization point, and only for consolidation). A subset per §6.1 scales
down roughly linearly.

## 8. Outputs

- One `docs/audits/system-audit-<date>.md` report per completed wave (not a
  register — a dated artifact, matching the archived skill's own report
  convention, minus the retired session-board/backlog wiring).
- Every finding filed as a `docs/claude/work/pipeline/` record with the
  schema in §5, `origin.rerun` naming the exact re-check command.
- Every Tier-3 finding also surfaced to the operator with the proposed fix,
  never enacted by the audit lane.
- A coverage statement per lane: behavioral coverage (capabilities exercised
  / asserted) AND reading coverage (files read / explicitly not read) — both
  stated, per §3.3, never silently partial.

## 9. What "done" means

A wave is done when every dispatched lane has: (a) landed its findings in the
schema above, with zero prose-only findings; (b) stated its coverage both
ways; (c) for every finding, named a detector or an explicit reason none is
possible; (d) had its cost read from `get_session` after archiving and
recorded. The audit as a whole is done for a given scope when every area in
that scope has a landed lane — **`landed` is not `actioned`**: a Tier-3
finding sitting with the operator, undecided, does not block calling the
audit itself complete; it blocks calling the *finding* resolved.

---

**This document makes no commitment.** It exists so the operator can pick a
scope in one read rather than reconstruct one from scratch, per the wave-6 OPS
lane's third assignment. See `docs/claude/work/MANAGER-CHECKLIST.json` row E71
for the tracking row.
