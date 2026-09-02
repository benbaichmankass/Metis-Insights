# Operating-Layer Inventory — the 32 skills and the 129 workflows, mapped onto the model

> **Status: MEASURED 2026-09-02.** This is **Phase G's inventory**, which
> [`operating-layer-build-plan-DESIGN.md`](../design/operating-layer-build-plan-DESIGN.md)
> § "What this plan does not decide" explicitly defers: *"which of the 32 skills, 124
> workflows and 13 registers map onto which function (Phase G's inventory, not a
> prerequisite)."*
>
> It is an **AUDIT plus a PROPOSAL**. Nothing here retires, creates or edits a skill or a
> workflow. Retiring machinery is E3 and it is a proposal to the operator; this document
> is the argued list, not the act.
>
> Audited against the four-document series:
> [operating-model](../design/operating-model-DESIGN.md) (structure, five session types,
> the 24 functions) ·
> [schema-and-state](../design/operating-layer-schema-and-state-DESIGN.md) ·
> [function-derivation](../design/operating-layer-function-derivation-DESIGN.md) ·
> [build-plan](../design/operating-layer-build-plan-DESIGN.md).

---

## 0 · Corrections to the brief and to the design series

Stated first, because three of them are load-bearing and two point the *opposite* way
from the documents a session would otherwise reason from.

| # | Claim as written | Measured 2026-09-02 | Direction |
|---|---|---|---|
| 1 | "21 workflows declare a `schedule:`" | **20.** Population: all 129 `.github/workflows/*.yml`, regex `^\s{2,}schedule:`; independently, exactly 20 files contain a `cron:` expression | minor |
| 2 | build-plan: the two dead Routines are an outstanding **operator hand-off**, "attempted and refused 2026-09-01" | **They are GONE from the register.** `list_triggers(enabled=true)` returns exactly 2 routines and neither is `Health Check Routine` nor `Sprint Continue Work` | **stale — a retirement that HAPPENED and is unrecorded** |
| 3 | function-derivation: **5 functions MISSING** (A3 · A5 · E1 · E2-as-governed · E3) | **All five have since shipped**, between that pass (2026-09-01) and now | **stale — the derivation understates progress** |
| 4 | derivation: E1/A1 is the readout, trigger *Cadenced*, autonomy *Full* | Shipped, but **it has no cadence and no freshness check** — see § 3.1. Its own guard note reads *"the readout itself is generated on demand, not in CI"* | **stale in the dangerous direction** |
| 5 | brief: `issue_write` / `add_issue_comment` / `create_pull_request` 403 for a sub-session | **`add_issue_comment` succeeded from this session** (comment `5505094635` on #6927). The relays were not needed for the board post | correction |

⚠️ **On (2), state the limit of the probe.** What is established is that the routines are
absent from the register *as this session can read it*, with a positive control: the same
filtered call returned two live routines, so the probe can find positives. What is **not**
established is *who* deleted them or *when*. Do not record this as "the operator actioned
the hand-off" — record it as "the routines are gone; attribution unknown".

---

## 0b · Corrections from the ENACTMENT pass (MI-66, 2026-09-02)

This document's § 5 records *"It enacted nothing."* A later session on the same
day enacted its CREATE, three of its four REPAIRs, P-C3 and P-C4. Four of its
measurements did not survive that contact and are corrected here rather than in
place, so a reader who saw the originals can tell what changed.

| # | As written | Re-measured 2026-09-02 | Why it matters |
|---|---|---|---|
| 6 | § 2.2: `docs/claude/work` referenced by **0** of 32 skills | **2** — `delegate-work` and `workplan-vs-architecture`, both via `SESSIONS.json` | **Sharpens the finding, does not weaken it.** Every OTHER term is genuinely 0: `work object`, `WIP`, `blocked_on`, `CYCLE-PRIORITY`, `READOUT`, `SUNSET-DISPOSITIONS`, `MANAGER-LEASE`. Only the sub-session REGISTRY was ever wired; the whole STEERING half was invisible |
| 7 | § 4.5: `ict-scalp-exit-sweep` is a **REPAIR** — "5 runs, last 2026-08-10, `failure`, left red 23 days" | Its push trigger names **two branches, both deleted from origin**. The red run is a historical artifact on a branch that no longer exists; the trigger has been structurally dead since | A CI failure to fix and a trigger that cannot fire are different findings with different remedies. And it is not alone: `m20-capture-census` and `m20-exit-lever-sweep` are pinned to the same dead branch and the audit flagged neither — **3, not 1** |
| 8 | § 4.5 / P-D3: `strategy-review-packets` — "0 on cron; the 04:40Z window passed… **re-read before grading**" | It fired: run #22, `event=schedule`, 09:00:43Z — **~4h20m late** — and concluded `failure` | The caution was right and the answer is worse than "hasn't fired". ⚠️ **The failure is a FALSE NEGATIVE**: PR #10771 merged at 09:20:18Z with all six checks `success`, and the job reported "the rows are… NOT on main" at 09:31:27Z. `comms/strategy_reviews/2026-09-02/INDEX.json` IS on main |
| 9 | § 4.3: `probes` / `due-list` — "1 of 2 windows each" | **`probes` 2 of 2** (09-01 success, 09-02 **failure**); **`due-list` 2 of 2, both success**. All ~4–5h late | The cadence question is CLOSED — they fire, consistently late. `probes` #55 died on `GraphQL: API rate limit already exceeded`, the SAME exhausted quota behind the false negative above: **one root cause, two symptoms, two of the three REPAIR items** |

⚠️ **What the enactment did NOT settle**, stated so it is not assumed:
`replay-pregate-nightly`'s deterministic death at head 10/22 is repaired at the
transport layer (the run is detached and polled), and **why the remote end went
away there is still not established** — an OOM kill on the 1-OCPU trainer would
look identical, and no kernel evidence has been read. A failure-path-only
evidence step now captures it so the next failure names its own cause.

⚠️ **And no workflow was retired.** § 4.5's eight RETIRE candidates remain
candidates, and the § 4.5 warning stands: 87 of 129 workflows still cannot be
graded on dormancy. The new `workflow-trigger-reachability` guard answers a
strictly narrower question — *can this push trigger fire at all?* — which needs
no run history. It is not P-D5.

---

## 1 · Half D — the 129 workflows

### 1.1 The headline: the fleet is itself the proof that C1 outruns C2

Every one of the 129 workflow files was assigned **one primary function** — what the
workflow exists to accomplish — against the 24 functions. Classification is machine-checked
(`scripts/ops/…`, reproduced in § 6) and covers 129 of 129 with no residual.

| Function | Workflows | Share |
|---|---:|---:|
| **C1** evidence production | **46** | **35.7%** |
| C5 deployment / tiered mutation | 32 | 24.8% |
| D1 liveness & incident detection | 10 | 7.8% |
| C6 post-deployment verification | 7 | 5.4% |
| F5 rule binding (executable checks) | 7 | 5.4% |
| E3 capability retirement | 6 | 4.7% |
| F3 concurrency coordination | 6 | 4.7% |
| F1 state of record | 4 | 3.1% |
| D2 incident disposition | 3 | 2.3% |
| D4 obligation tracking | 3 | 2.3% |
| B3 dispatch | 2 | 1.6% |
| C3 decision preparation | 1 | 0.8% |
| C4 decision recording | 1 | 0.8% |
| F6 notification | 1 | 0.8% |
| **C2 evidence disposition** | **0** | **0.0%** |
| **A · DIRECTION (all five functions)** | **0** | **0.0%** |

**46 workflows produce evidence. Zero read it.** One prepares a decision, one records
one. This is the measured *256 of 370 units superseded before anyone read them*
re-derived from a completely independent surface: the automation fleet is built 46:0 in
the direction the design names as the disease. The operating model's own words — *"anything
that raises C1 without raising C2 makes the system worse"* — describe 35.7% of this
repo's workflow estate.

**And the DIRECTION group has no automation at all.** Not one of A1–A5 is served by a
workflow. That is the "never built the steering" finding, confirmed from the machinery
side rather than the function side.

⚠️ **State what this classification is and is not.** It is a *primary-function* assignment,
one per file, made by reading each workflow's name and its own header comment. A workflow
can serve a second function incidentally (`sunset-pass` also produces evidence; `probes`
also detects). The counts are therefore a description of what the fleet was *built for*,
not a time-or-value-weighted measure of what it does. A different tie-breaking rule would
move a handful of files between C5/C6 and D1/D2; it would not move the 46-vs-0.

### 1.2 What is genuinely healthy, and should be left alone

Two things measured clean and both are load-bearing. Recording them matters because an
audit that only lists defects invites a reader to "fix" a working mechanism.

- **Documentation coverage is 129 of 129.** Every workflow file is referenced from
  `docs/github-actions-workflows.md` (1,200 lines). Verified with a negative control (a
  fabricated filename returns 0 matches), so the grep can distinguish absence. There is no
  undocumented workflow.
- **Scheduled-failure alerting is 20 of 20.** Every scheduled workflow appears in
  `claude-run-failure-alert.yml`'s `workflow_run` watch list, and no watch-list entry is
  dead (28 entries, all resolving to a real workflow). This **closes
  `BL-20260813-CRON-WORKFLOWS-FAIL-SILENTLY-ON-SCHEDULE-PATH`**, whose measurement was
  *"6 of 10 cron'd workflows"* unwatched. The population has doubled and the gap is now zero.

---

## 2 · Half C — the 32 skills

### 2.1 The DISPOSE hypothesis: confirmed in effect, WRONG in its stated cause

The brief's hypothesis was *"nothing in the current 32 owns DISPOSE."* Measured against the
contents of all 32 `SKILL.md` files, **that is not what is wrong, and the difference decides
the remedy.**

**Something does claim it.** `performance-review` gained research-pipeline ownership on
2026-08-30 and says so in its own frontmatter (L3): *"ALSO owns the research pipeline end to
end … **were their results READ and dispositioned** …"*. Its body carries a detailed
five-stage procedure (L326–422) — queue health → dispatch → **review/validate** → run
analyses → **record the disposition** — quoting the operator directive verbatim and calling
out that *"a review that reports 'N sweeps ran' has done stage 2 and skipped the three that
produce a decision."* `system-review` then **enforces** disposition as a required coverage
key (`research_results_disposition`), and the enforcement is real outside the prose:
`scripts/reports/render_system_report.py:806` carries it in `_REQUIRED_COVERAGE_KEYS`.

**So the machinery is not missing. The DEFINITION OF DONE is.** Disposition is absent from
every structural contract in the very skill that owns it:

| contract in `performance-review/SKILL.md` | disposition present? |
|---|---|
| `## Scope (what this skill DOES)` — 12 enumerated items (L48–85) | **no** |
| `## Output` — the required response fields (L679–711) | **no** |
| `comms/schema/performance_review_response.template.json` | **no research key at all** |
| `## Draining the backlog — a HARD COMPLETION GATE` (`count_untriaged MUST be 0`, L500/L527) | **covers `performance-review-backlog.json` only** |
| `## What you DO write` (L715–730) | **no** — omits both `research-review-backlog.json` and `research_disposition.append` |

**A `performance-review` session can emit a fully schema-conforming, "complete" response
having done none of Stages 1–5.** Nothing fails. That is the precise defect: **ownership was
declared in prose and never given teeth**, which is the same shape as the repo's own
`provenance-consumer-guard` class (a field written and never read) applied to a *duty*
instead of a field.

The one place a missing disposition actually fails anything is `system-review` — a different
session, where it is **one of thirteen** coverage keys inside a master review whose subject
is "what has the system been doing".

**Plain statement of the finding.** No session in the corpus exists *in order to* read
already-produced results, and no session fails if it does not. Disposition is a section
inside a trading-performance review and a checkbox inside a master review. The load-bearing
PRODUCE/DISPOSE split the operating model calls *"the load-bearing one"* has not been made.

### 2.2 The operating layer is invisible to every role pack

The operating model's anti-silo mechanism is **context = work object + role pack**, and the
function derivation states plainly that *"Role packs exist as 32 skills."* So the skills
**are** B4's role-pack half.

Measured across all 32 `SKILL.md` files, case-insensitively:

| term | skills referencing it |
|---|---:|
| `docs/claude/work` | **0** |
| `work object` / `work-object` | **0** |
| WIP ceiling / `WIP` | **0** |
| `blocked_on` | **0** |
| `CYCLE-PRIORITY` | **0** |
| `READOUT` / "constraint readout" | **0** |
| `MANAGER-LEASE` / `MANAGER-CHECKLIST` | **0** |
| `PRODUCE` / `DISPOSE` (session types) | **0** |
| operating-model `intent` | **0** (all 40 `intent` hits are the *intent multiplexer*, `src/runtime/intents.py`) |
| `sunset-pass` / `SUNSET-DISPOSITIONS` | **0** |

The single hit anywhere near the subject is `full-system-audit:629`, *"Philosophy — is the
operating model itself sound?"* — a question the audit asks, not a pointer to the layer.
`duty` is the only skill wired to newer register machinery, and only to `OPEN-ITEMS.json` and
`DUE.md`, **both of which predate the work store**.

**This is the structural finding of half C.** The operating layer shipped its object half on
2026-09-01 — store, intents, WIP ceiling, constraint readout, lease, checklist, cycle
priority, sunset pass — and **not one role pack was updated to know it exists.** A session
handed a work object still reads a role pack that describes the pre-2026-09-01 world of three
backlogs, `OPEN-ITEMS.json`, `DUE.md` and `ROADMAP.md`. B4's two halves are wired to different
systems.

⚠️ **This is not an argument for editing 32 files.** Most role packs are domain procedure
(how to wire a broker, how to run a sweep) and are correctly indifferent to where work is
tracked. The gap is concentrated in the handful that govern *how a session situates itself* —
§ 2.5 **P-C3** names them. (This read "§ 4.2 names them" until 2026-09-02; § 4.2 is
about workflow *retirement history* and names no skill. Corrected because a
session sent to the wrong section finds a list of deleted workflows and concludes
the skill list does not exist.)

### 2.3 E3's machinery shipped and no skill knows it exists

`sunset-pass.yml`, `scripts/ops/sunset_pass.py`, `scripts/ci/check_sunset_dispositions.py`,
`docs/claude/SUNSET-DISPOSITIONS.json` and `comms/sunset/2026-09-01/` all exist and are live.
**Zero skills reference any of them** — the only occurrence of the substring "sunset" under
`.claude/skills/` is a generic `rg -i` pattern inside `full-system-audit:344`.

The sunset pass currently carries **10 retirement candidates over 52 strategy legs** (per the
`CLAUDE.md` session brief). Retiring a leg is Tier-3 and must be dispositioned in
`SUNSET-DISPOSITIONS.json`. **No skill tells any session to go and do that**, and the one
skill that owns retirement-adjacent duty (`duty`, which drives every due signal to an owner)
has **zero** hits for `retire|retirement|sunset|decommission`.

**Retirement duty across the corpus, measured:**

| what gets retired | who owns it |
|---|---|
| a **VM** | `vm-migration` — genuine ownership |
| a **model** | `ml-review` (retire to `candidate` after N=3), `drift-remediation` |
| a **strategy leg** | `performance-review` — but *proposal only*, and L424 binds hard the other way: *"A red number is … NEVER an instruction to demote"* |
| an **exit lever** | `exit-refinement` (L189: "Demotion = delete the YAML lines") |
| a **backlog class** | `backlog-drain` (L27) — closing findings, not sunsetting a mechanism |
| a **skill / workflow / register / guard** | **nobody** |

The closest thing is `full-system-audit` §3.5 LIVENESS (L347–350), which is genuinely strong
on *detection* — *"an artifact present but unreachable/unrouted/unrun is presumed a corpse to
remove or to justify in writing … A build-arc + retire-arc with no delete-arc is a zombie"* —
but it disposes of zombies as ordinary tiered findings (L556–574), writes to no retirement
register, and states its design findings are *"proposals, not unilateral changes"* (L657).

So the build plan's *"nothing has ever retired a skill, register, workflow or guard"* holds,
and this audit can now say **why**: detection exists (`full-system-audit`), the forcing
function exists (`sunset-pass`), and **no role pack connects a session to either.**

### 2.4 Skill → session-type map

Mapping each of the 32 to the session type it serves. `—` means the skill is cross-cutting
(F1–F6) rather than owned by one type.

| Session type | Skills that serve it | n |
|---|---|---:|
| **CYCLE** (A1 consumed · A2 · A3 · A4 · C4) | *(none)* — `research-driver` and `workplan-vs-architecture` are the nearest, and neither consumes a constraint readout or records a decision | **0** |
| **PRODUCE** (C1) | `backtesting`, `model-training`, `macro-research`, `regime-selectivity`, `exit-refinement`, `new-strategy`, `llm-delegate` | 7 |
| **DISPOSE** (C2) | *(none as a session)* — claimed inside `performance-review`, enforced inside `system-review` | **0** |
| **DECIDE** (C3) | *(none)* — exactly one skill line touches a packet at all: `health-review:570` notes that a `PROMOTE`/`DEMOTE_SHADOW`/`KILL` badge "is a decision", and its own scope explicitly **routes it away** rather than making it. Nothing consumes a committed packet | **0** |
| **BUILD** (C5 · E2) | `new-broker`, `new-strategy`, `vm-ops`, `vm-migration`, `db-setup`, `db-wiring`, `credentials-and-vm-mutations`, `before-asking-the-operator`, `git-actions` | 9 |
| **OPERATE** (D1–D4) | `health-review`, `diag-data`, `duty`, `drift-remediation`, `backlog-drain` | 5 |
| **Cross-cutting F1–F6** | `session-coordination` (F3), `session-handoff` (F2), `doc-freshness` (F2/F5), `sprint-format` (F2), `delegate-work` (B2/B3), `full-system-audit` (F4/E3-detection), `system-review` + `system-report` (roll-up), `performance-review` (C2+OPERATE hybrid), `ml-review` (OPERATE+C3 hybrid), `research-driver` (B1/B2), `workplan-vs-architecture` (A4-adjacent) | 11 |

**Three of the six session types have no skill at all: CYCLE, DISPOSE, DECIDE.** Those are
exactly the three that convert evidence into a choice — the measured constraint. PRODUCE has
seven role packs; DISPOSE has none.

### 2.5 Proposal — skills

Every item is a **proposal to the operator**. Nothing below was enacted.

**P-C1 · Give DISPOSE a definition of done (highest leverage, smallest change).**
Do **not** write a new skill first. The procedure already exists at
`performance-review` L326–422 and is good. What is missing is the four structural hooks
that make a session fail without it:
1. add the research pipeline to `## Scope (what this skill DOES)`;
2. add a `research_pipeline` block to `comms/schema/performance_review_response.template.json`
   and to `## Output`;
3. extend the HARD COMPLETION GATE to `research-review-backlog.json` with its own
   `count_untriaged` assertion, matching the one at L527;
4. add `research-review-backlog.json` and `research_disposition.append` to
   `## What you DO write`.
This is Tier-1 (docs + a response schema) and it converts a declared duty into an enforced
one. **Argument against splitting DISPOSE into its own skill *first*:** the operating model
wants PRODUCE and DISPOSE to be separate *sessions*, and a separate skill is the natural
vehicle — but a new skill with no completion gate would reproduce the exact defect measured
here, one directory over. Wire the gate, then split.

**P-C2 · Then split: a `/dispose` skill owning C2 as its whole job.** Once the gate exists,
lift L326–422 out of `performance-review` into its own role pack whose definition of done is
*every landed-and-unread research unit in the window carries a verdict with a stated
population*. `performance-review` keeps trading performance and links to it.
⚠️ **A retirement is done when the old thing is GONE** — if this ships, the section must be
removed from `performance-review`, not left "for now".

**P-C3 · Update the ~6 role packs that govern how a session situates itself** so the
operating layer is reachable at all: `session-coordination`, `session-handoff`,
`doc-freshness`, `duty`, `research-driver`, `delegate-work`. They should name
`docs/claude/work/`, the WIP ceiling, and the constraint readout. **Do not sweep all 32** —
the domain packs are correctly indifferent to where work is tracked, and a blanket edit is
the "carrying everything" failure the model warns against.

**P-C4 · Point a role pack at the sunset pass.** `duty` is the right home: it already owns
*"every detected signal gets an owner"*, and 10 undispositioned retirement candidates are
exactly a detected signal with no owner. Add `comms/sunset/<date>/INDEX.json` as a due-list
source and `SUNSET-DISPOSITIONS.json` as a write target. Retiring a leg stays Tier-3 and
proposal-only; what changes is that *someone is made to look*.

**P-C5 · Merge candidate: `system-report` into `system-review`.** `system-report/SKILL.md` is
20 lines and states *"There is no separate procedure here."* It is a pure alias. Keeping it is
cheap; **retiring it is a genuine E3 exercise on a zero-risk target**, and the build plan's
done-condition for Phase G is *"something has actually been removed."* Flagged as the safest
available first removal, not as a priority.

**P-C6 · No skill is orphaned — do not retire on reference count.** All 32 are referenced
elsewhere in the repo (range 6–248 files; verified with a negative control). Retirement
candidacy must be argued on function overlap, and the only clear case is P-C5.

**Explicitly NOT proposed:** a CYCLE skill or a DECIDE skill. Both session types are
operator-present by design (A2 is *"None — operator"*), and the decision surfaces they would
read are themselves still unproven — `strategy-review-packets` has never proposed an action
(52/52 `hold`, all below the evidence floor) and the decision round-trip has never completed
one. A role pack for reading a surface that produces nothing would be capability built ahead
of the stage that pulls it, which is exactly what E2's pull rule exists to prevent.

---

## 3 · Half D — findings that do not depend on run history

### 3.1 A1's readout is Cadenced by design and has no cadence — CREATE

`scripts/ops/constraint_readout.py` shipped in Phase D. It computes the constraint and
renders `docs/claude/READOUT.md` + `docs/claude/CONSTRAINT.json`, whose headline is inlined
into the `CLAUDE.md` session brief. The operating model classifies **A1 as trigger
`Cadenced`, autonomy `Full`**.

**Measured: nothing runs it on a cadence, and nothing checks its freshness.**

- **No workflow invokes it.** `grep -rn constraint_readout .github/workflows/` → 0 hits.
- **CI runs `--self-test` only.** Its registration in `scripts/ci/run_guards.py:289` carries
  the note, in the guard's own words: *"self-test only; the readout itself is generated on
  demand, not in CI."*
- **Contrast with its sibling, which IS guarded.** `session-brief-guard` runs both
  `--self-test` **and** `--check --base origin/main`, so the session brief cannot go stale
  without reddening a PR. The readout has no equivalent.
- `CONSTRAINT.json` `generated_at` at time of audit: **2026-09-01T23:15:16Z** — i.e. as fresh
  as the last time a session remembered to type the command, which is precisely what the
  `CLAUDE.md` brief already warns: *"it is a dated snapshot, not a live read."*

This is the **`work_digest.py` defect recurring inside one day**, and in the function with
the largest blast radius: `work_digest.py` shipped with no trigger and appeared in 0 of 125
workflow files, which `work-digest.yml`'s own header records as a precedent it exists not to
repeat. A1 feeds A2 — the operator's cycle priority — and A1 depends on E1. A readout that
ages silently is worse than an absent one, because the brief presents it with a date most
readers will not check against today.

⚠️ **The remedy is a cron, not a guard.** A staleness `--check` would red every PR whenever
the world moved, which is the transient-red-strands-a-branch failure
(`BL-20260830-A-TRANSIENT-RED-BASE-PERMANENTLY-STRANDS-AN-AUTOMERGE-BRANCH`) that
`session-brief-guard` had to be diff-scoped to avoid. The shape that works here already
exists twice in this repo: `work-digest.yml` and `strategy-review-packets.yml` — a scheduled
job that regenerates and commits.

### 3.2 The two dead Routines are gone; the shape that killed them is back

The build plan lists deleting `Health Check Routine` (`trig_015diTGEy9jATecSPMFmPyNF`) and
`Sprint Continue Work` (`trig_01SvpLKYiTKgXkeLgV7Q3pEr`) as an outstanding **operator-only**
hand-off, *"attempted and refused 2026-09-01"*.

**Measured 2026-09-02: neither is in the register.** `list_triggers(enabled=true)` — a filter
whose documented behaviour hides fired one-shots and disabled routines, so a live-but-dormant
routine WOULD appear — returned exactly **2** routines, and neither is these. Positive
control: the call did return two live routines, so the probe finds positives.

⚠️ **What is established and what is not.** Established: they are absent from the register as
this session can read it. **Not** established: who deleted them, or when. Do not record this
as "the operator actioned the hand-off." Record it as *gone, attribution unknown*, and update
the build plan's retirement table to stop asking for an action that appears already done.

**But the shape is being reproduced today.** Both surviving enabled routines include
`trig_01NcP7PmwUeFPCbRN4G61VQX` — `enabled: true`, **no `cron_expression`**,
`next_run_at: 0001-01-01T00:00:00Z`, created 2026-09-02T05:35Z. That is **byte-identical in
register shape to the two dead Routines.** Here it is deliberate: a poke-only routine the
manager fires by hand via `fire_trigger`.

**That is the finding.** *A deliberate poke-only routine and a structurally-dead routine are
indistinguishable in the register.* `enabled: true` + no cron + `next_run_at: 0001-01-01` is
the same reading for both, and the register offers no field that separates *"fires only when
poked, on purpose"* from *"claims a cadence it can never have."* This is the collapsed-state
class this repo has a CI guard for, sitting in the control plane rather than in the code —
and it is the direct reason the two dead ones survived four months in plain sight. A reader
auditing the register today cannot repeat the 2026-09-01 diagnosis without knowing the
manager's intent out of band.

---

## 4 · Half D — the run-history census

**Population and method.** All **132** workflows in the GitHub Actions registry. For each,
lifetime run count via `list_workflow_runs` `total_count`; for the 20 cron-carrying files,
a second query filtered to `event=schedule`. Zero `could_not_read`. Measured 2026-09-02
~05:50–06:05Z.

⚠️ **`runs_total` is NOT a usage signal for the 101 issue-driven workflows, and reading it
as one would be the audit's own biggest trap.** Every issue opened in the repo triggers
*every* `issues:`-triggered workflow, which then evaluates its label filter and **skips**.
So `provision-live-vm.yml` reads 10,690 lifetime runs with `last_concl=skipped`, and
`claude-run-failure-alert.yml` reads **24,445**. Those are trigger evaluations, not work.
A high `runs_total` on an issue-driven workflow says nothing about whether it has ever
done anything, and a *low* one says only that it was added recently. **Retire/retain
verdicts below are therefore argued on cron evidence, on `runs_total == 0`, and on the
workflow's own declared purpose — never on issue-driven run counts.**

### 4.1 The registry gap: 132 registered, 129 on disk

Three registry entries have **no file on disk** — verified individually:
`pytest-diag.yml`, `econ-source-probe.yml`, `fmp-stable-probe.yml`. GitHub keeps a
workflow's record after its file is deleted. These are not defects; they are **completed
retirements**, and two of them are exemplary:

- `econ-source-probe.yml` (3 runs, last 2026-07-29) — deleted in *"feat(m1): FXStreet keyless
  economic-calendar source (replaces FMP)"*.
- `fmp-stable-probe.yml` (2 runs, last 2026-07-29) — deleted in *"docs(m28): FMP /stable
  free-tier findings — **NO-BUILD (verify-before-build)**"*.

A probe built to answer one question, run, read, acted on, and deleted. That is E3 working.

### 4.2 The build plan's retirement claim is wrong about workflows and right about skills

The build plan states: *"nothing has ever retired a skill, register, workflow or guard.
Complexity is monotonic by construction."*

**Measured — population: files deleted on `origin/main` between 2026-06-13 (the oldest
commit in this clone's fetched history) and 2026-09-02.** ⚠️ The clone is shallow, so this
is *"since 2026-06-13"*, **not** *"ever"*; the true totals can only be higher. Positive
control: 137 files were deleted in total across the tree, so the search finds deletions.

| machinery | deleted | verdict |
|---|---:|---|
| **workflows** | **35** | claim **REFUTED** |
| `scripts/ops/` scripts | 7 | claim refuted |
| **skills** (`SKILL.md`) | **0** | claim **HOLDS** |
| **CI guards** (`scripts/ci/`) | **0** | claim **HOLDS** |
| **registers** (`docs/claude/*.json`) | **0** | claim **HOLDS** |

The 35 workflow deletions fall in five commits, and one dominates: **2026-08-07 consolidated
30 per-guard workflows into a single `guards` job** — the largest retirement event in the
repo's fetched history. The others are genuine sunsets, including `grade-order-packages.yml`,
retired 2026-06-24 by explicit operator directive.

**Why the correction matters rather than being pedantry.** Phase G's done-condition is
*"something has actually been removed."* On the measured record that condition was **already
met before Phase G opened** — repeatedly, and in one case at a scale of 30 files. The forcing
function is not absent for workflows; it is absent for **skills, guards and registers**,
where the count is a clean zero. A Phase-G plan aimed at "nothing is ever removed" will aim
at the wrong target.

### 4.3 The cron fleet: 20 workflows, three distinct states

Grading a cron requires separating three states that a bare "has it run?" collapses:
**fired** · **had a window and missed it** · **has never had a window**.

| workflow | cron | schedule runs | state |
|---|---|---:|---|
| health-snapshot | `0 */6 * * *` | **442** | fires reliably |
| purge-artifacts | `0 3 * * *` | 86 | fires |
| diag-relay-sweep | `0 6 * * *` | 97 | fires |
| replay-pregate-nightly | `0 4 * * *` | 67 | fires — **5 of 5 most recent = `failure`** |
| broker-bracket-reconcile | `40 */6 * * *` | 41 | fires |
| macro-valuation-snapshot | `30 7 * * *` | 40 | fires |
| macro-producer-liveness | `0 12 * * *` | 37 | fires |
| econ-calendar-produce | `30 22 * * *` | 35 | fires |
| doc-audit-weekly | `0 12 * * 1` | 17 | fires |
| research-queue-dispatch | `20 6 * * *` | 7 | fires — 2 of 5 recent `failure` |
| econ-event-study | `10 23 * * 0` | 5 | fires |
| research-backtest-augment | `0 6 * * 1` | 4 | fires |
| oci-inventory | `0 6 * * 1` | 3 | fires |
| alpaca-settlement-soak-watch | `30 14 * * 1-5` | 2 | fires |
| **work-digest** | `20 2,6,…` | **1** | fired once (2026-09-02T00:19Z) — landed 2026-09-01T23:11Z |
| **probes** | `20 5 * * *` | **1** | see below |
| **due-list** | `50 5 * * *` | **1** | see below |
| **strategy-review-packets** | `40 4 * * *` | **0** | **window passed, nothing fired** |
| **sunset-pass** | `10 5 * * 1` | **0** | **has never had a window** — Mondays; landed Wed 2026-09-02T04:33Z |
| **work-decision-commit** | `35 1,3,5,…` | **0** | **has never had a window** — landed 2026-09-02T05:39Z, next slot 07:35Z |

**Read the last two rows correctly.** `sunset-pass` and `work-decision-commit` have zero
scheduled runs *because their first window has not arrived*. That is the third state, and
grading it as failure would be exactly the collapse both files' own headers warn about.
**Neither is a defect. Do not file one.**

**`probes` / `due-list` — the OPEN-ITEMS row was cleared on a true fact that is not the
whole fact.** `OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON` was
cleared today because `probes.yml` did fire on cron. Confirmed: `event=schedule`,
2026-09-01T10:12:17Z, success; `due-list` likewise at 10:29:14Z. But measured over the full
window since each landed:
- `probes` (landed 2026-09-01T01:10Z, cron 05:20Z) has had **two** windows — 09-01 and 09-02.
  It fired on **one**, and that firing was **~4h52m late**. The 2026-09-02 05:20Z window had
  passed 43 minutes before this measurement with no run.
- `due-list` (cron 05:50Z) is the same shape; its 09-02 window had passed only ~13 minutes
  before the read, which is **too close to call** and is recorded as such rather than as a miss.

So the honest state is **1 of 2 windows, with the one that fired nearly five hours late** —
not "the cron works." The row was right to clear on *"has it ever fired"*; the reliability
question behind it is still open and is a different question.

**`strategy-review-packets` — C3's cron has now had exactly one window and produced
nothing.** `OI-20260901-REVIEW-PACKET-…` states its first scheduled slot is
2026-09-02T04:40Z. That slot passed; a live re-query at ~06:05Z returned
`total_count: 0` for `event=schedule`. The workflow *has* run 20 times, all dispatch- or
issue-driven. ⚠️ **A late fire cannot be ruled out** — `probes` fired 4h52m late on this
same repo — so this is recorded as *window passed, nothing yet*, not as a confirmed miss.
Re-read before grading it.

### 4.4 `replay-pregate-nightly` is red every night and has been for at least five nights

67 scheduled runs lifetime; the **five most recent are all `failure`** (2026-08-28 through
2026-09-01). This is the workflow named in
`BL-20260813-CRON-WORKFLOWS-FAIL-SILENTLY-ON-SCHEDULE-PATH` — *"failed 3/3 consecutive
scheduled runs and NOTHING alerts"* — and in `due-list.yml`'s own header: *"`replay-pregate-nightly`
failed identically on 2026-08-13 and again on 2026-08-31, and the response in between was to
add it to an alert list. Detection was raised twice; disposition never happened."*

**It is now on the alert list** (§ 1.2: all 20 scheduled workflows are watched, and that
coverage gap is closed). So the alarm fires and the failure persists — which is precisely
the **desensitized-alarm P1** this repo calls its own worst failure mode: a nightly red that
everyone has learned to walk past. **REPAIR, and it is the single most actionable item in
half D.**

### 4.5 Verdict inventory — retain / retire / repair / create

⚠️ **87 of the 129 workflows CANNOT be graded on dormancy, and saying so is part of the
answer.** They are issue-label-driven; their run history is dominated by skipped
label-filter evaluations, the most recent of which is always today. There is no cheap
surface that answers *"has this workflow ever actually done work, and when last?"* — that
needs a per-workflow scan filtered to non-skipped conclusions. **This is the single biggest
blind spot in the estate**, and it is why the retire list below is short: an honest audit
cannot retire what it could not measure. See **P-D5**.

**REPAIR (4) — evidence-backed, ordered by cost of leaving it**

| workflow | evidence | why |
|---|---|---|
| `replay-pregate-nightly` | 67 schedule runs; **5 of 5 most recent = `failure`** | Red every night. Now correctly alarmed (§1.2), so it is generating a nightly page nobody acts on — the desensitized-alarm P1 in the making. Named in `BL-20260813` and in `due-list.yml`'s own header as the motivating example of *"detection raised twice, disposition never"* |
| `probes` + `due-list` | 1 of 2 windows each; the firing was **~4h52m late**; 2026-09-02's window passed with nothing | The OPEN-ITEMS row cleared on *"has it ever fired"* — true. Reliability is a **different, still-open question** |
| `strategy-review-packets` | 20 lifetime runs, **0 on cron**; the 04:40Z window passed, live re-query at 06:05Z returns `total_count: 0` | C3's whole point is a *committed, cadenced* packet. A late fire is possible; **re-read before grading** |
| `ict-scalp-exit-sweep` | 5 runs, last 2026-08-10, **`failure`** | Left red for 23 days |

**CREATE (1, and it is the highest-leverage item in this document)**

- **A cron for `constraint_readout.py --write`** (§ 3.1). A1 is `Cadenced`/`Full` by design
  and has neither. Copy the shape of `work-digest.yml` / `strategy-review-packets.yml`:
  scheduled job → regenerate → commit. This is the readout the operator's cycle priority
  rests on, and today it is only as fresh as the last session that typed the command.

**RETIRE — candidates only, all Tier-1 file deletions, all requiring an operator nod (8)**

Every one is >70 days dormant on a *real* (non-skipped) trigger, and each one's own header
declares it spent. **None is a confident call from the file alone — that is the whole
warning in the brief** — so each carries what a reviewer must check first:

| candidate | dormant | its own header says | check before deleting |
|---|---:|---|---|
| `hf-cron` | 122d, last run **failed** | *"Disabled the daily cron (CP-2026-05-02-02)"* | Is HF training genuinely abandoned? |
| `training-run` | 122d | *"Autonomous training/improvement run"* | Superseded by the trainer lane? |
| `oci-storage` | 114d | targets *"the RETIRED x86 micro `ict-bot` (terminated 2026-06-16)"* | Its target host no longer exists |
| `oci-storage-verify` | 114d | health check for the same setup | Same |
| `deploy-trainer-bootstrap` | 112d, last run **failed** | *"One-shot bootstrap"* | Needed to re-provision a trainer? |
| `training-rerun-5m` | 87d, last run **failed** | one named 2026-05-07 experiment | Experiment closed? |
| `provision-training-vm-auto-retry` | 84d | *"Cron-driven retry loop"* — **its cron is already gone** | Cron removed but file kept |
| `vm-driver` | 73d | *"Git-push-triggered remote driver"* | Superseded by `trainer-diag-relay` / `board-post`? |

**Not retire-listed despite 0 lifetime runs, and the reason matters:**
`sunset-pass` and `work-decision-commit` landed **today** and their first cron window has not
arrived. `continue-work` is already on the build plan's Phase-E retirement list.
`rotate-account-keys` is a credential path — never-used is the *desired* state for a break-glass
tool. `research-e2-horizon-arm` is queue-dispatched and may simply not have been selected.

**RETAIN — everything else (116).** Including all 87 ungradeable issue-driven relays: an
unmeasured workflow is not a dead one, and the correct response is P-D5, not deletion.

### 4.6 Proposal — workflows

**P-D1 · Cron the constraint readout.** § 3.1. The one CREATE.
**P-D2 · Fix or disable `replay-pregate-nightly`.** Five consecutive red nights with an alarm
attached is worse than either a green run or an honest disable. If the fix is not small, the
Prime-Directive-consistent move is to say so in the workflow and stop the nightly page, not
to leave the alarm ringing.
**P-D3 · Re-read `probes` / `due-list` / `strategy-review-packets` cron state in 24–48h**
before concluding anything. All three are within one or two windows of landing, and this repo
has measured a 4h52m scheduling lag. **Do not grade them today.**
**P-D4 · Put the eight retire candidates to the operator as one batch.** Deleting a workflow
file is Tier-1 and reversible via git; the registry keeps the record either way (§ 4.1).
Phase G's done-condition is already met historically (§ 4.2), so this is hygiene, not the
forcing function.
**P-D5 · Build the measurement that is missing: non-skipped run counts per workflow.** Without
it, 87 of 129 workflows are permanently unretireable-by-evidence, and every future sunset pass
will hit the same wall this one did. `sunset-pass.yml` already reads committed artifacts on a
cadence and is the natural host.
**P-D6 · Record the Routine-register collapsed state** (§ 3.2) — `enabled: true` + no cron +
`next_run_at: 0001-01-01` cannot distinguish a deliberate poke-only routine from a dead one.
This is a control-plane limitation, so the repo-side remedy is a convention: name poke-only
routines with a `POKE:` prefix so the register is self-describing.

**Explicitly NOT proposed: any mass deletion.** 35 workflows were already removed in five
commits (§ 4.2), 30 of them in one consolidation. The estate is not accreting unchecked; it
is *unmeasured*, which is a different problem with a different fix.

---

## 5 · What this audit did not do

- **It graded no issue-driven workflow on dormancy** (87 of 129) — the data does not exist. P-D5.
- **It did not re-read the three near-window crons** after their next slot. P-D3.
- **It established that the two dead Routines are absent, not who removed them.** § 3.2.
- **Its retirement history is bounded at 2026-06-13**, the oldest commit in this shallow
  clone. The 35/0/0/0 counts in § 4.2 are lower bounds, not lifetime totals.
- **It enacted nothing.** No skill, workflow, register or Routine was created, edited or
  deleted. The only file added is this one. ⚠️ **This remains true OF THIS AUDIT and is no
  longer true of its findings** — MI-66 enacted the CREATE, three REPAIRs, P-C3 and P-C4 on
  2026-09-02; see § 0b for the four measurements that did not survive that contact.

---

## 6 · Appendix — all 129 workflows

`fn` = primary operating-model function. `trig` = declared triggers (s=schedule, d=workflow_dispatch, i=issues, p=push, r=pull_request).
`runs` = LIFETIME run count — ⚠️ **for `i` rows this counts skipped label-filter evaluations and is NOT a usage signal** (§ 4).
`last` = date + event + conclusion of the most recent run.

| workflow | fn | trig | cron | runs | last |
|---|---|---|---|---:|---|
| `alpaca-options-probe` | C1 | i |  | 4130 | 2026-09-02 issues skipped |
| `alpaca-settlement-soak-watch` | C6 | sdi | `30 14 * * 1-5` | 3 | 2026-09-01 schedule success |
| `arm-candidate-diag` | D1 | di |  | 10686 | 2026-09-02 issues skipped |
| `board-post` | F3 | ip |  | 145 | 2026-09-02 push success |
| `bootstrap-labels` | F5 | dip |  | 87 | 2026-09-01 push success |
| `branch-protection-report` | F5 | i |  | 6629 | 2026-09-02 issues skipped |
| `branch-protection-sync` | F5 | dp |  | 2889 | 2026-09-02 push queued |
| `broker-bracket-reconcile` | D2 | sdi | `40 */6 * * *` | 43 | 2026-09-02 schedule success |
| `c1-conviction-ab` | C1 | di |  | 1446 | 2026-09-02 issues skipped |
| `cancel-queued-runs` | E3 | di |  | 5618 | 2026-09-02 issues skipped |
| `claude-pr-automerge` | F3 | p |  | 19 | 2026-08-22 push success |
| `claude-run-failure-alert` | D1 | - |  | 24445 | 2026-09-02 workflow_run skipped |
| `continue-work` | B3 | d |  | 0 | never |
| `cot-positioning-backfill` | C1 | di |  | 2063 | 2026-09-02 issues skipped |
| `crypto-signals-backfill` | C1 | di |  | 2063 | 2026-09-02 issues skipped |
| `cutover-live` | C5 | di |  | 10228 | 2026-09-02 issues skipped |
| `delete-merged-branches` | E3 | i |  | 6628 | 2026-09-02 issues skipped |
| `deploy-candidate` | C5 | di |  | 10684 | 2026-09-02 issues skipped |
| `deploy-trainer-bootstrap` | C5 | dip |  | 4 | 2026-05-13 push failure |
| `diag-relay-sweep` | D4 | sdi | `0 6 * * *` | 98 | 2026-09-01 schedule success |
| `doc-audit-weekly` | F5 | sdi | `0 12 * * 1` | 7224 | 2026-09-02 issues skipped |
| `due-list` | D4 | sdi | `50 5 * * *` | 51 | 2026-09-02 issues skipped |
| `dukascopy-coverage-probe` | C1 | d |  | 5 | 2026-08-29 workflow_dispatch success |
| `dukascopy-span-probe` | C1 | d |  | 1 | 2026-08-24 workflow_dispatch success |
| `e35-bracket-sweep` | C1 | d |  | 12 | 2026-08-31 workflow_dispatch success |
| `econ-calendar-backfill` | C1 | di |  | 1800 | 2026-09-02 issues skipped |
| `econ-calendar-produce` | C1 | sdi | `30 22 * * *` | 1898 | 2026-09-02 issues skipped |
| `econ-calendar-survey-backfill` | C1 | di |  | 1765 | 2026-09-02 issues skipped |
| `econ-event-study` | C1 | sdi | `10 23 * * 0` | 1856 | 2026-09-02 issues skipped |
| `external-comment-alert` | D1 | i |  | 1767 | 2026-09-02 issue_comment skipped |
| `external-issue-alert` | D1 | i |  | 4050 | 2026-09-02 issues skipped |
| `flip-override-walkforward` | C1 | di |  | 1247 | 2026-09-02 issues skipped |
| `get-diag-token` | C5 | di |  | 6653 | 2026-09-02 issues skipped |
| `gld-compat-matrix` | C1 | di |  | 1528 | 2026-09-02 issues skipped |
| `gpu-burst-train` | C1 | i |  | 3677 | 2026-09-02 issues skipped |
| `guards` | F5 | dpr |  | 3477 | 2026-09-02 pull_request in_progress |
| `health-snapshot` | D1 | sdi | `0 */6 * * *` | 7511 | 2026-09-02 schedule success |
| `hf-cron` | C1 | d |  | 568 | 2026-05-03 push failure |
| `ict-scalp-backtest` | C1 | di |  | 6991 | 2026-09-02 issues skipped |
| `ict-scalp-exit-sweep` | C1 | dip |  | 5 | 2026-08-10 push failure |
| `init-actions-secrets` | C5 | di |  | 5849 | 2026-09-02 issues skipped |
| `llm-delegate` | C1 | di |  | 25 | 2026-08-20 workflow_dispatch success |
| `m20-capture-census` | C1 | dip |  | 8 | 2026-08-17 workflow_dispatch success |
| `m20-exit-lever-sweep` | C1 | dip |  | 55 | 2026-08-29 workflow_dispatch success |
| `m28-value-grade` | C1 | di |  | 2045 | 2026-09-02 issues skipped |
| `m31-implied-vol-grade` | C1 | di |  | 2038 | 2026-09-02 issues skipped |
| `m32-credit-curve-grade` | C1 | di |  | 2033 | 2026-09-02 issues skipped |
| `m33-seasonality-grade` | C1 | di |  | 2029 | 2026-09-02 issues skipped |
| `m34-xfamily-grade` | C1 | di |  | 2027 | 2026-09-02 issues skipped |
| `macro-producer-liveness` | D1 | sdi | `0 12 * * *` | 2002 | 2026-09-02 issues skipped |
| `macro-valuation-backfill` | C1 | di |  | 2069 | 2026-09-02 issues skipped |
| `macro-valuation-snapshot` | C1 | sdi | `30 7 * * *` | 2113 | 2026-09-02 issues skipped |
| `merge-claim-audit` | F3 | i |  | 446 | 2026-09-02 pull_request_target success |
| `news-key-check` | C6 | di |  | 5474 | 2026-09-02 issues skipped |
| `oci-inventory` | D1 | sdi | `0 6 * * 1` | 333 | 2026-09-02 issues skipped |
| `oci-storage` | C5 | d |  | 7 | 2026-05-11 workflow_dispatch success |
| `oci-storage-verify` | C6 | di |  | 5 | 2026-05-11 workflow_dispatch success |
| `pr-close` | F3 | p |  | 4 | 2026-08-22 push success |
| `pr-opener` | F3 | p |  | 88 | 2026-09-02 push success |
| `probes` | D4 | sdi | `20 5 * * *` | 52 | 2026-09-02 issues skipped |
| `prop-report` | C5 | di |  | 4679 | 2026-09-02 issues skipped |
| `prop-tp-r-gate` | C1 | di |  | 209 | 2026-09-02 issues success |
| `provision-gateway-vm` | C5 | di |  | 10682 | 2026-09-02 issues skipped |
| `provision-ib-gateway` | C5 | di |  | 6625 | 2026-09-02 issues skipped |
| `provision-live-vm` | C5 | di |  | 10690 | 2026-09-02 issues skipped |
| `provision-training-vm` | C5 | di |  | 7211 | 2026-09-02 issues skipped |
| `provision-training-vm-auto-retry` | C5 | di |  | 336 | 2026-06-10 schedule success |
| `prune-landed-branches` | E3 | di |  | 213 | 2026-09-02 issues skipped |
| `prune-merged-claude-branch` | E3 | di |  | 197 | 2026-09-02 issues skipped |
| `pullback-frac-cross-leg-sweep` | C1 | d |  | 3 | 2026-08-24 workflow_dispatch success |
| `purge-artifacts` | E3 | sdi | `0 3 * * *` | 5707 | 2026-09-02 issues skipped |
| `pytest-collect` | F5 | dpr |  | 10854 | 2026-09-02 push queued |
| `pytest-run` | F5 | dpr |  | 9733 | 2026-09-02 push success |
| `regime-adx-cutpoint-sweep` | C1 | di |  | 1489 | 2026-09-02 issues skipped |
| `regime-cell-walkforward` | C1 | di |  | 1841 | 2026-09-02 issues skipped |
| `regime-debt-matrix` | C1 | di |  | 1842 | 2026-09-02 issues skipped |
| `replay-pregate-nightly` | C1 | sdi | `0 4 * * *` | 4383 | 2026-09-02 issues skipped |
| `repo-inventory` | F1 | dpr |  | 9410 | 2026-09-02 push queued |
| `research-backtest-augment` | C1 | sdi | `0 6 * * 1` | 1490 | 2026-09-02 issues skipped |
| `research-e2-horizon-arm` | C1 | d |  | 0 | never |
| `research-exit-head-build` | C1 | di |  | 1900 | 2026-09-02 issues skipped |
| `research-exit-head-replay-trainer` | C1 | di |  | 1332 | 2026-09-02 issues skipped |
| `research-panel-build` | C1 | di |  | 1921 | 2026-09-02 issues skipped |
| `research-queue-dispatch` | B3 | sd | `20 6 * * *` | 11 | 2026-09-01 schedule success |
| `research-symbol-p0-build` | C1 | di |  | 1874 | 2026-09-02 issues skipped |
| `reserve-live-ip` | C5 | di |  | 10156 | 2026-09-02 issues skipped |
| `reset-daily-risk-state` | C5 | i |  | 3858 | 2026-09-02 issues skipped |
| `reset-instance` | C5 | di |  | 6536 | 2026-09-02 issues skipped |
| `rotate-account-keys` | C5 | d |  | 0 | never |
| `scope-overlap-audit` | F3 | - |  | 158 | 2026-09-02 pull_request_target success |
| `set-diag-token` | C5 | di |  | 6653 | 2026-09-02 issues skipped |
| `strategy-review-packets` | C3 | sdi | `40 4 * * *` | 20 | 2026-09-02 issues skipped |
| `sunset-pass` | E3 | sd | `10 5 * * 1` | 0 | never |
| `sync-vm-secrets` | C5 | di |  | 5379 | 2026-09-02 issues skipped |
| `sysdyn-gas-calibrate` | C1 | di |  | 2063 | 2026-09-02 issues skipped |
| `system-actions` | C5 | di |  | 7490 | 2026-09-02 issues skipped |
| `terminate-instance` | C5 | di |  | 10682 | 2026-09-02 issues skipped |
| `test-alpaca-creds` | C6 | i |  | 4261 | 2026-09-02 issues skipped |
| `test-alpaca-from-vm` | C6 | i |  | 4247 | 2026-09-02 issues skipped |
| `trainer-diag-relay` | F1 | p |  | 10 | 2026-09-02 push success |
| `trainer-offload-train` | C1 | di |  | 1845 | 2026-09-02 issues skipped |
| `trainer-vm-diag` | F1 | di |  | 7284 | 2026-09-02 issues success |
| `training-rerun-5m` | C1 | dp |  | 11 | 2026-06-07 push failure |
| `training-run` | C1 | dp |  | 3 | 2026-05-03 workflow_dispatch success |
| `vix-term-backtest` | C1 | di |  | 2030 | 2026-09-02 issues skipped |
| `vm-bybit-diag` | D1 | di |  | 10152 | 2026-09-02 issues skipped |
| `vm-caddy-deploy` | C5 | di |  | 2837 | 2026-09-02 issues skipped |
| `vm-cloud-fix` | C5 | di |  | 7313 | 2026-09-02 issues skipped |
| `vm-cloud-open-ib-port` | C5 | di |  | 5330 | 2026-09-02 issues skipped |
| `vm-devnull-deploy-bootstrap` | C5 | di |  | 10118 | 2026-09-02 issues skipped |
| `vm-devnull-source-diagnose` | D1 | di |  | 6356 | 2026-09-02 issues skipped |
| `vm-diag-snapshot` | F1 | di |  | 7591 | 2026-09-02 issues skipped |
| `vm-driver` | C5 | p |  | 61 | 2026-06-21 push success |
| `vm-fix-devnull` | C5 | di |  | 10136 | 2026-09-02 issues skipped |
| `vm-git-credential-bootstrap` | C5 | di |  | 3496 | 2026-09-02 issues skipped |
| `vm-ib-gateway-deploy` | C5 | di |  | 4725 | 2026-09-02 issues skipped |
| `vm-ib-gateway-live-login-test` | C6 | di |  | 10112 | 2026-09-02 issues skipped |
| `vm-ib-gateway-recover` | D2 | di |  | 6582 | 2026-09-02 issues skipped |
| `vm-ib-gateway-selftest` | C6 | di |  | 4715 | 2026-09-02 issues skipped |
| `vm-ib-gateway-stop` | C5 | di |  | 5698 | 2026-09-02 issues skipped |
| `vm-ib-gateway-watchdog-enable` | C5 | di |  | 5490 | 2026-09-02 issues skipped |
| `vm-net-diag` | D1 | di |  | 7318 | 2026-09-02 issues skipped |
| `vm-net-fix` | C5 | di |  | 7317 | 2026-09-02 issues skipped |
| `vm-resize-live` | C5 | di |  | 5371 | 2026-09-02 issues skipped |
| `vm-web-api-recover` | D2 | di |  | 7330 | 2026-09-02 issues skipped |
| `vwap-backtest` | C1 | di |  | 7078 | 2026-09-02 issues skipped |
| `work-decision-commit` | C4 | sd | `35 1,3,5,7,9,11,13,15,17,19,21,23 * * *` | 0 | never |
| `work-digest` | F6 | sd | `20 2,6,10,14,18,22 * * *` | 1 | 2026-09-02 schedule success |
| `yfinance-lane-proof` | C1 | d |  | 19 | 2026-08-29 workflow_dispatch success |
