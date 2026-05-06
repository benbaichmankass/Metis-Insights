# CLAUDE.md

Lean router for Claude Code sessions in the ICT Trading Bot repo.

## CANONICAL WORKPLAN — SINGLE SOURCE OF TRUTH (read first, every session)

**Read `docs/claude/workplan.md` before anything else** — including the
rest of this file. The workplan is the **single source of truth** for
what the project is building, the merge tier model, the bot
responsibilities, the strategy timeframe rule, the milestone roadmap
(M0..M10), the auto-task routine, and every other architectural
constraint.

**The workplan overrides everything that contradicts it.** If anything
in this CLAUDE.md, `ROADMAP.md`, `docs/claude/milestone-state.md`, the
checkpoint log, sprint prompts, code comments, or any other tracked
file disagrees with the workplan, the **workplan wins** — silently
update the contradicting content (or remove it) as you encounter it.
Do not preserve outdated guidance because it was canonical at some
prior checkpoint.

**Verify before trusting "done".** When a milestone, sprint, task, or
file is marked complete in any doc / checkpoint / sprint summary, do
not accept the "done" status at face value. Verify the on-disk state
matches what the workplan requires. If the on-disk state has drifted
from the workplan (or never matched it), fix the drift in this
session before continuing other work — even if some prior session
claimed it was finished.

**Declutter authorization.** Anything not in the workplan is out of
scope and may be removed without ceremony — no migration sprint
required. The workplan is exhaustive; if a doc, sprint, or checkpoint
references something the workplan doesn't list, treat it as cruft
unless the operator says otherwise.

**Sprints continue.** Sprints (per `docs/claude/sprint-planning.md`)
remain the unit of work. Sprints execute *against* the workplan; the
workplan defines *what* to execute and *the rules* for execution.
Sprint and checkpoint numbering is **monotonic and unique across the
entire repo lifetime** — see `docs/claude/workplan.md` § "Sprint and
checkpoint numbering" for the rule and the current snapshot (next
sprint = **S-041**, highest used = S-035 with S-036..S-040 burned).

## First rule (after reading the workplan)

Do not load every project document by default. Start with this file, identify the task type, then read only the focused docs listed below.

## Resume rule (read before anything else, after the workplan)

1. Read `docs/claude/checkpoints/CHECKPOINT_LOG.md` — the **most recent entry**
   tells you exactly where to resume. **Verify on-disk state against the
   workplan before accepting the "next checkpoint" pointer at face
   value** — if the prior session's plan conflicts with the workplan,
   the workplan wins and the next-checkpoint pointer changes.
2. Read `docs/claude/checkpoint-workflow.md` for the rules.
3. Only then read the task-specific docs below.

Do **not** start from the top of the sprint plan. Always resume from the
latest checkpoint. Work **one task per session**, keep changes **PR-sized**,
stop and hand off if usage/context/timeout limits are near, and continue the
sprint even if a previous PR has not been merged yet.

At the **end of every session**, append an entry to the checkpoint log using
`docs/claude/checkpoints/HANDOFF_TEMPLATE.md`. The entry must contain:
1. Completed   2. Files changed   3. Tests run   4. Remaining   5. Next checkpoint.
The Telegram ping fires automatically off the checkpoint commit (VM-side
wiring per `docs/claude/telegram-pings.md`); the manual
`scripts/notify_session.py` is only a fallback. If the session is
blocked and needs operator input, commit `[BLOCKED-PM] <question>` and
open a draft PR titled `BLOCKED: <question>` — see § Telegram Reporting.

## Task routing

| Task | Read |
|---|---|
| Any session | `docs/claude/checkpoints/CHECKPOINT_LOG.md`, `docs/claude/checkpoint-workflow.md`, `docs/claude/INDEX.md` |
| End-of-session handoff | `docs/claude/checkpoint-workflow.md`, `docs/claude/checkpoints/HANDOFF_TEMPLATE.md` |
| Sprint planning | `docs/claude/sprint-planning.md` **(binding template — every sprint prompt must follow it)** |
| Bug fix / regression | `docs/claude/session-workflow.md`, `docs/claude/debug-memory.md`, `docs/claude/testing-policy.md`, `docs/claude/bug-log.md` (append after each fix) |
| Repo cleanup | `docs/claude/cleanup-policy.md`, `docs/claude/cleanup-report.md` |
| ML model work | `docs/claude/ml-training-policy.md`, `docs/claude/external-delegation.md` |
| Training / improvement session (strategy or model) | `docs/claude/training-improvement-workflow.md`, `docs/claude/ml-training-policy.md`, `docs/claude/colab-workflows.md` |
| Colab work | `docs/claude/colab-workflows.md` |
| Hugging Face work | `docs/claude/huggingface-workflows.md` |
| Deployment / Oracle VM | `docs/claude/deployment-ops.md`, `docs/claude/security-secrets.md` |
| Manual VM operator step (set env, restart svc, flip flag, rotate key) | **`docs/claude/colab-workflows.md` § "Operator VM steps"** — deliver a one-click `notebooks/operator/*.ipynb`, NOT a markdown CLI checklist |
| Running ON the VM (Telegram-dispatched runner) | `docs/claude/vm-operator-mode.md` **(binding tier policy)** |
| Git / PR / push | `docs/claude/git-workflow.md`, `docs/claude/security-secrets.md` |
| Telegram ping wiring | `docs/claude/telegram-pings.md` |
| Operator weigh-in needed (ping-PR pattern) | `docs/claude/telegram-pings.md` § "Ping-PR vs work-PR" + § "Live-mode invariant" in this file |
| Architecture lookup | `docs/claude/repo-map.md` |
| Prop account state / Velotrade integration | `docs/claude/prop-account-state.md` |

## VM-resident sessions (read first if `/etc/claude/vm-marker` exists)

If this session runs on the Oracle VM (the marker file is present), the
**tier policy in `docs/claude/vm-operator-mode.md` is binding** and overrides
any conflicting prompt below. Tier 3 actions are refused even with
operator approval. The runner reaches you via Telegram (`/vm`, `/vm_write`)
— do not try to escalate beyond your tier; reply with `ASK_OPERATOR:` and
let them re-issue the command.

## Telegram bots (two separate processes — do not conflate)

There are **two** Telegram bots running on the VM, each with its own token,
service unit, and source file:

| Bot username | Service | Source | Token env | Purpose |
|---|---|---|---|---|
| `@bict_trading_bot` | `ict-telegram-bot.service` | `src/bot/telegram_query_bot.py` | `TELEGRAM_BOT_TOKEN` | Trading UI: `/status /halt /signals /accounts …` plus the comms-handler that delivers Claude→operator pings |
| `@claude_ict_comms_bot` | `ict-claude-bridge.service` | `src/bot/claude_bridge.py` | `TELEGRAM_CLAUDE_BOT_TOKEN` | Claude bridge: free-text relay to the Anthropic API plus the recurring-session triggers (`/audit /improve_strategy /train_model /roadmap`) |

**When adding a new command, decide which bot it belongs to first.** Trading
ops → `telegram_query_bot.py`. Anything that triggers or queries a Claude
session → `claude_bridge.py`. Both bots have their own `set_my_commands()`
populating their `/` menu — the menu only updates after a service restart.

## Telegram test group (use for bot verification)

The operator has added both bots to a shared Telegram group. When a session
needs to verify that a new command is live and working correctly, send the
command in that group and inspect the reply — this gives the same view the
operator sees.

**Group invite:** https://t.me/+AEyPWWlkcKFhMDQ0

Rules:
- Use the group for **smoke-testing bot commands** after a deploy (e.g. `/roadmap`,
  `/audit`, `/status`), not for issuing live trading commands.
- Read the bot's reply directly in the group to confirm formatting, auth guard,
  and content — this replaces asking the operator "did the reply look right?".
- If the bot does not respond within ~10 minutes of a VM git-sync cycle, the
  systemd unit may need a restart — produce a one-click Colab notebook per the
  rule below rather than instructing CLI steps.

## Always do

- Keep changes small and reversible.
- Prefer scripts/notebooks that let Colab, Hugging Face, or the VM do heavy work.
- **For ANY manual VM operator step, deliver a one-click Colab notebook
  under `notebooks/operator/`, never a copy-paste CLI checklist.** Follow
  the structure in `notebooks/operator/rotate_api_keys.ipynb`. Full rules
  in `docs/claude/colab-workflows.md` § "Operator VM steps".
- Do not run training, full backtests, live trading, or deployment unless explicitly asked.
- Do not print secrets.
- Update the relevant `docs/claude/*.md` file after discovering a recurring bug, cleanup rule, or workflow improvement.
- For tests and notebooks, never pull market data from Binance or other
  key-gated exchanges. Use hand-crafted DataFrames, repo fixtures, or open
  keyless sources (Bybit public, Coinbase public, Kraken public,
  CryptoCompare, yfinance, or our HF datasets). See
  `docs/claude/testing-policy.md` → “Test data sources”.
- **Do not use `parse_mode="Markdown"` on Telegram replies whose content
  is dynamic** (DB columns, error strings, env-var names, file paths,
  Python identifiers — anything that may contain `*`, `_`, `[`, or
  backticks). Telegram's legacy Markdown parser rejects unbalanced
  delimiters with `BadRequest: Can't parse entities`. This bug shape
  has recurred in BUG-009 (#190 /signals), BUG-030 (#265 /last5), and
  BUG-031 (#273 /hourly). Use **plain text** (no `parse_mode`) by
  default; reach for `parse_mode="HTML"` only when the renderer
  explicitly escapes `&`, `<`, `>` (the pattern used by
  `/accounts_status`).

## Autonomous live-trading rule (MANDATORY — do not relitigate)

The trader is **designed to be autonomous**. Per-trade operator
confirmation is **not** part of the architecture and **must not** be
inserted into sprint plans, smoke tests, runbooks, or any checkpoint
table. The safety rails are:

1. **Per-account `mode: live | dry_run`** in `config/accounts.yaml`
   — applied via `RiskManager.dry_run`. **This is the SINGLE dry/live
   toggle in the codebase** (operator directive 2026-05-03). When
   `dry_run=True`, `RiskManager.evaluate()` returns
   `reason="account_mode_dry_run"`, the executor logs the would-be
   trade to the trade journal, and the exchange is not called. There
   is **no** process-level interlock. There is **no** strategy-level
   toggle. There is **no** profile-level / env-variable toggle.
   `DRY_RUN` and `ALLOW_LIVE_TRADING` env vars were removed; `MODE`
   is no longer required.
2. `RiskManager` per account — sizing, daily loss caps, max-drawdown,
   dry/live mode (above).
3. `safe_place_order` — payload + halt-flag + risk-cap rail. **Not** a
   mode gate.
4. The kill-switch flag (`/halt`) — operator can stop everything in
   one tap; default-running otherwise.

When proposing or running anything that touches live trading,
*assume autonomous execution*. Do not gate trades on `--confirm`
flags requiring human input, do not pause sprints "for the operator
to greenlight each LIVE order", do not insert "operator confirms
before placement" into checkpoint tables. The per-account mode + the
risk caps + the kill-switch are the policy. The operator pre-approves
the **system**, not each **trade**.

This applies to smoke tests too: a smoke trade fires the moment the
risk manager and `safe_place_order` accept it, no human in the loop.
If `qty` is over a hard safety cap (e.g. `0.001` BTC for plumbing
smokes), refuse to dispatch — but for any value below the cap, the
trader's autonomous rails are the policy.

If a future session is tempted to add operator confirmation per
trade, it's wrong. Tell the user it's wrong and link to this section.

If a future session is tempted to add a process-level dry/live
interlock, a strategy-level mode flag, or a profile-suffixed env
file, it's also wrong — the per-account `mode` field is the only
toggle. See BUG-039 (2026-05-03) for the full rationale.

## Telegram Reporting (MANDATORY)

The full spec lives in `docs/claude/telegram-pings.md`. The short version:

- **Every** commit that touches `docs/claude/checkpoints/CHECKPOINT_LOG.md`
  triggers a Telegram ping (VM-side wiring; ≤ 5 min latency).
- **Blocker pings** — when an autonomous session needs operator input,
  commit with `[BLOCKED-PM] <question>` in the subject **and** open a
  draft PR titled `BLOCKED: <question>` with the chat link in the body.
  This double-routes through Telegram + GitHub notifications.
- Sprint completion → final checkpoint with `COMPLETE` / `WRAPPED` in
  the title triggers a high-priority sprint-end ping.
- If the sandbox can't reach Telegram, append the ping payload to
  `docs/claude/pending-pings.jsonl`; the VM's git-sync drains it on
  next pull.
- Manual fallback (rarely needed): `PYTHONPATH=. python
  scripts/notify_session.py session …`.
- **Pings ride on PRs and commits** — that is the channel. ≤ 5 min
  delivery via the existing VM wiring is acceptable for everything
  except blockers (which double-route via the GitHub draft-PR
  notification). Do not add a synchronous notification dependency to
  any sprint workflow; commit-then-ping is the only contract.
- **Mid-session operator input** — when an autonomous session needs
  steering, the `[BLOCKED-PM]` commit + `BLOCKED:` draft PR is the
  mechanism. The PR body MUST include the **chat link** so the
  operator can click through and answer in the same session that's
  waiting. Then stop until they reply.
- **Ping-PR vs work-PR separation (MANDATORY).** A draft work-PR that
  is waiting on operator input does **not** by itself fire a Telegram
  ping — pings ride on **merged commits**. So when you need the
  operator to weigh in, you must:
  1. Open / keep the work PR as **draft** (`BLOCKED: <q>` or
     `(PM REVIEW): <q>`). This is what the operator clicks to
     review/approve. **Do not merge it yourself.**
  2. Open a **separate, tiny ping-PR** (≤ 5 lines) on its own branch
     `claude/ping-<slug>` whose only payload is an entry appended to
     `docs/claude/pending-pings.jsonl` (or a checkpoint-log
     append) with the question + link to the draft work-PR.
  3. **Self-merge the ping-PR** immediately. That merge is what
     fires Telegram. The work-PR stays draft.
  4. Stop. Do not start unrelated work until the operator replies.
  Never conflate the two: merging the work-PR to "fire the ping"
  means you've also approved your own pending change. The ping-PR
  is the channel, the work-PR is the content; they ride on
  different commits.
- **Training / improvement sessions** use four additional title
  prefixes (`[TRAINING-START]`, `TRAINING-PLAN:`, `TRAINING-RESULTS:`,
  `RECOMMENDATIONS (PM REVIEW):`) that all ride on the existing
  ping wiring — see `docs/claude/training-improvement-workflow.md`.

## Live-mode invariant (MANDATORY — every PR)

Before opening (and again before merging) any PR, run a positive check
that the system stays in live mode for live accounts:

1. Diff against `main` and search for any **added** line that flips a
   trading-mode flag away from live. The existing CI guard
   (`scripts/check_dry_run_in_diff.py` / `.github/workflows/dry-run-guard.yml`)
   covers the obvious patterns; if it fails, **do not bypass** —
   instead open a ping-PR (per the rule above) explaining the change
   and stop until the operator approves.
2. Check `config/accounts.yaml` and any `.env*` template files that
   the PR touches: every account whose `enabled` is unset / `true`
   must still have `mode: live` (or no `mode` field — default is
   live per the autonomous-live-trading rule). Any account that
   would be left in `dry_run` / `paper` after the PR merges must be
   explicitly approved by the operator via a ping-PR.
3. If the PR touches `src/runtime/orders.py`, `src/runtime/pipeline.py`,
   `src/runtime/trading_mode.py`, `src/units/accounts/*`, or anything
   that controls the live/dry routing, **always ping the operator**
   regardless of test outcome — this is a per-PR rule on top of the
   PM-review gate.
4. Record the result of (1)–(3) in the PR body under a `## Live-mode
   check` heading: green ✅ = no change to mode, ⚠️ = a flip was
   intentionally proposed (with the ping-PR link).

This rule is here because per-tick `failed_validation` messages have
recurred twice in two sprints, each time because a PR landed a code
path that bypassed the per-account live/dry contract without an
explicit operator confirmation.

## Architecture rules (MANDATORY — every PR)

These are the canonical unit-boundary contracts for the codebase. Every
PR must respect them; every sprint prompt must declare which units it
touches. The 2026-05-02 architecture audit
(`docs/claude/architecture-audit-2026-05-02.md`) enumerates the current
violations and the sprint sequence that resolves them — read it before
proposing any structural change.

1. **Unit separation.** Each unit owns a single responsibility and lives
   under its own folder. Cross-unit calls go through
   `src/core/coordinator.py` (the translator). Direct imports between
   units are boundary leaks. The canonical units are:
   - `src/units/strategies/` — signal + order-package generation.
   - `src/units/accounts/` — per-account risk + execution.
   - `src/units/dashboards/` — aggregated views.
   - `src/units/trading_school/` — strategy validation + retraining.
   - `src/data_layer/` — DB unit (signals log + order packages log +
     trade log; will move to `src/units/db/` per S-035).
   - `src/ui/` — UI helpers (read-only data formatters; will move to
     `src/units/ui/` per S-035).
   - `src/runtime/` — orchestration (pipeline, validation, mode flags).
   - `src/bot/` — Telegram-rendering shell (no business logic).

2. **Strategy unit responsibilities.** Strategies *generate*, *log*, and
   *monitor* — they MUST NOT execute. A strategy module:
   - Returns an `OrderPackage` (entry/sl/tp/symbol/direction/strategy/meta).
   - Logs the signal to the **signals log** in the DB unit.
   - Logs the order package to the **order packages log** in the DB unit.
     The package log row tracks: order id, originating strategy,
     entry/sl/tp, signal logic + confidence, timestamps for each
     step/update, status (open|closed).
   - Monitors open packages — while a trade is open, the strategy
     re-enters with fresh candles to update the package; updates are
     written back to the order packages log.
   - Forbidden: calling `execute_pkg`, `route_order`, `safe_place_order`,
     `account.place_order`, or any exchange SDK.

3. **Account / risk / execute boundary.** Each account in
   `config/accounts.yaml` is configured to follow specific strategies
   via `strategies: [...]`. Dispatch logic **must** filter by this list
   — a strategy's package only routes to accounts whose `strategies`
   includes its name. For each account that matches:
   1. The per-account `RiskManager` runs `approve(pkg)` +
      `position_size(pkg, balance)`.
   2. If approved + sized, `execute_pkg` submits the order via the
      account's exchange client (`bybit_client_for` / `binance_conn_for`).
   3. The account writes to the **trade log** in the DB unit
      (`Database.insert_trade`) on submission.
   4. While the trade is open and the order package gets updates from
      the strategy's monitor, the account re-runs `approve` and either
      modifies the live order, closes it, or leaves it.
   5. On close, the trade row is updated (status, exit_reason, PnL).
   `execute_pkg` is the **single canonical live-order entry point** —
   no other code path touches the exchange SDK for placement.

4. **UI unit mirrors the DB structure.** UI helpers query the DB unit's
   three logs and group as the operator expects:
   - **Trade log helpers** — group/filter **by account**
     (e.g. `processor.get_trades_by_account(account_name)`).
   - **Order package log helpers** — group/filter **by strategy**
     (e.g. `processor.get_packages_by_strategy(strategy_name)`).
   - **Signals log helpers** — same shape as packages, by strategy.
   Inverted groupings (trades-by-strategy, packages-by-account) are
   architecturally incorrect — file a sprint to remove them.

5. **Telegram bot is a thin shell over the UI unit.** The bot's
   `BOT_COMMAND_SPECS` only attaches existing UI helpers to commands
   and renders their output. The bot must NOT:
   - Open `trade_journal.db` or read `signal_audit.jsonl` directly.
   - Call `data_loaders` / `coord` for business logic (`coord` is for
     ad-hoc admin commands like `/halt`; everything that produces a
     view goes through `src/ui/`).
   - Make raw HTTP calls to exchanges.
   - Implement filtering, aggregation, or formatting that belongs in
     the UI unit.
   New commands add a UI helper first, then a one-line handler that
   calls it.

6. **Live by default + tell-me-if-not.** The system defaults to live
   trading — see § *Autonomous live-trading rule* above. The
   `liveness_watchdog` (S-029 PR3) pings the operator within 1 hour
   when actionable signals fire but no trades land. Silent failure is
   forbidden — every refusal path on the order route emits a
   diagnostic ping via `runtime_logs/pending_pings/`
   (`src/runtime/execution_diagnostics.py`).

### Enforcement

- **Sprint prompts** must declare which units they touch in the *Unit
  boundary declaration* section (`docs/claude/sprint-planning.md`).
- **PR descriptions** must list the unit(s) touched and confirm no new
  cross-unit imports outside `src/core/coordinator.py`.
- **The Live-mode invariant check** (above) already pings the operator
  when `src/runtime/orders.py`, `src/runtime/pipeline.py`,
  `src/runtime/trading_mode.py`, or `src/units/accounts/*` is touched.

If a fix requires temporarily breaking a rule, file a ping-PR explaining
why and stop until the operator approves.

## Bug log (MANDATORY)

Whenever a bug is identified and fixed, append a row to
`docs/claude/bug-log.md`. The row must include: date, sprint, area,
symptom, root cause, fix-PR, architectural-concern category. The log is
reviewed at the start of every planning sprint to spot recurring trouble
spots and decide where deeper architectural investment is worth it.

## Merging Rules (MANDATORY)

- For ALL sprint PRs: Create PR → run tests/lint → if green → **SELF-MERGE immediately via GitHub MCP tools**.
- No waiting for manual approval.
- ONLY flag for PM review (do not self-merge):
  1. New secrets/API key handling.
  2. Changes to live trading logic (`src/runtime/orders.py`).
  3. VM deployment scripts (`deploy/`).
- After self-merge, post `/sprintlet_status PR#X merged` to Telegram bot.

## Sprint Completion Checklist (ALWAYS LAST TASK)

1. Run full tests: `PYTHONPATH=. pytest tests/ -q --ignore=tests/test_main_loop.py`
2. Run `python scripts/secret_scan.py` — must be clean.
3. Create summary PR: `docs/sprint-summaries/sprint-NNN-summary.md` containing:
   - PR list (#XXX–#YYY)
   - Tests added
   - Checkpoint ID from `CHECKPOINT_LOG.md`
   - Deliverables table (file/unit → tests)
   - Deferred items (if any)
   - Lessons learned (1–3 bullets for future sprints)
4. **Self-merge summary PR** (docs-only, no code risk).
5. Propose 1–2 improvements to this `CLAUDE.md` for the next sprint.
6. Telegram: `/sprintlet_complete S-NNN`
7. Append final checkpoint to `CHECKPOINT_LOG.md`.

## Default verification

Run lightweight checks only:

```bash
python scripts/repo_inventory.py
python scripts/secret_scan.py
PYTHONPATH=. pytest --collect-only -q tests
```

If tests need optional dependencies, explain the missing dependency and do not install broad packages without approval.
