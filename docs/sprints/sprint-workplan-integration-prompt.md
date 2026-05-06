# Sprint S-WPI — Workplan Integration (M0..M10 reset)

> **Sprint type:** Doc-restructure + decision sprint. Multi-session.
> **Owner:** Claude Code (autonomous, with one Tier 2 ping for the S-015 pause/continue decision).
> **PM:** Ben. **Tech Lead:** Perplexity.
> **Created:** 2026-05-06 — kickoff after the operator delivered an updated workplan that elevates the web app, formalises a 3-tier merge model, splits the two-bot responsibilities, adds a model registry, and reorganises the roadmap into M0..M10.
> **Out-of-band naming:** `S-WPI` (Workplan Integration) — does not consume an `S-NNN` slot in the linear backlog.

---

## 1. Goal

Reconcile the entire repo's planning surface — `CLAUDE.md`, `ROADMAP.md`,
`docs/claude/milestone-state.md`, `docs/claude/sprint-planning.md`, the
sprint prompts under `docs/sprints/`, and the recurring-session triggers —
with the operator's 2026-05-06 workplan. Stop running the previous M-S0
through M-S-015 framing; start running the M0..M10 framing **without
breaking any in-flight code work**. The operator should leave this
sprint with: a clear active milestone under the new numbering, a
canonical CLAUDE.md that reflects the 3-tier merge model and the
two-bot split, a roadmap that matches what's actually been built, and
a documented decision on whether S-015 (Web Client V2 Component Tabs)
finishes now or pauses behind M3/M4/M5.

---

## 2. Dependencies

- **Sprint dependency** — BUG-058 (duplicate pings) merged on `main`
  (PR #423, commit `350cc39`). Without dedupe, every doc PR in this
  sprint would re-fire stale pings on every merge.
- **Sprint dependency** — BUG-059 (bot routing) merged on `main`
  (PR #426, commit `1c34d93`). Doc-restructure pings need to ride on
  `@claude_ict_comms_bot` (the bridge), not the trading bot.
- **External dependency** — operator confirmation on **two open
  questions** (see § 4):
  1. **S-015 pause vs continue** — new workplan puts web UI at M6, after
     risk controls (M3) + CI hardening (M4) + strategy testing
     workflow (M5). S-015 (Web Client V2 — Component Tabs) is in flight.
     Should we (a) pause S-015 at T0 (kickoff), (b) finish T1..T6 first
     then pivot, or (c) absorb S-015 into M6 as-is?
  2. **5m/1h timeframe rule** — current `config/strategies.yaml` uses
     `15m`/`1m` for turtle_soup and similar for vwap. The new workplan
     mandates 5m entries with 1h structure context. Should this sprint
     audit + propose a Tier 3 strategy-config PR (for explicit operator
     approval), or defer to a strategy-improvement session under
     M3 / M5?
- **Infra dependency** — none new. All restructure is doc edits.

If either § 4 question stays unanswered, the affected checkpoints
stop at the question and ping per the ping-PR pattern.

---

## 3. Deliverables

Concrete artefacts that exist after this sprint ships:

1. **Updated `CLAUDE.md`** with:
   - Three-tier merge model (Tier 1 self-merge / Tier 2 ping with
     merge/hold buttons / Tier 3 explicit operator approval), replacing
     the current self-merge / PM-review binary.
   - Two-bot table refresh — `@bict_trading_bot` owns trade alerts only;
     `@claude_ict_comms_bot` owns Claude session pings (post-BUG-059).
   - 5m entries / 1h structure as the canonical strategy timeframe
     rule (with a pointer to the audit checkpoint that finds drift).
   - Live + dry parity rule — strategies and risk-managers run in
     **both** modes so logs and decisions are comparable even when
     execution is disabled.
   - Logs registry — Signals / Order Package / Risk Manager Decision /
     Trade / Messages / Sprint-Task / Bug / Lessons / Comms /
     Deployment / Strategy Validation / Model Registry. Cross-references
     to the existing `data_layer` schema for the ones that exist; flag
     the ones that don't.
2. **Updated `ROADMAP.md`** mapped to M0..M10 with a "what's already
   shipped under the old framing" reconciliation table at the top.
3. **Updated `docs/claude/milestone-state.md`** — Active milestone
   moves from M-S-015 to whichever M0..M10 milestone the operator
   greenlights as next; recently-closed list shows the M0/M1/M2 mapping
   to M-S0/old-comms-work/M-S-014; queued list shows M3..M10.
4. **New `docs/claude/tier-policy.md`** (or extended `vm-operator-mode.md`)
   formalising the Tier 1/2/3 spec — what each tier covers, the
   merge/hold button payload, the audit trail.
5. **New `docs/claude/model-registry.md`** scaffolding — at minimum
   the schema (model name / version / type / role / I/O / training
   history / eval results / deployment status), an empty registry
   table, and a pointer to where the canonical registry lives (likely
   `data/models/registry.jsonl` or a SQLite table).
6. **Audit report `docs/audit/strategy-timeframe-2026-05-06.md`** —
   inventory of which strategies use which timeframes today, where
   the 5m/1h rule applies cleanly, where it breaks (e.g. turtle_soup's
   15m sweep + 1m entry pattern), and a recommended Tier 3 PR shape
   if the operator wants to enforce the new rule.
7. **Decision record `docs/claude/decisions/s015-pause-or-continue.md`**
   capturing the operator's answer to § 4 question 1 with the
   reasoning trail.
8. **Closing checkpoint `CP-2026-05-NN-WPI-COMPLETE`** — full PR list,
   architectural decisions, deferred items, lessons learned.

Each deliverable maps to at least one PR in § 4.

---

## 4. Checkpoints

One row per checkpoint, in expected merge order. PR sizes ≤ 400 LOC
docs; ≤ 50 LOC for any code.

| #   | Checkpoint title                              | What completes by then                                                                                          | Risk class | Wall-clock | Gates                  |
|-----|-----------------------------------------------|-----------------------------------------------------------------------------------------------------------------|------------|------------|------------------------|
| T0  | Kickoff (this PR)                             | Sprint prompt filed; CP-2026-05-06-WPI-01 appended; operator sees the workplan-integration plan + 2 open Qs.    | docs-only  | ≤ 30 min   | T1, T2 (parallel)      |
| T1  | Decision PR #1 — S-015 pause/continue         | Open a Tier 2 ping-PR + draft decision-record PR with the operator's three options + recommendation. Wait.      | docs-only / Tier 2 | ≤ 30 min + wait | T3, T4, T5 |
| T2  | Decision PR #2 — 5m/1h timeframe audit gate   | Open a Tier 2 ping-PR + draft decision-record PR with the audit findings + Tier 3 strategy-config recommendation. Wait. | docs-only / Tier 2 | ≤ 60 min + wait | T6 (audit only) |
| T3  | M3-PR #1 — CLAUDE.md restructure (Tier model + two-bot table + live+dry parity rule + logs registry) | New CLAUDE.md committed, no behaviour change yet. | docs-only | ≤ 90 min | T4, T5, T7         |
| T4  | M3-PR #2 — ROADMAP.md mapped to M0..M10       | Reconciliation table + per-milestone status; matches what's actually shipped.                                   | docs-only | ≤ 60 min | T5                     |
| T5  | M3-PR #3 — milestone-state.md reset           | Active block + Recently closed + Queued all reflect the new framing per the operator's S-015 decision (T1).     | docs-only | ≤ 45 min | T6, T8                 |
| T6  | M4-PR #1 — Strategy timeframe audit report    | `docs/audit/strategy-timeframe-2026-05-06.md` + decision record from T2.                                        | docs-only | ≤ 90 min | (none — feeds future strategy sprint) |
| T7  | M4-PR #2 — Tier policy doc                    | `docs/claude/tier-policy.md` formalising the 3-tier spec + merge/hold button payload + audit trail.             | docs-only | ≤ 60 min | T9                     |
| T8  | M4-PR #3 — Model registry scaffolding         | `docs/claude/model-registry.md` + canonical registry path decided + empty initial registry committed.           | docs-only | ≤ 60 min | T9                     |
| T9  | Sprint close                                  | Sprint summary + closing checkpoint + ping ride.                                                                | docs-only | ≤ 60 min | (sprint closed)        |

**Total wall-clock estimate:** ≤ 8 hours of Claude time across 5–6
sessions (T0 + T1/T2 ping waits + T3/T4/T5 in one session + T6/T7/T8
in another + T9 close). Operator wait time on T1/T2 is bounded by their
availability.

### 4b. Unit boundary declaration

| Unit                                  | Role in this sprint                                                                                                           |
|---------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `src/units/strategies/`               | **untouched** (the audit T6 reads strategy code but does not modify it; any 5m/1h enforcement is a follow-up Tier 3 sprint).   |
| `src/units/accounts/`                 | **untouched**.                                                                                                                |
| `src/units/db/` (`src/data_layer/`)   | **reads** — model-registry doc T8 references the schema but does not change it.                                                |
| `src/units/ui/`                       | **untouched**.                                                                                                                |
| `src/runtime/`                        | **untouched**.                                                                                                                |
| `src/bot/`                            | **untouched** (the BUG-059 routing fix already shipped pre-sprint).                                                            |
| `src/core/coordinator.py`             | **untouched**.                                                                                                                |
| `docs/`                               | **owns** — full restructure of `CLAUDE.md`, `ROADMAP.md`, `milestone-state.md`, plus new files under `docs/claude/`, `docs/audit/`, `docs/claude/decisions/`. |

No new cross-unit imports. This sprint is doc-restructure only.

---

## 5. Risk class & merge model

| Class           | Self-merge? | This sprint's PRs                                                                            |
|-----------------|:-----------:|----------------------------------------------------------------------------------------------|
| **docs-only**   | ✅           | T0 (this kickoff), T3, T4, T5, T6, T7, T8, T9.                                               |
| **Tier 2**      | ❌ (ping)    | T1 (S-015 pause decision), T2 (5m/1h timeframe decision). Decision-record PRs are draft + ping-PR. |
| **Tier 3**      | ❌ (operator) | _none in this sprint._ A 5m/1h timeframe enforcement PR — if the operator picks that path in T2 — is a **separate follow-up sprint** outside the WPI scope. |
| **infra / strategy / live** | n/a | _none._                                                                              |

**Default rule:** every doc PR self-merges after CI green. Decisions
that affect the live system (S-015 pause, strategy timeframe change)
ride on Tier 2 ping-PRs and wait for the operator.

---

## 6. Success criteria

- ✅ `CLAUDE.md` § "Telegram bots" matches BUG-059 reality
  (`@bict_trading_bot` = trade alerts only; `@claude_ict_comms_bot` =
  Claude session pings).
- ✅ `CLAUDE.md` has a "Merge tier policy" section listing Tier 1/2/3
  with examples for each.
- ✅ `ROADMAP.md` top section has a reconciliation table mapping the
  old `S-NNN` numbering to the new `M0..M10` numbering, plus a
  per-milestone status column.
- ✅ `docs/claude/milestone-state.md` Active milestone is one of M0..M10
  (operator's pick), not M-S-015.
- ✅ `docs/audit/strategy-timeframe-2026-05-06.md` exists and lists every
  strategy's current entry timeframe + structure timeframe.
- ✅ `docs/claude/decisions/s015-pause-or-continue.md` exists with the
  operator's recorded answer.
- ✅ `docs/claude/model-registry.md` exists with the schema + an empty
  initial registry committed at the canonical path.
- ✅ All sprint summary doc files contain the words "M0..M10
  workplan" so future sessions can `grep` for the reset point.
- ✅ Live trader uptime preserved (no `src/runtime/`, `src/units/accounts/`,
  `src/units/strategies/` touched). `scripts/check_dry_run_in_diff.py`
  clean every PR.
- ❌ No live-trading code touched in this sprint. (Strategy-timeframe
  enforcement is deferred to a follow-up Tier 3 sprint.)

---

## 7. Hard guardrails

1. Do **NOT** touch any code under `src/runtime/`, `src/units/`,
   `src/bot/`, `src/core/`, `src/web/`, `config/*.yaml`, `deploy/`,
   `scripts/` (other than docs).
2. Do **NOT** open a strategy-config PR in this sprint — even if the
   audit T6 finds clear drift. Defer to a follow-up Tier 3 sprint
   that the operator explicitly approves.
3. Do **NOT** force-merge T1/T2 decision PRs without the operator's
   reply — those are Tier 2 ping-and-wait.
4. PR size ≤ 400 LOC docs; ≤ 50 LOC code (none expected).
5. Live-mode invariant (`CLAUDE.md` § "Live-mode invariant"): every PR
   runs `scripts/check_dry_run_in_diff.py` before opening.
6. Pings ride on `@claude_ict_comms_bot` (post-BUG-059) via
   `notify_on_pull.py` writing to `runtime_logs/pending_claude_pings/`.
   Do **not** route this sprint's pings through the trader bot.

### Files Claude may modify

- `CLAUDE.md` (root).
- `ROADMAP.md` (root).
- `docs/claude/milestone-state.md`.
- `docs/claude/tier-policy.md` (new).
- `docs/claude/model-registry.md` (new).
- `docs/claude/decisions/*.md` (new dir).
- `docs/audit/strategy-timeframe-2026-05-06.md` (new).
- `docs/sprints/sprint-workplan-integration-prompt.md` (this file —
  mid-sprint refinements only).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (per-checkpoint entries).
- `docs/sprint-summaries/sprint-workplan-integration-summary.md` (T9).

### Files OFF LIMITS

Everything not on the modify list. Specifically:
- `src/**` (all of it).
- `config/**`, `deploy/**`, `scripts/**` (other than reading).
- `tests/**` (no test changes — this sprint is doc-only).
- Anything under `ml/`, `notebooks/`, `data/` (other than committing
  an empty `data/models/registry.jsonl` if T8's canonical-path
  decision lands there).

---

## 8. Hand-off

What follow-up sprints need from this one:

- **M3 (Risk controls foundation)** — opens after this sprint sets the
  Active milestone to M3. Existing risk caps + kill switch stay; sprint
  adds tests/audit + one Tier 2 PR for any cap value change.
- **M4 (Repo hygiene + CI)** — janitor sprint. The 12 architecture-audit
  items in `docs/claude/architecture-audit-2026-05-02.md` likely fold in.
- **M5 (Strategy testing workflow)** — Telegram `/test <strategy>`
  command + structured request artefact + dry-run/backtest plumbing.
- **M6 (Web app UI)** — depending on T1 decision, either resumes
  S-015 (Web Client V2) under the new naming, or kicks off fresh.
- **5m/1h timeframe enforcement sprint** (Tier 3) — only opens if T2
  decision goes that way; operator gates the strategy-config PR.

End of prompt.
