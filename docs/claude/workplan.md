# AI Trader — Original Workplan (HISTORICAL — superseded 2026-05-10)

> **Status:** Superseded 2026-05-10 by the S-CANON-1 canonical
> doc set. Authority order is now:
> 1. docs/CLAUDE-RULES-CANONICAL.md
> 2. docs/ARCHITECTURE-CANONICAL.md
> 3. ROADMAP.md
> 4. current sprint log under docs/sprint-logs/
>
> This file is preserved for historical context. Do not treat it
> as authoritative on policy or sequencing.

> **Authority (HISTORICAL — no longer in force):** This document was
> captured verbatim from the operator on **2026-05-06** as the original
> "decider" for what the project is building and the rules it follows.
> **That authority was superseded on 2026-05-10** by the canonical doc
> set (see the banner above). It is **no longer the decider** and does
> **not** win conflicts — when this file disagrees with
> `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`,
> `ROADMAP.md`, or `CLAUDE.md`, **the canonical doc wins.** Read this
> file only for the original goal + operating-principle framing, which
> the canonical set carries forward.
>
> **2026-05-08 correction (S-048 P1-A):** § "Telegram bots /
> @claude_ict_comms_bot / ClaudeBot workflow" was rewritten in
> this revision. The earlier wording described a 5-step two-way
> request/response loop on ClaudeBot with merge/hold buttons,
> required-action prompts, and recovery alerts. That description
> was inconsistent with the intended architecture and with the
> on-disk implementation; the operator confirmed on 2026-05-07
> that ClaudeBot is intentionally one-way and the S-027 two-way
> request/response system correctly lives on the trader bot.
> See `docs/audits/M1-comms-audit-2026-05-07-fresh.md` for the
> full audit context.
>
> **This document does NOT replace the rest of the documentation.**
> CLAUDE.md, README.md, the per-task docs under `docs/claude/`,
> the runbooks, the bug-log, and the architecture audits all stay
> required reading — they hold operational detail, conventions,
> and context this workplan deliberately does not duplicate. Use
> them as before.
>
> **Sprints continue.** This document does NOT replace the
> sprint-based execution model — sprints (per
> `docs/claude/sprint-planning.md`) remain the unit of work.
> Sprints execute *against* this workplan; the workplan defines
> *what* to execute and *the rules* for execution.
>
> **Verify-before-trusting-done.** When a milestone, sprint, or
> task is marked "done" in any doc, the next session **must verify**
> that the on-disk state actually conforms to this workplan before
> accepting the "done" status. If on-disk state has drifted from
> the workplan, fix the drift before continuing other work.
>
> **Consolidation, not deletion.** If documentation is bloated or
> redundant, it's fine to consolidate — fewer docs that say the
> same thing more clearly. But **don't delete unique content** just
> because it isn't restated in this workplan. The workplan is the
> constitutional layer; the other docs hold the operational
> substance. Only remove docs that genuinely duplicate workplan
> content or describe a state this workplan has explicitly
> superseded.

---

## Goal

Maintain a portfolio of AI trading strategies that each target at
least 1–2% weekly returns, with at least 2–3% weekly returns
overall, while prioritizing safety, visibility, auditability, and
controlled rollout of live behavior.

## Current priorities

The current phase of the project is **system hardening and
operational visibility**, not aggressive expansion of broker /
account infrastructure. Prop-trading infrastructure is **explicitly
deferred** for now; it should not be built until the system is
ready to support that trading mode in a deliberate later phase.
The web app is now a **crucial near-term priority** because it is
needed as a stable source of truth for understanding what the
system is doing in real time and across sessions.

## Core operating principles

1. **Safety before expansion.** No new live behavior should be
   introduced before risk controls, visibility, and validation
   paths are in place.
2. **Repo is the source of truth.** Plans, logs, comms artifacts,
   workflows, and state transitions should be repo-tracked
   wherever practical.
3. **Claude autonomy is the default.** Claude should keep working
   unless a task falls into a clearly defined approval category.
4. **Visibility is mandatory.** The system should always expose
   enough logs, dashboards, and status surfaces for the operator to
   understand what it is doing.
5. **Operator actions must be simple.** Any required VM action
   should come with a one-click Colab notebook or similarly simple
   copy-ready workflow written for a non-technical user.
6. **Use paid compute carefully.** Claude should focus on repo
   architecture, code changes, tests, and reviews, while Colab,
   Google AI Studio, and Hugging Face should absorb as much
   research and heavy compute work as possible.

---

## Milestone and session system

Claude must always create and maintain a milestone plan, and each
milestone must be broken into session-sized sprints and then
further into checkpoints, regardless of whether the work is
roadmap-based, ad hoc, or part of the recurring auto-task routine.

### Sprint and checkpoint numbering (MANDATORY)

Sprint numbers and checkpoint IDs are **monotonic and unique
across the entire repo lifetime**, regardless of which workplan,
roadmap, milestone, or sprint type they belong to. This is so the
project can keep track of work thoroughly even when the plan
changes mid-flight.

**Sprints (`S-NNN`):**

- The next sprint to file uses `(highest sprint number ever
  assigned anywhere in the repo) + 1`. Search every tracked file
  (docs, code, tests, summaries, comments) for `S-NNN` references
  before picking the next number.
- A sprint number is **fixed once assigned**. If the workplan
  changes mid-flight and the sprint's scope or title shifts, the
  sprint keeps its original number — only the title / scope
  changes.
- Numbers are **never reused**, never re-numbered, never
  deleted. A cancelled or superseded sprint keeps its number;
  the prompt file is annotated `SUPERSEDED by S-NNN` and left
  in place.
- Auto-task / ad-hoc / roadmap sprints all draw from the **same**
  numeric sequence. There is no per-track namespace.
- **Snapshot of the sequence as of 2026-05-06:** highest
  *assigned-and-used* number is **S-035** (architecture-audit
  2026-05-02). `S-040` appears once in
  `tests/test_s031_pr5_file_reads_in_ui.py:237` as a "future
  work" placeholder — not real work yet, but the number is
  considered burned. **The next sprint to file is S-041** (skip
  the burned S-036..S-040 range to keep the convention safe).

**Checkpoints (`CP-YYYY-MM-DD-NN`):**

- Format stays as in `docs/claude/checkpoint-workflow.md` —
  `CP-<sprint-date>-<NN>` with optional title suffix
  (`CP-YYYY-MM-DD-NN-<short-id>`). The date and the per-day NN
  combine to form a globally-unique ID; the date guarantees
  uniqueness across days, the NN guarantees uniqueness within a
  day.
- A checkpoint ID is **fixed once committed**. If the
  workplan changes after the checkpoint lands, the checkpoint
  keeps its ID — the entry body can be amended in a follow-up
  checkpoint, never by editing the original.
- Never reuse an NN within a date. Never reuse a checkpoint ID
  across the repo.

### Milestone types

- **Roadmap milestone** — A sprint that progresses the planned
  roadmap for the trading system, web app, operator tooling,
  logging, AI workflows, and deployment quality.
- **Ad-hoc milestone** — A sprint initiated by the operator to
  handle urgent bugs, incidents, investigations, or newly
  prioritized ideas outside the normal roadmap sequence.
- **Auto-task milestone** — A structured recurring sprint
  initiated by Claude's daily auto-task routine using instructions
  stored in the repo.

### Session requirements

Every session-sized sprint must include:

- Sprint title and purpose
- Scope and explicit non-goals
- Checkpoints
- Dependencies and blockers
- Risk tier and merge authority
- Required validation steps
- Required documentation updates
- A closing summary with next-step handoff

### Session closing

The final checkpoint of every sprint is documentation and
project-state maintenance. Claude must update all affected
documentation, including but not limited to:

- `README.md`
- The roadmap
- Sprint / task logs
- Relevant Claude instruction files and skill markdown files
- Bug log and lessons log where applicable
- Architecture docs impacted by the sprint

Claude should also update a central milestone / session state or
handoff file so future sessions can resume from repo state rather
than relying on chat continuity alone.

---

## Decision and merge authority

Claude follows a **three-tier operating model** for merge and
approval decisions.

### Tier 1 — Claude may self-merge

Claude may self-merge work that:

- Does not directly change live trading behavior.
- Is cleanup, documentation, tests, CI, observability, schemas,
  dashboard read-path work, or isolated tooling changes.
- Affects infrastructure only when safety can be proven by tests,
  dry-run validation, or staging checks.

### Tier 2 — Claude must ping the operator with a merge / hold decision

Claude must send a structured risk-summary ping and wait for a
decision when:

- A change touches the live order path, runtime orchestration,
  deployment timers, service behavior, or any integration that
  could break execution even if strategy logic does not change.
- Claude cannot fully prove safety end-to-end.
- A change may cause restart churn, duplicate sends, sync loops,
  or deployment instability.

The ping rides on the one-way ClaudeBot channel (see § "Telegram
bots / @claude_ict_comms_bot") and must include:

- PR title.
- One-sentence summary.
- One-sentence risk if broken.
- Validation already completed.
- Link to the PR.

The operator's Merge / Hold decision is registered on **GitHub**
(PR review + web-UI merge or `gh pr merge`), not via a Telegram
callback. Adding bot-side merge authority would expand the live
surface unnecessarily.

### Tier 3 — explicit operator approval required before merge

Claude must not merge without explicit approval when a change
involves:

- Strategy parameters.
- Entry or exit logic.
- Signal thresholds.
- Position sizing formulas.
- Risk cap values.
- Promotion of any strategy from dry-run to live.

This preserves maximum autonomy for engineering work while
reserving trading-behavior changes for deliberate operator review.

---

## VM and operator actions

The operator is non-technical and the system runs on a free-tier
Oracle VM, so any manual action must be made simple and low-risk.

### Rule

If Claude needs the operator to do something on the VM, Claude
must provide:

- A copy-ready Colab notebook script.
- Short markdown headings and explanations between cells.
- Pre-filled variables and paths.
- Clear instructions for what success should look like.

### Required pre-filled values

Claude should use these exact values in any notebook or
operator-run script:

```python
SSH_KEY_FILE = 'ict-bot-ovm-private.key'
VM_USER = 'ubuntu'
VM_HOST = "158.178.210.252"
REPO_DIR = '/home/ubuntu/ict-trading-bot'
```

### Repo references

- GitHub repository: `the-lizardking/ict-trading-bot`
- Git username: `the-lizardking`
- Git email: `ben.baichmankass@gmail.com`

### Colab secret references

Claude should align any updated notebooks or automation with the
existing Colab secret names already defined for API keys, Telegram
bot tokens, GitHub access, Hugging Face access, and SSH connection
details so the operator is not forced to rewire secret names
manually.

---

## System architecture

### Trader repo

Primary codebase: `the-lizardking/ict-trading-bot`.

### Dispatcher / Coordinator

The dispatcher is the coordination layer between strategies,
risk / account logic, connectors, dashboards, and operator tools.

Responsibilities:

- Route market and account data to the correct units.
- Accept strategy outputs and forward them to the risk layer.
- Maintain the canonical live / dry-run execution gate.
- Dispatch approved orders to connectors.
- Write normalized events and status changes into the system
  logs / database.

#### Live / dry-run rule

The dispatcher maintains the **only canonical** live / dry-run
switch in the system. Strategy logic and risk logic should
continue running in **both** live and dry-run modes so the
platform still produces comparable signals, decisions, and logs
even when execution is disabled.

### Connections unit

The connectors layer handles broker and platform API integrations
for:

- Market data pulls.
- Account data pulls.
- Position and trade status pulls.
- Order submission, cancel, and close flows.
- Data feeds for the dashboard and operator surfaces.

Connector logic stays thin and standardized so broker-specific
behavior does not leak into strategy or risk code paths.

### Strategies unit

The strategies unit:

- Consumes live data from the dispatcher.
- Performs signal and setup analysis.
- Produces normalized order packages.
- Attaches confidence scores and supporting evidence.
- Sends outputs to the downstream decision pipeline.

Subcomponents:

- Strategy rules.
- Model approval layer.
- Data intake adapters.

#### Strategy timeframe rule

Strategies should read **5-minute candles for execution logic**
and use the **1-hour timeframe for market structure context**.

This timeframe rule is the current default architecture constraint
when Claude is documenting, auditing, testing, or improving
strategy logic.

### Accounts manager

The accounts manager owns account-specific behavior and metadata,
including:

- Account registry.
- Platform mapping.
- Strategy / account assignment.
- Risk manager assignment.
- Account-level configuration and restrictions.

### Risk manager

Each account should have a dedicated risk manager with the correct
rules for acceptance, rejection, and position sizing, and every
decision should be logged with a reason in the **Risk Manager
Decision Log**.

Current priority is hardening the existing live-trading risk path.
Prop-trading-specific infrastructure is **deferred** and should
not be built now.

---

## Data and logging architecture

The database and logging system should support operations,
debugging, performance review, model review, and operator
visibility.

### Required logs

#### Signals Log

Logs every signal produced by every strategy, including at
minimum:

- Strategy id.
- Symbol / instrument.
- Timeframe.
- Timestamp.
- Source data / feed.
- Relevant supporting context where available.

#### Order Package Log

Logs every normalized order package and each lifecycle update,
including:

- Strategy id.
- Account target.
- Exchange / instrument.
- Entry, stop loss, and take profit.
- Confidence score.
- Action type (created, updated, sent, rejected, closed).

#### Risk Manager Decision Log

Logs every risk-layer decision, including:

- Order Package ID.
- Account ID.
- Decision outcome.
- Reason code.
- Triggered rule.
- Position-sizing result where applicable.

#### Trade Log

Includes only trade records pulled from the broker / account API,
**not** internally inferred trades.

#### Messages Log

Includes all messages sent to the operator, with timestamps, bot
identity, message type, and other useful delivery context where
practical.

#### Sprint / Task Log

Tracks completed roadmap sprints, ad-hoc sessions, and recurring
auto-task sessions.

#### Bug Log

Maintains a running record of bugs, suspected duplicates, related
issues, fixes, and status changes.

#### Lessons Log

Tracks lessons learned from implementation, debugging, validation,
and operations that can later be turned into better workflows,
guardrails, or skill docs.

### Additional logs and registries to add

To support the autonomous workflow and AI roadmap, the system
should also include:

- A **comms log** for Claude / operator communication state
  transitions on the trader-bot S-027 ask/answer surface
  (already implemented at `comms/log.ndjson`).
- A **deployment / change log** for timer changes, service
  changes, and operator actions.
- A **strategy validation log** for dry-run milestones, promotion
  gates, and review outcomes.
- A **model registry / performance log** for current AI models,
  their role in the pipeline, their training history, and their
  observed performance.

---

## Telegram bots

There are two Telegram bots with separate, **deliberately distinct**
responsibilities. The architecture is **two surfaces, two purposes**;
do not collapse them.

### AI Trader Bot — `@bict_trading_bot`

The trader bot is the operator's primary interface for:

- All trade-execution alerts and operational notifications.
- Trade controls (killswitch, close-all, live/dry-run toggle).
- Status / log read surfaces.
- The S-027 **two-way structured ask/answer channel** for
  operator-question flows (e.g. "which strategy do you want to
  test?"). Inline-keyboard menus, free-text capture, git
  writeback.

#### Notifications

For now, notifications remain **broad and comprehensive**. The
system should continue sending notifications for:

- Every entry to every log in the database.
- Hourly snapshots.
- Errors returned by any system component.
- Trade and account events.
- Other operational signals currently exposed to the operator.

Notification reduction or filtering can be done later once there
is enough real usage data to decide what is noise and what is
useful.

#### Operator commands

The AI Trader Bot supports:

- Toggle account live / dry-run.
- Killswitch.
- Close all positions.
- `/new_session <sprint_id>` (writes a `comms/requests/REQ-…json`
  artifact for Claude to read on next sync).
- `/test <strategy_name>` (writes a structured test-request artifact;
  the M5 consumer runs the backtest in the same poll cycle and writes
  the result back via `apply_answer` — see § "Strategy test command"
  below for the closed-loop flow).

#### Information menus

The AI Trader Bot provides menus for:

- Operator commands.
- Trader snapshot.
- Signals Log.
- Order Package Log.
- Trade Log.
- System health.
- Hourly update.
- VM stats.

#### Two-way comms surface (S-027)

Repo-driven structured ask/answer:

1. Claude writes a `comms/requests/REQ-…json` artifact in the repo.
2. The trader bot delivers it as an inline-keyboard Telegram menu.
3. The operator answers (button tap or "Other" → free text).
4. The bot writes the answer back into the artifact and commits
   with the `comms(response):` prefix.
5. Claude reads the answered artifact on its next sync cycle.

This surface is for **operator-question flows** — not for merge
decisions, which happen on GitHub.

#### Stuck-request recovery

If a request stays in `sent` past its `stuck_alert_threshold` or
hits its TTL without an answer, the bot fires a Telegram alert
with the request id and age. Requests that hit `expires_at`
without an answer transition to `EXPIRED` only after the alert
fires — never silently.

### ClaudeBot — `@claude_ict_comms_bot`

ClaudeBot is the **deliberately one-way Claude → operator
notification channel**. Used for:

- Sprint-start pings.
- Sprint-completion updates.
- Checkpoint commits.
- Blocker pings (urgent).
- Training-stage notifications.
- Tier 2 merge-review *announcements* (informational nudge with a
  link to the PR; the merge decision itself happens on GitHub).
- A small set of housekeeping commands: `/audit`,
  `/improve_strategy`, `/train_model` (which log to
  `runtime_logs/recurring_sessions.jsonl` and reply with starter
  prompts the operator pastes into a fresh Claude session);
  `/roadmap`, `/schedules`, `/start`, `/reset`, `/model`.

#### One-way design — no response path

ClaudeBot has **no response path back to Claude**. Operator
decisions flow through:

- **GitHub** (PR comments, reviews, merges) for merge decisions,
  Tier 2 / Tier 3 approvals, and structured discussion.
- **A new Claude session** that reads repo state for context
  changes, re-prioritization, and design conversations.
- **The trader-bot S-027 ask/answer surface** for structured
  operator-question flows initiated by Claude.

This is intentional — adding bot-side response handling on
ClaudeBot would duplicate the S-027 surface and expand the live
process surface without adding value over the existing
GitHub + S-027 paths.

#### Session sizing rule

Each milestone is broken into session-sized sprints that fit one
working session, and each session-sized sprint is broken into
checkpoints that can be completed, validated, or cleanly paused
before the next session begins.

---

## Dashboard apps

The web app is now a **crucial priority** because it needs to
become a stable source of truth for understanding live system
state, recent activity, and operational health.

### Repo and hosting boundary (MANDATORY)

The dashboard web app **lives in a separate repository** from
`ict-trading-bot` and **runs on Vercel** — **not** on the Oracle VM.
Do **not** add web-app source code, build configs, or dashboard UI
files to `ict-trading-bot`. The trader repo stays lean and focused
on the trader / dispatcher / strategies / accounts / risk units.

The dashboard is a **pure consumer** of the trader's data:

- It reads a published data feed (JSON over HTTP, or
  websocket) emitted by the dispatcher in this repo.
- It does **not** import any code from `ict-trading-bot`.
- It does **not** read the trader's databases, journal files, or
  runtime logs directly.
- It does **not** hold operator credentials beyond what the auth
  contract exposes through the published feed.

The trader repo's responsibility is to **publish a clean feed**
(schema, auth, rate-limit) and document it. Everything from the
feed onwards lives in the dashboard repo.

When work is needed on the dashboard itself (UI, layouts, charts,
auth flows), open a session in the **dashboard repo**, not here.
When work is needed on what the dashboard consumes (new fields,
new endpoints, new event types), that's a sprint in **this repo**
that ends with the new feed shape documented for the dashboard
session to pick up.

### Vercel app

The Vercel web app provides a reliable operator dashboard for:

- Summary performance.
- System live / dry-run state.
- Error highlights.
- Account balances and pnl.
- Open positions.
- Recent trades.
- Key logs and health signals.

### Mobile dashboard widget

The mobile surface provides a compact status snapshot suitable
for quick checks and lightweight monitoring.

### Dashboard build order

Built in two phases:

1. **Read-only operations dashboard first** for visibility and
   monitoring.
2. **Interactive controls later** once permissions, auth, logging,
   and operational confidence are stronger.

This preserves dashboard usefulness without expanding
execution-path risk too early.

---

## Auto-task routine

Claude runs **one daily auto-task routine** driven by a repo
instruction file.

### Auto-task workflow

1. Claude reads the active auto-task instructions doc.
2. The doc determines what session type is active and what area
   is in focus.
3. Claude performs one bounded sprint with checkpoints.
4. Claude updates logs, docs, roadmap state, and handoff
   artifacts before ending the session.

### Auto-task categories

#### Audit / debug

Review targeted parts of the system to:

- Find bugs.
- Verify compliance with architecture rules.
- Simplify and declutter the repo.
- Improve observability and docs.

#### Strategy improvement

Strategy-improvement sessions are clearly defined and documented.
They focus on **one aspect of one strategy at a time**, such as:

- Signal logic.
- Entry logic.
- Exit logic.
- Risk behavior.
- Market structure logic.
- Timeframe use.
- Missed-opportunity analysis.
- Trade-review analysis against actual candles and market context.

These sessions combine two inputs:

- Review of what the strategy is already doing in the current
  system.
- External research and idea gathering where appropriate.

#### Training / model-improvement sessions

Training or model-improvement sessions are clearly described in
the docs. They explain:

- Which model currently exists.
- What role it plays today.
- What training data or labels are already available.
- What gap or improvement is being targeted.
- What output artifact should be produced from the session.

#### Janitor Mode

Recurring low-risk auto-task mode focused on:

- Dead file audits.
- Stale service audits.
- Duplicate module audits.
- Missing test audits.
- Documentation drift audits.
- Naming and structure cleanup.

Claude completes as much of this work autonomously as possible
and only escalates when behavior or rollout risk requires a human
decision.

---

## Improvement, training, and backtesting session definitions

To make strategy work repeatable, the roadmap explicitly describes
the main research session types rather than treating them as
generic future work.

### Improvement sessions

Improvement sessions focus on improving an existing strategy
using the current repo implementation as the starting point. Each
session defines:

- The strategy under review.
- The exact component under review (entries, exits, structure
  filter, stop-loss logic, etc.).
- Evidence from recent trades or logs.
- Evidence from chart review and timeframe context.
- The proposed hypothesis for improvement.
- The validation method required before any live-impacting change
  is approved.

### Training sessions

Training sessions focus on strengthening the AI components
already in the system or preparing new ones in a controlled way.
Each session defines:

- The current model or candidate model.
- Its current role in the pipeline.
- The target improvement.
- The required data sources.
- The evaluation metrics.
- The output artifact (notebook results, dataset updates,
  experiment logs, model registry updates).

### Backtesting sessions

Backtesting sessions are a first-class workflow. Each backtesting
session defines:

- Which strategy is being tested.
- What symbols and timeframes are in scope.
- The candle timeframe for entries and the higher timeframe for
  structure context.
- Which market conditions or date ranges are being tested.
- Which metrics must be recorded (win rate, average R, drawdown,
  sample size, expectancy, trade count).

Backtesting documentation also defines:

- How to run a backtest.
- Where the backtest code lives.
- Where outputs are stored.
- How results are summarized for the operator.
- What criteria are required before a result can justify further
  dry-run or live consideration.

---

## Repeatable operator-triggered workflows

To make the system operationally useful, repeatable workflows
should be **command-driven** rather than dependent on ad-hoc
manual coordination.

### New session command

The operator command `/new_session <sprint_id>` (on the trader
bot) writes a structured `comms/requests/REQ-…-new-session.json`
artifact so Claude can initialize a targeted sprint context on
its next sync.

### Strategy test command

The Telegram command `/test <strategy_name>` (on the trader bot)
writes a structured `comms/requests/REQ-…-ts<strategy>.json`
artifact and the M5 backtest consumer (shipped 2026-05-09) runs it
inside the same comms-poll cycle.

Closed-loop flow:

1. The operator sends the command. ``cmd_test_strategy`` validates
   the strategy name against ``config/strategies.yaml`` and rejects
   unknowns immediately with the registered roster.
2. The trader bot writes the structured request into
   ``comms/requests/`` and pushes via ``COMMS_PUSH_ENABLED``.
3. The next ``CommsPoller.poll_once`` tick runs
   ``BacktestConsumer.scan_and_run`` (gated by
   ``M5_CONSUMER_ENABLED``).
4. The consumer spawns
   ``python -m src.backtest.run_backtest_m5 <strategy>`` with a
   ``M5_BACKTEST_TIMEOUT_S`` wall clock (default 120s); the
   subprocess persists a row to ``backtest_results`` and prints a
   JSON envelope.
5. The consumer writes a formatted summary back via
   ``apply_answer`` (PENDING → SENT → ANSWERED) and appends one
   NDJSON row to ``runtime_logs/validation.jsonl``.

Operator runbook: [`docs/runbooks/strategy-testing.md`](../runbooks/strategy-testing.md).

### Merge review flow

Tier 2 work uses **two surfaces in parallel**:

- ClaudeBot fires a one-way *announcement* ping with a link to
  the PR (informational; gives the operator a Telegram nudge so
  the PR doesn't sit unseen).
- The actual Merge / Hold decision happens on **GitHub** (PR
  review + web-UI merge or `gh pr merge`).

There is no Telegram-callback merge button. Adding one would
expand the live surface unnecessarily and duplicate functionality
that GitHub already provides natively.

### Stuck request recovery

The trader-bot S-027 surface fires Telegram alerts before
silently expiring stuck requests. See § "Telegram bots /
@bict_trading_bot / Stuck-request recovery" above.

---

## AI roadmap

The AI roadmap explicitly answers the following questions:

- What models currently exist in the system?
- What type of models are they?
- What exact function does each model serve in the trade flow?
- What training data, prompts, or approval logic are currently
  used?
- How is training or evaluation history recorded?
- How is live or dry-run model performance measured over time?

### Model registry requirement

A canonical model registry tracks:

- Model name and version.
- Model type.
- Current status.
- Pipeline role.
- Input / output definition.
- Training history.
- Evaluation results.
- Deployment status.

This is the source of truth for the current AI layer in the
project.

---

## Milestone roadmap

The roadmap is organized into milestones; each milestone is
divided into session-sized sprints; each session-sized sprint is
divided into checkpoints with clear handoffs. The web app moves
earlier because it is a critical source-of-truth requirement.

| Milestone | Type        | Focus                          | Main outcome                                                                                  |
|-----------|-------------|--------------------------------|-----------------------------------------------------------------------------------------------|
| **M0**    | auto-claude | Workflow foundation            | Master protocol, session state files, logging conventions, handoff rules                      |
| **M1**    | auto-claude | Comms infrastructure           | Repo-based Claude / operator comms, Telegram writeback, dedupe, docs, tests                   |
| **M2**    | auto-claude | Web app source of truth        | Read-only dashboard backend and core status data surfaces                                     |
| **M3**    | auto-claude | Risk controls foundation       | Hard risk caps, kill switch, status controls, order-layer refusal tests                       |
| **M4**    | auto-claude | Repo hygiene + CI              | Janitor cleanup, canonical paths, GitHub Actions, test / lint automation                      |
| **M5**    | auto-claude | Strategy testing workflow      | Telegram-triggered test flow, validation logging, backtest workflow docs                      |
| **M6**    | auto-claude | Web app UI                     | Dashboard UI for pnl, status, open positions, logs, and recent actions                        |
| **M7**    | pm-sprint   | Strategy review gate           | Review validation results and decide promote, hold, or kill                                   |
| **M8**    | pm-sprint   | Strategy tuning                | Parameter review and approval-required strategy changes                                       |
| **M9**    | auto-claude | AI / model roadmap             | Model registry, current-model audit, training and performance tracking                        |
| **M10**   | auto-claude | HF / data pipeline             | Dataset publishing, artifact packaging, reproducible research workflow                        |

This sequence prioritizes communications, visibility, and safety
before deeper strategy expansion or new AI complexity.

---

## Practical rules for Claude

### Claude must always

- Read the current roadmap, milestone state, active blockers, and
  relevant docs at the start of every session.
- Produce or update a milestone / session plan before coding.
- Keep PRs small and reviewable where practical.
- Prefer file-based repo-tracked state over hidden assumptions.
- Use tests, dry-run, staging evidence, and validation artifacts
  before claiming safety.
- End every session with docs, logs, and next-step handoff
  updates.

### Claude must not

- Change strategy behavior silently.
- Promote a strategy to live without approval.
- Add secrets to the repo.
- Make the operator do technical VM work without a simple
  notebook or one-click workflow.
- Assume canonical file paths or deployment details without
  inspecting the repo first.

### Claude should prefer

- Free compute for research, backtests, and data processing where
  possible.
- Repo-driven communications over informal coordination.
- Reversible changes, clear audit trails, and explicit rollout
  logic.

---

## Key updates in this version (2026-05-06, with 2026-05-08 correction)

- Prop-trading infrastructure is **deferred** until later and is
  not part of the current build plan.
- The web app is **elevated** to a core near-term requirement
  because it must become the operator's stable source of truth.
- All notifications **remain enabled** for now and can be reduced
  later after observing real usage.
- A formal **model registry** is now part of the AI roadmap.
- Improvement sessions, training sessions, and backtesting
  sessions are described as clear repeatable workflows.
- The current strategy timeframe rule is **5-minute candles for
  execution with 1-hour market structure context**.
- The term **milestone** replaces the old sprint-level label,
  while actual execution is broken down into session-sized
  sprints and checkpoints.
- **2026-05-08:** § "Telegram bots / @claude_ict_comms_bot /
  ClaudeBot workflow" rewritten — ClaudeBot is intentionally
  one-way; the two-way ask/answer surface lives on the trader
  bot via S-027; merge decisions happen on GitHub.
