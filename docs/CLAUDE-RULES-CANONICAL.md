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
   `shadow`. **Never add a third gate** — never hide a capability behind
   a separate default-off `*_ENABLED` flag. **The 2026-05-22 MES
   discovery demonstrated why:** the IB `ib_paper` account declared
   `mode: live` with all three strategies, yet MES never traded because
   a `MULTI_SYMBOL_ENABLED` env defaulted off — a hidden third gate. The
   fix removed the flag and derives the traded-symbol set from
   `config/accounts.yaml` (`_resolve_tick_symbols` unions every
   configured account's `symbols`). What accounts.yaml + strategies.yaml
   declare, runs.

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
`main` and commit Tier-1 work there directly once it is validated; you ask the
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

- Order-path integrations (`src/runtime/orders.py`,
  `src/units/accounts/execute.py`).
- Deploy timer changes (`deploy/*.timer`, `deploy/*.service`).
- Service unit changes (`ict-trader-live`, `ict-web-api`,
  `ict-telegram-bot`, `ict-git-sync`, `ict-hourly-snapshot`,
  `ict-heartbeat`, etc.).
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

If a future incident demonstrates that this loop also fails in
practice, the right response is to reconsider this decision then,
with that incident's specifics, not to pre-empt it with infrastructure
that papers over the discipline gap.

## Multi-session coordination (2026-06-28, binding)

Multiple Claude sessions may run concurrently. Two failure modes recur; this
section closes them. The **operational workflow is the binding
`session-coordination` skill** (`.claude/skills/session-coordination/SKILL.md`),
the **live state is `docs/claude/session-board.json`**, and the `SessionStart`
hook surfaces both at session init. Read them before acting.

1. **Know your capabilities before reaching for a tool.** On PM-side / Claude
   Code on the web sessions: `run_workflow` 403s — drive workflows via labelled
   issues (the diag / system-action relays); direct VM egress is usually
   firewalled (live reads go through the `vm-diag-snapshot` relay, `/api/diag/*`
   only); the GitHub MCP drops intermittently — retry with backoff, never treat
   the first failure as an expired token; there is no `create_label`. Full
   contract: root `CLAUDE.md` § "PM-side session capabilities".

2. **Serialize merges — the merge protocol.** Before merging ANY PR: (a) list
   open PRs (the real-time truth), (b) claim the single `merge_slot` on the
   board, (c) sync your branch to `main` **last** — `git fetch origin main` +
   merge/rebase immediately before merging so it is not behind, (d) let CI go
   green on the *synced* head, (e) merge, (f) release the slot. This stops two
   sessions racing a merge and forcing each other "behind" `main` → the
   branch-protection require-up-to-date re-run churn (observed twice on
   2026-06-28).

3. **One PR = one concern.** Never add unrelated work to a branch that already
   has an open PR — it pollutes the PR and invalidates its CI run (and a new
   head SHA strands any merge-gate watcher). Start a fresh branch off `main` for
   a distinct deliverable, even mid-session.

Consistent with § "Why no new mechanical guardrails" above, this is discipline +
a shared board + the hook surfacing it — **not** a new CI gate (operator
decision, 2026-06-28). The hard safety net remains GitHub branch-protection
(require-up-to-date); the board only coordinates intent + the one merge slot.

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
