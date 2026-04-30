# Sprint planning policy

Every sprint prompt committed under `docs/sprints/sprint-NNN-prompt.md` must
satisfy this template before work starts. Title-only roadmaps and free-form
prompts are no longer acceptable — they create rework, hidden dependencies,
and merge-order bugs.

The point is to plan **at the checkpoint level**, not the sprint level, so we
can see dependencies before we hit them and pick the smartest order of work.

## Required sections

### 1. Goal (one paragraph)

What does the operator get when this sprint ends? One paragraph, written for
the person who'll review the merged stack a week from now. Do not paste the
acceptance criteria — those live in § 6.

### 2. Dependencies

A bulleted list of things that must already be true for the sprint to even
start. Each bullet is one of:

- **Sprint dependency** — "S-NNN must have merged" + the specific PR or
  checkpoint that gates this one.
- **Infra dependency** — "the harness from S-NNN must exist on `main`",
  "the Oracle VM must be reachable", "an env var X must be set somewhere".
- **External dependency** — "Bybit account funded with USDT", "operator
  online during business hours", "intraday OHLCV data available on the host".

If a dependency is missing or uncertain, the sprint **cannot start** until
either the dependency lands or the prompt is rewritten to remove it.

### 3. Deliverables

Concrete artifacts that exist after the sprint ships. "A backtest harness"
is not a deliverable; "`scripts/sprint015/run_backtest.py` with `≥ 5`
contract tests" is. Each deliverable maps to at least one PR.

### 4. Checkpoints

The actual planning. **One row per checkpoint** in the order the operator
expects them to land:

| # | Checkpoint title | What completes by then | Risk class | Wall-clock | Gates which next checkpoint |
|---|---|---|---|---|---|
| T0 | … | … | infra / strategy / model / docs | … | T1, T2 |
| T1 | … | … | … | … | T3 |
| … | … | … | … | … | … |

Conventions:

- **Risk class** decides self-merge vs DRAFT-for-PM (see § 5).
- **Wall-clock** is the planner's honest estimate. If the actual run blows
  past 2× the estimate, the sprint stops and the prompt is revised. No
  "just one more thing" fishing.
- **Gates** lets the planner spot serial bottlenecks. If T3 / T4 / T5 all
  list T2 as their gate but T2 is itself gated on an external dependency,
  the sprint is too fragile.

### 5. Risk class & merge model

Every PR opened during the sprint maps to a risk class:

| Class | Self-merge? | Examples |
|---|:-:|---|
| **infra** | ✅ self-merge | harness, fetcher, sampler, scripts, fixtures, reports, checkpoints, sprint prompts, doc updates |
| **strategy / model** | ❌ DRAFT for PM | `config/strategies.yaml` parameter changes, `src/units/strategies/*.py` code changes, model promotion, regime-filter wiring |
| **deploy / live** | ❌ DRAFT for PM | `deploy/*.service` units, secrets handling, `src/runtime/orders.py`, `src/main.py`, `src/runtime/risk_counters.py` |
| **docs-only** | ✅ self-merge | reports, summaries, runbook updates, this kind of file |

If a checkpoint is mixed-risk, **split it** into separate PRs. Don't bundle a
config tweak with a docs update — the docs land instantly, the config waits
for PM, and reviewers can't tell what's gated on what.

### 6. Success criteria

Measurable. Each criterion is something a script or a person can check after
the sprint closes:

- "✅ `pytest tests/sprintNNN/` returns 0."
- "✅ `docs/backtests/sprintNNN/baseline.md` exists and lists per-fold metrics."
- "✅ Live PR (#NNN) has been merged by PM **or** explicit deferred-with-reason
  note in the final checkpoint."
- "❌ Failed experiments do not get their own PRs — they live in the summary
  report only."

Avoid vibes ("the harness is ready", "VWAP feels better"). If you can't write
the test, you can't claim the criterion.

### 7. Hard guardrails

A short list — typically inherited from the standing repo guardrails in
`CLAUDE.md` (live-trading code paths, secrets, deploy units, etc.) — plus
sprint-specific ones the planner has identified. Off-limits files explicitly
named.

### 8. Hand-off

What the next sprint needs from this one. If the next planning session
should pick up specific deliverables / known issues / deferred work, list
them here so the planner can read this section first and skip everything
else.

## When to revise the prompt mid-sprint

If during execution a checkpoint reveals:

- A dependency the planner missed — **stop**, document, revise the prompt.
- A wall-clock estimate that's > 2× off — **stop**, document, revise.
- A risk class that should have been DRAFT but was self-merged (e.g. a
  config change snuck into an infra PR) — **stop**, log the bug in
  `docs/claude/bug-log.md`, propose a corrective PR.

Mid-sprint revisions ship as a commit to the sprint prompt with a clear
message ("S-015 prompt: add no-Bybit-for-training rule + split-session
execution"). Don't silently shift the goalposts.

## Anti-patterns to avoid

1. **Bullet-list goal, no checkpoint plan.** "Improve VWAP" with no
   checkpoint table → planner is gambling.
2. **Hidden dependencies.** "T4 sweeps parameters" without mentioning that
   T2 must lock the baseline first.
3. **Mixed-risk PRs.** Bundling a config tweak with a docs update.
4. **Synthetic / wrong-resolution data backdoors.** If real data is
   unavailable, the sprint stops or the prompt explicitly downgrades the
   deliverable. Do not let the harness silently substitute fake data —
   training on incorrect resolution is worse than no result at all.
5. **Tests that vibe-check.** Every success criterion needs a passing
   command or a person ticking a box. "It looks right" is not a test.
6. **Skipping the bug log.** Every defect found and fixed mid-sprint goes
   in `docs/claude/bug-log.md`.

## Cross-references

- `docs/claude/bug-log.md` — bug log convention.
- `docs/claude/telegram-pings.md` — required Telegram pings on checkpoint
  commits and on blockers.
- `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` — what each checkpoint
  entry must contain.
- `docs/claude/checkpoint-workflow.md` — the rules for appending a
  checkpoint at session end.
