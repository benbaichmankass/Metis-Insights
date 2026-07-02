# ICT Trading Bot — CLAUDE.md

> **Production environment — live money is at risk.** You have full, autonomous
> access to everything you need to operate this system. The operator grants
> permission by tier; they do not do the work for you. Read this section before acting.

## How you operate

You are the **only interface** to this repository and its production systems —
both VMs, the databases, and the GitHub Actions automation. The single
exception is secrets a human must add to GitHub Actions (exchange/prop
**account keys**). Everything else you do yourself, autonomously, through the
repo and the workflows it ships. The operator's role is to **approve
tier-gated actions and set direction** — not to fetch logs, SSH into a VM, or
run commands on your behalf.

### Instruction hierarchy (highest precedence first)

This list is mirrored verbatim in [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
§ "Document Priority" — **the two must always agree** (enforced by the
`canonical-doc-coherence` CI check).

1. **[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)** — how you operate: access, honesty, permission tiers, workflows, session discipline.
2. **[`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)** — system architecture, trade/comms pipeline, contracts.
3. **[`ROADMAP.md`](ROADMAP.md)** — the centralized record: every milestone/sprint, status, and dates.
4. **The current sprint log** under `docs/sprint-logs/`.
5. **Skills** under [`.claude/skills/`](.claude/skills/) — binding, composable workflows.
6. **This file (`CLAUDE.md`)** — repo orientation + dashboard REST-API reference.
7. **Focused implementation specs** (sprint prompts, subsystem specs) and workflow-helper docs (e.g. [`docs/github-actions-workflows.md`](docs/github-actions-workflows.md)).
8. **`docs/claude/*` and historical notes** — supporting detail.

When sources disagree, the higher one wins. If a higher doc is silent, defer to
the next. If you find a contradiction, fix it (run the `doc-freshness` skill) —
don't route around it.

### Every session

- **Start:** read [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) and the latest roadmap/sprint entry. Read any file you'll change in full; for Tier-2/3 files also read its recent history (`git log -p <file>`) so you don't undo a load-bearing, operator-approved decision.
- **End:** run the **`doc-freshness`** skill to confirm no canonical doc now contradicts your changes, and log any minor issue you noticed but didn't fix to the **health-review backlog** (`docs/claude/health-review-backlog.json`) so a future health-review picks it up.
- **Field beats comment:** when a YAML field, config constant, or DB row disagrees with a surrounding comment, docstring, or non-canonical note, the *field* is the truth — fix the stale text, never flip the field on inference. (This caused the PR #1358 incident.)

## Access & autonomy

Everything you need is already wired into the repo:

- **VMs** — the SSH key (`VM_SSH_KEY`) and diag token (`DIAG_READ_TOKEN`) live in Actions secrets. You read both VMs (live trader `ict-bot-arm`, `141.145.193.91`; trainer `ict-trainer-vm`, `158.178.209.121`) and run tiered changes through GitHub Actions workflows you dispatch yourself — the diag relays for reads, `system-actions` for tiered mutations, and the direct diag API when the session is configured for it. Skills: `diag-data`, `vm-ops`, `git-actions`.
- **Databases** — full read access via the diag/journal relays and the Data Explorer API. You validate integrity and wiring yourself (skill: `db-wiring`).
- **GitHub** — issues, PRs, files, branches, CI, secret scanning via the GitHub MCP tools.

So retrieve the state you need yourself, then act — you never wait on the
operator to look something up. The only actions you genuinely cannot perform
are physical or credential ones: rotating exchange/prop **account keys**,
clearing an OCI console CAPTCHA, or anything that needs a human at a broker.
When you hit one, say so plainly and tell the operator exactly what to do
(e.g. "add `X` to Actions secrets"). That is the one real hand-off.

## Honesty

Give only true, verifiable answers. If you don't know something, say "I don't
know" and state how you'd find out. Never guess, speculate, or report work you
didn't do as done. On a live trading system a confident wrong answer is worse
than "I need to check" — verify against the actual code, config, diag output,
or database before you assert.

> **Other canonical references** (the top three are in the hierarchy above):
> [`docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`](docs/SPRINT-LOG-TEMPLATE-CANONICAL.md)
> — mandatory sprint-log format; and
> [`docs/github-actions-workflows.md`](docs/github-actions-workflows.md) — the
> GitHub Actions reference. When this file disagrees with a canonical doc, the
> canonical doc wins.
>
> **Repo identity:** `benbaichmankass/ict-trading-bot`. Older
> `the-lizardking/ict-trading-bot` references in historical sprint
> summaries are preserved as record.

## Dashboard consumer (adopted 2026-05-12)

The FastAPI on `:8001` is consumed by a **Streamlit dashboard** hosted on
Streamlit Community Cloud, repo `benbaichmankass/ict-trader-dashboard`,
entry point `streamlit_app.py`. **Canonical public URL (single source of
truth):** `https://ict-trader-dashboard-z67ryan2ttrxjdvk6ozcjc.streamlit.app/`
— this is what the Telegram system-report ping deep-links into
(`…/?report=<report_id>` opens the report on the Reports page; see the
`system-report` skill). The Streamlit Python server makes the
upstream HTTP call directly — there is no Vercel rewrite, no Cloudflare
tunnel, no `cf-worker`. The previous React+Vercel+CF stack was retired
in [ict-trader-dashboard#32](https://github.com/benbaichmankass/ict-trader-dashboard/pull/32);
the rationale lives in [`docs/audit/vercel-edge-vs-cf-worker.md`](docs/audit/vercel-edge-vs-cf-worker.md).

**For Claude sessions touching the bot API:** the consumer name has
changed (Streamlit, not Vercel) but the contract has not. Same endpoints,
same shapes, same nullability rules. CORS isn't load-bearing for
Streamlit (the upstream call is server-to-server) but `DASHBOARD_ORIGIN`
in the systemd unit can stay set for now — it's a no-op, not harmful.

## Permission tiers

You work on `main` and commit there directly for Tier-1 work. You ask the
operator only when the tier requires it. Full definitions, examples, and the
verification rules: [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § Permission Tiers.

| Tier | Scope | What you do |
|---|---|---|
| **Tier 1** | Docs, tests, CI, tooling, observability / read paths, non-live refactors, retrieving + analyzing state | Commit to `main` once validated. No approval needed. |
| **Tier 2** | Runtime / deploy / order-path / service / timer changes, DB writebacks, data-mutation jobs | Prepare + validate, get one operator OK in chat, then ship and verify the post-state. |
| **Tier 3** | Strategy logic + params, risk caps / sizing, account-mode flips, live promotion | Analyze and propose the exact change; merge only with explicit operator approval. |

## The two execution gates

Exactly two declared, default-permissive switches decide whether a strategy
trades — both visible in YAML and surfaced on `/api/bot/config`:

- **Account level** — `config/accounts.yaml::mode: live | dry_run`. The only path that may write `mode:` is the `set-account-mode` system-action (operator-gated).
- **Strategy level** — `config/strategies.yaml::execution: live | shadow`. `live` (default) executes; `shadow` runs and logs order packages everywhere (live data collection) but never sends a live order. Enforced in `Coordinator.multi_account_execute` by folding into the same `effective_dry` resolution as `mode:` — no new order path.

Both default permissive, so omitting either never strands capability — a
strategy or account is demoted only by an *explicit* `dry_run` / `shadow`.
There is **no third gate**: never hide a capability behind a separate
default-off `*_ENABLED` flag (the pattern that stranded MES — `ib_paper` was
`mode: live` with all strategies, but a default-off `MULTI_SYMBOL_ENABLED`
meant it never traded). What `accounts.yaml` / `strategies.yaml` declare, runs.

The trader runs 24/7 and never switches itself off — no auto-flip, no breaker
that toggles mode, no "safety" default that goes dry on boot. Transient issues
route through `RiskManager` per-trade: the account stays live and individual
trades are refused with a logged cause. Full Prime Directive + enforcement:
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § Prime Directive;
mode-mutation contract in [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md).

## Prop-trading accounts (scalable architecture)

Prop-firm accounts (e.g. Breakout) are first-class, multi-account, and integrated
into the **standard** strategy flow — full design:
[`docs/integrations/prop-accounts-architecture-DESIGN.md`](docs/integrations/prop-accounts-architecture-DESIGN.md).
The model:

- **Account → ruleset binding.** Every account resolves (via
  `src/prop/account_rulesets.py`) to a backtest unit: prop accounts → their
  prop ruleset (`config/prop_rulesets/*.yaml`: breach rules + `economics` +
  BANK-ASAP withdrawal); all others → a `standard` ruleset from the account's
  `risk` block.
- **Mandatory per-account compatibility.** The `backtesting` and `new-strategy`
  skills require running `scripts/prop/account_compat_matrix.py` so a strategy is
  never routed to an account it wasn't evaluated against under that account's
  rules (prop → cost-aware EV+survival via `src/prop/montecarlo.py::run_ev_montecarlo`;
  standard → net-of-fee performance).
- **Telegram-ping execution.** A prop account "executes" by emitting a
  `prop_signal` ticket (FCM + Telegram) for a supervised assistant to place — NOT
  a broker API. Built multi-account: one signal → per-account legs with a
  discrepancy banner (`src/prop/multi_account_ticket.py` +
  `src/prop/breakout_notify.py`). The live wiring (alt-variant strategies, the
  prop account in `accounts.yaml`, the executor) is **Tier-3**, gated on
  real-venue validation + operator approval.

## Skills (composable workflows) — skill-first lookup is binding

Concrete workflows live as skills under [`.claude/skills/`](.claude/skills/),
written granularly so you can chain them (retrieve data → inspect a VM →
dispatch an action → review).

**Skill-first lookup is binding** — see
[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) § Generation
Discipline. Before generating ANY task output (operator instructions, code,
workflows, runbooks, PR descriptions), your FIRST action is to scan the
skills catalog. If a skill matches: invoke it and derive from it, not from
a precedent artifact. If no skill matches but one *would* prevent future
inconsistency, **propose one in chat** — low cost, operator approves, you
create it. The catalog is the contract; precedents are example outputs.

Skipping the skill check and going straight to precedent matching is the
violation pattern that produces every other violation pattern in this repo.
The companion rule — **precedents are not authoritative** — requires
auditing any artifact you reference against current canonical rules before
copying its shape. Non-compliant precedents either get fixed in your PR or
logged to the health-review backlog; never silently replicated.

## Tiered system-actions (production mutations)

Privileged mutating actions on the live VM run through the **`system-actions`**
GitHub Actions workflow, which exposes a fixed, audited allowlist. You dispatch
them yourself by opening a labelled issue; Tier-1 actions fire autonomously,
Tier-2 after an operator OK in chat. Full allowlist + tiers:
[`docs/claude/system-actions.md`](docs/claude/system-actions.md).

## VM authority split (adopted 2026-05-11)

Two VMs, two trust contracts. A Claude session is acting on exactly
one of them at a time.

| VM | Role | Trust contract | Default posture |
|---|---|---|---|
| `ict-bot-arm` (`141.145.193.91`, Ampere A1.Flex 2 OCPU / 12 GB; migrated off the x86 micro `158.178.210.252` on 2026-06-14) | **Live trader** — runs `ict-trader-live.service`, holds money-at-risk | [`docs/claude/vm-operator-mode.md`](docs/claude/vm-operator-mode.md) | **Restricted.** Tier-1 read autonomous; Tier-2 mutations need operator ack (PM-side issue → `system-actions.yml`); Tier-3 paths (live order code, risk caps, key rotation) are hard-blocked. **Account-mode flips have a sanctioned wire: `set-account-mode` operator action; code paths that flip mode outside that action are Tier-3 violations.** |
| `ict-trainer-vm` (`VM.Standard.A1.Flex`, Ampere A1) | **Training center** — runs the ML lifecycle (datasets, training, registry, eval), no live trade authority of its own | [`docs/claude/trainer-vm-mode.md`](docs/claude/trainer-vm-mode.md) | **Autonomous.** Claude provisions, SSHes, installs, syncs read-only DB from live, runs training cycles, writes the registry up to `advisory` stage, terminates + re-provisions — all without operator-in-the-loop. |

The separation has two gates (2026-05-19 update; see
`docs/ARCHITECTURE-CANONICAL.md` § Change log for the
shadow-default-flip rollout):

1. **Stage gate** — autonomous-Claude on the trainer VM can write a
   model into the registry up to `advisory`, but only the `advisory`
   stage ever influences the order package. Models at `shadow` log
   predictions but never change order decisions; models at `candidate`
   are refused by the shadow factory. (Stage ladder collapsed 7→3 on
   2026-06-16 — canonical `candidate → shadow → advisory`; the legacy
   names `research_only`/`backtest_approved` alias to `candidate` and
   `limited_live`/`live_approved` to `advisory` via
   `ml.manifest.canonical_stage`, so old registry rows still resolve.)
2. **Promotion gate** — the `shadow → advisory` transition (and
   every step beyond) is the operator-approved gate. Promoting
   past shadow is the move that turns a model from "observing" to
   "influencing." This is the live-trading switch.

Since the default flip, models at `shadow` auto-wire onto every
strategy's predictor list when the strategy YAML omits
`shadow_model_ids` (or sets it to `None`). An explicit `[]` opts a
strategy out; an explicit list pins specific ids. This means
shadow-mode logging is enabled-by-default for any newly-trained
model — the operator's role is the promotion gate, not the YAML
wire-up. See trainer-vm-mode.md § 5 for the full lifecycle.

**Hard limits that survive the split** (apply on either VM):

- Never SSH into the **live** VM from a trainer-scoped session.
- Never merge a PR to `main` that touches `config/strategies.yaml`,
  `config/accounts.yaml`, `config/risk_caps.yaml`,
  `src/runtime/orders.py`, `src/runtime/risk_counters.py`, or any
  unit file the live VM consumes **without explicit operator
  approval** — these are Tier-3. The canonical gate is "explicit
  product approval required before merge" (see
  [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
  § Permission Tiers). By default open the PR, mark it draft, and
  ping the operator; once the operator approves, you may merge and
  deploy.
- Never copy production secrets to the trainer.
- Never provision past the OCI Always Free 4-OCPU / 24-GB Ampere tenancy
  ceiling. **Topology as of 2026-06-14 (live→Ampere cutover COMPLETE):**
  - **Live trader** — `VM.Standard.A1.Flex` **2 OCPU / 12 GB** (Ampere, aarch64;
    `ict-bot-arm`, `141.145.193.91`). Migrated off the x86 micro on 2026-06-14
    via `.github/workflows/cutover-live.yml`. `/data/bot-data` is a directory on
    the 45 GB boot volume (NOT a separate block-volume mount), so its units take
    the env-only `data-dir-nomount.conf` drop-in, auto-selected by
    `scripts/install_systemd_units.sh` — see
    [`docs/runbooks/live-vm-migration-ampere.md`](docs/runbooks/live-vm-migration-ampere.md).
  - **Trainer** — `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere; `158.178.209.121`).
  - **IB Gateway** — `VM.Standard.A1.Flex` 1 OCPU / 6 GB (Ampere; `ict-ib-gateway`,
    private IP `10.0.0.251`) — its own dedicated box. **Ampere usage: trainer 1 +
    gateway 1 + live 2 = 4 of 4 OCPU (12+6+6 = 24 of 24 GB) — the Always-Free
    Ampere pool is now full.** The retired x86 micro `158.178.210.252` was a
    *separate* AMD Always-Free allocation (retiring it frees/costs no Ampere
    budget); it was **terminated 2026-06-16** via `terminate-instance` (by OCID,
    display_name `ict-bot`) after a clean soak — no longer a rollback target.

  The 2026-06-10 wedge cascade root cause was the **heavy IB-Gateway
  (Java/Xvfb/IBC) sharing the 1 GB micro** with the trader → swap-thrash. The
  fix was to **move the gateway off the money box onto its own Ampere VM**
  (gateway isolation); the trader reaches it over the private subnet
  (`config/accounts.yaml::ib_paper.ib_host = 10.0.0.251`). Recovery is one
  deterministic daily `docker restart` (`ict-ib-gateway-reset.timer`,
  **06:05 UTC** — retimed 2026-07-02 from 05:30, which was actually inside
  IBKR's own ~03:45–05:45 UTC reset window and so raced the outage it existed
  to fix, BL-20260623-002) on the gateway VM, **plus** the reactive ~5-min
  `ict-ib-gateway-watchdog.timer` (re-armed 2026-06-22, BL-20260622-GATEWAY-MIDDAY-WEDGE
  — catches a mid-day wedge the daily reset alone would miss; it now also
  carries a `--suppress-window-utc 03:45-05:45` flag so it never burns a
  restart attempt on a wedge it can't actually fix). Full topology + rationale:
  [`docs/runbooks/ib-integration.md`](docs/runbooks/ib-integration.md) §
  "Gateway isolation redesign".

  The **live→Ampere migration COMPLETED 2026-06-14.** Rationale (still valid):
  with the gateway isolated, the 2-vCPU / 1-GB x86 micro held the trader on CPU
  fine (loadavg ~1.2 on 2 cores) but hit 90%+ memory with `kswapd` active — 1 GB
  was too small for the grown stack. Free-tier ceiling math: the Ampere pool is
  4 OCPU / 24 GB; trainer (1/6) + gateway (1/6) leave exactly **2 OCPU / 12 GB**
  for live, which is the verified shape of the candidate (`ict-bot-arm`,
  filling the pool to 4/24, $0). The x86 micro is a *separate* AMD Always-Free
  allocation, so retiring it costs no Ampere budget. **Post-cutover follow-ups**
  (most closed 2026-06-14: ✅ `ict-git-sync` re-enabled — the candidate
  auto-deploys from `main`; ✅ `ib_insync` confirmed already present in the trader
  venv — MES/MGC/MHG trade live; remaining: optional dedicated `/data` block
  volume; ✅ micro decommissioned 2026-06-16 via `terminate-instance` by OCID) are tracked in
  [`docs/runbooks/live-vm-migration-ampere.md`](docs/runbooks/live-vm-migration-ampere.md).
  Migration tooling (`provision-live-vm`, `cutover-live`, `terminate-instance`)
  remains for rollback / future moves.

When in doubt about scope, default to the **live-VM** rules and ask.

## Project-level skills — the three-way review split (2026-05-26)

This repo ships **three** project-level Claude Code review skills, each
with its own scope, output schema, and backlog. Earlier this was one
omnibus `/health-review` that mixed system health, trade scoring, and
model status; that proved too broad — each kind of review wants a
different rubric and a different backlog. As of 2026-05-26 the three
sessions are:

| Command | Skill file | Scope | Output template | Backlog |
|---|---|---|---|---|
| `/health-review` | [`.claude/skills/health-review/SKILL.md`](.claude/skills/health-review/SKILL.md) | **Technical / pipeline / data health.** Pipeline plumbing (signal→order→trade), DB integrity + data validity, service state, alert delivery, monitor cadence, strategy silence, sprint-doc drift. Also reviews the cron health-snapshot report. Trainer **service** state only (model detail belongs to /ml-review). | [`comms/schema/health_review_response.template.json`](comms/schema/health_review_response.template.json) | [`docs/claude/health-review-backlog.json`](docs/claude/health-review-backlog.json) — **system bugs**, wiring gaps, minor doc drift. |
| `/performance-review` | [`.claude/skills/performance-review/SKILL.md`](.claude/skills/performance-review/SKILL.md) | **Trading + strategy performance.** Per-strategy aggregates (win rate, PnL, hold times, rejection clusters), per-order-package A-F decision grading (anchored on `signal_logic`, persisted to [`comms/claude_strategy_scores.jsonl`](comms/claude_strategy_scores.jsonl)), comparison vs real closed-trade PnL, **M13 AI-analyst insights cache cross-check** (`/api/bot/insights/*`), Tier-3 tweak proposals. | [`comms/schema/performance_review_response.template.json`](comms/schema/performance_review_response.template.json) | [`docs/claude/performance-review-backlog.json`](docs/claude/performance-review-backlog.json) — **strategy follow-ups**, tweak ideas to revisit, performance puzzles. |
| `/ml-review` | [`.claude/skills/ml-review/SKILL.md`](.claude/skills/ml-review/SKILL.md) | **ML lifecycle.** Trainer-VM service health, training cycles, dataset builds, the full registry; per-model status (latest training metric + shadow/live track record); promotion / demotion recommendations against the 3-stage ladder (`candidate → shadow → advisory`); forward-looking experiment proposals (new manifests, features, datasets, targets, sweeps). | [`comms/schema/ml_review_response.template.json`](comms/schema/ml_review_response.template.json) | [`docs/claude/ml-review-backlog.json`](docs/claude/ml-review-backlog.json) — **AI experiment follow-ups**, promotion-criteria notes. |

For **all three:**

- Claude pulls the live runtime state **itself** via the diag relays
  (`vm-diag-snapshot.yml` for the live VM, `trainer-vm-diag.yml` for
  the trainer). The operator does not paste, download, or fetch a
  snapshot — that would violate the autonomy mandate above. (Pasted
  bundles are accepted only as optional cross-check.)
- Each session ends with **a one-line update to the Claude channel**
  (`@claude_ict_comms_bot`) via the `send-ping` system-action — see
  [`docs/claude/telegram-pings.md`](docs/claude/telegram-pings.md).
- None of the three is a code-quality audit — for that, use the
  `review` / `security-review` skills.
- None of the three asks scoping questions — the scope of each is
  fixed in its SKILL.md.
- None writes to `src/`, `config/`, or any live-path file. Tier-3
  changes are *proposed* (in `proposed_tweaks[]` /
  `promotion_recommendations[]` / `experiments_proposed[]`); the
  operator approves and the change ships via a normal PR.

The [`SessionStart` hook in `.claude/settings.json`](.claude/settings.json)
announces all three at session init so a fresh Claude knows which to
pick.

### The master roll-up: `/system-review` (2026-06-22; reframed 2026-06-23)

The three reviews stay separate, but **`/system-review`**
([`.claude/skills/system-review/SKILL.md`](.claude/skills/system-review/SKILL.md))
is the master session that ties them together. **The work is the REVIEW; the
report is just its deliverable** (operator directive, 2026-06-23). It is a
**WORK session, not a report-generator**: it runs all three reviews (their
individual pings **suppressed**, one consolidated ping instead), AND it assesses
**strategy promotion/demotion readiness** (where each strategy stands vs its
gate), **ML training-cycle + soak health** (are cycles running, dataset builds
OK, soaks accruing not stalled), **raises flags loudly** when something is
degrading, **finds bugs and proposes/applies the fixes** (Tier-3 calls go to
the operator with the exact change; everything else it drives), and **works the
three review backlogs down** (`docs/claude/{health,performance,ml}-review-backlog.json`
— drain open items each run, not just count them). A **review-coverage guard**
(`consolidated.review_coverage` — `strategy_promotion` + `ml_training_health` +
`soak_status` + `flags_raised` + `backlog_drive`) fails the run if any required
assessment is missing, so a review can't silently skip the
promotion/training/soak mandate or skip working the backlogs.

`/system-report` remains a **back-compat alias** that runs the same session —
the artifact name stays "report" everywhere it's load-bearing
(`/api/bot/reports`, `comms/reports/`, the dashboard + Android Reports tabs).

It synthesizes a single **time-windowed executive report**: technical health,
every trade with a per-trade decision dossier (entry/exit + Claude grade + model
scores + signal logic, **split real/paper/prop**, adaptive depth by window), the
PnL trend vs the prior window, a market-context read, the ML fleet, and the
review-coverage block. Windows: `--window=since-last|daily|weekly|monthly`
(default `since-last`; `since-last` reads the prior report's timestamp from
`comms/reports/index.json`). The renderer
([`scripts/reports/render_system_report.py`](scripts/reports/render_system_report.py),
stdlib-only) writes a self-contained **responsive** `report.html` (+ `.md`/`.json`)
under `comms/reports/<window>/<ts>/` (committed → stable GitHub link), and the
file-backed `/api/bot/reports` surface drives a **Reports** log of links in both
the dashboard (desktop) and the Android app (mobile). Output schema:
[`comms/schema/system_report_response.template.json`](comms/schema/system_report_response.template.json);
format spec: [`docs/reports/system-report-DESIGN.md`](docs/reports/system-report-DESIGN.md).
Scheduling (auto daily/weekly/monthly) is a documented phase-2 — v1 is
on-demand.

See also [`docs/runbooks/health-check.md`](docs/runbooks/health-check.md)
for the collect → review design (pre-split, still mostly accurate for
the technical-health half).

## Project Overview
Automated ICT (Inner Circle Trader) futures trading bot running on a VPS.
Exposes a FastAPI REST API on port 8001 consumed by the Streamlit dashboard
(`ict-trader-dashboard`).

## Architecture
```
VPS (systemd)
  ├── ict-trader-live.service ─── trading pipeline (pipeline.py via src/main.py)
  └── ict-web-api.service     ─── FastAPI :8001
                                   ├── /api/bot/stats    ← Streamlit dashboard
                                   ├── /api/bot/logs     ← Streamlit dashboard
                                   ├── /api/bot/positions← Streamlit dashboard
                                   ├── /api/bot/signals  ← Streamlit dashboard
                                   ├── /api/bot/liquidity← Streamlit dashboard (S-064)
                                   ├── /api/bot/config   ← Streamlit dashboard (S-064)
                                   ├── /api/bot/trades/closed ← Streamlit dashboard (#557)
                                   ├── /api/bot/performance ← Android app (windowed aggregate stats)
                                   ├── /api/bot/backtests← Streamlit dashboard (M5 P4)
                                   ├── /api/bot/strategies ← Streamlit dashboard Strategies tab
                                   ├── /api/bot/shadow/predictions ← (S-AI-WS8-PART-2)
                                   ├── /api/bot/shadow/stats       ← (S-AI-WS8-PART-2)
                                   ├── /api/bot/shadow/drift       ← (S-AI-WS8-PART-3)
                                   ├── /api/bot/ml/status          ← Streamlit Models page (S-AI-WS8-PART-2 trainer mirror)
                                   ├── /api/bot/ml/cycle           ← Streamlit Models page (trainer cycle events)
                                   ├── /api/bot/ml/sessions        ← Streamlit Models page (per-manifest training sessions)
                                   ├── /api/bot/ml/registry        ← Streamlit Models page (model registry)
                                   ├── /api/bot/ml/builds          ← Streamlit Models page (dataset-build health)
                                   ├── /api/bot/ml/db_pulls        ← Streamlit Models page (live→trainer DB sync)
                                   ├── /api/bot/ml/runs/{m}/{r}    ← Streamlit Models page (per-run metrics)
                                   ├── /api/pnl/history  ← Streamlit dashboard (S-063, no-session)
                                   ├── /api/pnl
                                   ├── /api/status
                                   ├── /api/bot/insights/* ← Streamlit dashboard (M13 S1+S2)
                                   ├── /api/diag/*       ← PM-side read-only (S-051)
                                   └── /api/health
```

`ict-web-api.service` runs from `/opt/ict-trading-bot` (a symlink to
`/home/ubuntu/ict-trading-bot`, the only working tree). The symlink is
created on first run by `scripts/deploy_diag.sh`; if it goes missing,
the API CHDIRs to a non-existent path and crashloops.

The dashboard consumer is the **Streamlit** app at `benbaichmankass/ict-trader-dashboard`
(`streamlit_app.py` on Streamlit Community Cloud). The Python server
makes the upstream call to `http://141.145.193.91:8001` directly
(the Ampere live trader since the 2026-06-14 cutover; was the x86 micro
`158.178.210.252`) — no tunnel, no Vercel rewrite. The dashboard's
`BOT_API_URL` was repointed at cutover. Pre-2026-05-12 architectures (React on
Vercel → CF named tunnel) are retired; see
[ict-trader-dashboard/CLAUDE.md](https://github.com/benbaichmankass/ict-trader-dashboard/blob/main/CLAUDE.md)
and [`docs/audit/vercel-edge-vs-cf-worker.md`](docs/audit/vercel-edge-vs-cf-worker.md)
(the latter kept as the historical record of why the CF stack was tried
and abandoned). The Cloudflare tunnel integration has been **purged from
the repo** (full-system-audit cleanup): the `ict-cloudflared-tunnel`
service unit, its drop-in, the `*_cloudflare_tunnel.sh` scripts, and the
`*-cloudflare-tunnel` system-actions are all gone — the Streamlit
server-side upstream call needs no tunnel.

## Key Directories
```
src/
  runtime/
    pipeline.py         — main trading loop
    health.py           — 7-point health check suite
    outcomes.py         — structured logging helpers
  web/
    api/
      main.py           — FastAPI app, CORS middleware, router mounts
      auth.py           — session/token auth helpers
      routers/
        dashboard.py    — /api/bot/{stats,logs,positions,signals} (S-014)
        bot_config.py   — /api/bot/config (S-064)
        liquidity.py    — /api/bot/liquidity (S-064)
        trades_closed.py — /api/bot/trades/closed (#557)
        backtests.py    — /api/bot/backtests (M5 P4)
        shadow.py       — /api/bot/shadow/{predictions,stats} (S-AI-WS8-PART-2)
        health_snapshots.py — /api/bot/health/{latest,history,snapshot,services} (#820, 2026-05-11)
        insights.py     — /api/bot/insights/{summary,recent,strategy/{name},health,history,usage} (M13 S1+S2)
        trade_scores.py — /api/bot/trades/scores (#820, 2026-05-11)
        diag.py         — /api/diag/* endpoints (S-051, token-gated read)
        pnl.py          — /api/pnl
        pnl_history.py  — /api/pnl/history (S-063, no-session)
        status.py       — /api/status
    runtime_status.py   — writes runtime_logs/runtime_status.json (DO NOT DELETE—imported by pipeline)
runtime_logs/
  signal_audit.jsonl    — structured pipeline audit log (primary log source for dashboard)
  validation.jsonl      — M5 backtest-run audit log (one NDJSON row per /test invocation)
  heartbeat.txt         — mtime used to detect if bot is alive
trade_journal.db        — canonical SQLite (live VM: /data/bot-data/trade_journal.db).
                          trades carries reconcile_status (orphan-flap hardening
                          2026-06-24): NULL=unspecified / 'unreconciled' (an orphan
                          to resolve — the red-flag state) / 'reconciled' (tied to
                          its real order package) / 'superseded' (a phantom flap
                          duplicate void-flagged by the historical reconciliation
                          pass, excluded from analytics). Orphan is an EXPLICIT
                          queryable terminal state, never inferred from setup_type.
                          Tables: trades, order_packages, signals (dual-write),
                          backtest_results (on-demand /test runs only),
                          daily_risk_state (per-account daily PnL + equity-high —
                          self-healing rebuild from trades + balance snapshot,
                          see src/units/accounts/risk.py), strategy_versions
                          (boot snapshot of config/strategies.yaml),
                          account_context_snapshots (per-signal pre-decision
                          account state — equity, daily PnL, daily equity-high,
                          drawdown%, open-trades-count — keyed by
                          (order_package_id, account_id); S-MLOPT-S12 Part B,
                          best-effort writer in src.units.accounts.context_snapshot,
                          gated by ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED),
                          prop_tickets / prop_fills / prop_account_status
                          (Breakout manual-bridge P2/P3 — outbound prop tickets,
                          inbound fill/close + account-status report-backs;
                          ISOLATED from `trades` so prop never leaks into the
                          real-money/paper KPIs; src/prop/prop_journal.py).
trainer_store.db        — federated read-mostly sidecar (live VM:
                          /data/bot-data/trainer_store.db). Trainer/ML lifecycle
                          data ingested from runtime_logs/trainer_mirror/:
                          training_cycle, dataset_builds, db_pulls,
                          model_registry, experiment_runs, backtest_sweeps.
                          Browsable in the Data Explorer alongside the journal.
```

### Canonical persistence model (S-PERSIST-CANON, 2026-05-23)

One central, queryable store, federated across two SQLite files on the
OCI block volume (`/data/bot-data`), both browsable from the dashboard's
**Data Explorer**:

- **`trade_journal.db`** — everything the LIVE trader produces (trades,
  order_packages, signals, backtest_results, daily_risk_state,
  strategy_versions). Every Python caller resolves its path through the
  single `src.utils.paths.trade_journal_db_path()` resolver; the shell
  side uses `scripts/ops/_lib.sh::runtime_db_path`. The
  `canonical-db-resolver` CI guard forbids the CWD-relative fallback (and
  inline `TRADE_JOURNAL_DB` env-reads) in both shell and Python — that
  fallback is what created the stray duplicate journals under each
  process's working directory.
- **`trainer_store.db`** — everything the TRAINER produces, ingested from
  the file-based trainer mirror (`runtime_logs/trainer_mirror/`) by
  `src/units/db/trainer_store.py` (idempotent, lazy + mtime-gated). Kept
  separate from the money DB so ingest never contends with the live
  trader. The `/api/bot/ml/*` and `/api/bot/backtests/sweeps` file-based
  endpoints remain; the sidecar makes the same data SQL-queryable.

## Dashboard REST API (S-014)

Unauthenticated GET routes — Tier 1 read surface. See
`docs/api-tier-policy.md` for the complete tier inventory (Tier 1 / 2 /
2.5 / 3) and the rules for adding routes.

| Endpoint | Returns | Data source |
|----------|---------|-------------|
| `GET /api/bot/stats` | `BotStats` JSON | `trade_journal.db` + `psutil` + `heartbeat.txt` |
| `GET /api/bot/logs?limit=N&since=ISO_TS&level=CSV` | `LogEntry[]` **newest-first**, merged from the pipeline audit log (signals classify as `trade`) **and** `outcomes.jsonl` (operator WARN/ERROR/CRITICAL outcomes; `critical`→`error`) — the latter is why the non-INFO tag chips were previously empty. Each row also carries `source` (`pipeline`/`outcome`). `limit` 1..1000 (default 100); `since` drops older rows (drives the Android time-frames); `level` is a CSV filter ∈ {info,warn,error,trade}. All optional. | `runtime_logs/signal_audit.jsonl` (fallback `bot.log`) + `runtime_logs/outcomes.jsonl` |
| `GET /api/bot/positions?include_paper=BOOL` | open positions — each carries `accountClass` (`"paper"`/`"real_money"`) plus the legacy `isDemo` flag, and `assetClass` (coarse reporting bucket — crypto/index/commodity/bond/equity/fx/unknown — for symbol-group filtering). **Paper rows excluded by default**; `include_paper=true` includes them (`include_demo` is a deprecated alias). | `trade_journal.db` WHERE status='open' |
| `GET /api/bot/signals` | recent ICT detections — each carries `strategy`, `pattern`, `confidence`, `price`, and `zones[]` (drawable decision geometry the strategy already logged: `{kind:"fvg",low,high}` + `{kind:"sweep",price}` for ict_scalp) | `runtime_logs/signal_audit.jsonl` filtered to buy/sell. `zones` are assembled from geometry the signal builder records (e.g. `fvg_low/high`, `sweep_level`) — never a separately-computed indicator. |
| `GET /api/bot/liquidity?symbol=X` | per-symbol liquidity zones (S-064) | `runtime_logs/liquidity_state.json` (pipeline writes per-tick) |
| `GET /api/bot/config` | effective config view (S-064) — each account carries its public fields incl. `symbols` (added 2026-06-11: the canonical per-account instrument list) and `account_class` (`"paper"`/`"real_money"` — the funding category, added 2026-06-15) | `config/accounts.yaml` + `config/strategies.yaml` + `runtime_logs/runtime_status.json`; secrets redacted |
| `GET /api/bot/accounts/balances` | `{present, source, as_of, age_seconds, balances:{<account_id>:{balance, ts, delta_1h, open_positions, api_ok}}}` | **DB-authoritative (WC-5, 2026-06-16):** the canonical `trade_journal.db::balance_snapshots` table (append-only history; latest row per account, written by the hourly-report `account_snapshots()` dual-write). The legacy `runtime_logs/balance_snapshots.json` (latest-only, overwritten each cycle) is a degraded fallback for the window before the table is populated — `source` (`"db"`/`"json_fallback"`) records which served. **Read-only, connection-free** — never opens an exchange socket; reflects the last recorded balance. Tier 1. |
| `GET /api/bot/db/tables` | `{present, db, dbs:[...], tables:[{name, rows, columns:[{name,type}], db}]}` | **Federated** read-only DB explorer (Data Explorer tab) over BOTH halves of the canonical store: the live trader's `trade_journal.db` AND the trainer-store sidecar `trainer_store.db` (trainer/ML lifecycle data ingested from the trainer mirror — see `src/units/db/trainer_store.py`). Each table carries a `db` field (`"trade_journal"` / `"trainer_store"`). Tier 1; no secrets in either DB. The sidecar is lazily rebuilt from the mirror on read (mtime-gated). |
| `GET /api/bot/db/table/{name}?db=&limit=&offset=&order_by=&order_dir=&filter_col=&filter_op=&filter_val=` | `{table, db, columns, rows, total, limit, offset}` | one paginated page of a table from whichever federated DB owns it (auto-routed by name; optional `db` selector ∈ {trade_journal, trainer_store}). **SELECT-only** (read-only `mode=ro` connection); table/column identifiers validated against the live schema (no identifier injection), filter values bound. `filter_op ∈ {eq,ne,gt,lt,gte,lte,like}`; `limit` 1..500. 404 on unknown table. |
| `GET /api/bot/trades/closed?limit=N&since=ISO_TS&account_id=X&include_paper=BOOL` | `ClosedTrade[]` (#557) — each row carries `accountClass` (`"paper"`/`"real_money"`) plus legacy `isDemo`, and `assetClass` (coarse reporting bucket for symbol-group filtering). **Paper rows are excluded by default**; `include_paper=true` adds them (`include_demo` is a deprecated alias; effective = `include_paper OR include_demo`); `account_id=X` returns only that account (and always wins over the include flags). | `trade_journal.db::trades` filtered to closed + non-backtest, joined to `order_packages` for the closed-at proxy |
| `GET /api/bot/performance?window=24h\|7d\|30d\|all` | `{window, since, error, totalTrades, wins, losses, winRate, totalPnl, expectancy, totalR, expectancyR, rTradeCount, rCoverage, profitFactor, maxDrawdown, perStrategy:[{name, trades, wins, winRate, totalPnl, expectancy, totalR, expectancyR, rTradeCount}], perAssetClass:[{assetClass, trades, wins, winRate, totalPnl, expectancy, totalR, expectancyR, rTradeCount}], equity:[{t, cum}], paper:{…same shape…}, demo:{…back-compat alias of paper…}}` — windowed aggregate trade analytics computed **in SQL over the full history within the window (uncapped)**. The R-metrics normalise pnl by each trade's own risk so cross-instrument trades compare on one axis (null when risk is unknown; `rCoverage`/`rTradeCount` report how much of the window is R-measured — never a raw-pnl fallback). `profitFactor` (gross profit / gross loss) and `maxDrawdown` are **null** (not 0) when uncomputable. Replaces consumer-side rollups over the 200-row `/trades/closed` cap (which pinned the Android Performance "Trades" count at 200). Excludes backtest + paper rows like `/stats`; close-time basis is the **epoch-ms-aware** `COALESCE(<closed_at normalised>, op.updated_at, t.timestamp)` (shared `src/web/api/_closed_at.py` — the reconciler-filled close path writes `trades.closed_at` as a raw epoch-ms string, which an unguarded `datetime()` drops from the window; this was the "0 closed trades in 24h while lifetime is non-zero" bug, fixed for `/performance` + `/stats` pnl24h to match `/trades/closed`). Additively returns a `paper` sub-block (and a back-compat `demo` alias) with the same shape computed over paper-account rows. Zeroed envelope (HTTP 200, `error` set) on unknown window / DB error. Tier 1 | `trade_journal.db::trades` LEFT JOIN `order_packages` |
| `GET /api/bot/order-packages?limit=N&since=ISO_TS&strategy=X&include_paper=BOOL` | `{rows:[{orderPackageId, createdAt, updatedAt, strategy, symbol, assetClass, direction, entry, sl, tp, confidence, status, closeReason, linkedTradeId, pnl, tradeStatus, accountClass, isDemo, signalLogic, meta, modelScores, claudeScore}], count, claude_log_present}` (`assetClass` = coarse reporting bucket for symbol-group filtering) — **decision-level** view (one row per order package = the bot's actual decision). Each row carries `accountClass` (`"paper"`/`"real_money"`) plus legacy `isDemo`; **paper rows excluded by default**, `include_paper=true` includes them (`include_demo` deprecated alias). `signalLogic` / `meta` are the decision reasoning the bot recorded at signal time (`order_packages.signal_logic` / `meta` TEXT columns, JSON-decoded; `meta` typically carries setup_type / killzone / bias). **`modelScores`** is the per-model ML decision captured at signal time — `{model_id: {stage, score}}` from the `order_packages.model_scores` column (populated by `strategy_signal_builders._emit_shadow_preds` → `shadow_adapter.capture_shadow_preds` → persisted in `_log_new_order_package`); `null` for pre-column rows. It's the cheap-SELECT replacement for recomputing per-trade aggregates from `shadow_predictions.jsonl`. All three are `null` when unset; they power the dashboard/Android open-trade detail card. `claudeScore` = the Claude strategy-decision grade `{grade, score, entryQuality, exitQuality, riskManagement, executed, rationale, reviewedAt}` or `null` until a `/health-review` scores it. | `trade_journal.db::order_packages` LEFT JOIN `trades` (PnL + backtest/demo filter) + `comms/claude_strategy_scores.jsonl` (by `order_package_id`); backtest + paper rows filtered by default; Tier 1 |
| `GET /api/bot/positions/net` | `{positions:[{symbol, net_qty}], count}` — current **signed net qty per symbol** aggregated across all live accounts (S11/M11). Symbols at net 0 are excluded. Best-effort: a missing/corrupt DB returns an empty list, never a 5xx. Tier 1 | `trade_journal.db` (open, non-backtest rows) via `src.runtime.positions.net_positions_by_symbol` (`src/web/api/routers/attribution.py`) |
| `GET /api/bot/strategy/attribution` | `{strategies:[{strategy, open_trades, closed_trades, winning_trades, losing_trades, win_rate, total_pnl}], generated_at}` — per-strategy lifetime closed-trade stats + a live open-trade count (S11/M11). **Real-money only** (account_class-authoritative, is_demo fallback — excludes paper AND prop, per the "real and paper never blended" contract); win/loss/total restricted to resolved trades (`pnl IS NOT NULL`); reconciler `orphan_adopt`/superseded artifacts excluded. Tier 1 | `trade_journal.db::trades` (`src/web/api/routers/attribution.py`) |
| `GET /api/bot/candles?symbol=X&interval=Y&limit=N` | `{symbol, interval, source, candles:[{time, open, high, low, close, volume}], count, error}` — OHLCV from the **same exchange the strategies trade the symbol on** (BTCUSDT → Bybit; MES/MGC/MHG → IBKR), via `src.runtime.market_data.connector_for_symbol` + `fetch_candles` (the signal builders' path). `time` is epoch seconds. Backs the dashboard live chart so candles match the bot's view (replacing the flaky Yahoo Finance feed). Best-effort: empty `candles` + `error` on any failure (e.g. MES when the IB account has no `ib_port`) so the dashboard falls back to yfinance. Short in-process cache. **Env: the `ict-web-api` unit needs `BYBIT_TESTNET=false` + Bybit creds for mainnet candles.** Tier 1 | Bybit (CCXT) / IBKR market data via the canonical fetcher |
| `GET /api/bot/strategies` | per-strategy config, **live-runtime status** (`loaded`/`running` from `runtime_status.json`), **per-account routing** (`accounts:[{id,live}]` from `accounts.yaml`), lifetime trade stats, descriptions (`{short, how_it_works}` from `config/strategy_descriptions.json` — no hardcoded fallback), changelog; plus a top-level `runtime` block (`bot_running`, `last_tick_utc`, `tick_age_seconds`, `loaded_strategies`) | `config/strategies.yaml` + `config/accounts.yaml` + `config/strategy_descriptions.json` + `config/strategy_changelog.json` + `runtime_logs/runtime_status.json` + `trade_journal.db`; Tier 1 |
| `GET /api/bot/strategies/{name}/tune` | `{present, date, dir, results:[strategy_tune_result/v1, ...]}` — newest-date M8 parameter-sweep results for a strategy (one entry per tuned param). `present:false` cleanly when none. Each result carries the OOS/k-fold grid + an **advisory** Tier-3 value proposal; the harness never writes config. Tier 1 (M8) | `runtime_logs/strategy_tunes/<UTC-date>/<strategy>__<param>.json` (written by `scripts/ml/strategy_tune_sweep.py`) |
| `GET /api/bot/strategies/{name}/review` | `{present, packet_path, summary_md_path, packet}` — newest **M7 strategy-review packet** for a strategy (gate doc: `docs/strategy-review-gate.md`); the `packet` carries the action badge (`KILL`/`DEMOTE_SHADOW`/`TUNE`/`HOLD`/`PROMOTE`), `n_closed`/`win_rate`/`expectancy`/`pnl_total`, the matrix `reasons[]`, and any Tier-3 SLA due-by. `present:false` (HTTP 200) when the strategy has never been reviewed; strategy name validated `[a-z0-9_]+`. **Read-only** (Tier-3 actions are *read* here, not enacted). Tier 1 (M7) — consumed by the Streamlit dashboard Strategies tab | `runtime_logs/strategy_reviews/<UTC-date>/<name>.json` (written by `scripts/ml/strategy_review_packet.py`) |
| `GET /api/bot/backtests?limit=N&strategy=X` | `BacktestRun[]` (M5 P4) | `trade_journal.db::backtest_results` (M5 consumer writes one row per `/test <strategy>`); newest-first by id; headline metrics only |
| `GET /api/bot/backtests/sweeps?limit=N` | `{present, dir, mirror_age_seconds, sweeps:[{date, summary_md, metrics, extra_metrics, generated_at}]}` | strategy-improvement / validation sweeps mirrored from the trainer VM into `runtime_logs/trainer_mirror/backtests/<UTC-date>/` (`SUMMARY.md` + `all_metrics.json`), published by `scripts/ops/publish_trainer_mirror.sh`; newest-first by date. The `backtest_results` table above only ever holds on-demand `/test` runs (M5 consumer, env-gated default-off) — the operator's real backtest sweeps come through this route. |
| `GET /api/bot/shadow/predictions?limit=N&model_id=X&stage=X&since=ISO` | envelope `{log_present, log_path, records[], count}` (S-AI-WS8-PART-2) | `runtime_logs/shadow_predictions.jsonl` (WS7 audit log); newest-first; reuses `ml.shadow.inspector` |
| `GET /api/bot/shadow/stats?model_id=X&stage=X&since=ISO` | envelope `{log_present, log_path, records[], count}` per-`(model_id, stage)` aggregate (S-AI-WS8-PART-2) | same log; aggregated via `ml.shadow.inspector.aggregate` |
| `GET /api/bot/shadow/drift?model_id=X&stage=X&reference_days=N&current_days=N&bins=N&score_min=F&score_max=F` | drift envelope `{log_present, log_path, model_id, stage, reference_window_start, current_window_start, reference_count, current_count, verdict, ks, ks_verdict, psi, psi_verdict, reference_mean, current_mean, reference_stdev, current_stdev}` (S-AI-WS8-PART-3) | same log; window-over-window score-distribution comparison via `ml.shadow.drift.compute_drift` (KS + PSI) |
| `GET /api/bot/health/latest` | `{present, path, snapshot}` envelope wrapping the most recent `artifacts/health/latest.json` (#820, 2026-05-11) | `artifacts/health/latest.json` |
| `GET /api/bot/health/history?hours=N&include_payload=BOOL` | `{present, dir, hours, snapshots[]}` — newest-first timestamped snapshots (#820, 2026-05-11). `hours` clamped 1..336 (default 24); `include_payload=true` embeds each snapshot's full JSON | `artifacts/health/health_check_<TS>.json` files |
| `GET /api/bot/health/snapshot?lines=N` | `{present, path, lines[]}` tail of the raw text snapshot (#820, 2026-05-11) | `artifacts/health/health_snapshot.txt` |
| `GET /api/bot/health/services` | `{systemctl_available, services: [{unit, state, sub_state, active_enter_iso}, ...]}` for the allowlisted bot units (#820, 2026-05-11) | `systemctl show` against `ict-trader-live.service` + `ict-web-api.service` |
| `GET /api/bot/trades/scores?limit=N&include_open=BOOL` | `{log_present, log_path, backfill_log_present, backfill_log_path, shadow_record_count, trades: [{trade_id, symbol, status, opened_at, closed_at, scores[{model_id, stage, count, score_first, score_last, score_min, score_max, score_mean, first_ts, last_ts, backfill_kind}, ...]}, ...]}` — per-trade shadow-prediction score aggregates (#820 2026-05-11; PR #1521 added `feature_row` capture + symbol-filtered join 2026-05-19; PR #1538 added the retroactive backfill envelope fields `backfill_log_*` + per-score `backfill_kind` 2026-05-19; PR #1548 canonicalized the writer path so real-time + backfill records resolve under the same `runtime_logs_dir()` root). | `trade_journal.db::trades` JOIN `runtime_logs/shadow_predictions.jsonl` (real-time) + `runtime_logs/shadow_predictions_backfill.jsonl` (one-shot historical replay written by `python -m ml backfill-shadow-predictions`) |
| `GET /api/bot/insights/{summary,recent,strategy/{name},health}` | `{summary_md, grade, signals[], data_window, row_counts, generated_at, cache_age_seconds, model_id, cache_present, cache_path}` — AI Analyst insights (M13 S1+S2). **Cache-only read path:** the router never calls Anthropic and never imports the `anthropic` SDK; it returns whatever the `ict-insights-generator.{service,timer}` (fast tier, every 15 min) and `ict-insights-generator-strategies.{service,timer}` (slow tier, every 60 min) most recently wrote to `runtime_logs/insights/<endpoint>.json`. Cache miss → 200 placeholder envelope (`cache_present: false`). `/recent?limit=N` echoes the requested `limit` in `requested_limit`. `/strategy/{name}` rejects names outside `[a-z0-9_]+`. Cache `model_id` reflects whichever `INSIGHTS_MODEL_MODE` produced it: `template:v1`, an Anthropic model id, or a Gemini model id. | `runtime_logs/insights/{summary,recent,strategy_<name>,health}.json`, written by the M13 generator process |
| `GET /api/bot/insights/history?endpoint=X&hours=N&limit=N&strategy_name=Y` | `{rows, count, endpoint, hours, limit, strategy_name, table_present}` — newest-first historical rows from `trade_journal.db::insights_history` (M13 S1 / PR F). Each row carries decoded `signals` + `data_window` + `row_counts` + full `payload` so the consumer can drill in without a second query. Empty rows + `table_present:false` when the generator hasn't written to the DB yet. | `trade_journal.db::insights_history` |
| `GET /api/bot/insights/usage` | `{current_month_usd, current_month_tokens, current_month_calls, budget_usd, month_start, by_endpoint:[{endpoint, status, calls, spent}], price_table_as_of, table_present}` — calendar-month spend + per-endpoint split (M13 S1 / PR F). Template-mode rows carry `cost=0` and `tokens=0`; Anthropic/Gemini rows carry the real numbers from the public price table. | `trade_journal.db::insights_usage` |
| `GET /api/pnl/history?days=N&account_id=X` | `PnlHistoryPoint[]` (S-063) — `account_id=X` scopes to one account (default: all real-money accounts; **paper excluded** via the `account_class`-aware predicate, falling back to `is_demo` for un-backfilled rows). | `trade_journal.db` (closed trades, realised PnL per UTC day) |
| `GET /api/bot/pnl/exchange?days=N` | `{summary:{fill_count, total_fees, symbol_count, window_days, total_realized_pnl, total_unrealized_pnl}, by_symbol:[{symbol, fill_count, gross_qty, gross_notional, total_fees, first_exec_time, last_exec_time, realized_pnl, unrealized_pnl, open_qty_signed, last_price}]}` — **exchange-truth P&L attribution** from the exchange-fills store (FIFO buy/sell lot pairing; realised = matched-lot PnL − fees, unrealised marks open lots against the last fill price). `days` 1..90 (default 7). Insulated from `trade_journal.db` schema bugs; zero aggregates when the fills puller has never run. Tier 1 | `runtime_state/exchange_fills.sqlite` (populated by `scripts/pull_exchange_fills.py`) via `src.runtime.exchange_fills_store` (`src/web/api/routers/pnl_exchange.py`) |
| `GET /api/bot/news/recent?limit=N` | `{present, log_path, count, records[]}` — newest-first tail of the M9 news layer's shadow-soak log (per-actionable-signal decision/adjustment/veto/query/symbol + applied influence downsizes). `present:false` until the layer is active (`NEWS_SOURCE=rss`, or `newsapi` + `NEWS_API_KEY`). Tier 1 | `runtime_logs/news_decisions.jsonl` (written by `src.news.news_audit` + `src.runtime.news_sizing`) |
| `GET /api/bot/exit-ladder/soak?limit=N&venue=api\|prop&account_id=X&differing=BOOL` | `{present, log_path, count, records[], summary{total_scanned, by_venue, differing, differing_pct}}` — newest-first tail of the **ExitPlan exit-ladder shadow-soak** (dynamic-take-profit consistency P3). One row per executed order: the **laddered exit that would be used** (materialized ExitPlan sized to the order's real qty) vs the **single SL/TP target actually placed** (`differs_from_single_target`). Observe-only — nothing reads it back to drive an exit (graduation to the real laddered exit is the backtest-gated P4). `present:false` until the first live opening order writes a row. Tier 1 | `runtime_logs/exit_ladder_soak.jsonl` (written by `src.runtime.exit_ladder_soak` from `execute.py` (venue=api) + `breakout_executor.py` (venue=prop)) |
| `GET /api/bot/allocator/soak?limit=N&symbol=X&regret=BOOL` | `{present, log_path, count, records[], summary{total_scanned, disagree, disagree_pct, mean_regret}}` — newest-first tail of the **portfolio capital-allocator shadow-soak** (M18 P0c). One row per tick with **≥ 2 actionable candidates** (a genuine choice): what a capital allocator **would** pick (the top-ranked candidate of the full opportunity set the intent multiplexer surfaces, M18 P0b) vs what the aggregator **actually** routed, + the **regret** (`top_score − executed_score`, ≥ 0 — EV left on the table). The ranking `score_kind` is the strategy-**confidence proxy** at P0c; M18 P1 swaps in the cost-aware `EV_net` scorer. `regret=true` filters to disagreements. Observe-only — routing is unchanged; nothing reads it back (graduating the allocator to *select* the subset is the backtest-gated M18 P2+). `present:false` until the first multi-candidate tick writes a row. Tier 1 | `runtime_logs/allocator_soak.jsonl` (written by `src.runtime.allocator_soak` from `intent_multiplexer.py`) |
| `POST /api/bot/prop/report` | ingest the **inbound prop fill/close or account-status report-back** (Breakout manual-bridge P2). Body is JSON: a **fill/close** (`account_id, symbol, direction, status∈{placed,open,filled,closed,skipped}, entry_price, exit_price, qty, pnl, pnl_percent, reason, ticket_id?`) or an **account status** (`kind:"account_status", account_id, balance, equity, realized_today, unrealized, day_start_balance?`). **`status:"placed"`** is the working-order state — a limit/pending order placed but NOT filled yet (no position, no P&L): it advances the ticket to `placed` (distinct from `filled`) and fires **no** notification; when the limit later trips the operator reports `open`/`filled`. Lifecycle `emitted → [placed] → filled → closed`. A `status:"closed"` fill fires the `prop_closed` notification (the trade-close follow-up); `open`/`filled` fire `prop_fill`. Writes the prop journal, links the fill to its outbound ticket, advances the ticket lifecycle. **Tier 2** (DB write + notification); **token-gated via `DASHBOARD_API_TOKEN`** when set. 400 on a structurally-bad report. | `trade_journal.db::{prop_fills,prop_account_status,prop_tickets}` via `src.prop.prop_report.ingest_report` |
| `GET /api/bot/prop/fills?account_id=X&limit=N` | `{present, count, fills[]}` — inbound prop fill/close reports, newest-first | `trade_journal.db::prop_fills` |
| `GET /api/bot/prop/tickets?account_id=X&status=Y&limit=N` | `{present, count, tickets[]}` — OUTBOUND prop tickets the bot emitted (`emitted`/`placed`/`filled`/`closed`/`skipped`; `placed` = limit order placed but not yet filled), newest-first | `trade_journal.db::prop_tickets` (written by `breakout_executor.emit_prop_ticket`) |
| `GET /api/bot/prop/status?account_id=X` | `{account_id, present, status, rule_distance}` — latest account-status snapshot + **computed rule-distance**: distance to the daily-loss limit ($150 = 3% on the $5k account) and the static-DD floor ($300 / $4700 floor), resolved from the account's prop ruleset. Drives the dashboard rule-distance panel. Null any value not derivable from the snapshot. Tier 1 | `trade_journal.db::prop_account_status` + `config/prop_rulesets/breakout.yaml` |
| `GET /api/bot/prop/reconcile?account_id=X` | `{present, summary{tickets_total, fills_total, unacted_count}, unacted_tickets[]}` — **un-acted tickets** (emitted, past `valid_until`, no matching fill reported back) — the P3 drift alert. Tier 1 | `trade_journal.db::{prop_tickets,prop_fills}` via `src.prop.prop_reconcile` |
| `GET /api/bot/reports?limit=N&window=X` | `{present, count, total, window, reports:[{id, window, generated_at, window_start, window_end, roll_up_grade, headline, html_path, json_path, md_path}, ...]}` — newest-first index of consolidated **system reports** (the `/system-report` master skill's output). `window ∈ {since-last,daily,weekly,monthly}` filters. **File-backed, Tier 1, read-only** (no DB table). Surfaced as a "Reports" log of links in the Streamlit dashboard + Android app. | `comms/reports/index.json` (committed; VM mirrors via `ict-git-sync`) via `src/web/api/routers/reports.py` |
| `GET /api/bot/reports/{report_id}` | `{present, report, html, json_present}` — one report's index metadata + its rendered self-contained responsive `report.html` body (for inline embed / WebView). 404 on unknown id; artifact paths validated under `comms/reports/` (no traversal). Tier 1 | `comms/reports/<window>/<ts>/report.html` |
| `GET /api/bot/roadmap` | `{present, lastUpdated, summary{done,active,pending,total}, milestones:[{id, type, focus, status, statusEmoji, statusLabel, statusDetail, sprintRefs, sprintIds, sprintCount}], sprints:[{id, title, objective, dateStart, dateEnd, milestone, path}], sprintCount}` — the parsed **product roadmap**: the `## M0..M15 Milestone Roadmap` table (each milestone with a normalized `status` ∈ done/in_progress/next/planned/blocked/reopened + a progress roll-up), plus the full **sprint-log index** (every `docs/sprint-logs/*.md`, newest-first by end date). Each sprint is mapped to its owning milestone via the explicit `docs/sprint-logs/<id>.md` links in the milestone cell + the Historical Sprint Ledger's `M-mapping` column + a filename-prefix fallback; one-off thematic sprints resolve `milestone:null`. **File-backed, Tier 1, read-only** (no DB table) — backs the Streamlit dashboard's **Roadmap** tab. Best-effort: a missing/garbled `ROADMAP.md` degrades to an empty envelope, never a 5xx. Short in-process cache keyed on file mtimes. | `ROADMAP.md` + `docs/sprint-logs/*.md` via `src/web/api/routers/roadmap.py` |
| `GET /api/bot/roadmap/sprint/{sprint_id}` | `{present, id, path, milestone, dateStart, dateEnd, objective, sections:[{heading, body}], markdown}` — one sprint log parsed into its `##` sections plus the raw markdown (the work-session write-up: objective, work completed, validation, docs updated, …). `present:false` (HTTP 200) on unknown id; `sprint_id` validated `[A-Za-z0-9._-]+` and resolved strictly under `docs/sprint-logs/` (no traversal → 400). Tier 1 | `docs/sprint-logs/<sprint_id>.md` |
| `POST /api/bot/devices/register` | upsert a device by its FCM token (M12 S1) — `{token, platform?∈{android,ios}, label?, subscriptions?}` → `{id, token_suffix, platform, label, subscriptions, created_at, last_seen_at, is_new}`. **Idempotent on token** (re-register updates `last_seen_at`/label/subscriptions, no dup row). The raw token is never echoed back (only `token_suffix` = last 8 chars). Unknown subscription kinds (vs `src.runtime.mobile_push.event_kinds`) → 400. The only write the Android app makes to the bot. Tier 1 | `trade_journal.db::device_tokens` (`src/web/api/routers/devices.py`) |
| `GET /api/bot/devices` | `{count, devices:[{id, token_suffix, platform, label, subscriptions, created_at, last_seen_at}]}` — registered devices (raw tokens never exposed). **Token-gated via `DASHBOARD_API_TOKEN`** when set (serves permissively when unset). Tier 1 | `trade_journal.db::device_tokens` |
| `GET /api/bot/devices/event-kinds` | `{kinds:[{kind, label, description, in_flight}]}` — the canonical push event-kind taxonomy the Android Notifications screen iterates to build per-kind toggles (so the app needn't mirror the list). Tier 1 | `src.runtime.mobile_push.event_kinds` |
| `DELETE /api/bot/devices/{id}` | `{id, deleted:true}` — revoke a device (lost phone). 404 on unknown id. Token-gated via `DASHBOARD_API_TOKEN` when set. Tier 1 | `trade_journal.db::device_tokens` |
| `PATCH /api/bot/devices/{id}/subscriptions` | `{id, subscriptions}` — replace a device's per-kind push subscription prefs (`{subscriptions: null\|[kinds]\|{kind:bool}}`; null/empty = all). 404 on unknown id; unknown kinds → 400. Token-gated when `DASHBOARD_API_TOKEN` set. Tier 1 | `trade_journal.db::device_tokens` |
| `POST /api/auth/login` | `{access_token, token_type, expires_in}` (S-013 M3 PR #1) — issues a bearer session token for the session-gated `/api/pnl` + `/api/status` routes given `{email, password}`. **Issuance only**: `require_session` enforcement on protected routes is a separate PR — neither the Streamlit dashboard nor the Android app calls this path today (both consume only no-session routes), so it's invisible to either consumer. 500 (generic `auth_unavailable` body, no secret-name leak) if the auth env vars are unset. | env-configured allowed email + password hash (`src/web/api/auth.py`) |

### `BotStats` shape
```json
{
  "pnl24h": 124.50,
  "totalPnL": 3200.00,
  "openTrades": 2,
  "winRate": 68.5,
  "status": "running",
  "datasource": "live",
  "vmHealth": { "cpu": 32.1, "memory": 48.5, "disk": 21.0 },
  "paperOpenTrades": 1,
  "paper": { "pnl24h": 0.0, "totalPnL": 0.0, "openTrades": 1, "winRate": 0.0 }
}
```

The top-level `pnl24h` / `totalPnL` / `openTrades` / `winRate` are **real-money
only** — paper rows are excluded (account_class-authoritative, is_demo
fallback). **Real and paper performance are never blended** (P4 of the
live-trade management contract, operator directive): the paper-side aggregates
ride in an **additive `paper` sub-block** (same shape) plus a distinct
`paperOpenTrades` count alongside the real `openTrades` KPI, so a consumer
renders the two as separate sections — there is no combined real+paper total
anywhere.

### `Position` shape (`/api/bot/positions`)
```json
{
  "id": "42",
  "account": "bybit_2",
  "accountClass": "real_money",
  "assetClass": "crypto",
  "symbol": "BTCUSDT",
  "side": "buy",
  "qty": 0.001,
  "entryPrice": 80700,
  "unrealizedPnl": 12.45,
  "openedAt": "2026-05-11T03:00:00Z",
  "stopLoss": 80300,
  "takeProfit": 81450,
  "pattern": "vwap"
}
```

`accountClass` (`"paper"`/`"real_money"`, added 2026-06-15) is the
funding-category axis, orthogonal to the technical `mode:` gate. Never
null — falls back to the legacy `is_demo` boolean for rows predating the
`account_class` column/backfill. `isDemo` is still emitted alongside for
back-compat.

`assetClass` (`"crypto"`/`"index"`/`"commodity"`/`"bond"`/`"equity"`/`"fx"`/
`"unknown"`, added 2026-06-29) is the coarse **reporting** bucket for the row's
`symbol` so a consumer can group/filter positions by asset group without
maintaining a symbol map of its own. Resolved by the same authoritative
`src/web/api/_asset_class.py::asset_class_for_symbol` the `/performance`
`perAssetClass` aggregate uses (config-driven via `config/instruments.yaml`,
heuristic fallback). **Reporting-only** — never the order path; never null
(worst case `"unknown"`). Also emitted on `/api/bot/trades/closed` and
`/api/bot/order-packages` rows.

`stopLoss` / `takeProfit` / `pattern` were added in #820 (2026-05-11) so the
dashboard's live overview chart can render TP/SL price-lines and the
positions table can show the active strategy. Each is **nullable** —
older rows or rows where the writer didn't populate the field serialize
as `null`. Renderers must treat null as "not provided" (em-dash) rather
than `0` / `"unknown"`.

`options` (added 2026-06-27, Alpaca options Slice-5) is the **defined-risk
structure block** for an options-expression row (account_class `paper`,
`alpaca_options_paper`): `{structure:"debit_vertical", contracts, net_debit,
max_loss_usd, max_gain_usd, width, breakeven, expiration, legs:[{symbol(OCC),
side, intent, ratio, strike, type}]}`. **`null` for any non-options row.**
Decision-time geometry persisted in the trade's `notes.options` at open by
`execute._log_trade_to_journal` (via `options_overlay.options_structure_dict`)
and surfaced here connection-free — **per-leg live greeks/PnL are a documented
follow-up** (the `/positions` endpoint never opens a broker socket). The
options-lifecycle reconciler (`order_monitor._reconcile_options_expiry_and_assignment`)
closes these rows on broker-confirmed expiry/assignment, with realised PnL
sourced from `/v2/account/activities` cash (NOT the equity local-PnL formula —
options rows are deferred in `_sweep_local_pnl_for_unpriced`).

**PnL-resolution contract (2026-06-16, live-trade management contract — full
record in the ARCHITECTURE-CANONICAL 2026-06-16 change-log row +
`docs/audits/live-trade-management-contract-2026-06-16.md`):** every account
resolves PnL the same way — **prefer broker truth, else local compute** —
keyed on `contract_value_usd` (`config/instruments.yaml`). Whether an
integration provides broker truth is a *declared capability*
(`clients.BROKER_PNL_READER_EXCHANGES`, today `{bybit}`), default-local, no
gate. So `unrealizedPnl` on `/positions` is broker-sourced when available, else
a server-side mark-to-market fallback (`unrealizedPnlSource="markprice_local"`,
multiplier-aware); and non-Bybit (IBKR/Alpaca/OANDA) *realised* PnL on
`/trades/closed` is filled by `order_monitor._sweep_local_pnl_for_unpriced`
(`notes.pnl_source="local_compute"`) rather than left NULL.

## CORS
CORS is configured in `src/web/api/main.py`. Allowed origins:
- `http://localhost:5173` (Vite dev) — legacy; no longer used by the live dashboard, harmless to leave in the list.
- `http://localhost:3000` — legacy.
- Value of `DASHBOARD_ORIGIN` env var (legacy Vercel URL; a no-op for the server-side Streamlit dashboard — see the note below).

**Note (2026-05-12):** the Streamlit dashboard makes its upstream call
server-side, so CORS isn't load-bearing for it. The env var + middleware
stay in place for any future browser-direct consumer.

## Environment Variables

> This table is a **curated subset** of operator-relevant toggles, not the
> full set of env vars the runtime reads. Other load-bearing runtime flags
> live in code with sensible defaults and are documented at their call
> sites — notably `MULTI_STRATEGY_INTENT_LAYER` (`intent_multiplexer.py`,
> **default on** — the core intent-aggregation switch), `RECONCILER_GRACE_SECONDS`,
> `ORPHAN_POSITION_POLICY`, `STUCK_STRATEGY_THRESHOLD_MINUTES` /
> `STUCK_STRATEGY_TIMEFRAME_MULT`, `STRATEGY_REFUSAL_COOLDOWN_SECONDS`,
> `HEARTBEAT_INTERVAL_SECONDS`,
> `TICK_INTERVAL_SECONDS`, `HALT_FLAG_PATH`, and `MONITOR_BLINDNESS_ALERT_TICKS`
> (`order_monitor.py`, default `3` — consecutive ticks a position's `monitor()`
> may fail to run before the exit-coverage monitor-blindness alert fires; a
> tuning knob, not an enable gate — the alerting is always on).

| Variable | Purpose |
|----------|---------|
| `DASHBOARD_ORIGIN` | Legacy Vercel app URL — added to CORS allow-list. No-op for the Streamlit dashboard but kept for future browser-direct consumers. |
| `DASHBOARD_API_TOKEN` | Optional bearer token for auth routes |
| `SIGNAL_DUAL_WRITE_DISABLED` | When truthy, `signal_audit_logger._dual_write_to_db` skips hydrating `trade_journal.db::signals` (JSONL stays the source of truth). Default off → dual-write on. Toggle on the live VM via the `enable-signal-dual-write` / `disable-signal-dual-write` operator actions. |
| `TRADE_JOURNAL_DB` | Canonical trade-journal SQLite path (live VM: `/data/bot-data/trade_journal.db`). Resolved by the single Python resolver `src.utils.paths.trade_journal_db_path()` (env → `$DATA_DIR/trade_journal.db` → repo-root; never a CWD-relative basename). The `canonical-db-resolver` CI guard forbids re-introducing the old inline `os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"` fallback that seeded the stray duplicate journals. |
| `TRAINER_STORE_DB` | Path to the trainer-store sidecar SQLite (default `$DATA_DIR/trainer_store.db`). Holds trainer/ML lifecycle data ingested from `runtime_logs/trainer_mirror/`; federated into the Data Explorer alongside `trade_journal.db`. Resolved by `src.utils.paths.trainer_store_db_path()`. Read-mostly — ingest writers never touch the money DB. |
| `DIAG_READ_TOKEN` | Bearer for `/api/diag/*` (read-only). Unset → endpoints return 503 |
| `M5_CONSUMER_ENABLED` | Auto-install the M5 backtest consumer in the comms poll loop. Default off; set to `1`/`true` on the VM systemd unit. **Optional research tooling, NOT a required capability** — so default-off is intentional and Prime-Directive-compliant (the no-`*_ENABLED`-gate rule applies to *required* capabilities, not opt-in tooling that adds load to the live trader's poll loop). Explicitly carved out in `docs/audits/env-gate-purge-2026-05-10.md` § exclusions; toggle via the `enable-m5-consumer`/`disable-m5-consumer` system-actions. Operator runbook: `docs/runbooks/strategy-testing.md` |
| `M5_BACKTEST_TIMEOUT_S` | Wall-clock cap per backtest subprocess (default 120s) |
| `BACKTEST_DATA_PATH` | Override the candle CSV the M5 backtest runner reads |
| `VALIDATION_LOG_PATH` | Override the M5 validation NDJSON path (default `runtime_logs/validation.jsonl`) |
| `FLIP_POLICY` | Conflict-resolution behaviour in the intent layer when the desired net side opposes the held position (`src/runtime/intents.py`). **Default `hold` since 2026-05-31** (PR #2451, operator-approved after the 24-cell walk-forward verified PASS — `docs/audits/walkforward-flip-policy-2026-05-30.md`): keep the position; the position-owner's monitor()/SL/TP exits — removes flip-churn. Alternatives: `reverse` (legacy close-and-reopen — the rollback path; set `FLIP_POLICY=reverse` on the VM to revert without a redeploy), `flat` (close, no re-open). Mirrors `scripts/backtest_system.py --flip-policy`. |
| `FLIP_CONFIDENCE_THRESHOLD` | **Confidence-gap override for the hold policy** (Tier-3; `src/runtime/intents.py::resolve_flip_confidence_threshold`). Default `0.0` — **disabled, hold-policy behaviour is unchanged**. When set to a positive float (e.g. `0.15`), the `hold` policy may be overridden for a signal whose confidence exceeds the existing position's entry confidence by at least this gap (`new_conf − existing_conf ≥ threshold`). The flip is allowed only when BOTH this gap AND the age gate (`FLIP_MIN_POSITION_AGE_HOURS`) are satisfied; either alone is insufficient. Existing confidence is read from `trade_journal.db` via `get_existing_position_info` (fail-permissive: a read failure keeps the hold, never flips). Logged at INFO with `hold_confidence_override` reason prefix in the delta. **Requires operator approval (Tier-3 order-routing change) before deploying.** |
| `FLIP_MIN_POSITION_AGE_HOURS` | **Minimum position age for the confidence-gap flip override** (Tier-3; `src/runtime/intents.py::resolve_flip_min_position_age_hours`). Default `0.0` — no age requirement (the gap alone is sufficient when `FLIP_CONFIDENCE_THRESHOLD > 0`). When set (e.g. `4.0`), the confidence override is suppressed for positions younger than this many hours, protecting fresh trades from being reversed by the next signal. Works in tandem with `FLIP_CONFIDENCE_THRESHOLD`: both must pass. Age is derived from `trades.created_at` (epoch-ms or ISO string); if unparseable, treated as 0h (fail-permissive — never strands a genuine old position). |
| `REGIME_BAR_SCORING_DISABLED` | Kill-switch for the **per-bar regime scoring** path (S-MLOPT-S13 / M14 Phase 3.1, `src/runtime/regime_bar_scoring.py`). Default off → on: each tick `run_pipeline` scores every `shadow`-stage regime head on its own `(symbol,timeframe)` bar cadence (independent of any actionable signal), writing to `runtime_logs/shadow_predictions.jsonl` so the strong regime heads (1h/MES) accrue an order-influencing track record (`MB-20260529-001`). **Observe-only** — only `ShadowPredictor.predict`, never the order path; deduped to one record per closed bar. **Per-tick cost** is bounded by predictor grouping (one fetch per `(symbol, timeframe)` group, not per head) + a wall-clock fetch gate (`_BAR_SECONDS` − 30s buffer, so a 1h head is fetched ~1×/hour, a 5m head ~1×/5min) — the `MB-20260609-001` fix after the 2026-06-09 CPU wedge. Set truthy on the VM to disable without a redeploy. |
| `REGIME_BAR_SCORING_BUDGET_S` | Per-tick **wall-clock budget** (seconds, default `6.0`; `0` = unlimited) for one `emit_regime_bar_predictions` call (`src/runtime/regime_bar_scoring.py`). The fetch-gate + dedup caches are per-process and **empty on a fresh restart**, so without a budget the FIRST tick after a restart fetches every `(symbol,timeframe)` group (incl. blocking IBKR fetches for MES) AND scores every shadow head in one synchronous mega-tick — pegging the 2-core live VM and freezing the heartbeat (the `BL-20260609-001` 2026-06-10 **cold-start** wedge, distinct from the steady-state `MB-20260609-001` fix). The budget caps how long one call may run; once exceeded, remaining **whole** groups are deferred to the next tick (their fetch gate stays un-armed + heads stay unseen, so each is picked up intact later), spreading the cold-start burst across ticks instead of stalling the loop. |
| `REGIME_ROUTER_DISABLED` | **Kill-switch for the regime hard gate** (`src/runtime/intents.py::_regime_router_active`). The router is **BASELINE-ON** since the Design-A vol-gate go-live (2026-06-28): a *required* live order-routing capability must not sit behind a default-off `*_ENABLED` flag (Prime Directive) — if such a var were dropped on a redeploy/VM-migration the gate would silently stop enforcing and the money-losing `trend_vol` OFF-cells would trade again (the netting-guard / Ampere failure class). By default `Coordinator.aggregate_intents` drops every OFF-cell candidate intent (per `config/regime_policy.yaml`) BEFORE the reinforcement / conflict-resolution logic runs and emits a `regime_hard_gate` audit row with `enforced:true`; the `regime_shadow_gate` row is **not** emitted on the same tick, so the audit log cleanly partitions "would have gated" from "did gate" by event name. Set `REGIME_ROUTER_DISABLED` truthy → off (shadow-log only, `enforced:false`) — the sanctioned rollback (one env flip + restart, no redeploy). **Legacy:** a leftover *explicit* falsy `REGIME_ROUTER_ENABLED` (`0`/`false`) still disables (honoured so a VM mid-migration with the old var set isn't surprised); the live VM's `REGIME_ROUTER_ENABLED=true` is now redundant (still on, harmless). The backtest harness sets `REGIME_ROUTER_DISABLED=1` on any non-`--regime-router on` run so the A/B baseline arm stays shadow-only. Fail-permissive on any policy load / verdict exception (keeps the intent). |
| `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` | Kill-switch for the **per-signal account-context snapshot writer** (S-MLOPT-S12 Part B / M14 Phase 2.4, `src/units/accounts/context_snapshot.py` + `src/core/coordinator.py::_capture_account_context_snapshots`). Default off → on: `Coordinator.multi_account_execute` writes one row per `(order_package_id, eligible_account)` into `trade_journal.db::account_context_snapshots` BEFORE the per-account RiskManager runs — capturing equity, daily PnL, daily equity-high, drawdown%, and open-trade count as they stood pre-decision. **Observe-only** — the snapshot feeds the optional `include_snapshots=True` LEFT JOIN in the `account_context` family (closes `MB-20260604-003`); the trader's order flow never reads back from this table. Best-effort writer (swallows all exceptions); set truthy on the VM to disable without a redeploy. |
| `CROSS_ASSET_LIVE_DISABLED` | Kill-switch for the **live cross-asset peer-feature computation** (S-CROSS-ASSET-PROBE D2a, `src/runtime/cross_asset_live.py`). Default off → on: the per-bar regime scorer (`regime_bar_scoring.py`) computes the `xa_*` peer-asset feature block (peers per `config/cross_asset.yaml`, e.g. `ETHUSDT: [BTCUSDT, SOLUSDT]`) at score time for any cross-asset regime head (one whose feature list has `xa_*` cols), reusing the offline pure fns so live==train. **Observe-only** — only conditions a shadow-stage head's features (→ `shadow_predictions.jsonl`); never the order path. Peers ride the target's existing gated fetch cadence (no new fetch rate); fail-permissive (peer error → xa cols left missing/NaN, the head degrades, never a fabricated zero vector). Set truthy on the VM to disable without a redeploy (the peer config then resolves empty → the feature merge is a no-op). The eventual `c_reg` conviction contribution (D2b) is operator/backtest-gated. |
| `REGIME_ML_VERDICT_MODE` / `ML_VOL_VERDICT_THRESHOLD` | **Design-A regime-router ML vol-verdict** (`off` (default) / `shadow` / `use`; `src/runtime/regime/ml_vol_verdict.py` + `intents.py`, PR #4748, 2026-06-27). Drives the `vol_regime` axis from the **advisory** regime head's `P(volatile)` (thresholded at `ML_VOL_VERDICT_THRESHOLD`, default `0.5`) instead of the frozen-edge `vol_detector`. `off`→unchanged; `shadow`→emit a `regime_ml_vol_shadow` agreement audit row (frozen-vs-ML), decision unchanged; `use`→**substitute the advisory head's ML vol label into the gate DECISION** via `intents._decision_vol_regime` (actually wired 2026-06-28; before that `use` was a documented placeholder that still used the frozen label). **For `use` to change a real-money outcome ALL three must hold:** (a) an OFF cell exists for the `(trend, vol)` pair (`trend_vol` cells ARE authored — merged #4868); (b) the gated strategy's **SYMBOL** has an advisory regime head — resolution is **per-SYMBOL** (`ml_vol_regime_for_symbol`), NOT per-`(symbol, timeframe)`: BTC has the 15m advisory head (`btc-regime-15m-lgbm-v2`), so **every** BTC cell — incl. `trend_donchian` (1h) + `squeeze_breakout_4h` (4h) — resolves the ML label (confirmed live), **not** frozen; a symbol with no advisory head resolves `unknown`→frozen (permissive); and (c) the hard gate is active (baseline-on; kill-switch `REGIME_ROUTER_DISABLED`). Fail-permissive (ML unknown / no head / exception → keep the frozen label, never strands a signal). The 4-arm A/B (`docs/research/A-vol-gating-AB-evidence-2026-06-27.md`) showed the ML label beats the frozen label decisively. **Tier-3** (order-routing-affecting); `use` + the threshold are operator-gated, walk-forward-gated. **BTC real-money enforce is LIVE** (2026-06-28; the 15m head covers all BTC cells per-symbol). ETH/SOL cells (when authored) await promoting their own 15m heads shadow→advisory (`MB-20260628-VOLGATE-GOLIVE`). |
| `CONVICTION_SIZING_MODE` / `CONVICTION_SIZING_DIRECTION` / `CONVICTION_SIZING_ACCOUNTS` | **Design-B conviction-driven sizing apply path** (`off` (default) / `annotate` / `apply`; `src/runtime/conviction_sizing.py::apply_conviction_sizing` + `coordinator.py`, PR #4748). A NEW apply path (distinct from the flagless annotator soak), gating a real reductive/symmetric size influence — same role `NEWS_INFLUENCE_MODE` plays (passes `env-gate-guard` as a `*_MODE`, not the previously-rejected `*_ENABLED`). `off`→byte-for-byte unchanged. `DIRECTION ∈ {reductive (default), symmetric}`; `ACCOUNTS` allowlist (empty=all). **The backtest A/B FAILED the gate for symmetric c_strat-only sizing (4.5× worse maxDD) → stays at `off`/annotate.** Tier-3; `apply` is operator-gated + backtest-gated. |
| `POSITION_NETTING_GUARD_ENABLED` | **Removed 2026-06-17 — the netting guard is now BASELINE (unconditional).** It was a default-OFF kill-switch over a *correctness* fix (per-trade=per-position: monocle no-pyramiding via `src/core/coordinator.py::multi_account_execute` + `positions.py::has_open_trade_for_strategy`; reconciler 2-observation close-confirm via `order_monitor.py::_reconcile_open_trades`). Per the Prime Directive a required capability must not sit behind a default-off flag — and that gate let the fix silently regress when the 2026-06-14 Ampere migration dropped the `.env` var (paper netting artifacts reappeared 2026-06-15, real-money `bybit_2` exposed). Now always-on (`positions.py::position_netting_guard_active_for` returns True unconditionally); a leftover value in `.env` is **ignored**. Same class as the removed `NAKED_POSITION_AUTOPROTECT` / `MONITOR_RECONCILE_ENABLED` gates. Still journals a `reentry_suppressed_netting_guard:<action>` rejection row when it suppresses a netted add. |
| `POSITION_NETTING_GUARD_ACCOUNTS` | **Removed 2026-06-17** alongside `POSITION_NETTING_GUARD_ENABLED` — the guard is baseline on every account (a no-op where it can't apply, e.g. brokers that attach SL/TP atomically and never net same-direction adds), so there is no scope to narrow. A leftover value in `.env` is **ignored**. |
| `RECONCILER_CLOSE_CONFIRM_SECONDS` | Tuning knob (default `60`, clamped `>= 0`) for the **2-observation close-confirm** shared by TWO reconciler close paths: (a) the **netting-guard** half (`_reconcile_open_trades`, now unconditional — the netting guard is baseline as of 2026-06-17) — min seconds a filled trade must read net-flat across ≥2 observations before it closes; and (b) the **reverse reconciler's orphan close-on-disappear** (`_reconcile_orphan_exchange_positions`, BL-20260614-ORPHANBLIP) — an `orphan_adopt` row that reads absent from the exchange snapshot must stay absent across ≥2 observations this many seconds apart before it closes, so a logged-out IB Gateway's empty-portfolio blip can't close + re-orphan it. **(b) is always on** (the monitor reconciler / self-heal runs unconditionally on every tick — the `MONITOR_RECONCILE_ENABLED` gate was removed 2026-06-15, BL-20260615-MGCNAKED — and (b) is NOT gated by the netting guard either — baseline correctness, not a feature flag); this knob only tunes the window. `0` keeps the extra-grace-tick requirement (a second confirming observation) with no added time wait. Read at call time (next-tick effect). Sibling of `RECONCILER_GRACE_SECONDS`. |
| `RECONCILER_READOPT_GUARD_SECONDS` | Window (seconds, default `300`, clamped `>= 0`; `0` disables) for the **re-adopt flap guard** in the reverse reconciler (`_reconcile_orphan_exchange_positions`, BL-20260618-RECONCILE-DUP). When an IB gateway flaps during the broker reset window (logged-out → empty portfolio → back), or the re-attached strategy's monitor closes the DB row at an `sl_cross` on a position the broker never actually closed, the still-present exchange position would be **re-adopted next pass** — looping N times into N phantom `adopted_orphan` trades (one MGC position became 18 closed trades, −$20,127). The guard refuses to re-adopt a `(account, symbol, direction)` whose `adopted_orphan` row (`setup_type='adopted_orphan'`, covers both the bare and strategy-reattached paths) closed within this window — a just-closed adopted orphan that reappears is a flap, not a new position; it's suppressed + alerted (`detect_only`) instead, the real exchange position staying operator-/SL-protected. Fail-open (a read error never blocks a genuine adoption). Read at call time (next-tick effect). Sibling of `RECONCILER_CLOSE_CONFIRM_SECONDS` (the close-side confirm). |
| `RECONCILER_SNAPSHOT_MIN_FILL_AGE_S` | **Fresh-fill grace** (seconds, default `300`, clamped `>= 0`; `0` disables) for the P3b position-snapshot reconciler (`_reconcile_orphan_exchange_positions`, BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE). On an integration without a per-order status reader (alpaca/oanda), a just-placed bracket-MARKET order can take minutes to fill AND propagate to the open-positions snapshot — during that window the position reads **absent** yet is NOT flat (it's pending fill). Without a minimum age the 2-observation confirm alone false-closes it: the IWM/alpaca_paper trade 2771 was closed `exchange_flat_reconciled` ~2.5 min after open, then the SAME still-open position was re-adopted as an `adopted_orphan` 2 min later (a close→re-adopt flap that also left a phantom realised PnL). The gate skips a **strategy-attributed** row younger than this (the close-on-disappear pass for `adopted_orphan` rows is unaffected — an adopted orphan is by definition already confirmed live on the exchange). Fail-open: an unparseable or non-positive age (clock skew / future-dated) is treated as old enough so a genuinely-stale flat row is never stranded. Read at call time (next-tick effect). Sibling of `RECONCILER_CLOSE_CONFIRM_SECONDS` / `RECONCILER_READOPT_GUARD_SECONDS`. Paired hardening: `AlpacaClient.positions()` / `OandaClient.positions()` now return `None` (not `[]`) on a read failure, and `account_open_positions`'s alpaca branch gates an empty snapshot on a verified-live `balance()` before trusting `[]` as flat — mirroring the IB `net_liquidation` guard. |
| `OPTIONS_LIFECYCLE_LOOKBACK_DAYS` | Lookback window (days, default `4`) the **options-lifecycle reconciler** (`order_monitor._reconcile_options_expiry_and_assignment`, Alpaca options Slice-4) passes as the `after=` bound when polling `/v2/account/activities` for option expiration/assignment/exercise events. Scoped to options-expressing accounts (`account_expresses_options` truthy); closes a row only when a broker-confirmed lifecycle event is seen AND the underlying holds no remaining open option position (never on mere position-absence — the anti-incident guard). Realised PnL = close-side activity cash − open debit; those rows are deferred in `_sweep_local_pnl_for_unpriced` so the equity formula never prices them. Read at call time (next-tick effect). |
| `IB_FETCH_TIMEOUT_S` | Hard cap (seconds, default `8.0`) on IB market-data `reqHistoricalData` in `src/exchange/ib_connector.py`. A logged-out Gateway accepts the socket yet never returns bars; without a bound that hangs the whole pipeline tick (incl. Bybit) and starves the liveness heartbeat (restart-loop incident, 2026-06-05, PR #2814). |
| `IB_PROBE_TIMEOUT_S` | Hard cap (seconds, default `5.0`) on the post-connect **liveness probe** in `IBClient.connect()` (`src/units/accounts/ib_client.py`, PR #2827). A `reqCurrentTime` round-trip verifies the IB session is actually usable (a socket-accept is not proof); on timeout `connect()` raises `IBConnectionError` and trips the circuit breaker so the dead gateway can't block the trader loop. **Set `<= 0` to SKIP the probe entirely** (2026-06-10, gateway-isolation): over the cross-host socat-relayed gateway VM, `reqCurrentTime` does not resolve on the persistent loop even though the connection is healthy (logs on, data farms OK, sync completes, read path works), so the probe false-trips the breaker and blocks MES. With the gateway on its own VM it can no longer starve the trader's CPU, and `IB_FETCH_TIMEOUT_S` still bounds each fetch, so skipping the probe is the sanctioned escape hatch for the isolated topology. Default (`5`) keeps the probe ON for the same-box/loopback case. |
| `IB_BREAKER_COOLDOWN_S` | Window (seconds, default `120.0`) the `IBClient.connect()` **circuit breaker** stays open after a probe/connect failure — subsequent connect attempts fast-fail without touching the socket until it elapses, then retry (auto-recovers when the Gateway comes back). Keeps a wedged IB Gateway fully isolated from Bybit/BTCUSDT (PR #2827). |
| `IB_PLACE_CONFIRM_S` | Bounded **post-place rejection window** (seconds, default `3.0`; `<= 0` restores the legacy fire-and-forget) on `IBClient.place` (PR #3406, BL-20260611-001). `placeOrder` is async — IBKR's accept/reject lands on the event loop AFTER the call, so fire-and-forget reported success even when IBKR rejected the order outright (the 3.643-fractional-contract MHG order, trade #2531: journal row stayed open, watchdog orphaned it 30 min later). `place()` now pumps the loop up to this bound and surfaces an immediately-rejected/cancelled parent as a journaled failure; an order still pending at the deadline is treated as accepted so the tick never stalls past the bound. Sibling fix in `RiskManager.position_size`: `market_type: futures` accounts size in **whole contracts** (sub-1-contract = per-trade refusal) regardless of configured `qty_precision`/`min_qty`. The equity analogue (BL-20260622-ALPACA-FRACTIONAL-SIZE): integrations in `risk.WHOLE_UNIT_QTY_EXCHANGES` (today `{alpaca}` — bracket orders reject fractional shares) size in **whole shares** via the `whole_units` flag `position_size` resolves from the account's exchange (`requires_whole_unit_qty`), since the `RiskManager` is built from only the `risk` sub-block and never sees the exchange. |
| `IB_CLOSE_CONFIRM_S` | Bounded **post-place flatten-confirmation window** (seconds, default `6.0`; `<= 0` restores the legacy accept-is-success behaviour) on `IBClient.close` (BL-20260624-MHG-CLOSE-CONFIRM). The close-side analogue of `IB_PLACE_CONFIRM_S`, but stricter: for an OPEN, "not rejected" is enough (a non-filling open just means no position); for a CLOSE, an *accepted-but-unfilled* opposing market order leaves a **real position open** while the monitor's exchange-first close path marks the DB row closed (`sl_cross`) — the position is then orphaned and, because `IBClient.close` Step 1 already cancelled its protective bracket, left **naked** until a later reconcile re-adopts it. That was the perpetual MHG/ib_paper flap: adopt → `sl_cross` "close" that never flattened → re-orphan (within the `RECONCILER_READOPT_GUARD_SECONDS` window it surfaced as the `detect_only` "re-adopt suppressed" alert). `close()` now re-reads the live IB position after placing the opposing order and requires it to actually reach flat within this window; if it doesn't, it returns `retCode 1` so `close_open_position` → the monitor leaves the DB row **open**, naked-autoprotect re-arms a bracket next tick, and the close is retried — i.e. "DB closed" always means "broker confirmed flat". A position-read failure mid-poll is NOT treated as flat (keeps polling to the deadline). Sibling of `IB_PLACE_CONFIRM_S`. |
| `IB_CLOSE_RETRY_COOLDOWN_S` | **Close-retry cooldown** (seconds, default `300`, clamped `>= 0`; `0` disables) for the monitor's full-close path (`order_monitor._apply_update`, BL-20260624-MHG-CLOSE-CONFIRM follow-up). Once `IBClient.close` returns the *not-confirmed-flat* signal (the position was accepted-but-unfilled — a venue that can't fill right now, e.g. market closed for the contract / gateway mid-reset), re-attempting the active close *every tick* would cancel the re-armed protective bracket (`close()` Step 1) and place another non-filling order — churn that leaves the position briefly naked each tick and keeps cancelling the very stop that would flatten it when the venue reopens. While within this window the monitor **defers the active close** for a `(account, symbol, direction)` and leaves the bracket armed to do the job; the marker is set on an unconfirmed close and **cleared on a confirmed one**. Scoped to the `not confirmed flat` error only — a transient/other close failure still retries next tick. In-process (a restart re-arms from scratch — fail-safe, never closes early). Read at call time. Sibling of `IB_CLOSE_CONFIRM_S`. |
| `IB_GATEWAY_CPUS` / `IB_GATEWAY_MEMORY` | Hard resource caps the IB-Gateway **Docker container** is created with (`scripts/install_ib_gateway_docker.sh`, defaults `0.75` CPU / `1500m` mem; `--memory-swap` pinned to `--memory` so the container can't swap). The Gateway is a heavy Java GUI app under Xvfb; an unauthenticated re-login loop during IBKR's reset window can spin it hot. **Historically** (pre-2026-06-10) the gateway shared the 1 GB / 2-vCPU live micro with the trader, and that hot-spin starved the trader's single-threaded main loop (loadavg ~10 → heartbeat froze → ~25-min wedge, the 2026-06-10 cascade) — which is why the gateway was **isolated onto its own dedicated Ampere VM** (gateway-isolation, Plan B; see § "VM authority split" and `docs/runbooks/ib-integration.md`). The cap now applies on that **gateway VM** (1 OCPU / 6 GB) as a defensive bound so the container can't peg its own box during a churn; it no longer competes with the trader (different VMs). `docker restart` preserves these flags, so the daily reset path stays capped too. (The trader micro's own contention — trader vs web-api + sidecars — is handled separately by `CPUWeight`/`Nice` on `ict-trader-live.service`.) Apply to an already-running container without a restart via `docker update --cpus=<n> --memory=<m> ib-gateway`; new containers get the cap from the script. |
| `NEWS_ENABLED` | **Removed 2026-06-10** — the legacy separate enable gate is gone (it was an "on by omission" footgun: code-default `true`). Activation is now **source-driven** (see `NEWS_SOURCE`); there is no on/off flag, and a leftover `NEWS_ENABLED` value in the environment is **ignored**. Per-symbol queries/keywords live in `config/news_symbols.yaml`; full reference in `docs/news_layer.md`. |
| `NEWS_SOURCE` | Feed backend **and the activation gate** for the **M9 news layer** (`src/news/`): `rss` (free, **keyless**, **real-time** — feeds in `config/news_feeds.yaml`; **always active when selected**) or `newsapi` (default; **active only when `NEWS_API_KEY` is set** — the free tier is ~24h delayed, so prefer `rss`). When the selected source is unusable (newsapi with no key), the layer is a cheap neutral no-op — it never blocks a trade. A live source **can** veto (`pipeline.py:477`), so selecting `rss` / setting a key is the deliberate activation. |
| `NEWS_API_KEY` | NewsAPI key — required only when `NEWS_SOURCE=newsapi`. Unused for `rss`. |
| `NEWS_VETO_ENABLED` | **Default `true` (on-by-omission) — a LIVE trade-blocking gate.** The news veto (`src/news/news_score.py::_get_veto_enabled`, checked in `pipeline.py` before `multi_account_execute`) blocks the signal for **every account incl. real money** when an item has `sentiment < NEWS_VETO_SENTIMENT_THRESHOLD` (−0.6) AND `impact > NEWS_VETO_IMPACT_THRESHOLD` (0.7). Inert when the news layer is inactive (default `NEWS_SOURCE=newsapi` + no key → no articles → no veto), but **armed whenever the source is active** (`NEWS_SOURCE=rss`) — so activating the source for soak data ALSO arms the veto (operator-confirmed intended, 2026-06-28: "selecting rss is the deliberate activation"). It is a per-trade refusal with a Telegram ping (Prime-Directive-correct shape), not an account-mode flip. Set `false`/`0`/`no` to disable. Tier-3 to change on the VM (a live trade-blocking condition). Distinct from the influence **sizing** half (`NEWS_INFLUENCE_MODE`, default `off` — observe-until-opt-in). |
| `NEWS_INFLUENCE_MODE` | Gate for the **graduated news-influence sizing** hook (`src/runtime/news_sizing.py`, applied in `Coordinator.multi_account_execute` after the advisory downsize). `off` (default) / `annotate` (no resize) / `downsize`. **Reductive-only** — shrinks the per-account qty toward `NEWS_INFLUENCE_SIZE_FLOOR` when the news (and any imminent event) opposes the trade direction, never enlarges. Inert when off or when the news layer isn't active. Design: `docs/news-influence-DESIGN.md`. Tier-3. |
| `NEWS_INFLUENCE_SIZE_FLOOR` / `NEWS_INFLUENCE_OPPOSE_THRESHOLD` / `NEWS_INFLUENCE_EVENT_RISK_WEIGHT` | Tuning knobs for the news-influence factor (defaults `0.5` / `0.05` / `0.5`): the smallest fraction a downsize may leave, the opposition dead-band, and how strongly an (injected) `event_risk` downsizes. Only consulted when `NEWS_INFLUENCE_MODE=downsize`. |
| `PROP_EXPIRY_PROMPT_SECONDS` / `PROP_EXPIRY_PROMPT_MAX_AGE_HOURS` | Knobs for the **prop ticket-expiry Yes/No prompt** (`src/prop/prop_expiry_prompt.py`, called once per trader tick from `src/main.py`). When a prop ticket passes its `valid_until` with no report-back, the bot asks the operator on the prop bot — inline **Yes/No** buttons (handled in `src/bot/claude_bridge.py` `propexp:*`): **No** → the ticket is logged `expired`; **Yes** → it moves to `awaiting_report` and the operator gets the `REPORT_PROMPT` to paste the fill (linked via `match_fill_to_ticket`, which now accepts `expiry_prompted`/`awaiting_report`). Lifecycle: `emitted → expiry_prompted → expired \| awaiting_report → filled/closed`. **Baseline, no enable gate** (Prime Directive); idempotent via the status flip (prompted exactly once, only after a confirmed send — no state file). `PROP_EXPIRY_PROMPT_SECONDS <= 0` pauses prompting; `PROP_EXPIRY_PROMPT_MAX_AGE_HOURS` (default `12`) bounds how stale a ticket may be before the bot stops asking, so a historical backlog can't spam on first deploy. Design: `docs/integrations/prop-telegram-inbound-DESIGN.md` § "Expired-ticket Yes/No prompt". |
| `PROP_MONITOR_PULSE_SECONDS` | Cadence (seconds, default `900` = 15 min) of the **prop monitoring pulse** — a periodic "still monitoring" heartbeat per OPEN prop trade (`src/prop/prop_monitor_pulse.py`, called once per trader tick from `src/main.py`). Because the prop account is a manual bridge with no broker feed, the per-tick `order_monitor` never sees prop positions; this pulse reassures the operator the system is still actively tracking the trade between report-backs ("still monitoring · no change"), without replacing the real-time `prop_fill`/`prop_closed` events (new event kind `prop_monitor`, Telegram via the prop bot + FCM). A newly-opened prop position pulses immediately, then every interval. **Baseline (no enable gate)** — the only knob is this cadence; set `<= 0` to pause pulses without a redeploy. Per-position cadence state is `runtime_logs/prop_monitor_pulse.json` (pruned to live positions). |
| `ACCOUNT_REACHABILITY_CHECK_SECONDS` / `ACCOUNT_DOWN_ALERT_THRESHOLD` / `ACCOUNT_DOWN_ALERT_SKIP` | Knobs for the **broker-account-down latched alert** (`src/runtime/account_reachability_alert.py`, called once per trader tick from `src/main.py`; BL-20260629-ACCOUNT-DOWN-ALERT). A supposed-to-be-live broker account reading **unreachable** (IB gateway logged out, exchange API 401-ing, creds rotated out) now fires its OWN loud, latched operator alert — one `🔴 [ALERT] DOWN` on a confirmed cross-into-down, one `🟢 [OK]` on recovery — instead of going unflagged (the IB gateway was dark across reviews and surfaced only quietly). Reachability uses the SAME primitive the reverse reconciler calls each tick (`account_open_positions`: `None` ⇒ down, list ⇒ up). Scope is **all declared-live, non-shelved accounts** — `mode: live` on a probeable exchange (`bybit`/`interactive_brokers`/`alpaca`/`oanda`), which excludes the dry/shelved `ib_live`/`oanda_practice` and the API-less `breakout_1`. `ACCOUNT_REACHABILITY_CHECK_SECONDS` (default `600` = 10 min) is the probe cadence — **on by default** (it's observability, not a trade gate; the same shape as `PROP_MONITOR_PULSE_SECONDS`), `<= 0` pauses it without a redeploy. `ACCOUNT_DOWN_ALERT_THRESHOLD` (default `2`) is how many consecutive down reads confirm an outage before the DOWN ping fires (so a transient blip never pings). `ACCOUNT_DOWN_ALERT_SKIP` is a CSV of account-ids to skip (escape hatch for a live account intentionally expected-down). The alert fires one Telegram + one typed `WARNING` FCM push (the loud Warning channel). Latch state is `runtime_logs/account_reachability_alert_state.json` (persists across restarts so the consecutive-down counter survives a bounce); `account_reachability_alert.down_accounts()` exposes the currently-latched-down set for the health-review / system-review skills, which now treat any down live account as a **mandatory** standalone flag. |

## Diagnostic API (S-051)

Token-gated read-only surface for PM-side Claude / operator scripts. All
endpoints return 503 if `DIAG_READ_TOKEN` is unset, 401 on bad bearer.

| Endpoint | Returns |
|----------|---------|
| `GET /api/diag/snapshot?limit=N` | bundle: heartbeat, status, audit tail, order_packages, trades, vm_health, service states |
| `GET /api/diag/audit?limit=N` | tail of `runtime_logs/signal_audit.jsonl` |
| `GET /api/diag/journal?table={order_packages\|trades}&limit=N` | read-only SELECT |
| `GET /api/diag/audit_query?since=&until=&event=&strategy=&symbol=&side=&limit=&offset=` | **historical, time/event-filtered audit read** backed by the `trade_journal.db::signals` dual-write — reaches arbitrary history (unlike `/audit` + `/log_file?name=audit`, which tail only the last `_MAX_LIMIT`=1000 lines ≈ ~15 min of a busy day). `since`/`until` are ISO-8601 (`Z`/`+00:00`/naive all accepted, treated as UTC); `event` matches the audit event type (`regime_shadow_gate`, `vwap_eval`, …) inside the `meta` JSON; `strategy`/`symbol`/`side` exact-match typed columns; `offset` pages. Rows are newest-first and carry the typed columns merged with the full `meta` payload (`regime`/`adx_14`/`enforced`/`cell`/…). `dual_write_present:false` + `error:signals_table_absent` ⇒ the dual-write hasn't populated `signals` (check `SIGNAL_DUAL_WRITE_DISABLED`). |
| `GET /api/diag/status` | heartbeat + status.json + vm_health |
| `GET /api/diag/services` | `systemctl is-active` per allowlisted unit |
| `GET /api/diag/journalctl?unit=<name>&lines=N&since=<iso>&until=<iso>` | systemd journal tail; `since`/`until` accept strict ISO-8601 (`2026-05-10T21:13:00Z`) and forward to `journalctl --since`/`--until` for historical-window pulls (PR #821, FU-20260511-001) |
| `GET /api/diag/log_file?name={audit\|status\|heartbeat\|bot_log\|advisory_decisions\|shadow_predictions\|shadow_predictions_backfill\|ibkr_mes_pull\|news_decisions\|conviction_sizing\|conviction_arbitration\|exit_ladder_soak\|allocator_soak}&lines=N` | log file tail. `conviction_sizing` (P2) / `conviction_arbitration` (P3) are the observe-only unified-confidence soak logs — the would-be conviction size / arbitration vs the actual, never order-influencing; tail them to verify the soak is accruing before P4/P5 graduate it. `exit_ladder_soak` is the ExitPlan laddered-vs-single-target soak (dynamic-take-profit consistency P3 — also surfaced publicly at `/api/bot/exit-ladder/soak`). `allocator_soak` is the M18 portfolio capital-allocator soak (would-pick vs routed + regret on ≥2-candidate ticks — also at `/api/bot/allocator/soak`). |
| `GET /api/diag/db_info` | resolved `trade_journal.db` path + inode + table list + per-table row counts (and per-table error string when a SELECT raises). Companion to `/journal`; a trader-vs-web-api inode mismatch on the same logical path is the canonical signature of the 2026-05-09 `order_packages returns []` mystery. |
| `GET /api/diag/version` | `{git_sha, captured_at}` — git SHA of the running web-api process (same resolver as `runtime_status.json::git_sha`). Used by `scripts/deploy_pull_restart.sh` to confirm a post-deploy restart actually rolled the code forward; `git_sha:"unknown"` on sandbox/dev hosts is a soft failure. |
| `GET /api/diag/shadow_stats?model_id=X&stage=X&since=ISO` | token-gated diag mirror of `GET /api/bot/shadow/stats` (which is not under `/api/diag/`, so the relay can't reach it) — per-`(model_id, stage)` aggregate shadow-prediction stats `{log_present, log_path, records[], count}` (FU-20260516-001). 503 if `ml.shadow.inspector` import fails. |
| `GET /api/diag/exchange_positions?account_id=X` | read-only **exchange-side** open positions per account — the BROKER's truth, not the journal (added 2026-06-19). Per-account `positions` is `null` (could-not-read), `[]` (genuinely flat), or `[{symbol, side, size, entry_price, unrealised_pnl}, …]`. Opens a brief read-only client per account via `account_open_positions` (the same primitive the live reconciler calls); places NO order. Lets a web/PM session confirm whether a journal orphan actually exists on the broker before cleanup. |
| `GET /api/diag/broker_account_status?account_id=X` | read-only **broker account authorization** flags per account — answers "can this account actually place an order?", distinct from whether its creds merely authenticate for reads (added 2026-07-01, BL-20260701-ALPACA-STATUS-VISIBILITY). Populated for **Alpaca** accounts: `status_flags` = `{status, trading_blocked, account_blocked, trade_suspended_by_user, transfers_blocked, shorting_enabled, crypto_status, currency}` from `GET /v2/account` via `AlpacaClient.account_status()`; `null` + `error` on a read failure. Other exchanges return `supported:false`. The triage path for a "reads OK (`/accounts/balances` `api_ok:true`) but orders 401 `unauthorized`" split — the balance snapshot only proves the account is *readable*, not *trade-enabled*. Opens a brief read-only client per Alpaca account (the executor's own factory, resolving the account's live key+secret pair); places NO order. |

See `docs/claude/vm-operator-mode.md` § 9 for the trust contract.

### Reaching `/api/diag/*` from a PM-side / web session

Two transports, identical JSON — **try direct, fall back to the relay.**

1. **Direct HTTP (preferred, when configured).** If the session's cloud
   environment sets `DIAG_BASE_URL` + `DIAG_READ_TOKEN` and Network
   access permits egress, fetch in one shot:
   `scripts/ops/diag_fetch.sh '<path>'` (exit `0` = JSON; exit `3` =
   fall back). These vars cover the **live VM only** — there is no
   `/api/diag/*` surface on the trainer VM.
2. **GitHub-issue relay (fallback).** Open an issue titled
   `[diag-request] <path>` with label `vm-diag-request`; the
   `vm-diag-snapshot` workflow runs the fetch over SSH + curl, posts
   the JSON back as an issue comment, and closes the issue.

Full flow, the direct/relay contract, token management
(`get-diag-token` / `set-diag-token`), and failure modes are in
`docs/claude/diag-relay.md`. The bearer lives in repo secrets
(`VM_SSH_KEY`, `DIAG_READ_TOKEN`) and on the VM; deliver it for a cloud
env var via the `get-diag-token` workflow, not by hand-copying.

**Trainer VM** has no HTTP diag API — read it via the `trainer-vm-diag`
relay (arbitrary SSH bash, label `trainer-vm-diag-request`). SSH from a
web session is impossible regardless (proxy is HTTP/HTTPS-only), so
trainer access is relay-only.

## PM-side session capabilities (Claude Code on the web)

What the sandbox session can and can't do directly. Future sessions
should not re-derive this — if the contract changes, edit here.

**MCP tools available** — `mcp__github__*` (subset: issue
read/write, PR read/write/merge, file read/create/update, branch
create, secret scanning, **but no `create_label`, no artifact download;
**`run_workflow` 403s** (verified 2026-06-11) while `actions_list` /
`get_job_logs` DO work — run-log read is available since the 2026-06
MCP update**), Google Drive (file search
+ read), Hugging Face (hub search, doc fetch), Bigdata.com (market
data), Gmail (read-only labels).

**The hosted GitHub MCP drops intermittently — DO NOT treat it as an
expired token.** In long-running sessions the `mcp__github__*` server
disconnects and reconnects repeatedly (a single 2026-05-29 session saw
~6 cycles). A call that lands during a drop fails with
`MCP server "github" requires re-authorization (token expired)` — but
this is a **transient, self-healing blip, not a real OAuth expiry**: a
cheap retry (e.g. `get_me`) succeeds seconds later, as verified that
session. **Correct handling:** on that error, wait a few seconds and
retry with backoff (2s/4s/8s/16s) — `ToolSearch "select:mcp__github__get_me"`
then `get_me` is a good liveness probe. Only escalate to the operator
after the failures persist for **several minutes across multiple
retries**. **Never ask the operator to "re-authorize GitHub" on the
first hit** — they cannot trigger an in-session reauth on Claude Code
on the web, and 16h-long monitoring loops are exactly what surface
these drops, so a premature hand-off strands the task on a false alarm.
The underlying connector stability is Anthropic-hosted (not fixable
from this repo); the durable workaround for a VM-data task that must not
depend on GitHub is the **direct diag path** (`DIAG_BASE_URL` +
`DIAG_READ_TOKEN` + `scripts/ops/diag_fetch.sh`), which needs the
environment created at **Full** network access — at the default
**Trusted** level egress to the VM is firewalled and the issue relay is
the only channel.

**Network from inside the session** — governed by the cloud
environment's **Network access** level (None / Trusted / Full /
Custom). At the default **Trusted** level outbound is allowlisted to
package registries + `*.github.com` / `*.anthropic.com` etc., and
arbitrary IPs (incl. the Oracle VM) are firewalled —
`dangerouslyDisableSandbox: true` does **not** help, the egress
restriction is enforced one layer below the Bash sandbox. To reach the
live VM's diag API directly, the environment must be set to **Full**
(or **Custom** allowlisting the host) AND carry the `DIAG_BASE_URL` +
`DIAG_READ_TOKEN` env vars — see "Reaching `/api/diag/*`" above. Note
the security proxy is HTTP/HTTPS-only even at Full, so SSH/raw-TCP to
the VMs never works from a web session; and a raw `http://IP:port` may
still be dropped (point `DIAG_BASE_URL` at an HTTPS hostname if so).
Network-access changes take effect on a **new** session, not the
running one.

**No custom MCP servers.** Claude Code on the web doesn't honour
project `.mcp.json` and can't run `claude mcp add`. To get richer
GitHub powers (workflow_dispatch, run artifacts, label CRUD), the
operator has to either (a) wait for Anthropic to expand the hosted
GitHub MCP, or (b) move the ops session to Claude Code desktop / CLI
and install `github/github-mcp-server`. Until then, the workarounds
below are the contract.

**Workarounds shipped:**

- **VM diag access (read-only)** — issue-driven, see § "Reaching
  `/api/diag/*` from a PM-side / web-sandbox session" above and the
  full doc at `docs/claude/diag-relay.md`.
- **VM operator actions (narrow mutating)** —
  `.github/workflows/system-actions.yml` exposes a fixed
  allowlist (`status-check`, `pull-latest-logs`, `pull-and-deploy`,
  `restart-bot-service`, `reboot-vm`, `set-account-mode`, …).
  Tier-1 actions are autonomous; Tier-2 actions require an operator
  ack first (in-conversation approval is sufficient). Two dispatch
  paths, identical allowlist + audit:
  - `workflow_dispatch` — operator clicks "Run workflow" in the
    Actions UI.
  - **Issue-driven** — open a labelled issue (`system-action`)
    with body `action: <name>\nreason: <text>` (plus `account:` +
    `mode:` lines for `set-account-mode`). Workflow runs, comments
    back, closes the issue. Body parsing rides through env
    (`ISSUE_BODY`), not inline interpolation.

  Full contract: `docs/claude/system-actions.md`. **Account-mode
  flips have one sanctioned wire (`set-account-mode`); strategy
  parameter changes, risk caps, and live order code remain Tier-3
  PRs.**
- **Web-API self-heal (autonomous, single-purpose)** —
  `.github/workflows/vm-web-api-recover.yml` is the issue-driven
  recovery path for `ict-web-api.service`. When the diag relay
  starts returning curl exit 7 (`Failed to connect to 127.0.0.1`),
  the FastAPI process serving `/api/diag/*` is down and Claude is
  blinded. Open a labelled issue (`vm-web-api-recover`) to fire a
  fixed-form `systemctl restart ict-web-api.service` + health
  probe; the workflow comments back and closes. Restart-only, no
  edits, no other unit touched. Wrapper:
  `scripts/ops/restart_web_api.sh`.
- **Prop report-back POST (the diag relay's write counterpart)** —
  `.github/workflows/prop-report.yml` is the issue-driven path to
  `POST /api/bot/prop/report` (the read-only `vm-diag-snapshot` relay
  is GET-only and can't POST). Open a labelled issue (`prop-report`)
  whose **body** is a single JSON object (optionally inside a ```json
  fence — stripped) in one of the two `src/prop/prop_report.py`
  shapes (fill/close, or `kind:"account_status"`); the workflow
  validates it (`jq -e 'type=="object"'`), POSTs it to the VM over
  SSH + curl, and comments the endpoint's JSON response + HTTP status
  back before closing. **Tier 2** (DB write + notification); it
  sources `DASHBOARD_API_TOKEN` from `/etc/ict-trader/web-api.env`
  **on the VM** and sends the bearer header only when set (never
  reaches the runner / run log). The untrusted body rides a base64
  hop, never inline-interpolated. Full flow:
  `docs/claude/diag-relay.md` § "Posting a prop report-back".
- **Repo label creation** — `.github/workflows/bootstrap-labels.yml`
  self-creates the labels other workflows filter on. Edit the
  `LABELS` array in that file and merge; the next push runs the
  sync. No `create_label` MCP needed.
- **Broker-credential propagation (Actions → VM)** —
  `.github/workflows/sync-vm-secrets.yml` is the canonical path for
  mirroring broker-credential Actions secrets to the live trader's
  `.env` (added 2026-06-02). One workflow declares the full known
  set (`REQUIRED_SECRETS` + `OPTIONAL_SECRETS`); adding a new broker
  appends env-var names there. Idempotent — re-running with no
  change is a no-op. Values ride through SSH `SendEnv` and never
  reach run logs. Replaces the per-broker workflow pattern the
  earlier Bybit-only `rotate-account-keys.yml` followed; that
  workflow stays in place as the legacy Bybit path pending a
  separate migration PR.
- **Actions-secret placeholder pre-creation** —
  `.github/workflows/init-actions-secrets.yml` creates empty
  placeholder repo Actions secrets so the operator pastes values
  into pre-existing slots (Settings → Secrets → Update) instead of
  clicking "New repository secret" N times. Idempotent — already-set
  names are skipped, never overwritten. Used by Claude as the first
  step on a new-broker hookup ping. Dispatchable via
  `workflow_dispatch` (UI / Actions API) or via issue label
  `init-actions-secrets` (Claude-driven; PR #2652).
- **Trainer VM full visibility** — `.github/workflows/trainer-vm-diag.yml`
  is the unrestricted SSH relay for the trainer VM. Claude opens a
  `trainer-vm-diag-request`-labelled issue with a `cmd:` block
  (any bash) and the output comes back as an issue comment. No
  operator approval needed — trainer VM is autonomous territory.
  See `docs/claude/trainer-vm-mode.md` § 9 for usage and the
  complete list of what Claude pulls routinely.
- **Workflow dispatch** — there's no general-purpose workaround.
  Workflows that need to be Claude-driven from a session must use
  an `issues.opened` (or `pull_request.opened`) trigger filtered to
  a label. Pattern is the diag relay (`vm-diag-snapshot.yml`),
  `vm-web-api-recover.yml`, `init-actions-secrets.yml`,
  `purge-artifacts.yml` (label `purge-artifacts-now`), and now
  `system-actions.yml` (whose Tier-2 ack is the operator's
  in-conversation approval — Claude carries that approval into the
  issue body).
- **Alpaca account lookups (read-only)** — if the operator has connected
  the official [Alpaca MCP server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
  to a session, it gives fast direct account/portfolio/market-data reads
  (buying power, positions, margin status) without a diag-relay round trip.
  **Its trading tools must never be used from a session touching this
  repo** — they place orders directly against Alpaca's API, bypassing
  `RiskManager.position_size()` (the repo's one sanctioned order path) and
  the journal, which would surface as an un-audited phantom orphan. The
  operator scopes this with `ALPACA_TOOLSETS` to exclude the trading
  category; full writeup, the risk, and the setup contract:
  `docs/claude/alpaca-mcp-server.md`.

## Running Locally
```bash
pip install -r requirements.txt
uvicorn src.web.api.main:app --port 8001 --reload
```

## Important Notes
- `src/web/runtime_status.py` is imported by `src/runtime/pipeline.py` — do NOT delete it
- `heartbeat.txt` mtime is the canonical "is the trader process responsive" signal. Refreshed every `HEARTBEAT_INTERVAL_SECONDS` (default 60 s) from inside `src/main.py`'s sleep loop — so it fires between ticks too, not just at tick completion. A pipeline hang stops the heartbeat (the loop is on the main thread, no daemon) so liveness still reflects pipeline health. Thresholds derived from the same cadence: `< cadence × 3` → running, `< cadence × 10` → paused, else stopped. Helper at `src/runtime/heartbeat.py::heartbeat_label`. Prior history: 2 min threshold (way too tight for a 15-min tick) → 10 min in 2026-05-07 → 18 min (tick × 1.2) on 2026-05-08 → finally cadence-based with 60 s heartbeat the same day, after the tick-coupled basis kept under-counting healthy idleness.
- **External liveness watchdog (`ict-liveness-watchdog.{service,timer}`, 2026-05-11)** is the per-minute dead-man switch on top of the in-process heartbeat. Runs `scripts/check_heartbeat.py` every 60 s; Telegrams `[CRITICAL] Trader heartbeat stale` after 5 min of stale mtime; auto-restarts `ict-trader-live.service` after 8 min total stall (autoheal opt-in via `--auto-restart-after 3`, currently ON). **Restart-loop containment (`--max-restarts 5` / `--cooldown-min 3` / `--restart-startup-grace-seconds 180`, hardened 2026-06-09, BL-20260605-001):** restarts are capped per stall episode (then a one-shot `[CRITICAL] EXHAUSTED` ping + alert-only until the heartbeat recovers, which resets the budget), spaced by a cooldown, and skipped while the trader is inside its post-restart startup grace (so it's never killed mid-first-tick). A restart that fails to *dispatch* (e.g. `systemctl` timing out under CPU saturation — the 2026-06-09 incident, `BL-20260609-001`) does NOT consume an attempt or start the cooldown, so the watchdog retries next check instead of going silent. **Boot-grace (`--boot-grace-seconds 600`, added 2026-05-28):** for the first 10 min after a host boot the watchdog suppresses heartbeat missing/stale alerts AND autoheal (the trader is expected to be starting under systemd) and sends no "recovered" ping when it comes up — so a VM reboot no longer spams `[CRITICAL] heartbeat stale` + `[OK] recovered` on top of the reboot ping; a heartbeat still stale once the window closes alerts as a genuine failure-to-recover (uptime read from `/proc/uptime`, fail-open to "long up" so a real stall is never silently suppressed). Stdlib-only so it works even when the trader's venv is wedged. Full operator runbook: [`docs/runbooks/liveness-watchdog.md`](docs/runbooks/liveness-watchdog.md). Not to be confused with `ict-heartbeat.{service,timer}` which is the once-daily operator status digest at 13:00 UTC (`scripts/daily_heartbeat.py`). **Note (2026-05-12 incident):** the watchdog correctly auto-restarted the trader after the 16h heartbeat-writer silent failure, but the new process retained whatever state was making bybit_2 dry. The Prime Directive (above) addresses the conceptual root cause: no auto-flip code paths should exist. The watchdog's restart behaviour is unchanged — restarting is fine; what was wrong was the flip itself.
- **IB Gateway auto-heal watchdog (`ict-ib-gateway-watchdog.{service,timer}`, 2026-05-28; reactive auto-restart re-armed 2026-06-22, BL-20260622-GATEWAY-MIDDAY-WEDGE)** — runs **on the gateway VM** (auto-enabled only where `/etc/ict-vm-role`==`gateway`; NOT on the trader). It probes `ib_paper` every ~5 min and, after 2 sustained-wedge checks, runs the local `scripts/ops/restart_ib_gateway.sh` `docker restart`. **History:** the reactive restart was disarmed 2026-06-10 (gateway-isolation redesign) in favour of one deterministic daily `docker restart` (`ict-ib-gateway-reset.timer`, 05:30 UTC) — because the reactive churn could starve the box the gateway then SHARED with the trader. That objection is moot now the gateway is isolated, and daily-only left a real gap: a MID-DAY wedge had no recovery until the next 05:30 (observed 2026-06-22 — an open MHG position tripped MONITOR BLIND; recovery needed a manual `vm-ib-gateway-recover`). So the bounded reactive guards are re-armed, with the daily reset kept as belt-and-suspenders. **2026-07-02 update (BL-20260623-002):** the daily reset was retimed 05:30 → **06:05 UTC** — 05:30 was actually *inside* IBKR's own documented ~03:45–05:45 UTC reset window, not after it, so the one deterministic restart the whole design relied on was racing the outage it existed to fix (confirmed recurring 2026-06-23 and 2026-07-02). The watchdog's `ExecStart` now also carries `--suppress-window-utc 03:45-05:45`: a wedge detected inside that window is still logged/alerted but never drives a restart (freezing, not resetting, the streak so it resumes the instant the window closes) — a restart attempted inside the window couldn't succeed anyway and was only burning the `--cooldown-min` budget the *next*, potentially-effective restart needed. The historical description below is kept as record. It is the MES dead-man switch for the *broker session* — distinct from the liveness watchdog above, which guards the *trader process*. Fired `scripts/check_ib_gateway.py` every 5 min (timer `OnBootSec=3min` / `OnUnitActiveSec=5min`); probes `ib_paper` via `ib_connect_check` — a logged-out Gateway still reports `connected=true` but `net_liquidation=None`, so **health = connected AND net_liquidation populated** — and after 2 consecutive wedged checks runs `scripts/ops/restart_ib_gateway.sh` (the same `docker restart` as the manual `vm-ib-gateway-recover` workflow). Guard rails `--restart-after 2 --max-restarts 3 --cooldown-min 20` mean a genuine IBKR lockout can never become a restart loop; once exhausted it alert-only escalates to Telegram. Heals the overnight IBKR-reset wedge that used to leave MES dark for hours pending a manual recover. Full runbook: [`docs/runbooks/ib-integration.md`](docs/runbooks/ib-integration.md) § Auto-heal watchdog; the root-cause investigation (IBC nightly auto-restart unreliable) is health-review backlog `BL-20260527-003`. Queryable on the diag surface (`/api/diag/services` + `/api/diag/journalctl?unit=ict-ib-gateway-watchdog.service`) since it was added to `_CANONICAL_UNITS` (#2192).
- **Naked-position auto-protect is unconditional baseline behaviour (no flag).** Each monitor tick `order_monitor._check_naked_positions` scans open live trades whose SL/TP is missing/non-positive, resolves the levels from the most recent matching order package (`_resolve_protective_levels`, direction + symbol-or-base-futures-root), and re-arms a broker-side GTC OCA bracket via `_attempt_naked_autoprotect` → `IBClient.place_protective`; the reconciler's adopt/re-attach paths do the same via `_rearm_broker_protection_after_recovery`. **IB-only** (Bybit/OANDA/Alpaca attach SL/TP atomically at entry, so a naked orphan can't occur there); non-IB accounts no-op and the trade falls back to a one-shot naked-position alert. A live position with no stop is an unacceptable state the system must always correct — there is **no enable gate** (Prime Directive: no default-off flag in front of a required capability). The earlier `NAKED_POSITION_AUTOPROTECT` toggle was removed 2026-06-15 (BL-20260615-MGCNAKED); a leftover value in `.env` is ignored.
- The old HTMX UI (`web/static/`, `web/templates/`, `src/web/api/routers/ui.py`) has been removed
- The old Streamlit UIs (`src/web/backtest_ui.py`, `src/web/config_ui.py`) have been removed
- The old `cf-worker/` directory was removed (2026-05-12), and the **entire Cloudflare tunnel integration was purged from the repo in the full-system-audit cleanup**: the `ict-cloudflared-tunnel` service unit + drop-in, the four `*_cloudflare_tunnel.sh` scripts, the `*-cloudflare-tunnel` system-actions (+ their tests/allowlist), and the `cloudflare-named-tunnel` runbook are all gone. The Streamlit dashboard makes its upstream call server-side and needs no tunnel. If the `ict-cloudflared-tunnel.service` unit is still installed on the live VM, stop + disable it (`sudo systemctl disable --now ict-cloudflared-tunnel.service`). Historical sprint logs/audit (`S-CFW-*`, `vercel-edge-vs-cf-worker.md`) are kept as the record of why CF was tried and retired.
