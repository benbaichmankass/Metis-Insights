# Document index — the centralized register

> **Doc status:** `unknown` · category `unknown` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

**One table. Every document a session might read as instruction or as evidence.**

Operator, 2026-09-07:

> *"we need a library for our documents. Like, we're having a hard time keeping
> track of things, and you're right that a lot of things probably look fine if
> you just open them on their own. So maybe, like, one centralized table where
> we keep track of everything, and everything has to be organized correctly and
> logged and categorized."*

## The failure this exists to stop, measured 2026-09-07

A manager reported roadmap status **to the operator** off
[`docs/research/WORKPLAN-2026-08-14.md`](research/WORKPLAN-2026-08-14.md), which
declares **`Status: ACTIVE`** in its own header while being **three generations
superseded** (08-14 → 08-21 → 08-26 → 08-29). Twelve work-plan documents exist,
`ROADMAP.md` pointed readers at the wrong one, and
[`docs/claude/CYCLE-PRIORITY.json`](claude/CYCLE-PRIORITY.json) — newer than
every work plan — is what actually drives sessions. Three surfaces, three
answers, and the operator got a wrong report.

**It looked fine opened on its own.** That is why this ships as two artifacts,
not one: this table, **and** a status stamp in every registered document's own
header. A reader who opens one file directly must not be misled. An index alone
would not have prevented the failure, because the manager never opened it.

## What keeps it true

[`scripts/ci/check_document_index.py`](../scripts/ci/check_document_index.py)
runs in CI on every PR and **fails** when:

| # | condition |
|---|---|
| **R1** | a document exists in the population and is **not registered** here |
| **R2** | a registered row names a file that **no longer exists** |
| **R3** | a file's own header status **disagrees** with its row here |
| **R4** | a row is `superseded` without naming a `superseded_by` |
| **R5** | a row uses a `category` or `status` outside the closed sets below |

It carries a **planted-positive control** (`--self-test`) that runs on every
invocation and fails the job unless each rule actually fires on a known-bad
input. A guard nobody has seen fail is not evidence.

## Category — a closed set of six

Assigned from the repo's own directory structure (a real structural fact about
where this repo puts things), or individually for the handful of paths where no
directory rule applies. **`instruction`, `evidence` and `history` are kept
apart because conflating them is how a superseded plan gets obeyed** — and
`plan` is its own category for exactly the same reason: on 2026-09-07 a *plan*
was read as an *instruction*.

> The set deliberately says `lookup`, not `reference` — `reference` is already
> a **status** value, and one word meaning two things in one table is how a
> vocabulary stops being closed.

| category | rows | meaning |
|---|---|---|
| `instruction` | 44 | A session must OBEY it. Binding. |
| `architecture` | 87 | A declared contract about how the system is built; read as truth about the system. |
| `plan` | 63 | A forward commitment -- what we intend to do next. NEVER obeyed as instruction. |
| `evidence` | 323 | A measurement that may be CITED. True of its date and population. |
| `history` | 367 | A record of what happened. Never obeyed, never cited as a current measurement. |
| `lookup` | 44 | Consulted for a fact. Neither obeyed, nor a measurement, nor a record of events. |
| `unknown` | 71 | **Could not be categorised. Not a category — the absence of one.** |

## Status — a closed set of six

| status | rows | meaning |
|---|---|---|
| `live` | 44 | Current. A session may act on it today. |
| `superseded` | 3 | Overtaken by a named successor. REQUIRES `superseded_by`. |
| `closed_unfinished` | 9 | Abandoned mid-flight. NOT the same fact as superseded -- record what was left. |
| `historical` | 369 | A record of something that happened. Correct forever, actionable never. |
| `reference` | 0 | Consulted on demand. Neither current-and-actionable nor superseded. |
| `unknown` | 574 | NOBODY HAS CHECKED. Not a soft 'live'. The honest state, and a required one. |

⚠️ **`superseded` and `closed_unfinished` are DIFFERENT FACTS and are never
collapsed.** Overtaken by a successor is not the same as abandoned mid-flight.
Where a document closed unfinished, the row records what was left — or records
plainly that the file itself does not say.

⚠️ **`unknown` is a real, required state, and it is not a soft `live`.** It
means *nobody has checked*. A status this register cannot establish is written
`unknown` rather than guessed: an invented status is worse than an absent one,
because it reads as checked. That is exactly how `WORKPLAN-2026-08-14.md`
fooled a manager.

## How a status is established — the `basis` column

Every row names what established it. A row whose status basis is
`not-assessed` is making **no claim at all**.

| basis | what it means |
|---|---|
| `ci:canonical-doc-coherence-ACTIVE_DOCS` | a CI guard enforces this document's currency; the guard failing on drift **is** the evidence it is live. Imported from that guard, never copied. |
| `harness:skill-is-loaded-and-invocable` | the harness loads and offers this skill to every session; a stale one is executed, not merely read. |
| `dir:record-of-a-completed-session` | a record of a session that already happened — what the directory *means*, not an inference about the file. |
| `self-declared:<word>` | the document declares itself dead. See the asymmetry below. |
| `deferred:MI-159` | a concurrent session owns this determination. See below. |
| `not-assessed` | **nobody looked.** Status is `unknown`. |

### The self-declaration asymmetry — deliberate

A document that declares itself **dead** is recorded on that declaration. A
document that declares itself **alive** is **not** — it is `unknown`.

That asymmetry *is* the lesson of 2026-09-07. A self-declared death is rarely
wrong in the dangerous direction; a self-declared life is precisely the claim
that misled a manager. Measured across this population, **201 of 999 documents
declare something status-like in their first 15 lines, in an entirely
uncontrolled vocabulary** — including `tier`, `scope`, `a proposal`,
`measured`, and `credentialfree pipeline built`. There was no controlled status
vocabulary anywhere in this repo before this file.

### The work-plan family — imported from MI-159, not re-derived

⚠️ **These rows read `deferred:MI-159` / `unknown` until #11241 merged
(`34e7a2bd`, 2026-09-07). Do not re-quote that.** While it was open, a
concurrent session owned the determination and two sessions deciding
independently which plan is live would have reproduced the exact defect both
were fixing. It has landed, so the deferral is over and this register now reads
its answer — `live` ×1, `superseded` ×3 (each naming its successor),
`closed_unfinished` ×6, `historical` ×2.

**It is IMPORTED, never copied.** The basis `mi159:plan-status-header:<state>`
comes from running
[`check_one_live_workplan.py`](../scripts/ci/check_one_live_workplan.py)'s own
`discover` + `parse` over the tree — the same discipline
`ci:canonical-doc-coherence-ACTIVE_DOCS` uses. A second hand-maintained table of
"which work plan is live" is precisely the multi-surface drift this register
exists to stop; if that guard and this index ever disagree, one of them is
broken and it is not a fact to reconcile by hand.

Its vocabulary is nearly ours. `closed_finished` is the one value we do not
carry, and it maps to `historical` — a completed plan is a record of something
that happened: correct forever, actionable never. Where a plan is
`closed_unfinished`, this table carries **what was left**, taken from the file's
own header rather than summarised.

**`ROADMAP*.md` stays `unknown`, deliberately.** MI-159 corrected its milestone
statuses the same day, but that guard's discovery matches work plans only and
`ROADMAP.md` carries no machine-readable status header — so there is nothing to
import, and this register does not assert a status it cannot establish.

## Documents exempt from the HEADER rule (R3), and why

A generator rewrites these wholesale, so a stamp written into one would be
erased on its next run — leaving an index and a header disagreeing with nobody
at fault. They stay **registered**; only R3 is waived.

**The waiver is verified, never presence-only:** the named generator must exist
*and* must name the file, checked at build time. A marker naming a generator
that does not exist grants nothing. (This repo has been bitten by presence-only
markers that were cheaper to lie to than to satisfy — `new-table-wiring-guard`.)

| document | generator | waiver |
|---|---|---|
| `docs/claude/DUE.md` | `scripts/ops/render_due_list.py` | verified: generator exists and names the file |
| `docs/claude/READOUT.md` | `scripts/ops/constraint_readout.py` | verified: generator exists and names the file |
| `docs/strategy-coverage-matrix.md` | `scripts/check_strategy_coverage.py` | verified: generator exists and names the file |
| `docs/training-population-matrix.md` | `scripts/check_training_population.py` | verified: generator exists and names the file |

## Maintaining this file

Rebuild it — never hand-edit a row:

```bash
python3 scripts/ops/document_index.py --census   # report only, no writes
python3 scripts/ops/document_index.py --write    # rebuild table + stamp headers
```

Adding a document to `docs/**` (at ANY depth, top level included), `ROADMAP*.md`, `CLAUDE.md` or
`.claude/skills/**` and not re-running `--write` **fails CI (R1)**. That is the
point: registration is not optional, and it is not left to memory.

## The table

**Population: 999 documents** — every file matching the git pathspecs
`:(glob)docs/**/*.md`, `ROADMAP*.md`, `CLAUDE.md`, `:(glob).claude/skills/**/*.md`
as tracked by `git ls-files` (so an untracked scratch file can never silently
enter or leave the register). **999 registered.**

⚠️ **The `:(glob)` prefix is part of the population, not decoration.** These are
git PATHSPECS: without it, `*` crosses `/` and `**/` needs a literal intervening
slash, so `docs/**/*.md` silently excluded all 28 `.md` files sitting directly in
`docs/` — including the two that outrank `CLAUDE.md` — while this table reported
100% coverage over what was left (population 971, all registered, guard `OK`).
Fixed 2026-09-07, population 971 → 999; the guard's `--self-test` now plants a
real top-level `docs/*.md` and fails if the population builder cannot see it.

<!-- DOCUMENT-INDEX-ROWS-BEGIN -->

| path | category | status | superseded_by | last_verified | basis | note |
|---|---|---|---|---|---|---|
| `.claude/skills/backlog-drain/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/backtesting/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/before-asking-the-operator/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/credentials-and-vm-mutations/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/db-setup/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/db-wiring/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/delegate-work/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/diag-data/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/doc-freshness/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/drift-remediation/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/duty/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/exit-refinement/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/full-system-audit/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/git-actions/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/health-review/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/llm-delegate/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/macro-research/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/ml-review/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/model-training/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/new-broker/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/new-strategy/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/performance-review/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/regime-selectivity/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/research-driver/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/session-coordination/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/session-handoff/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/sprint-format/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/system-report/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/system-review/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/vm-migration/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/vm-ops/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `.claude/skills/workplan-vs-architecture/SKILL.md` | instruction | live | — | 2026-09-07 | `dir:skills-are-binding-workflows / harness:skill-is-loaded-and-invocable` | — |
| `CLAUDE.md` | instruction | live | — | 2026-09-07 | `read:repo-orientation-and-binding-brief / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `ROADMAP.md` | plan | unknown | — | never | `read:hierarchy-rank-3-milestone-record / not-assessed` | MI-159 (#11241) corrected this file's milestone statuses 2026-09-07; it carries no machine-readable status header, so no status is asserted here |
| `ROADMAP_MACRO.md` | plan | unknown | — | never | `read:macro-milestone-record / not-assessed` | MI-159 (#11241) corrected this file's milestone statuses 2026-09-07; it carries no machine-readable status header, so no status is asserted here |
| `docs/AI-TRADERS-ROADMAP.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/ARCHITECTURE-CANONICAL.md` | architecture | live | — | 2026-09-07 | `read:hierarchy-rank-2 / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/CLAUDE-RULES-CANONICAL.md` | instruction | live | — | 2026-09-07 | `read:hierarchy-rank-1 / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/DOCUMENT-INDEX.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/ICT_BOT_MASTER_INSTRUCTIONS.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md` | instruction | unknown | — | never | `read:mandatory-format-spec / not-assessed` | — |
| `docs/TELEGRAM-SPEC.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/TRADE-PIPELINE.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/api-tier-policy.md` | instruction | unknown | — | never | `read:ci-enforced-tier-inventory / not-assessed` | — |
| `docs/architecture.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md` | architecture | unknown | — | never | `dir:architecture-declares-contracts / not-assessed` | — |
| `docs/architecture/ai-model-platform.md` | architecture | unknown | — | never | `dir:architecture-declares-contracts / not-assessed` | — |
| `docs/architecture/model-inventory.md` | architecture | unknown | — | never | `dir:architecture-declares-contracts / not-assessed` | — |
| `docs/architecture/multi-strategy-architecture-target.md` | architecture | unknown | — | never | `dir:architecture-declares-contracts / not-assessed` | — |
| `docs/architecture/oci-block-storage.md` | architecture | unknown | — | never | `dir:architecture-declares-contracts / not-assessed` | — |
| `docs/audit/2026-04-30-housekeeping.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/2026-04-30-unit-independence.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/2026-05-12-end-to-end-audit.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/held-pr-automerge-2026-09-02.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/repo_audit.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012-deployment-runbook.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012-wiring-audit.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/01-strategy-inventory.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/02-registry-inventory.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/03-service-config-mapping.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/04-phantom-services.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/05-entrypoints.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/06-dry-run-surface.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/07-risk-caps.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/08-pm-decisions.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-012/09-pr-sequence.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/sprint-013-deployment-runbook.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audit/vercel-edge-vs-cf-worker.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/M1-comms-audit-2026-05-07-fresh.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/M1-comms-audit-followups-fresh.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/audit-blindspot-zombies-2026-06-10.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/authored-cell-reaudit-register.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/automerge-request-gate-2026-09-02.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/backtest-harness-validation-2026-05-30.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/bybit2-broker-reconciliation-2026-07-13.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/closed-pr-salvage-2026-06-28.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/conviction-weight-sweep-2026-06-16.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/dashboard-truth-and-persistence-2026-06-16.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/dual-vm-pipeline-audit-2026-05-14.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/env-gate-inventory-2026-06-28.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/env-gate-purge-2026-05-10.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/fade-breakout-complement-2026-05-24.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-pipeline-structural-audit-2026-05-17.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-06-28.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-07-09.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-07-26.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-07-31.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-08-04.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-08-20.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-08-23.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/full-system-audit-2026-09-09.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/fvg-range-complement-2026-05-30.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/live-trade-management-contract-2026-06-16.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/local-pnl-fallback-2026-06-16.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/ml-infra-audit-2026-07-19.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/operating-layer-skills-workflows-inventory-2026-09-02.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/order-packages-zero-qty-2026-06-26.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/order-rejections-2026-05-28.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/position-netting-sltp-2026-06-08.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s-audit-f/pipeline-runtime.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s-audit-f/prop-bridge.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s-audit-f/web-api-routers.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s-audit-g/SUMMARY.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s-audit-h/SUMMARY.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/s9-yz-shadow-promotion-2026-06-09.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/session-invokable-actions-inventory-2026-09-02.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/silent-empty-2026-05-10.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/silent-empty-reporting-2026-05-10.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/squeeze-breakout-complement-2026-05-24.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/strategy-loss-drivers-2026-05-23.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/system-portfolio-backtest-2026-05-30.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/audits/walkforward-flip-policy-2026-05-30.md` | evidence | unknown | — | never | `dir:audits-are-measurement / not-assessed` | — |
| `docs/automation/oci-storage-setup.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/backtests/sprint-015/harness-validation.md` | evidence | unknown | — | never | `dir:backtests-are-measurement / not-assessed` | — |
| `docs/backtests/sprint-015/smoke-test-daily.md` | evidence | unknown | — | never | `dir:backtests-are-measurement / not-assessed` | — |
| `docs/backtests/sprint-015/summary.md` | evidence | unknown | — | never | `dir:backtests-are-measurement / not-assessed` | — |
| `docs/bot.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/DUE.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/ERROR-FEED-DIGEST.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/INDEX.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/READOUT.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/TASK-PRIORITY-2026-09-07.md` | plan | live | — | 2026-09-07 | `read:ranks-tasks-under-the-current-cycle-priority / read:MI-162-opened-it-anchored-to-the-current-cycle-priority` | ranks TASKS under CY-20260906-TRADING-TRUTH; companion to the live work plan WORKPLAN-2026-08-29.md, not a replacement for it |
| `docs/claude/WORKPLAN-2026-08-21.md` | plan | superseded | `docs/claude/WORKPLAN-2026-08-26.md` | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:superseded` | — |
| `docs/claude/WORKPLAN-2026-08-26.md` | plan | superseded | `docs/claude/WORKPLAN-2026-08-29.md` | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:superseded` | — |
| `docs/claude/WORKPLAN-2026-08-29.md` | plan | live | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:live` | — |
| `docs/claude/WORKPLAN-NIGHT-2026-08-29.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: The N1/N2/N8 items were not audited by this session; `ROADMAP.md`'s |
| `docs/claude/actions-budget.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/alpaca-mcp-server.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/api-key-inventory.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/architecture-audit-2026-05-02.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/audit-log.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/auto-task-daily-trade-audit.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/backlog-decision-digest-2026-06-23.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/bug-log-pending/README.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/bug-log.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/checkpoint-workflow.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/checkpoints/CHECKPOINT_LOG.md` | history | historical | — | 2026-09-07 | `dir:checkpoints-are-session-state-records / dir:record-of-a-completed-session` | — |
| `docs/claude/checkpoints/CP-2026-05-07-17-s048-fresh-m1-audit.md` | history | historical | — | 2026-09-07 | `dir:checkpoints-are-session-state-records / dir:record-of-a-completed-session` | — |
| `docs/claude/checkpoints/CP-2026-05-10-04-s067-phase2-followups.md` | history | historical | — | 2026-09-07 | `dir:checkpoints-are-session-state-records / dir:record-of-a-completed-session` | — |
| `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` | history | historical | — | 2026-09-07 | `dir:checkpoints-are-session-state-records / dir:record-of-a-completed-session` | — |
| `docs/claude/ci-status-checks.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/cleanup-policy.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/cleanup-report.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/closed-flat-invariant-phase2-wiring.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/closed-flat-invariant.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/colab-workflows.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/comms-architecture.md` | architecture | unknown | — | never | `name:declares-a-contract / not-assessed` | — |
| `docs/claude/comms-timer-assessment.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/coordination-board.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/board-body-template.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/debug-memory.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/decomposition-rules.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/deployment-ops.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/claude/diag-relay.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/claude/diagnoses/MI-124-never-firing-legs-diagnosis.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/diagnoses/MI-126-sunset-absence-not-zero.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/diagnoses/MI-127-pnl-null-on-closes-and-trade-4350.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/diagnoses/MI-166-pipeline-integrity-pass-1.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/diagnoses/MI-167-pipeline-integrity-pass-2.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/diagnoses/MI-169-remediation-halted-order-id-mismatch.md` | evidence | unknown | — | never | `dir:a-diagnosis-is-a-finding / not-assessed` | — |
| `docs/claude/dispositions/2026-09-06-1C-three-live-alarms.md` | history | historical | — | 2026-09-07 | `dir:dispositions-record-decisions-taken / dir:record-of-a-completed-session` | — |
| `docs/claude/env-gate-purge-phase2-annotations.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/exchange-truth-attribution.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/external-delegation.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/git-workflow.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/huggingface-workflows.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/janitor-2026-05-07-deadfiles.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/janitor-2026-05-07-missing-tests.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/janitor-2026-05-07-ui-consolidation.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/m20-m31-operator-decisions-2026-08-16.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/milestone-state.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/ml-training-policy.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/model-refinement-lifecycle.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/next-session-prompt.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/open-considerations.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/operating-protocol.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/pipeline-health-check.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/prop-account-state.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/recurring-sessions.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/repo-map.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/security-secrets.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/session-handoff.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/session-workflow.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/sprint-planning.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/system-actions.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/claude/telegram-pings.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/testing-policy.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/trading-mode-flags.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/trainer-resource-protocol.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/claude/trainer-vm-mode.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/claude/training-improvement-workflow.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/ui-processor-audit.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/claude/vm-operator-mode.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/claude/vm-resource-management.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/web-automations.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/DORMANT-SWEEP-2026-09-11.md` | evidence | live | — | 2026-09-11 | `mi-243:self-measured-2026-09-11 / population-stated` | MI-243 dormant sweep: 710 objects graded, 4 cohort rules stated with counts (21 closed / 554 held on an operator decision / 47 read individually); `data_accrual` 0->3, no lifecycle widening. |
| `docs/claude/work/RETIRED-MIRRORS-2026-09-11.md` | evidence | live | — | 2026-09-11 | `ops-lane:self-measured-2026-09-11 / population-stated` | Audit trail for enacting DEC-20260911-WORK-STORE-BACKLOG-MIRRORS (`retire_mirrors`): 553 of 554 verified Phase-C mirror objects retired at main 825964034, 1 excluded for a live typed blocked_on edge from WO-20260901-PHASE-H. Closes ZERO findings — all four backlogs untouched. |
| `docs/claude/work/IN-FLIGHT-OWNER-LIVENESS-2026-09-11.md` | evidence | live | — | 2026-09-11 | `mi-236:self-measured-2026-09-11 / population-stated` | MI-236 evidence: `in_flight` graded across BOTH registers at main 4d8b30109 — checklist 27 in_flight (8 supported / 13 unsupported / 6 could-not-establish), work objects 8 (2 / 4 / 2). The objects register is what `check_wip_ceiling.py` binds on and sits at 8 of 8, so at most two of the eight slots holding the ceiling shut have an owner the registry shows active. Records why a strictly-terminal grader catches 1 of the 6 rows the row named (five owners are `idle`, not terminal) and why the registry is a reliable NEGATIVE, which is what lets this run in CI with no `mcp__*` tool. |
| `docs/claude/work/IN-FLIGHT-ROW-DISPOSITIONS-2026-09-11.md` | evidence | live | — | 2026-09-11 | `mi-265:self-measured-2026-09-11 / population-stated` | MI-265: the per-ROW disposition layer MI-236 deliberately did not do — each of the 17 unsupported `in_flight` rows graded against its OWN done_condition, not its owner's liveness. Frees the WIP ceiling 8 -> 5 with evidence per row: two objects `done` (the automation-PR class fix OBSERVED over 3 scheduled runs, one landing a strand with no human; the partial-close question answered by #11514 against the live config with a positive control), one `waiting` (the /api/bot/db/* gate's refusal half observed first-hand at 401 with controls, its SPA half unverifiable from this repo), one left `in_flight` because it moved BACKWARDS (health backlog 853 unresolved against a 765 done_condition). Records two negatives deliberately NOT over-read — a 422 from an empty login body and a 401 from a missing header both precede the auth-env check, so neither is evidence about auth configuration. |
| `docs/claude/work/HANDOFF-20260903-DAY.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/work/HANDOFF-20260905-DAY.md` | history | historical | — | 2026-09-07 | `name:record-of-a-past-state / dir:record-of-a-completed-session` | — |
| `docs/claude/work/JOURNAL-VENUE-FLEET-AUDIT-2026-09-08.md` | evidence | live | — | 2026-09-08 | `mi-177:self-measured-2026-09-08 / population-stated` | MI-177 fleet audit: 8/11 accounts read both sides, 25 symbol pairs, 24 exact + 1 known MGC divergence; 3 accounts not read. |
| `docs/claude/work/UNREAD-ACCOUNT-VENUE-AUDIT-2026-09-08.md` | evidence | live | — | 2026-09-08 | `mi-189b:self-measured-2026-09-08 / population-stated` | MI-189b lane pass 5: the 3 accounts MI-177 could not read. 3 attempted, 3 determined — ib_live + oanda_practice could_not_read (dry gate), breakout_1 RECONCILES via prop_account_status (as-of, 8.5d stale). |
| `docs/claude/work/EXIT-EVAL-60S-ROOTCAUSE-2026-09-10.md` | evidence | live | — | 2026-09-10 | `self-measured-2026-09-10 / population-stated` | Root cause of the exit-eval 60s breaches — and the TWO populations do NOT share one. Within-process: 100% slow-PASS (1218 of 1218, a COMPLETE census, have pass_ms > 30s and none has a residual above one cadence period), 97.0% predate 2026-08-22, leaving 14 in 18 days of which 10 sit in the IBKR reset window where each IB package costs a serialized 29.0s pinned-thread queue timeout (3 x 29.0s + ~4s reconciles the recorded 91,187ms pass). Restart gap: residual cadence sleep 0-30s + systemd downtime ~0.3s + startup-to-thread 6-15s + COLD first pass 12.7-16.9s, reconciling EXACTLY on all four boundaries measured — i.e. 19-60s BY CONSTRUCTION against a 60s requirement. Deploy speed is NOT the cause. Two Tier-2 remedies PROPOSED, not applied. |
| `docs/claude/work/BACKLOG-DRAIN-2026-09-09.md` | evidence | live | — | 2026-09-09 | `self-measured-2026-09-09 / population-stated` | Backlog-drain session: burn-down 765 -> 763 unresolved (3 closed, 1 filed) with per-close evidence, and the MECHANISM answer — duplication, retirable classes and staleness all REFUTED by measurement; a hand-adjudicated n=18 sample finds 78% of rows need an operator decision, a Tier-2/3 change or a research run, so a closing-only session structurally cannot be the counterweight to the filing rate. |
| `docs/claude/work/BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md` | evidence | live | — | 2026-09-08 | `mi-204:self-measured-2026-09-08 / population-stated` | MI-204: the bybit_1 ETHUSDT "orphan" (trade 5569) is the same position the netting reconciler false-closed 4 min earlier. Root cause `_bybit_position_protection` grades a symbol off `get_positions` rows[0], so a zero-size hedge book reads FLAT — 3 provable false closes, +$115.86 fabricated ESTIMATED pnl, real money in the exposed population. Fixes proposed, NOT applied (Tier-2 order path). |
| `docs/claude/work/BYBIT2-ETH-PHANTOM-CLOSE-2026-09-09.md` | evidence | live | — | 2026-09-09 | `mi-221:self-measured-2026-09-09 / population-stated` | MI-221 (P1, REAL MONEY): journal trade 5471 (bybit_2 ETHUSDT long 0.04) reads closed on `sl` while the operator's terminal shows it live and unprotected. The two questions have ONE cause — the reconciler's flat test (order_monitor.py:4348) and every position reader share `clients.py::account_open_positions`. The close acted on a real Bybit closed-pnl record (-0.25284115 @ 2451.27), NOT an inferred SL cross, and `_classify_broker_exit` graded it correctly. Three venue reads say FLAT vs the operator's eyes — UNRECONCILED, and the raw `get_positions` payload could not be produced because zero-size rows are dropped silently. 3 backlog rows filed; remediation HELD (Tier-2/3). |
| `docs/claude/work/MI-222-ROSTER-SWEEP-ALREADY-EXISTS-THE-GAP-IS-THE-COLLAPSED-READ-2026-09-09.md` | evidence | live | — | 2026-09-09 | `mi-222:source-verified-2026-09-09 / population-stated` | MI-222: the roster-driven sweep this lane was told to BUILD already exists inside `account_open_positions` (`clients.py:1410`, from BL-20260713) and already ran on bybit_2/ETHUSDT returning nothing — building it again would duplicate an enumeration that reports the target position CLEAN. Corrected structural finding with a source-verified table. The real gap is one layer down: `_emit`'s `if size <= 0: return` collapses venue-flat / zero-size-row / no-row into one value inside the reader every sweep uses, violating "states are never collapsed" at the source; it has blocked two real-money P1s one day apart. Ships Tier-1 only (10 characterization tests + this doc); remediation is Tier-2/3 and remains HELD. |
| `docs/claude/work/MGC-REMEDIATION-ABORT-2026-09-08.md` | evidence | live | — | 2026-09-08 | `self-measured-2026-09-08 / population-stated` | MGC remediation ABORTED: 548 is not stale (rests in a second disjoint OCA group); venue avg cost attributes the 11 real lots to trade 5531, not 5353, inverting the assumed fix. Measure only. |
| `docs/claude/work/STRATEGY-REVIEW-WINDOW-FLOOR-2026-09-09.md` | evidence | live | — | 2026-09-09 | `self-measured-2026-09-09 / population-stated` | Phase F: two of the three clears-when clauses on OI-20260901-REVIEW-PACKET-... were ALREADY MET and the row still read as outstanding — the cron has fired (7 schedule runs, 3 success, indexes on main with matching generated_at) and floor_state reads `none_gradeable` on 7 of 8 indexes, not `unknown`. The condition itself measured over 8 days: graded 52 / actionable 0 on 8 of 8, no leg above 10 closes in a 7d window, ~17x shortfall against the n>=20 floor. Widening mostly fails — the generator's own horizon reads 70d to grade the median reachable leg and 32 of 52 ungradeable at ANY window. Decision prepared as DEC-20260909-STRATEGY-REVIEW-WINDOW-FLOOR; window and floor UNCHANGED. |
| `docs/claude/work/MEASUREMENT-DEFECTS-2026-09-06.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/README.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/decision-drain-routine-prompt.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/manager-wake-routine-prompt.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/spawn-prompts/2026-09-06/1A-observation-sweep.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/spawn-prompts/2026-09-06/1B-loss-attribution.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/spawn-prompts/2026-09-06/1C-live-alarms.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/work/spawn-prompts/2026-09-06/README.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/claude/workplan.md` | plan | historical | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:historical` | — |
| `docs/cross-asset-regime-conviction-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/data/dataset-schema.md` | architecture | unknown | — | never | `dir:schema-and-taxonomy-are-contracts / not-assessed` | — |
| `docs/data/dataset-taxonomy.md` | architecture | unknown | — | never | `dir:schema-and-taxonomy-are-contracts / not-assessed` | — |
| `docs/data/versioning-policy.md` | architecture | unknown | — | never | `dir:schema-and-taxonomy-are-contracts / not-assessed` | — |
| `docs/deployment.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/design/alpaca-trade-scoped-close-PROPOSAL.md` | plan | live | — | 2026-09-08 | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/b6-split-packet-2026-08-29.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/boot-read-size-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/d1b-live-time-stop-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/decision-push-back-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/decision-push-back-FEASIBILITY.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/evidence-floor-horizon-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/exit-mechanism-construction-PROCESS.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/gross-exposure-governance-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/ib-protective-price-reconciler-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/llm-burst-worker-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/local-vision-backend-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/m31-p5-telemetry-reading-lever-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/close-confirmation-scope-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/operating-layer-build-plan-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/operating-layer-function-derivation-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/operating-layer-schema-and-state-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/operating-model-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/position-telemetry-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/strategy-demote-and-tune-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/design/strategy-leg-offline-edge-basis-PROPOSAL.md` | plan | live | — | 2026-09-09 | `dir:design-declares-contracts / not-assessed` | — |
| `docs/design/tp-sentinel-cap-venue-scope-PROPOSAL.md` | architecture | unknown | — | never | `dir:design-declares-contracts / not-assessed` | — |
| `docs/exit-coverage-architecture.md` | architecture | unknown | — | never | `name:declares-a-contract / not-assessed` | — |
| `docs/github-actions-workflows.md` | unknown | live | — | 2026-09-07 | `not-assessed / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/hf_claude_patch.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/integrations/breakout-compliance-2026-06-16.md` | architecture | unknown | — | never | `dir:integration-specs-declare-contracts / not-assessed` | — |
| `docs/integrations/breakout-poc-manual-bridge-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/integrations/bybit-ai-skill.md` | architecture | unknown | — | never | `dir:integration-specs-declare-contracts / not-assessed` | — |
| `docs/integrations/dxtrade-contract-template.md` | architecture | unknown | — | never | `dir:integration-specs-declare-contracts / not-assessed` | — |
| `docs/integrations/huggingface-datasets.md` | architecture | unknown | — | never | `dir:integration-specs-declare-contracts / not-assessed` | — |
| `docs/integrations/ibkr-equity-etf-support-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/integrations/metacopier-bridge-DESIGN.md` | architecture | closed_unfinished | — | 2026-09-07 | `name:design-declares-a-contract / self-declared:parked` | self-declared abandoned/parked; WHAT WAS LEFT is not recorded in the file |
| `docs/integrations/prop-accounts-architecture-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/integrations/prop-telegram-inbound-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/live-exit-monitor-cadence-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/ml/market-raw-adapters.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/ml/model-registry-policy.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/ml/optimization-roadmap.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/ml/orderflow-capture-design.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/ml/training-center.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/netting-partial-close-attribution-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/news-influence-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/news_layer.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/operator/colab-key-rotation.md` | lookup | unknown | — | never | `dir:operator-setup-procedures / not-assessed` | — |
| `docs/operator/github-actions-oci-secrets.md` | lookup | unknown | — | never | `dir:operator-setup-procedures / not-assessed` | — |
| `docs/operator/setup-api-keys.md` | lookup | unknown | — | never | `dir:operator-setup-procedures / not-assessed` | — |
| `docs/pipeline/stage-contracts.md` | architecture | unknown | — | never | `name:declares-a-contract / not-assessed` | — |
| `docs/proposals/tier2-sweep-candle-anchoring-2026-07-30.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/reference/bot-api-reference.md` | lookup | live | — | 2026-09-07 | `dir:reference-is-lookup / ci:canonical-doc-coherence-ACTIVE_DOCS` | — |
| `docs/reports/system-report-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/A-multisymbol-ETH-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A-regime-router-ml-vol-verdict-DESIGN-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A-vol-gating-AB-evidence-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A-vol-gating-AB-plan-2026-06-27.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/research/A-vol-gating-ETH-SOL-OFFcell-evidence-2026-07-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A-vol-gating-OFFcell-design-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A-vol-gating-SOL-revalidation-fcpcv-v2-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/A1-backtest-augment-runner-SCOPE-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/AI-TRADER-RESEARCH-PLAN-2026-07-19.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/research/ALPACA-LIVE-GOLIVE-STATUS-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/AUTONOMOUS-WORKPLAN-2026-07-30.md` | plan | superseded | `docs/research/RESEARCH-PROGRAM-2026-07-30.md` | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:superseded` | — |
| `docs/research/B-conviction-graduation-DESIGN-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/B-conviction-sizing-backtest-evidence-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/C1-conviction-sizing-evidence-2026-08-04.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/CAPITAL-ACTIVATION-PACKET-2026-08-14.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/D2-spike-A-pooled-labels-evidence-2026-07-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/EXIT-GEOMETRY-REBUILD-SESSION-PROMPT.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/CALENDAR-TIME-EDGE-SURFACE-CENSUS-2026-09-09.md` | evidence | live | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/FAITHFUL-BACKTEST-PLATFORM-DESIGN-2026-08-04.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M-regime-direction-filter-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M1-econ-calendar-source-probe-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M1-econ-calendar-spine-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M1-econ-event-study-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M18-allocator-backtest-findings-2026-06-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M19-next-direction-deep-research-brief-2026-07-05.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M19-next-direction-recommendation-2026-07-05.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-E1-block-size-derivation-2026-08-13.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-exit-capture-census-2026-08-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-exit-head-PROGRAM.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-exit-lever-promotion-packet-2026-08-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-exit-monitor-decouple-evidence-2026-08-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-exit-refinement-2026-07-12.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-momentum-exhaustion-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M20-path-b-floor-and-trail-widen-2026-08-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20-trail-decay-resweep-2026-08-09.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M20X-vol-conditional-trail-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M21-entry-refinement-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M23-decision-label-wall-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M23-meta-labeling-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M23-phase1-C1-results-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase1-ev-gate-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase1-pooled-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase1-trend-donchian-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase1-variantC-DESIGN-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase2-labelvol-eval-widening-2026-07-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M23-phase2-labelvol-findings-2026-07-19.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M24-net-r-cost-aware-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M24-net-r-regrade-findings-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M25-btc-regime-robustness-diagnosis-2026-07-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M25-btc-sol-promotion-packets-2026-07-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M25-frozen-dataset-remediation-2026-07-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M25-promotion-consolidation-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M25-readiness-assessment-2026-07-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M25-rg4-sweep-2026-07-19.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M26-P0-conflict-bleed-findings-2026-07-19.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M26-P1-conflict-taxonomy-2026-07-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M26-regime-transition-conflict-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M27-GLD-1H-PROMOTION-PACKET-2026-08-14.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-MGC-15m-exit-refinement-assessment-2026-07-28.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-MGC-15m-step2-scope-2026-07-28.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-MGC-15m-findings-2026-07-28.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-batch2-futures-findings-2026-07-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-batch2-futures-gap-diagnostic-2026-07-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-batch3-equities-findings-2026-07-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-batch4-xauusd-findings-2026-07-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-crypto-findings-2026-07-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P0-repull-followups-2026-07-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P1-15m-findings-2026-07-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-P1-15m-promotion-proposal-2026-07-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M27-scalp-expansion-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M28-D4-D5-value-construction-sweep-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-P0-schema-2026-07-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-P4-fred-producer-unwired-2026-07-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-P4-value-gate-run-2026-07-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-fmp-stable-freetier-findings-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-macro-value-speculation-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M28-signal-RnD-program.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-signal-research-ledger.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-signal-research-methodology.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M28-value-backfill-test-in-place.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M29-P1b-gas-seed-calibration.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M29-P1c-gas-dual-calibration.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M29-ai-system-dynamics-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M29-sleeve-honest-horizon-ic-regrade-2026-07-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M30-deep-research-SESSION-PROMPT.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M30-intrabar-exit-head-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M31-track-b-soak-plan.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/research/M36-C4-conditioned-lifecycle-run-2026-07-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/M36-macro-intelligence-and-crowding-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/M5-vix-term-corrected-cost-reexamination-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MB-20260701-gate4-money-AB-2026-07-17.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MB-20260701-mapping-reconcile-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MB-20260701-operating-curve-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MB-20260701-vt004-evidence-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MI-154-scalp-exit-head-ordering-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/MI-154b-scalp-exit-head-artifact-2026-09-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ML2-trend-vol-cell-walkforward-2026-08-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/P0.2b-fills-vs-journal-2026-08-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/P1-vol-target-LIVE-PARAM-correction-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/P1-vol-target-result-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/P5-orb-mes-result-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/P7-xsec-momentum-firstpass-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: BOTH tracks. **Track 1** (higher-frequency microstructure off |
| `docs/research/R6-VM-RESIDENCY-VERDICT-2026-08-28.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-CAPABILITY-INDEX.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-INFRA-AUDIT-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-INFRA-AUDIT-2026-08-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-PROGRAM-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-RIGOR-STANDARD.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/RESEARCH-WORKFLOW-ARCHITECTURE-2026-08-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ROADMAP-REVIEW-WORKPLAN-2026-08-04.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: Not audited when this status was assigned — *we did not look*. |
| `docs/research/S-P0-LABEL-AUGMENT-2026-08-04.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/SOL-fc-family-AB-evidence-2026-07-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0-data-corpus-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T0.1-embedding-AB-evidence-2026-07-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0.1-embedding-followup-evidence-2026-07-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0.1-live-param-divergence-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0.1-live-parity-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T0.2-unsupervised-regime-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T0.3-conviction-embedding-evidence-2026-07-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0.4-fc-sltp-geometry-evidence-2026-07-05.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T0.4-forecast-features-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T0.4-live-parity-spike-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T1-gpu-burst-spend-SPEC.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T1.1-deep-sequence-model-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T1.1-tcn-label-sensitivity-evidence-2026-07-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T1.2-ssl-encoder-AB-evidence-2026-07-04.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T1.2-ssl-encoder-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/T1.3-ranker-SESSION-PROMPT.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/T1.3-ranker-findings-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/W0.2-soak-read-2026-08-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/WHY-BROKER-TRUTH-ISNT-REACHING-THE-JOURNAL-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/WORK-PLAN-2026-08-02.md` | plan | closed_unfinished | — | 2026-09-07 | `name:plan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: Not audited when this status was assigned — *we did not look*. |
| `docs/research/WORKPLAN-2026-08-05.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: Not audited when this status was assigned — *we did not look*. |
| `docs/research/WORKPLAN-2026-08-14.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: **Lane 0 (Live-capability integrity) only** — the single lane |
| `docs/research/WORKPLAN-desoak-and-milestone-closeout-2026-07-26.md` | plan | closed_unfinished | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:closed_unfinished` | what was left: Not audited when this status was assigned — *we did not look*. |
| `docs/research/WS-B-candle-shard-labelvol-scoping-2026-07-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ai-model-strategy-roadmap-2026-07-01.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/research/alpaca-200-affordability-sweep-2026-08-25.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-close-blast-radius-mechanism-2026-09-08.md` | evidence | live | — | 2026-09-08 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-inverse-etf-route-2026-09-10.md` | evidence | live | — | 2026-09-10 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-live-roster-candidates-2026-09-10.md` | evidence | live | — | 2026-09-10 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-live-refusal-verdict-2026-09-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-live-splg-iaum-promotion-sizing-2026-07-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-options-PHASE1-spec.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-options-l3-research-memo.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-over-close-population-2026-09-07.md` | evidence | live | — | 2026-09-07 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-portfolio-roster-compat-gate-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-proxy-signal-vs-order-symbol-2026-08-25.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-roster-data-sources-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-roster-phase1-data-depth-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-roster-phase2-capital-efficiency-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/alpaca-roster-phase3-compat-gate-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/artifacts/m27/coverage.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/backtest-live-exit-fidelity-2026-09-08.md` | evidence | live | — | 2026-09-08 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/bracket-calibration-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/bracket-expectation-construction-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/bracket-geometry-decision-packet-2026-08-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/bracket-target-reachability-2026-08-24.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/broker-truth-ib-flex-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/bybit-close-qty-legalization-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/c-reg-lens-enablement-2026-07-28.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/capital-allocation-ai-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/capital-allocation-ai-PROMPT.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/chop-scalp-capital-efficiency-2026-07-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/cross-asset-diversification-2026-06-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/cross-asset-feature-probe-2026-06-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/cross-asset-strategy-scope-2026-06-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/crypto-edge-audit-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/crypto-finetune-proposals-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/declare-the-9-cannot-be-valued-yet-2026-08-24.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/digest-cadence-carrier-2026-09-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/donchian-stop-gate-is-drawdown-bound-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/dukascopy-coverage-adjudication-2026-08-24.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e0-exit-census-harness-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e0-exit-census-live-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e1-exogenous-panel-preconditions-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e2-feature-information-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e2-gate-decision-and-sol-clustering-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e2-horizon-arm-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e3-barrier-geometry-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e3-joint-lever-screen-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-bracket-geometry-sweep-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-bracket-is-not-a-decision-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-matrix-recheck-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-passed-unshipped-proposal-2026-08-31.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-resweep-verdict-diff-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/e35-rr-floor-walkforward-2026-08-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/eth-pullback-prop-swap-aware-2026-06-25.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/eth-regime-classweight-recalibration-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/evidence-workflow-landing-triage-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-attribution-broker-truth-2026-08-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-capture-deepdive-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-eval-fetch-attribution-2026-08-21.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-head-figure-reconciliation-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-lever-wiring-audit-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-trailing-and-banking-measurement-2026-09-08.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-trailing-banking-broker-truth-2026-09-09.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-banking-tailcap-and-reachability-2026-09-09.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-location-fidelity-2026-09-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/banking-half-2026-09-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/be-floor-sweep-2026-09-07.md` | evidence | live | — | 2026-09-07 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-management-ml-experiment-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/exit-reason-frozen-at-close-2026-08-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/exit-sweep-positive-control-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/expansion-backtesting-research-2026-06-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/fleet-model-scorecard-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/flip-override-walkforward-2026-08-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/hf-prop-strategy-research-plan-2026-06-16.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/research/ib-intraday-shortlist-backtest-2026-07-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ib-intraday-strategy-survey-2026-07-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ib-metals-native-backtest-2026-07-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ib-pipeline-stability-review-2026-07-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ict-scalp-5m-real-money-verdict-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ict_scalp_5m-modernization-research-plan-2026-07-20.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/lane-p-compat-verdict-diff-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/leveraged-etf-research-2026-06-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m15-phase0-results-2026-06-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m15-soak-report-2026-06-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m15-ws-b-spy-qqq-walkforward-2026-06-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m15-ws-c-alt-sweep-2026-06-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/mi235-landing-machinery-review-2026-09-11.md` | evidence | live | — | 2026-09-11 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-1d-fleet-pooling-2026-08-13.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-1d-pullback-preregistration-2026-08-14.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-exit-head-binding-term-2026-08-14.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-fold-dispersion-2026-08-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-fold-dispersion-preregistration-2026-08-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-lever-firing-rate-2026-08-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-operator-decision-queue-2026-08-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-readiness-2026-08-22.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-split-boundary-loss-2026-08-14.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m20-what-remains-2026-08-13.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/m30-to-m16-integration-backbone-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/market-alternatives-2026-06-10.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/mgc-trend-1h-walkforward-2026-07-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/mi147-backtest-soak-chain-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ml-buildout-strategy-2026-06-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ml-strategies-deep-review-2026-06-25.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ml2-predictive-bracket-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/netting-partial-close-packet-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/offload-model-stage-correction-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/operating-model-measurement-2026-09-08.md` | evidence | live | — | 2026-09-08 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/options-level3-alpaca-research-BRIEF-2026-06-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/overnight-2026-07-20-morning-packets.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/overnight-strategy-research-2026-06-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/pairs-extensions-2026-07-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/pairs-sleeve-PROPOSAL-2026-07-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/pairs-sleeve-real-money-readiness-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/partial-close-producer-never-fired-2026-09-09.md` | evidence | live | — | 2026-09-09 | `mi-209c:self-measured-2026-09-09 / population-stated` | MI-209c: WHY the partial-close producer has never fired. Answer is done-condition clause (b) — reachable in principle, unreachable as configured. Sole emitter is `turtle_soup.monitor()`; it is routed to 0 of 11 accounts (live `/api/bot/config`, control: trend_donchian -> 3) and pinned `execution: shadow`. Its 3 trades of 5589 never opened a position. Census re-established independently: trades 5589 / packages 4500, matching MI-209b, so clause (d) is NOT met. Margin measured as a labelled FLEET PROXY (turtle_soup has 0 telemetry rows): peak_r >= 1.0R on 43/118, all ESTIMATED provenance. NOTE §0 — the question was already answered by MI-188b on 2026-09-08; ~2/3 of this unit was re-derivation. |
| `docs/research/pnl-optimal-conflict-resolution-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/position-sizing-confidence-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/promotion-gatecheck-and-mes-labeling-2026-06-26.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/prop-dynamic-exits-faster-banking-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/prop-firm-testing-tool-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/pullback-2h-direction-walkforward-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/pullback-frac-cross-leg-2026-08-24.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/pullback-stop-retune-and-capr-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/r-metric-contamination-2026-09-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/reachable-take-profit-proposal-2026-09-07.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/qld-tqqq-examined-2026-09-08.md` | evidence | live | — | 2026-09-08 | `mi-195:self-measured-2026-09-08 / population-stated` | MI-195: the two never-swept live legs EXAMINED. Substrate was a routing gap, not a missing dataset (QLD 5084 bars from 2006-06-21, TQQQ 4167 from 2010-02-11). 175 TP cells each, ZERO pass -> `unexamined` becomes a measured `none`. Cause is the reachability ceiling: measured cap_r median 1.40 (QLD) / 0.99 (TQQQ) vs 3.14 on the QQQ control -- TQQQ below every live median MI-156 measured, QLD second-lowest. QLD `sm2` passed Path B but is a STOP cell and is NOT proposed. Window is full-history, NOT the corpus's 1830d -- not directly comparable. |
| `docs/research/reads-everything-convergence-status-2026-07-16.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/real-money-0for13-attribution-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/real-money-close-attribution-2026-09-08.md` | evidence | live | 2026-09-08 | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/recombination-sweep-2026-06-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-cell-walkforward-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-conditional-strategy-weighting-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/regime-coverage-debt-analysis-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-debt-matrix-corrected-cost-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-debt-matrix-equity-futures-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-debt-matrix-fee-ab-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-debt-matrix-results-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-map-step1-results-2026-06-18.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-roster-matrix-2026-06-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/regime-router-design-2026-06-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/research-framework-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/research-queue-operationalisation-2026-08-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/research-to-results-cost-gate-DESIGN-2026-08-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/research-to-results-gap-2026-07-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/trade-prioritisation-ranking-key-AB-2026-09-08.md` | evidence | live | — | 2026-09-08 | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/roadmap-redundancy-audit-2026-09-07.md` | evidence | live | — | 2026-09-07 | `mi-160:self-measured-2026-09-07 / population-stated` | MI-160 redundancy census over all 33 milestone rows: 0 proposed. |
| `docs/research/roadmap-toolbox-assessment-2026-07-29.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/research/roll-adjusted-continuous-futures-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/session-handoff-2026-06-01-afternoon.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/session-handoff-2026-06-01-evening.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/session-handoff-2026-06-01-night.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/session-handoff-2026-06-01.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/session-handoff-m15-soak-2026-06-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/signal-research-framework-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/small-tf-directions-2026-07-15.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/sol-pullback-stale8-deep-corpus-2026-08-18.md` | evidence | closed_unfinished | — | 2026-09-07 | `dir:research-is-measurement / self-declared:not shipped` | self-declared abandoned/parked; WHAT WAS LEFT is not recorded in the file |
| `docs/research/strategy-primitives-recombination-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/research/target-r-derivation-crypto-pullback-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/technical-quant-research-ledger.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/technical-quant-research-platform-scoping-2026-07-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/technical-quant-research-platform-validation-2026-07-27.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/technical-signal-research-ledger.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/timeout-bars-harness-vs-live-2026-08-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/trade-record-integrity-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/tradeable-universe-2026-06-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/trainer-compute-ceiling-proposal-2026-07-29.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/trend-donchian-live-anomaly-rootcause-2026-07-11.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/uncarried-spec-census-2026-09-06.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/wave1-followthrough-2026-08-03.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/wave1-sweeps-2026-08-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/where-edge-lives-entry-wall-2026-06-30.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ws-a-s1-futures-sweep-2026-06-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ws-a-s2-retune-2026-06-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ws-a-s3-significance-2026-06-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/ws-a-s3b-fee-headroom-2026-06-02.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/xrp-pullback-conditional-hit-rate-2026-08-19.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/xrp-pullback-entry-axis-2026-08-24.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/xrp-pullback-joint-geometry-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/research/xrp-pullback-trail-axis-and-inert-levers-2026-08-23.md` | evidence | unknown | — | never | `dir:research-is-measurement / not-assessed` | — |
| `docs/runbooks/alpaca-integration.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/backfill-orphan-pnl.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/devnull-guard.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/gpu-burst.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/health-check.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/hourly-snapshot.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/ib-integration.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/ib-live-login-2fa.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/insights.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/live-smoke-test.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/live-vm-ip-single-source.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/live-vm-migration-ampere.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/liveness-watchdog.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/merge-queue.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/mobile-push.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/model-drift-remediation.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/restore-webapp-auth.md` | lookup | live | — | 2026-09-11 | `MI-266: ordered restore steps + the measurement contract for the SPA auth gate` | — |
| `docs/runbooks/monitor-reconciler.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/mounted-storage.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/netting-pnl-reconciliation.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/oanda-integration.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/regime-head-soak-to-advisory.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/reserved-ip-stable-egress.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/spot-margin.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/strategy-testing.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/trainer-backtest.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/training-vm.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/runbooks/webapp-https-caddy.md` | lookup | unknown | — | never | `dir:runbooks-are-procedures-to-consult / not-assessed` | — |
| `docs/security/api-network-hardening-PLAN-2026-06-28.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/security/intrusion-surface-audit-2026-06-28.md` | evidence | unknown | — | never | `name:audit-measures / not-assessed` | — |
| `docs/security/permissions-tiers.md` | instruction | unknown | — | never | `name:policy-prescribes / not-assessed` | — |
| `docs/sizing-legalization-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/sprint-logs/S-047.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS10-FU-doc-audit.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS10-FU-pre-commit.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS10.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS3.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS4-FU.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS4.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-A.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-B-PART-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2A.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2B.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-C-FU.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-C.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-D.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-E.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS5-F.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS6-PART-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-FU-shadow-log-rotation.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-3.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-4.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-5.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS7-PART-6.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS8-PART-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS8-PART-2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS8-PART-3.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS9-FU-2-training-cycle-stabilization.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS9-FU-run-cycle.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AI-WS9.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ALPACA-LIVE-FIRST-LEG-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ALPACA-PIPELINE-AUDIT-2026-07-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ALPACA-T1-AND-WORKPLAN-2026-08-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ANDROID-S1-2026-05-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ANDROID-S3-2026-05-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ANDROID-S6-2026-05-27.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ANDROID-S8-2026-05-27.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ANDROID-S9-2026-05-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-API-TIER-POLICY-GUARD-2026-08-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-ALERTING-PROVENANCE-AND-ALPACA-2026-08-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-COMPLIANCE-2026-06-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-GH-2026-06-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-P0-CLOSEOUT-2026-07-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-P1-TRAINER-HONESTY-2026-07-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-P2-ENFORCEMENT-2026-07-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-BRACKET-EXPECTATIONS-AND-CANDLE-FEED-2026-08-24.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-BROKER-TRUTH-ROLLOUT-20260729.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-BYBIT-COVERAGE-DEPLOY-VERIFY-2026-07-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-BYBIT-KEY-ROTATION-2026-06-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CANON-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CANON-FU-1-workplan-superseded.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CANON-FU-2-cfi-wiring.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CANON-FU-3-branch-protection.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CFW-1-FU-gha-deploy.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CFW-1-FU2-worker-retired.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CFW-1-cloudflare-worker.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CHOP-SCALP-RESEARCH-2026-07-15.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CI-STORAGE-BUDGET-2026-06-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-COLLAPSED-STATES-2026-08-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-COST-MODEL-VENUE-FEE-2026-07-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-COVERAGE-AND-CASCADE-CLASSIFY-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CROSS-ASSET-DIVERSIFY-2026-06-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-CROSS-ASSET-PROBE-2026-06-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-DBWIRE-TRADE-PKG-LINK-2026-05-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-DIAGTOKEN-GATE-AND-ALPACA-AFFORDABILITY-2026-08-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-DTP-EXITPLAN-2026-06-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-E35-MATRIX-RECHECK-2026-08-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-E35-RESWEEP-DISPOSITION-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-E35-SHIP-REVERSED-GEOMETRY-2026-08-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-EDGE-WALL-TRILOGY-2026-06-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ETH-REGIME-RG4-RETRAIN-2026-06-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-EXIT-ATTRIBUTION-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-EXIT-PROTECTION-CLUSTER-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-EXPANSION-ETF-BREADTH-2026-06-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-FLIP-OVERRIDE-DISARM-2026-08-11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-FULL-SYSTEM-AUDIT-FIX-PROGRAM-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-FULL-SYSTEM-REVIEW-807M08-20260730.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-GATE0-G1-EXIT-LABEL-RECLASSIFICATION-2026-08-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-GATE0-MEASUREMENT-TRUST-2026-08-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-GATEWAY-WATCHDOG-2026-06-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-GOV-CLEANUP-PHASES-4B-7-2026-05-24.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-HEALTH-REVIEW-2026-07-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-HEALTH-REVIEW-GRADING-PROP-2026-07-03.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-IB-EXIT-COVERAGE-2026-08-14.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-IB-LIVENESS-GITSYNC-2026-07-06.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-IB-NATIVE-FUTURES-RESEARCH-2026-07-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-IB-VENUE-SESSION-GATE-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-IBKR-EQUITY-ETF-SUPPORT-2026-07-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ICTSCALP-PHASE0-2026-07-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-INTENT-HOLD-2026-05-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-JANITOR-BRANCH-CLEANUP-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LANE-P-SIGNAL-JOURNAL-AXIS-2026-08-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LANE0-DECISIONS-2026-08-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LANE0-STANDING-CHECK-BLINDSPOTS-2026-08-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LEVERAGED-ETF-2026-06-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LINEAR-MARGIN-FIX-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LIVETRADE-MGMT-2026-06-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LIVETRADE-MGMT-S2-2026-06-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LLM-BURST-WORKER-2026-08-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-LLM-BURST-WORKER-SCHEDULING-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M1-SURVEY-CONSENSUS-NULL-20260730.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M13-S1-S2-AI-ANALYST-2026-05-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-ALPACA-LIVE-2026-06-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-ALPACA-LIVE-2026-06-27.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-PHASE0-2026-06-10.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-PHASE2-OANDA-2026-06-11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-PHASE3-AND-2B-2026-06-11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M15-PHASE4-SOAK-2026-06-11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-EMBEDDINGS-FOLLOWUP-2026-07-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-FC-GRADUATION-PROGRAM-2026-07-04.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-FC-SHADOW-T12-P1-2026-07-03.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-FC-SLTP-GEOMETRY-2026-07-05.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-GPU-BURST-2026-07-02.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-NEXT-DIRECTION-2026-07-05.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-OVERNIGHT-2026-07-06.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-SOL-FC-GRADUATION-2026-07-06.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-T0.1-EMBEDDINGS-2026-07-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-T1.1-DEEP-SEQUENCE-2026-07-02.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M19-T1.2-SSL-AB-2026-07-04.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-ARM-REACHABILITY-VOL-THRESHOLD-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-B9-TIMEOUT-BARS-BLAST-RADIUS-2026-08-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-CORPUS-SCHEMA-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-CORPUS-UNION-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-COVERAGE-AND-VERIFY-2026-08-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-DISPERSION-ISOLATION-AND-QUEUE-2026-08-15.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-E0-E1-EXIT-SUBSTRATE-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-E2-FEATURE-INFORMATION-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-E2-HORIZON-ARM-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-E3-EXIT-MECHANISM-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-E35-ACTIVE-TRADE-MANAGEMENT-2026-08-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-EXIT-COVERAGE-MATRIX-2026-08-12.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-EXIT-LOOP-DECOUPLE-2026-08-12.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-EXIT-REFINEMENT-2026-07-12.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-FEED-AND-TP-CAP-OWNER-2026-08-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-GUARD-WIRING-AND-INERT-FOLDS-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-ICTSCALP-ETH15M-STALE-SHIP-2026-07-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-LADDER-AND-COLLAPSED-STATES-2026-08-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-LADDER-VERDICTS-AND-PAIRS-CLOSE-PATH-2026-08-10.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-LIVE-REACHABILITY-BASIS-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-PER-ERA-P80-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-PULLBACK-FRAC-CROSS-LEG-2026-08-24.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-PULLBACK-STALE-BACKLOG-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-READINESS-AND-CRITICALS-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-TPCAP-PROVENANCE-AND-BLOCK-SIZE-2026-08-13.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-TREND-ENGINE-RETIREMENT-2026-08-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-USO-LEVER-REACHABILITY-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-WF-EFFECTIVE-FOLDS-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M20-WRITTEN-AND-NEVER-READ-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M21-ENTRY-REFINEMENT-2026-07-13.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M22-D2-PAIRS-READINESS-2026-07-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M22-D2-PAIRS-SLEEVE-2026-07-15.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M24-FUNDING-CAPTURE-2026-07-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M25-FCPCV-DURABLE-20260721.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M25-PROMOTION-EXECUTION-20260720.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M27-ALTCOIN-PROMOTION-2026-07-21.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M27-ICTSCALP-EXIT-REFINEMENT-2026-07-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M27-P0-BATCH1-2026-07-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M28-VALUE-CONSTRUCTION-D4D5-20260727.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M28-VALUE-CONSTRUCTION-P4-20260727.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M30-BACKTEST-FIRST-20260727.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M30-PLATFORM-BUILDOUT-20260727.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M30-TRACK1-EXIT-HEAD-20260728.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-HARNESS-MFE-DIST-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-P3-READERS-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-P4-MFE-PARITY-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-P5-RR-FLOOR-HARNESS-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-POSITION-TELEMETRY-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-TERMINAL-STAMP-2026-08-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M31-VERIFY-AND-COVERAGE-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M36-TRACK-D-20260727.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M7-STRATEGY-REVIEW-GATE-2026-06-09.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M8-CLOSEOUT-2026-06-10.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M8-STRATEGY-TUNING-S0.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M8-STRATEGY-TUNING-S1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M8-STRATEGY-TUNING-S2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-M8-STRATEGY-TUNING-S3.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MEANTIME-WSA-2026-06-02.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MES-AUTOHEAL-2026-05-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MES-GOLIVE-2026-05-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MI159-ONE-LIVE-WORKPLAN-2026-09-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MI161-DOCUMENT-LIBRARY-2026-09-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MI195-QLD-TQQQ-EXAMINED-2026-09-08.md` | history | historical | — | 2026-09-08 | `mi-195:self-measured-2026-09-08 / population-stated` | MI-195: qld/tqqq examined — routing gap not missing dataset; 175 TP cells each, zero pass, so `unexamined` becomes a measured `none`; cap_r 1.40/0.99 vs 3.14 control. Also files document_index.py --write as a 436-doc false-verification trap. |
| `docs/sprint-logs/S-MI215-SOAK-DOCTRINE-REACHES-M7-GATE-2026-09-09.md` | history | historical | — | 2026-09-09 | `mi-215:self-measured-2026-09-09 / population-stated` | MI-215: the soak-doctrine guard reached the M7 gate by NO code path; check D (AST, alias closure) now does, positive-controlled and ratcheted at 7 known branches. Records the measured premise correction — the offline evidence infra is head-scoped, 51 of 52 packets read `backtest_anchor: null`, so no per-leg edge artifact exists to re-point at. |
| `docs/sprint-logs/S-ML-FEATURE-GAPS-20260729.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML-FORWARD-T13-RANKER-2026-07-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML-REGIME-CLASSIFIER-FIX-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML-RESEARCH-WEEK-WSA-2026-07-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML-TIER1-OFFLINE-2026-07-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML-VOLGATE-RECONCILE-2026-07-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML2-CELL-REAUDIT-2026-08-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ML2-GATE-FLOOR-2026-08-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-CLOSEOUT-2026-06-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S10.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S12.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S13.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S14.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S15.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S15a.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S15b.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S3.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S4.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S5.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S6-FU-2.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S6-FU.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S6.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S7.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S8-FU.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S8.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MLOPT-S9.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-MSE-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-NIGHT-N3-N7-E35-RESWEEP-2026-08-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-NOTIF-STREAMLINE-2026-07-08.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-OFFLINE-VOL-AXIS-20260730.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-OPERATING-MACHINERY-AUDIT-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-OPERATOR-OWED-REGISTER-2026-08-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-OPS-COMMENT-RACE-FIX-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-OPTIONS-DISPATCH-AUDIT-2026-07-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ORPHAN-FLAP-HARDENING-2026-06-25.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-P1X-REALR-NETTEDPNL-20260806.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PAIRS-CONTROL-EXIT-RESIDUE-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PAPER-PORTFOLIO-2026-07-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PB-ETH-PULLBACK-ADX-20260726.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PER-ACCOUNT-ARBITRATION-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROBE-COVERAGE-EXPANSION-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROFIT-GAPS-2026-05-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROP-INBOUND-LOOP-2026-06-21.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROP-RISK-ACCOUNT-ID-2026-06-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROVENANCE-EXITLEAK-ROOTCAUSE-2026-07-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROVENANCE-IB-EXECUTIONS-2026-07-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-PROXY-EQUITIES-ALPACA-LIVE-2026-07-07.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-R3-OFFLOAD-TRANSPORT-AND-TEST-ISOLATION-2026-08-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-R6-VM-RESIDENCY-VERDICT-2026-08-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-RECOMB-SWEEP-2026-06-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S0-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S1-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S2-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S3-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S4-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S5-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REFACTOR-S6-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REGIME-A-VOLVERDICT-LIVE-2026-06-27.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REGIME-DEBT-REC5-FOLLOWUPS-2026-07-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REGIME-DEBT-WALKFORWARD-2026-07-29.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-REGIME-DIVERSIFY-2026-06-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-RESEARCH-BACKLOG-C30UJ9-2026-08-02.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-RESEARCH-DISPOSITION-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-RINA-REVIEW-2026-06-04.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ROADMAP-RECONCILE-2026-07-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ROADMAP-REVIEW-WORKPLAN-2026-08-04.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ROADMAP-STATUS-REVIEW-2026-08-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ROADMAP-WORKPLAN-REVIEW-2026-08-14.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-ROADMAP-WORKPLAN-UPDATE-2026-08-05.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SECURITY-INTRUSION-AUDIT-2026-06-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SECURITY-TOKEN-COMPROMISE-2026-08-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-FVG-RANGE-2026-05-30.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S0-2026-05-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S1-2026-05-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S2-2026-05-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S3-2026-05-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S4-A-2026-05-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-IMPROVE-S9-2026-05-24.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAT-REFINE-0618.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRATEGY-COVERAGE-GUARD-2026-07-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-STRAY-OCA-SOAK-WRITER-2026-09-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSREV-EXIT-COVERAGE-AND-CANCEL-2026-08-16.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSREV-TRADE-MECHANICS-2026-08-18.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSREVIEW-FOLLOWUP-2026-07-02.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSREVIEW-PROP-ADX-2026-07-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-AUDIT-REVIEW-2026-08-13.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-REPORT-2026-06-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-REVIEW-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-REVIEW-20260810.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-REVIEW-20260821.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-SYSTEM-REVIEW-STRUCTURAL-2026-08-24.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-T2-RESEARCH-QUEUE-AND-TEST-INTEGRITY-2026-08-27.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TEST-CACHE-FLAKE-FIX-2026-05-20.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TIER3-DISPOSITIONS-2026-08-23.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TRAINER-BT-1.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TRAINER-RESOURCE-OOM-2026-07-17.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TREND-DONCHIAN-ROOTCAUSE-2026-07-11.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-TREND-HARNESS-CONVERGENCE-2026-08-08.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-V2-LIGHTGBM-PROMOTION-PREP-2026-05-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VM-CUTOVER-RETRO-2026-06-15.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VOLGATE-ETHSOL-EVIDENCE-2026-07-06.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VOLGATE-GOLIVE-2026-06-28.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VWAP-ANCHOR-EXPERIMENT-2026-05-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VWAP-PARAM-SWEEP-2026-05-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VWAP-POLICY-INVESTIGATION-2026-05-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VWAP-POLICY-LIVE-WIRE-2026-05-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-VWAP-SWEEP-DISPATCH-2026-05-19.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WALLET-TRUTH-SCOPE-AND-STRAY-OCA-ARM-2026-08-31.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WAVE0-EXIT-FETCH-20260821.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WEEKLY-REVIEW-EXEC-2026-08-01.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WORKPLAN-CONTINUATION-2026-08-22.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WORKPLAN-G1-2026-08-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WORKPLAN-GATE0-2026-08-26.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WORKPLAN-PHASE0-20260822.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-WORKPLAN-REPLAN-20260821.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/S-XRP-TPSL-LEGCAP-2026-07-21.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/T-NOISE-health-check-cleanup.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/T1-named-cloudflare-tunnel.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-logs/T2-unified-path-resolution.md` | history | historical | — | 2026-09-07 | `dir:sprint-logs-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-plans/2026-04-28-audit.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/CONFLICT-POLICY-WALKFORWARD-SCOPE-2026-05-30.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/CONSOLIDATION-ROADMAP-2026-05-31.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/CROSS-ZERO-2026-05-31.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/CURRENT-SPRINT.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/M18-CAPITAL-ALLOCATOR-EXECUTION-PLAN.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/M20-EXIT-REFINEMENT-SESSION-PROMPT.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/MEANTIME-EXPANSION-PROGRAM-2026-06-02.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/README.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ROADMAP-2026-05-19.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/ROADMAP-AI-ANALYST-2026-05-26.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/ROADMAP-ANDROID-COMPANION-APP-2026-05-26.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/ROADMAP-INTEGRATED-SIM-2026-05-30.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/ROADMAP-MULTI-STRATEGY-REFACTOR-2026-05-20.md` | plan | unknown | — | never | `name:roadmap-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/S-047-STATUS.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/S-047-bybit2-spot-margin.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/STRATEGY-IMPROVEMENT-PROGRAM-2026-05-23.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/README.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws1-architecture-baseline.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws3-data-foundation.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws4-followups.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws4-training-center.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws5-baseline-models.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws6-open-source-models.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws7-advisory-influence-operator-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws7-deployment-tiers.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws7-fu-registry-wire.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ai-traders/ws9-runtime-split.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/bug-042-monitor-loop-reconciler.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/ict-validation-report.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/s006-synthetic-report.md` | plan | unknown | — | never | `dir:sprint-plans-are-forward-commitments / not-assessed` | — |
| `docs/sprint-plans/sprint-plan-2026-04-27.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/sprint-plan-2026-04-28.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-plans/sprint-plan-2026-04-30.md` | plan | unknown | — | never | `name:plan-is-a-forward-commitment / not-assessed` | — |
| `docs/sprint-summaries/m1-p1-followups-and-m2-close-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/operator-onboarding-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-008.5-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-009-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-010-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-011-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-012-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-013-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-014-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-021-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-022-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-023-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-024-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-025-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-026-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-027-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-028-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-041-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-042-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-043-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-044-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-045-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-046-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-048-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-067-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-S0-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-telegram-format-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprint-summaries/sprint-velotrade-phase2-summary.md` | history | historical | — | 2026-09-07 | `dir:sprint-summaries-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/recurring-hardening-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/recurring-model-training-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/recurring-strategy-improvement-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-012-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-013-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-014-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-015-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-017-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-020-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-021-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-041-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-044-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-045-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-046-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-048-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-061-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-062-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-063-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-064-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-065-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/sprints/sprint-067-prompt.md` | history | historical | — | 2026-09-07 | `dir:sprints-are-session-records / dir:record-of-a-completed-session` | — |
| `docs/strategies/ict.md` | architecture | unknown | — | never | `dir:strategy-specs-declare-behaviour / not-assessed` | — |
| `docs/strategies/ict_scalp_5m.md` | architecture | unknown | — | never | `dir:strategy-specs-declare-behaviour / not-assessed` | — |
| `docs/strategies/supported_strategies.md` | architecture | unknown | — | never | `dir:strategy-specs-declare-behaviour / not-assessed` | — |
| `docs/strategies/vwap_mean_reversion.md` | architecture | unknown | — | never | `dir:strategy-specs-declare-behaviour / not-assessed` | — |
| `docs/strategy-coverage-matrix.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/strategy-readiness-ladder.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/strategy-review-gate.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/strategy-tuning.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/training-population-matrix.md` | unknown | unknown | — | never | `not-assessed / not-assessed` | — |
| `docs/unified-confidence-risk-DESIGN.md` | architecture | unknown | — | never | `name:design-declares-a-contract / not-assessed` | — |
| `docs/workflows/README.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/accounts-risk.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/accounts.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/app-unit-config.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/app.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/backtest-trigger.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/backtest-ui.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/dashboards.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/db.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/return_commands.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/strategies.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/telegram_bot.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workflows/trading_school.md` | lookup | unknown | — | never | `dir:workflow-notes-are-lookup / not-assessed` | — |
| `docs/workplan.md` | plan | historical | — | 2026-09-07 | `name:workplan-is-a-forward-commitment / mi159:plan-status-header:historical` | — |

<!-- DOCUMENT-INDEX-ROWS-END -->
