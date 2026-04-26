# Claude Project Setup Guide for `ict-trading-bot`

This document is meant to be used in two ways:

1. As the **project context / instructions** for the Claude project space.
2. As the **setup guide** for connecting Claude Code to the GitHub repo and using it to automate development tasks.

The goal is to make Claude useful as a development copilot for a multi-strategy trading platform rather than just a general chat assistant. Claude Code is designed to read a codebase, edit files, run commands, and integrate with development workflows, and Anthropic’s web setup docs describe repository connection through the Claude GitHub App and cloud environments for repo-based sessions.[cite:256][cite:257][cite:268]

## Project summary

Repository: `the-lizardking/ict-trading-bot`.

This project began as an ICT trading bot and is now being expanded into a **multi-strategy trading platform**. The current evolution path includes:

- Existing ICT strategy support.
- New VWAP mean reversion strategy for MES futures on NinjaTrader.
- Shared backtesting and research workflows.
- Shared runtime and deployment workflows.
- A Telegram bot used as the operational control surface.
- Future support for additional strategies beyond ICT and VWAP.[cite:168][cite:205]

Development workflow so far has used Google Colab for development, GitHub as the source of truth, and Oracle Cloud for deployment/runtime. The repo has already been extended with deployment scripts, runtime log structure, and bot-related service planning.[cite:169][cite:170][cite:171]

## What Claude should understand about this project

Claude should treat this repository as a **strategy platform**, not a single bot. The most important architectural direction is to separate:

1. Strategy logic.
2. AI/model logic.
3. Execution adapters.
4. Runtime orchestration.
5. Bot/user interface logic.

All major changes should move the repo toward a registry-driven, multi-strategy design rather than increasing hardcoded ICT-specific branching. This is consistent with scalable automated-trading architecture guidance and with the project’s current roadmap.[cite:230][cite:233][cite:236]

## Current strategic goals

Near-term goals:

- Add a full VWAP mean reversion strategy path.
- Build a strategy-specific AI model for VWAP as a scoring/filter layer.
- Refactor the repo into a true multi-strategy platform.
- Integrate VWAP execution with NinjaTrader.
- Extend the live trader so multiple strategies can run safely.
- Update the existing Telegram bot to become strategy-aware.

Bot design goal:

The Telegram bot should remain a single bot, but its control flow should become **strategy-aware**. Strategy selection and environment selection should be separate dimensions. The desired interaction model is:

1. Choose strategy: ICT, VWAP, future strategies.
2. Choose environment where relevant: paper or live.
3. Choose action: start, stop, status, logs, positions, backtests, model status, reports.

This is preferred over replacing `paper/live` with `ICT/VWAP`, because strategy identity and runtime mode are different concerns operationally.[cite:170][cite:171]

## Recommended architectural direction

Claude should steer changes toward the following structure:

- `src/strategies/ict/`
- `src/strategies/vwap/`
- `src/models/ict/`
- `src/models/vwap/`
- `src/execution/`
- `src/runtime/`
- `src/bot/`
- `configs/strategies/`
- `docs/strategies/`
- `reports/backtests/`

Core principles:

- Shared infrastructure, separate alpha models.
- Rules-based strategies should be testable without AI layers.
- New strategies should be registered via config/registry, not hand-wired into many files.
- Strategy outputs should follow a common schema.
- Logging and reporting should be strategy-aware and reusable.

## Claude’s job on this repo

Claude should be used primarily for **developer acceleration**, especially on repetitive engineering work such as:

- repo refactors,
- creating folder/module scaffolding,
- generating tests,
- writing and updating docs,
- adding config schemas,
- wiring strategy registration,
- updating deployment scripts,
- helping redesign the Telegram bot menu flow,
- creating implementation plans before code.

Claude should not be used as an unquestioned authority on trading logic or execution assumptions. For strategy logic, it should propose, explain, and implement carefully, but changes should remain auditable and backtestable.

## High-priority work streams Claude should help with

### 1. Multi-strategy architecture

Claude should help convert the repo from ICT-specific structure to a strategy-platform structure. That includes:

- strategy registry,
- common interfaces,
- shared config patterns,
- strategy-aware reporting,
- strategy-aware runtime launch flow.

### 2. VWAP strategy implementation

Claude should use the project’s VWAP strategy document as the source of truth and implement that spec gradually. The first goal is a rules-based baseline before adding the model layer.[cite:205]

### 3. AI/model workflow

Claude should help design and implement a **separate VWAP model**, not prematurely merge BTC/ICT/VWAP into one shared alpha model. The preferred structure is shared pipeline plus strategy-specific models, with optional shared regime models later.[cite:206][cite:211][cite:217]

### 4. Telegram bot redesign

Claude should help refactor the existing bot into a registry-driven bot where strategy IDs are first-class inputs throughout the command system. This refactor should make adding future strategies easier.

### 5. Deployment and operations

Claude should help preserve the Colab → GitHub → Oracle deployment workflow while making it more robust and strategy-aware. Existing deployment conventions and service structure should be respected where possible.[cite:169][cite:171]

## Constraints and working preferences

These are project-specific operating assumptions Claude should follow:

- GitHub is the source of truth.
- Colab is still a valid development environment and often the most convenient one for the user.
- Oracle Cloud is the deployment/runtime environment.
- Credentials should never be hardcoded into notebooks or committed to the repo.[cite:172][cite:254]
- Docs and planning artifacts should be committed to the repo when useful.
- Prefer small, reviewable changes over giant sweeping rewrites.
- For large changes, Claude should start with a plan and file impact summary before editing code.
- For risky refactors, Claude should identify affected modules, propose a migration sequence, and preserve backward compatibility when practical.

## Coding and collaboration rules for Claude

Use this section directly in the Claude project instructions if desired.

### Project rules

- Always treat this as a production-oriented trading system, not a toy app.
- Prefer clarity, testability, and traceability over cleverness.
- Do not introduce hidden coupling between strategies.
- Keep strategy logic, model logic, execution, runtime, and bot logic separated.
- When implementing a new feature, state which layer it belongs to.
- Before large changes, inspect the repo and summarize the existing structure first.
- Before coding, propose a short plan and list the files likely to change.
- After coding, summarize what changed, what still needs validation, and what should be tested manually.
- Do not hardcode secrets or environment-specific credentials.
- Prefer config files and registry patterns over hardcoded branching.
- If modifying commands or user flows, keep future strategies in mind.
- For backtesting and model code, be explicit about assumptions, leakage risks, and execution realism.
- For strategy logic, preserve a rules-only mode even when an AI filter exists.

### Output style rules

- For implementation tasks, start with a brief plan.
- For repo refactors, include a file-by-file impact summary.
- For strategy tasks, distinguish between spec assumptions and implemented behavior.
- For bot tasks, include example user flows.
- For model tasks, list labels, features, splits, and leakage risks explicitly.
- For deployment tasks, include rollback and safety considerations.

## Best way to structure Claude project memory/context

Claude Code best-practice guidance strongly emphasizes explicit context management, especially via repo instructions and lightweight project-level guidance files, because context quality is a major determinant of output quality.[cite:260]

Recommended context layers:

### Layer 1 — Project instructions

Use a concise project description plus the rules section above in the Claude project space.

### Layer 2 — Repo docs Claude should be pointed to

Create or maintain these files in the repo:

- `README.md` — high-level repo overview.
- `docs/architecture.md` — platform architecture.
- `docs/strategies/ict.md` — ICT strategy notes.
- `docs/strategies/vwap_mean_reversion.md` — VWAP strategy spec.
- `docs/work-plans/vwap_strategy_work_plan.md` — the current work plan.
- `docs/deployment.md` — Colab/GitHub/Oracle workflow.
- `docs/bot.md` — Telegram bot command model.

### Layer 3 — Optional root instruction file

Add a root `CLAUDE.md` file in the repo with:

- critical project rules,
- architecture summary,
- testing commands,
- where the important docs live,
- coding expectations.

This pattern is widely recommended in community Claude Code best-practice material because it keeps core rules persistent and discoverable during repo work.[cite:260][cite:263]

## How to connect Claude to the repo

Anthropic’s Claude Code web quickstart says the setup is repository-based via the Claude GitHub App, and the app needs explicit access to each GitHub repository you want Claude to use.[cite:257] Anthropic’s GitHub integration help also describes connecting repositories directly so Claude can use them as development context.[cite:268]

### Option A — Claude Code on the web with GitHub

This is probably the cleanest path for you.

1. Go to `claude.ai/code` and sign in.[cite:257]
2. Connect GitHub when prompted.[cite:257]
3. Install the **Claude GitHub App** and grant access to your repository or your GitHub account’s selected repositories.[cite:257][cite:255]
4. Ensure `the-lizardking/ict-trading-bot` is included in the repo access list.[cite:257]
5. Create a new Claude Code web session/environment from that repository.[cite:257]
6. Start by asking Claude to inspect the repo and summarize the architecture before making edits.

### Option B — Claude Code terminal/CLI linked to GitHub

Anthropic’s docs indicate you can also connect from terminal if you use the Claude Code CLI and GitHub CLI, including web setup via `/web-setup` that links local GitHub auth to Claude Code’s web environment.[cite:257]

A practical flow is:

1. Clone the repo locally or on a development machine.
2. Install the Claude Code CLI.
3. Authenticate Claude Code.
4. Run the setup flow that links GitHub access.
5. Open the repo folder and start sessions from there.

This option is good if you want Claude working more like a terminal coding agent rather than a web-only repo editor.

## How to let Claude automate tasks safely

There are two main patterns.

### Pattern 1 — Interactive repo sessions

Use Claude interactively for tasks like:

- “Inspect the repo and propose a multi-strategy refactor plan.”
- “Create the strategy registry skeleton.”
- “Refactor the Telegram bot command model to accept `strategy_id` and `mode`.”
- “Scaffold the VWAP strategy package with tests and docs.”

This is best for architecture-sensitive work where you want review between steps.

### Pattern 2 — GitHub-based automation

There are documented ways to use Claude through GitHub automation, including GitHub App setups and repository secrets such as `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` for workflow-based usage.[cite:255]

This is useful for automating tasks like:

- implementation from well-scoped GitHub issues,
- code review assistance,
- doc generation,
- repetitive repo maintenance.

If you eventually want issue-driven automation, follow a GitHub workflow approach only after the repo’s instructions and guardrails are in place.[cite:255]

## Suggested first setup tasks in Claude

Once the repo is connected, do these in order:

1. **Repo audit**
   - Ask Claude to inspect the repo and produce a current architecture summary.
2. **Context file creation**
   - Ask Claude to create `CLAUDE.md` plus missing docs under `docs/`.
3. **Strategy registry plan**
   - Ask Claude to identify where ICT-specific assumptions are hardcoded.
4. **Refactor plan**
   - Ask for a staged refactor plan with file impact.
5. **Telegram bot redesign plan**
   - Ask for the current command tree, then a future registry-driven bot design.
6. **VWAP implementation readiness check**
   - Ask which prerequisites are missing before coding the VWAP strategy.

This sequence follows the common “explore, plan, code” best-practice pattern recommended by Claude Code practitioners, rather than jumping directly into implementation with incomplete context.[cite:260][cite:263]

## Example prompts to use in Claude

### Initial repo understanding

```text
Inspect this repository and do not code yet. Summarize:
1. current architecture,
2. where strategy-specific logic is hardcoded,
3. how the bot/runtime/deployment pieces are organized,
4. the biggest blockers to turning this into a multi-strategy platform.
Then propose a staged refactor plan.
```

### Context file generation

```text
Create a root CLAUDE.md for this repository.
Keep it concise and practical.
Include architecture summary, project rules, testing guidance, and pointers to key docs.
Do not make unrelated code changes.
```

### Registry design

```text
Find all places where the current code assumes a single ICT strategy.
Do not code yet.
List the affected files and propose how to introduce a strategy registry and strategy-aware config model.
```

### Telegram bot redesign

```text
Inspect the Telegram bot code and explain the current command flow.
Then propose how to redesign it so the same bot supports multiple strategies through a registry-driven menu, where strategy selection and paper/live mode are separate.
Do not implement yet.
```

### VWAP readiness

```text
Based on the current repo structure, identify what must exist before the VWAP strategy can be added cleanly.
Separate the answer into architecture prerequisites, strategy code prerequisites, model prerequisites, NinjaTrader integration prerequisites, and bot/runtime prerequisites.
```

## Recommended task categories to automate first

Highest ROI tasks for Claude on this repo:

- Scaffolding folders and module skeletons.
- Writing/maintaining docs.
- Config/schema migrations.
- Tests for strategy and runtime modules.
- File-by-file refactor assistance.
- Bot handler rewrites that follow repeatable patterns.
- Deployment and service-file cleanup.

Lower-confidence tasks that need more review:

- Trading logic changes that alter entry/exit behavior.
- Model labels and training assumptions.
- Execution and risk logic.
- Live-order routing.

## Safety notes

- Never give Claude broad permission to invent strategy rules that are not documented.
- For trading and model changes, require a written plan before implementation.
- Review any environment-variable, token, or deployment-script changes carefully.
- Keep secrets in GitHub/Colab/Oracle secret stores, never in repo files.[cite:172][cite:254]
- Prefer PR-sized changes even if Claude can edit many files at once.

## Recommended next actions

1. Connect the repo to Claude Code via GitHub.[cite:257][cite:268]
2. Add a root `CLAUDE.md` file to the repo.
3. Add or clean up `docs/architecture.md`, `docs/deployment.md`, and strategy docs.
4. Run a repo audit in Claude before asking it to code.
5. Start with the strategy registry / architecture thread, not VWAP coding first.[cite:168]

If this setup is done well, Claude can become a useful repo-level automation partner for the repetitive engineering parts of the project while still leaving strategy decisions and live-trading risk under explicit human control.[cite:256][cite:260]
