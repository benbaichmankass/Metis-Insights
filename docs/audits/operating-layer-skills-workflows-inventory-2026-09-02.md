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
§ 4.2 names them.

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

