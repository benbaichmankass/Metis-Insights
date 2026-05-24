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

1. **[`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)** — how you operate: access, honesty, permission tiers, workflows, session discipline.
2. **[`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)** — system architecture, trade/comms pipeline, contracts.
3. **[`ROADMAP.md`](ROADMAP.md)** — the centralized record: every milestone/sprint, status, and dates.
4. **The current sprint log** under `docs/sprint-logs/`.
5. **Skills** under [`.claude/skills/`](.claude/skills/) — concrete, composable workflows.
6. **This file (`CLAUDE.md`)** — repo orientation + dashboard REST-API reference.
7. **`docs/claude/*` and historical notes** — supporting detail.

When sources disagree, the higher one wins. If a higher doc is silent, defer to
the next. If you find a contradiction, fix it (run the `doc-freshness` skill) —
don't route around it.

### Every session

- **Start:** read [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md) and the latest roadmap/sprint entry. Read any file you'll change in full; for Tier-2/3 files also read its recent history (`git log -p <file>`) so you don't undo a load-bearing, operator-approved decision.
- **End:** run the **`doc-freshness`** skill to confirm no canonical doc now contradicts your changes, and log any minor issue you noticed but didn't fix to the **health-review backlog** (`docs/claude/health-review-backlog.json`) so a future health-review picks it up.
- **Field beats comment:** when a YAML field, config constant, or DB row disagrees with a surrounding comment, docstring, or non-canonical note, the *field* is the truth — fix the stale text, never flip the field on inference. (This caused the PR #1358 incident.)

## Access & autonomy

Everything you need is already wired into the repo:

- **VMs** — the SSH key (`VM_SSH_KEY`) and diag token (`DIAG_READ_TOKEN`) live in Actions secrets. You read both VMs (live trader `158.178.210.252`; trainer `ict-trainer-vm`, `158.178.209.121`) and run tiered changes through GitHub Actions workflows you dispatch yourself — the diag relays for reads, `system-actions` for tiered mutations, and the direct diag API when the session is configured for it. Skills: `diag-data`, `vm-ops`, `git-actions`.
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
entry point `streamlit_app.py`. The Streamlit Python server makes the
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

## Skills (composable workflows)

Concrete workflows live as skills under [`.claude/skills/`](.claude/skills/),
written granularly so you can chain them (retrieve data → inspect a VM →
dispatch an action → review). Prefer a skill over improvising. When you hit a
mistake a workflow would have prevented, **propose a new skill** for it — that
is how this library grows.

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
| `instance-20260414-1555` (`158.178.210.252`) | **Live trader** — runs `ict-trader-live.service`, holds money-at-risk | [`docs/claude/vm-operator-mode.md`](docs/claude/vm-operator-mode.md) | **Restricted.** Tier-1 read autonomous; Tier-2 mutations need operator ack (Telegram `/vm_write` or PM-side issue → `system-actions.yml`); Tier-3 paths (live order code, risk caps, key rotation) are hard-blocked. **Account-mode flips have a sanctioned wire: `set-account-mode` operator action; code paths that flip mode outside that action are Tier-3 violations.** |
| `ict-trainer-vm` (`VM.Standard.A1.Flex`, Ampere A1) | **Training center** — runs the ML lifecycle (datasets, training, registry, eval), no live trade authority of its own | [`docs/claude/trainer-vm-mode.md`](docs/claude/trainer-vm-mode.md) | **Autonomous.** Claude provisions, SSHes, installs, syncs read-only DB from live, runs training cycles, writes the registry up to `live_approved` stage, terminates + re-provisions — all without operator-in-the-loop. |

The separation has two gates (2026-05-19 update; see
`docs/ARCHITECTURE-CANONICAL.md` § Change log for the
shadow-default-flip rollout):

1. **Stage gate** — autonomous-Claude on the trainer VM can write a
   model into the registry up to `live_approved`, but only stages
   in `{advisory, limited_live, live_approved}` ever influence the
   order package. Models at `shadow` log predictions but never
   change order decisions; models at `research_only` / `candidate`
   / `backtest_approved` are refused by the shadow factory.
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
  unit file the live VM consumes. Open the PR, mark it draft,
  ping the operator.
- Never copy production secrets to the trainer.
- Never provision past the OCI Always Free 4-OCPU / 24-GB tenancy
  ceiling. Live trader holds 1 / 6; trainer holds 1 / 6; up to 2 / 12
  remains for side-cars.

When in doubt about scope, default to the **live-VM** rules and ask.

## Project-level skills (`/health-review`)

This repo ships a **project-level Claude Code skill** at
[`.claude/skills/health-review/SKILL.md`](.claude/skills/health-review/SKILL.md).
It is the on-demand entry point to Claude's Layer-2 review routine —
when the operator invokes `/health-review` in chat (or asks for "the
health review" / "the layer-2 review"), Claude **pulls the live
runtime state itself** via the diag relays (`vm-diag-snapshot.yml`
for the live VM, `trainer-vm-diag.yml` for the trainer VM) and emits
a JSON response per
[`comms/schema/health_review_response.template.json`](comms/schema/health_review_response.template.json).
The operator does not paste, download, or fetch a snapshot — the
relays give Claude autonomous read access, so asking for one would
violate the autonomy mandate above. (A pasted `health_snapshot.txt`
is accepted only as an optional cross-check.)

**This is NOT a code-quality audit** — do not invoke it for
codebase review, security scan, or dependency check. Use the `review`
or `security-review` skills for those instead. The
[`SessionStart` hook in `.claude/settings.json`](.claude/settings.json)
emits the same directive into every web-session's context at init so
this can't be missed.

When to invoke `/health-review`:
- The operator asks for the health review, the layer-2 review, or to
  sanity-check the live bot's runtime state.
- The cron health-snapshot Telegram ping comes back `🟡 watch` /
  `🚨 concern` and the operator wants a deeper look.

The full review procedure (relay pulls, decision rubric, output schema,
"don't ask scoping questions / never ask the operator to paste a
snapshot") lives in the skill file. The autonomous review **does** write
two repo artifacts — it appends per-trade scores to
[`comms/claude_trade_scores.jsonl`](comms/claude_trade_scores.jsonl)
(keyed by `trade_id`) and drains
[`docs/claude/health-review-backlog.json`](docs/claude/health-review-backlog.json)
— but never touches `src/`, `config/`, or the live path. See also
[`docs/runbooks/health-check.md`](docs/runbooks/health-check.md) for the
collect → review design.

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
                                   ├── /api/diag/*       ← PM-side read-only (S-051)
                                   └── /api/health
```

`ict-web-api.service` runs from `/opt/ict-trading-bot` (a symlink to
`/home/ubuntu/ict-trading-bot`, the only working tree). The symlink is
created on first run by `scripts/deploy_diag.sh`; if it goes missing,
the API CHDIRs to a non-existent path and crashloops.

The dashboard consumer is the **Streamlit** app at `benbaichmankass/ict-trader-dashboard`
(`streamlit_app.py` on Streamlit Community Cloud). The Python server
makes the upstream call to `http://158.178.210.252:8001` directly —
no tunnel, no Vercel rewrite. Pre-2026-05-12 architectures (React on
Vercel → CF named tunnel) are retired; see
[ict-trader-dashboard/CLAUDE.md](https://github.com/benbaichmankass/ict-trader-dashboard/blob/main/CLAUDE.md)
and [`docs/audit/vercel-edge-vs-cf-worker.md`](docs/audit/vercel-edge-vs-cf-worker.md).
If the operator tears down `ict-cloudflared-tunnel.service` on the VM
(via `teardown-cloudflare-tunnel` operator action), nothing downstream
relies on it.

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
                          Tables: trades, order_packages, signals (dual-write),
                          backtest_results (on-demand /test runs only),
                          daily_risk_state (per-account daily PnL + equity-high —
                          self-healing rebuild from trades + balance snapshot,
                          see src/units/accounts/risk.py), strategy_versions
                          (boot snapshot of config/strategies.yaml).
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
| `GET /api/bot/logs` | `LogEntry[]` | `runtime_logs/signal_audit.jsonl`, fallback `bot.log` |
| `GET /api/bot/positions` | open positions | `trade_journal.db` WHERE status='open' |
| `GET /api/bot/signals` | recent ICT detections — each carries `strategy`, `pattern`, `confidence`, `price`, and `zones[]` (drawable decision geometry the strategy already logged: `{kind:"fvg",low,high}` + `{kind:"sweep",price}` for ict_scalp) | `runtime_logs/signal_audit.jsonl` filtered to buy/sell. `zones` are assembled from geometry the signal builder records (e.g. `fvg_low/high`, `sweep_level`) — never a separately-computed indicator. |
| `GET /api/bot/liquidity?symbol=X` | per-symbol liquidity zones (S-064) | `runtime_logs/liquidity_state.json` (pipeline writes per-tick) |
| `GET /api/bot/config` | effective config view (S-064) | `config/accounts.yaml` + `config/strategies.yaml` + `runtime_logs/runtime_status.json`; secrets redacted |
| `GET /api/bot/accounts/balances` | `{present, as_of, age_seconds, balances:{<account_id>:{balance, ts}}}` | `runtime_logs/balance_snapshots.json` (the balances the trader already tracks via the hourly-report `account_snapshots()`). **Read-only, connection-free** — never opens an exchange socket; reflects the last recorded balance. Tier 1. |
| `GET /api/bot/db/tables` | `{present, db, dbs:[...], tables:[{name, rows, columns:[{name,type}], db}]}` | **Federated** read-only DB explorer (Data Explorer tab) over BOTH halves of the canonical store: the live trader's `trade_journal.db` AND the trainer-store sidecar `trainer_store.db` (trainer/ML lifecycle data ingested from the trainer mirror — see `src/units/db/trainer_store.py`). Each table carries a `db` field (`"trade_journal"` / `"trainer_store"`). Tier 1; no secrets in either DB. The sidecar is lazily rebuilt from the mirror on read (mtime-gated). |
| `GET /api/bot/db/table/{name}?db=&limit=&offset=&order_by=&order_dir=&filter_col=&filter_op=&filter_val=` | `{table, db, columns, rows, total, limit, offset}` | one paginated page of a table from whichever federated DB owns it (auto-routed by name; optional `db` selector ∈ {trade_journal, trainer_store}). **SELECT-only** (read-only `mode=ro` connection); table/column identifiers validated against the live schema (no identifier injection), filter values bound. `filter_op ∈ {eq,ne,gt,lt,gte,lte,like}`; `limit` 1..500. 404 on unknown table. |
| `GET /api/bot/trades/closed?limit=N&since=ISO_TS` | `ClosedTrade[]` (#557) | `trade_journal.db::trades` filtered to closed + non-backtest, joined to `order_packages` for the closed-at proxy |
| `GET /api/bot/strategies` | per-strategy config, **live-runtime status** (`loaded`/`running` from `runtime_status.json`), **per-account routing** (`accounts:[{id,live}]` from `accounts.yaml`), lifetime trade stats, descriptions, changelog; plus a top-level `runtime` block (`bot_running`, `last_tick_utc`, `tick_age_seconds`, `loaded_strategies`) | `config/strategies.yaml` + `config/accounts.yaml` + `config/strategy_changelog.json` + `runtime_logs/runtime_status.json` + `trade_journal.db`; Tier 1 |
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
| `GET /api/pnl/history?days=N` | `PnlHistoryPoint[]` (S-063) | `trade_journal.db` (closed trades, realised PnL per UTC day) |

### `BotStats` shape
```json
{
  "pnl24h": 124.50,
  "totalPnL": 3200.00,
  "openTrades": 2,
  "winRate": 68.5,
  "status": "running",
  "datasource": "live",
  "vmHealth": { "cpu": 32.1, "memory": 48.5, "disk": 21.0 }
}
```

### `Position` shape (`/api/bot/positions`)
```json
{
  "id": "42",
  "account": "bybit_2",
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

`stopLoss` / `takeProfit` / `pattern` were added in #820 (2026-05-11) so the
dashboard's live overview chart can render TP/SL price-lines and the
positions table can show the active strategy. Each is **nullable** —
older rows or rows where the writer didn't populate the field serialize
as `null`. Renderers must treat null as "not provided" (em-dash) rather
than `0` / `"unknown"`.

## CORS
CORS is configured in `src/web/api/main.py`. Allowed origins:
- `http://localhost:5173` (Vite dev) — legacy; no longer used by the live dashboard, harmless to leave in the list.
- `http://localhost:3000` — legacy.
- Value of `DASHBOARD_ORIGIN` env var (set to Vercel URL on the VPS).

**Note (2026-05-12):** the Streamlit dashboard makes its upstream call
server-side, so CORS isn't load-bearing for it. The env var + middleware
stay in place for any future browser-direct consumer.

## Environment Variables
| Variable | Purpose |
|----------|---------|
| `DASHBOARD_ORIGIN` | Legacy Vercel app URL — added to CORS allow-list. No-op for the Streamlit dashboard but kept for future browser-direct consumers. |
| `DASHBOARD_API_TOKEN` | Optional bearer token for auth routes |
| `SIGNAL_DUAL_WRITE_DISABLED` | When truthy, `signal_audit_logger._dual_write_to_db` skips hydrating `trade_journal.db::signals` (JSONL stays the source of truth). Default off → dual-write on. Toggle on the live VM via the `enable-signal-dual-write` / `disable-signal-dual-write` operator actions. |
| `TRADE_JOURNAL_DB` | Canonical trade-journal SQLite path (live VM: `/data/bot-data/trade_journal.db`). Resolved by the single Python resolver `src.utils.paths.trade_journal_db_path()` (env → `$DATA_DIR/trade_journal.db` → repo-root; never a CWD-relative basename). The `canonical-db-resolver` CI guard forbids re-introducing the old inline `os.environ.get("TRADE_JOURNAL_DB") or "trade_journal.db"` fallback that seeded the stray duplicate journals. |
| `TRAINER_STORE_DB` | Path to the trainer-store sidecar SQLite (default `$DATA_DIR/trainer_store.db`). Holds trainer/ML lifecycle data ingested from `runtime_logs/trainer_mirror/`; federated into the Data Explorer alongside `trade_journal.db`. Resolved by `src.utils.paths.trainer_store_db_path()`. Read-mostly — ingest writers never touch the money DB. |
| `DIAG_READ_TOKEN` | Bearer for `/api/diag/*` (read-only). Unset → endpoints return 503 |
| `M5_CONSUMER_ENABLED` | Auto-install the M5 backtest consumer in the comms poll loop. Default off; set to `1`/`true` on the VM systemd unit. Operator runbook: `docs/runbooks/strategy-testing.md` |
| `M5_BACKTEST_TIMEOUT_S` | Wall-clock cap per backtest subprocess (default 120s) |
| `BACKTEST_DATA_PATH` | Override the candle CSV the M5 backtest runner reads |
| `VALIDATION_LOG_PATH` | Override the M5 validation NDJSON path (default `runtime_logs/validation.jsonl`) |

## Diagnostic API (S-051)

Token-gated read-only surface for PM-side Claude / operator scripts. All
endpoints return 503 if `DIAG_READ_TOKEN` is unset, 401 on bad bearer.

| Endpoint | Returns |
|----------|---------|
| `GET /api/diag/snapshot?limit=N` | bundle: heartbeat, status, audit tail, order_packages, trades, vm_health, service states |
| `GET /api/diag/audit?limit=N` | tail of `runtime_logs/signal_audit.jsonl` |
| `GET /api/diag/journal?table={order_packages\|trades}&limit=N` | read-only SELECT |
| `GET /api/diag/status` | heartbeat + status.json + vm_health |
| `GET /api/diag/services` | `systemctl is-active` per allowlisted unit |
| `GET /api/diag/journalctl?unit=<name>&lines=N&since=<iso>&until=<iso>` | systemd journal tail; `since`/`until` accept strict ISO-8601 (`2026-05-10T21:13:00Z`) and forward to `journalctl --since`/`--until` for historical-window pulls (PR #821, FU-20260511-001) |
| `GET /api/diag/log_file?name={audit\|status\|heartbeat\|bot_log\|advisory_decisions\|shadow_predictions\|shadow_predictions_backfill}&lines=N` | log file tail |

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
create, secret scanning, **but no `create_label`, no `run_workflow`,
no artifact download, no run-log read**), Google Drive (file search
+ read), Hugging Face (hub search, doc fetch), Bigdata.com (market
data), Gmail (read-only labels).

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
- **Repo label creation** — `.github/workflows/bootstrap-labels.yml`
  self-creates the labels other workflows filter on. Edit the
  `LABELS` array in that file and merge; the next push runs the
  sync. No `create_label` MCP needed.
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
  `vm-web-api-recover.yml`, and now `system-actions.yml` (whose
  Tier-2 ack is the operator's in-conversation approval — Claude
  carries that approval into the issue body).

## Running Locally
```bash
pip install -r requirements.txt
uvicorn src.web.api.main:app --port 8001 --reload
```

## Important Notes
- `src/web/runtime_status.py` is imported by `src/runtime/pipeline.py` — do NOT delete it
- `heartbeat.txt` mtime is the canonical "is the trader process responsive" signal. Refreshed every `HEARTBEAT_INTERVAL_SECONDS` (default 60 s) from inside `src/main.py`'s sleep loop — so it fires between ticks too, not just at tick completion. A pipeline hang stops the heartbeat (the loop is on the main thread, no daemon) so liveness still reflects pipeline health. Thresholds derived from the same cadence: `< cadence × 3` → running, `< cadence × 10` → paused, else stopped. Helper at `src/runtime/heartbeat.py::heartbeat_label`. Prior history: 2 min threshold (way too tight for a 15-min tick) → 10 min in 2026-05-07 → 18 min (tick × 1.2) on 2026-05-08 → finally cadence-based with 60 s heartbeat the same day, after the tick-coupled basis kept under-counting healthy idleness.
- **External liveness watchdog (`ict-liveness-watchdog.{service,timer}`, 2026-05-11)** is the per-minute dead-man switch on top of the in-process heartbeat. Runs `scripts/check_heartbeat.py` every 60 s; Telegrams `[CRITICAL] Trader heartbeat stale` after 5 min of stale mtime; auto-restarts `ict-trader-live.service` after 8 min total stall (autoheal opt-in via `--auto-restart-after 3`, currently ON). Stdlib-only so it works even when the trader's venv is wedged. Full operator runbook: [`docs/runbooks/liveness-watchdog.md`](docs/runbooks/liveness-watchdog.md). Not to be confused with `ict-heartbeat.{service,timer}` which is the once-daily operator status digest at 13:00 UTC (`scripts/daily_heartbeat.py`). **Note (2026-05-12 incident):** the watchdog correctly auto-restarted the trader after the 16h heartbeat-writer silent failure, but the new process retained whatever state was making bybit_2 dry. The Prime Directive (above) addresses the conceptual root cause: no auto-flip code paths should exist. The watchdog's restart behaviour is unchanged — restarting is fine; what was wrong was the flip itself.
- The old HTMX UI (`web/static/`, `web/templates/`, `src/web/api/routers/ui.py`) has been removed
- The old Streamlit UIs (`src/web/backtest_ui.py`, `src/web/config_ui.py`) have been removed
- The old `cf-worker/` directory has been removed (2026-05-12). It was a deprecated Cloudflare Worker proxy that never worked (CF error 1003: Workers can't fetch raw IPv4). With the dashboard now on Streamlit, no tunnel is needed at all. The `ict-cloudflared-tunnel.service` on the VM can be torn down via the existing `teardown-cloudflare-tunnel` operator action whenever you want — nothing depends on it anymore.
