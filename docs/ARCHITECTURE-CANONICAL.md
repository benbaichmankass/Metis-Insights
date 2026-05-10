# Architecture — Canonical (v2)

> **Status:** Canonical. Adopted in sprint **S-CANON-1** (2026-05-10).
> **Repo:** `benbaichmankass/ict-trading-bot`.
> **Authority:** This document supersedes the older
> [`docs/architecture.md`](architecture.md) and the architecture
> sections of the root `CLAUDE.md`. When this doc and an older note
> disagree, this doc wins.
> **Companion:** [`CLAUDE-RULES-CANONICAL.md`](CLAUDE-RULES-CANONICAL.md)
> covers Claude's operating rules; this doc covers system design only.
> **AI scope:** AI-specific architecture (data → feature → model →
> orchestration → deterministic control) is documented in
> [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
> and is owned by ROADMAP.md milestones M9 + M10. When that doc and
> this one overlap on AI scope, the AI-platform doc wins for AI-only
> design questions; this doc remains canonical for system-wide design.

## Purpose

Canonical description of how the ICT trading bot project is structured
and how the major systems work together. Process policy lives in the
rules doc; this doc is system design.

Update this doc whenever real repo architecture changes, when subsystems
are introduced or moved, or when a sprint discovers that the docs no
longer match the implementation.

## Architectural Principles

- Live trading stability takes precedence over feature growth.
- Research, staging, and live trading must remain clearly separable.
- Operator communications must remain isolated from core trading logic.
- Deployment behavior must be explicit and documented.
- Every production-critical subsystem must have a clear owner file
  path, validation path, and logging path.
- Duplicate files, unclear canonical entrypoints, and undocumented side
  effects are architecture problems and are treated as such.

## System Layers

### 1. Runtime trading layer
Market-driven execution behavior:
- market-data intake (`src/exchange/`, `src/runtime/market_data.py`),
- strategy evaluation (`src/units/strategies/`),
- runtime pipeline orchestration (`src/runtime/pipeline.py`,
  entrypoint via `src/main.py`),
- order construction and validation (`src/runtime/orders.py`,
  `src/runtime/validation.py`),
- per-account risk gating (`src/units/accounts/risk.py`,
  `src/units/accounts/prop_risk.py`,
  `src/runtime/risk_counters.py`),
- broker execution (`src/units/accounts/execute.py`),
- runtime logs and state outputs (`runtime_logs/`, `trade_journal.db`).

### 2. Research and validation layer
Idea generation, backtesting, dry-run qualification:
- `notebooks/`,
- `experiments/`,
- `src/backtest/`,
- `src/ml/` (where present),
- backtest dispatch from `src/bot/test_strategy_consumer.py`
  (auto-consumed `test_strategy:<name>` requests).

### 3. Operator control and communication layer
Asynchronous Claude ↔ operator channel:
- repo-backed request artifacts (`comms/requests/`),
- archive (`comms/archive/`),
- schemas (`comms/schema/{request,response}.schema.json`),
- bot polling and writeback (`src/bot/comms_handler.py`,
  `src/bot/telegram_query_bot.py`,
  `src/comms/{models,state,store,templates,log}.py`).

### 4. Deployment and environment layer
Repo sync, services, timers, and runtime application of repo changes:
- systemd unit files in `deploy/`,
- deploy scripts in `scripts/` and `scripts/ops/`,
- environment scaffolding (`.env.example`,
  `scripts/render_env_from_master.py`,
  `config/master-secrets.template.yaml`),
- VM bootstrap (`scripts/vm_bootstrap.sh`),
- web API self-heal (`scripts/ops/restart_web_api.sh`,
  `.github/workflows/vm-web-api-recover.yml`).

### 5. Governance and documentation layer
Canonical docs, sprint logs, roadmap, audits, workflow docs, evidence
trails (`docs/`, `ROADMAP.md`, `CLAUDE.md` root pointer).

## End-to-End Trade Pipeline

The trade pipeline is implemented in `src/runtime/pipeline.py` and
driven from `src/main.py`. The intended sequence is summarised below;
the **canonical step-by-step map**, with files, inputs/outputs, and
failure modes for every stage, lives in
[`TRADE-PIPELINE.md`](TRADE-PIPELINE.md). The dashboard's **Trade
Process** tab fetches that document at runtime — keep it current.

### Step 1 — Market data intake
Exchange connectors (`src/exchange/bybit_connector.py`,
`src/exchange/binance_connector.py`) and the market-data helpers in
`src/runtime/market_data.py` produce candles and tick state.

### Step 2 — Strategy evaluation
Strategy modules in `src/units/strategies/` (e.g. `turtle_soup.py`,
`vwap.py`) consume market data and emit signals. Strategy logic is kept
separate from broker execution.

### Step 3 — Strategy output normalization
Signals are normalised to the internal order/intent representation used
by the runtime pipeline. The runtime audit logger
(`src/utils/signal_audit_logger.py`) writes
`runtime_logs/signal_audit.jsonl` for every decision.

### Step 4 — Risk gating
Before any order reaches broker execution, risk controls decide whether
to allow the signal:
- `RiskManager.approve()` in `src/units/accounts/risk.py` (per-account
  caps: `pos_size`, `daily_usd`, `max_dd_pct`),
- prop-account logic in `src/units/accounts/prop_risk.py`,
- runtime counters in `src/runtime/risk_counters.py`,
- the kill-switch flag (`HALT_FLAG_PATH = /tmp/trader_halt.flag`,
  consumed in `pipeline.py`),
- news veto via `src/news/news_pipeline.py`.

### Step 5 — Runtime order validation
`src/runtime/orders.py::safe_place_order` validates quantities, sizing,
and execution prerequisites. Hard refusal paths exist for invalid or
disallowed orders. Closed-flat invariant lives in
`src/runtime/closed_flat_invariant.py`.

### Step 6 — Broker execution
Only after the steps above does the broker-specific executor send a
live order or simulate one in dry-run mode. Per-account dry/live mode
is set in `config/accounts.yaml` (`mode: live | dry_run`). This is the
only canonical execution gate; there is no process-level interlock.

### Step 7 — Logging and state updates
The runtime records:
- structured signal audit (`runtime_logs/signal_audit.jsonl`),
- pipeline outcomes (`src/runtime/outcomes.py`),
- heartbeat (`runtime_logs/heartbeat.txt`, refreshed every
  `HEARTBEAT_INTERVAL_SECONDS`, default 60s),
- runtime status (`runtime_logs/status.json`),
- trades and order packages (SQLite `trade_journal.db`).

### Step 8 — Operator visibility and control
The Telegram bot (`src/bot/telegram_query_bot.py`) plus the FastAPI
diag surface (`src/web/api/routers/diag.py`) expose status, halt and
resume actions, and pending requests. The Vercel dashboard
(`ict-trader-dashboard`) consumes the unauthenticated Tier 1 endpoints
documented in [`api-tier-policy.md`](api-tier-policy.md).

## Research and Validation Pipeline

1. **Concept generation** — notes / Colab notebooks / exploratory
   backtests in `notebooks/`.
2. **Backtest harness** — `src/backtest/` and dispatched runs via
   `scripts/run_backtest.sh`.
3. **Multi-symbol / multi-timeframe validation** — `experiments/`.
4. **Evidence capture** — `experiments/<sprint>/results/*.json` and
   `runtime_logs/validation.jsonl`.
5. **Repo port** — strategy modules under `src/units/strategies/`,
   wired into `config/strategies.yaml`.
6. **Dry-run / staging** — per-account `mode: dry_run` in
   `config/accounts.yaml`.
7. **Promotion decision** — Tier 3, requires explicit operator approval.

## Operator Communication Pipeline

The flow is repo-driven and auditable.

### Flow
1. Claude writes a structured request artifact in `comms/requests/`.
2. The VM pulls (`ict-git-sync.timer`, default 5min interval).
3. The Telegram bot detects pending requests and sends them.
4. Operator answers in Telegram (button, "Other" + text, or free text).
5. Bot writes the answer back, sets `status` to
   `answered` / `partially_answered`, and commits.
6. The VM pushes; Claude reads on the next sync.

### Required properties
- isolated from trading logic (no `src/runtime/` or `src/units/` code
  imports `src.comms`),
- atomic file writes (tmp + rename),
- idempotent and safe on restart,
- resistant to duplicate sends,
- resilient to malformed files (unknown `schema_version` is rejected),
- documented for a non-technical operator
  ([`comms/README.md`](../comms/README.md)).

### State model
Statuses: `pending`, `sent`, `partially_answered`, `answered`,
`acknowledged`, `expired`, `cancelled`. Stuck-request alert and
final pre-expiry alert (M1 P1-B) prevent silent expiry.

## Deployment and Sync Pipeline

### Flow
1. Changes merge to `main` on `benbaichmankass/ict-trading-bot`.
2. `ict-git-sync.service` (triggered by `ict-git-sync.timer`, every
   5 min) pulls into `/home/ubuntu/ict-trading-bot` (the working tree).
3. `/opt/ict-trading-bot` is a symlink to the working tree, created by
   `scripts/deploy_diag.sh` on first run.
4. Services reload as designed
   (`ict-trader-live`, `ict-web-api`, `ict-telegram-bot`).
5. Logs in `runtime_logs/` and journalctl confirm whether the update
   applied cleanly.
6. Operator-driven actions go through
   `.github/workflows/operator-actions.yml`
   (allowlisted: `status-check`, `pull-latest-logs`, `pull-and-deploy`,
   `restart-bot-service`, `reboot-vm`).

### Components
| Concern | File |
|---|---|
| Canonical branch | `main` |
| Sync service | `deploy/ict-git-sync.service` |
| Sync timer | `deploy/ict-git-sync.timer` (every 5 min) |
| Trader service | `deploy/ict-trader-live.service` |
| Web API service | `deploy/ict-web-api.service` |
| Telegram bot service | `deploy/ict-telegram-bot.service` |
| Heartbeat timer | `deploy/ict-heartbeat.{service,timer}` |
| Hourly snapshot | `deploy/ict-hourly-snapshot.{service,timer}` |
| Smoke once | `deploy/ict-smoke-once.service` |
| Claude bridge | `deploy/ict-claude-bridge.service` |
| Env-check | `deploy/ict-env-check.service` |
| Deploy script | `scripts/deploy_diag.sh`, `scripts/deploy_pull_restart.sh` |
| VM bootstrap | `scripts/vm_bootstrap.sh` |
| Web API restart wrapper | `scripts/ops/restart_web_api.sh` |

Rollback / recovery steps and the live-trading deploy procedure live in
[`DEPLOYMENT_LIVE_TRADING.md`](../DEPLOYMENT_LIVE_TRADING.md) and
[`docs/claude/deployment-ops.md`](claude/deployment-ops.md).

## GitHub Actions and Automation Layer

GitHub Actions are part of the architecture, not a side note.
The canonical reference is
[`docs/github-actions-workflows.md`](github-actions-workflows.md). It
catalogues every workflow under `.github/workflows/` with trigger,
purpose, secrets, outputs, and the rules for when Claude may modify it.

Current workflows include CI guards (`pytest-collect`, `ruff-lint`,
`secret-scan`, `dry-run-guard`, `env-gate-guard`,
`silent-empty-guard`), VM ops (`operator-actions`, `vm-diag-snapshot`,
`vm-web-api-recover`, `vm-net-diag`, `vm-net-fix`, `vm-cloud-fix`),
training (`training-run`, `training-rerun-5m`, `hf-cron`),
inventory/labels (`repo-inventory`, `bootstrap-labels`,
`branch-protection-sync`), and the autonomous follow-on driver
(`continue-work`).

## Repo Responsibility Map

| Area | Path | Notes |
|---|---|---|
| Runtime pipeline | `src/runtime/` | `pipeline.py`, `orders.py`, `validation.py`, `health.py`, `heartbeat.py`, `outcomes.py` |
| Strategies | `src/units/strategies/` | Strategy modules; wired via `config/strategies.yaml` |
| Strategy registry | `src/strategy_registry.py` | Single source of truth for which strategies exist |
| Account / risk | `src/units/accounts/` | `risk.py`, `prop_risk.py`, `execute.py`, `__init__.py` (`load_accounts`) |
| Exchange connectors | `src/exchange/` | Bybit, Binance |
| ICT detection | `src/ict_detection/` | Reusable signal-detection components |
| News layer | `src/news/` | `news_pipeline.py` |
| Bot / comms code | `src/bot/`, `src/comms/` | Telegram handlers, comms store, schemas |
| Web API | `src/web/api/` | FastAPI app + routers; runtime status writer at `src/web/runtime_status.py` |
| Comms artifacts | `comms/` | Operator request/response artifacts and schemas |
| Config | `config/` | `accounts.yaml`, `strategies.yaml`, `units.yaml`, env templates |
| Deploy | `deploy/` | systemd unit + timer files |
| Scripts / ops | `scripts/`, `scripts/ops/` | Deploy, diag, ops wrappers |
| Tests | `tests/` | Unit + integration |
| Docs | `docs/` | Canonical docs (this dir), claude operating notes, sprint logs |
| AI-platform doc | [`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md) | AI-specific architecture (M9 + M10). Subordinate canonical doc; covers the model layer + deployment tiers + Oracle/HF runtime split. |
| GitHub Actions | `.github/workflows/` | All CI / VM ops / training workflows |

## AI-traders training workflow (separate from live trading)

Live trading is fully deterministic — no model is in the live
path. The AI-traders training pipeline is a **separate concern**
that produces research-only baselines under `ml/`. Operator-driven
training sessions follow the established workflow:

1. **Collect feedstock.** The `/health-review` skill emits per-trade
   `trade_decision_grades[]` against the live 6-hour window. These
   labelled grades flow into the `trade_outcomes` family
   ([`ml/datasets/families/trade_outcomes.py`](../ml/datasets/families/trade_outcomes.py))
   and the `setup_labels` family
   ([`ml/datasets/families/setup_labels.py`](../ml/datasets/families/setup_labels.py))
   as their primary label source.
2. **Build datasets.** `python -m ml.datasets build <family>` writes
   versioned artifacts under `<output>/<family>/<scope>/<tf>/<version>/`
   with mandatory metadata + leakage discipline. Buildable families:
   `trade_outcomes`, `backtest_results`, `market_raw`,
   `market_features`, `setup_labels`. Family taxonomy:
   [`docs/data/dataset-taxonomy.md`](data/dataset-taxonomy.md).
3. **Train baselines.** `python -m ml train <manifest>` runs a YAML
   manifest end-to-end (split → fit → evaluate → register). Established
   manifests:
   - [`ml/configs/baseline-trade-outcome-winrate.yaml`](../ml/configs/baseline-trade-outcome-winrate.yaml)
     (WS5-A; per-strategy historical winrate).
   - [`ml/configs/baseline-trade-outcome-global.yaml`](../ml/configs/baseline-trade-outcome-global.yaml)
     (WS4-FU; global-mean sanity baseline).
   - [`ml/configs/baseline-regime-classifier.yaml`](../ml/configs/baseline-regime-classifier.yaml)
     (WS5-B-PART-2; 3-class regime classifier on `market_features`).
   - [`ml/configs/baseline-setup-quality.yaml`](../ml/configs/baseline-setup-quality.yaml)
     (WS5-C; setup-quality scorer on `setup_labels`).
4. **Compare runs.** `python -m ml compare <id-a> <id-b>` surfaces
   shared-metric deltas as JSON.
5. **Promotion is gated.** Even a clean training run lands at
   `target_deployment_stage: research_only`. Promotion to `live-approved`
   or `champion` requires `python -m ml promote --by <name> --reason <text>`
   and operator approval.

Training sessions MUST use these established baselines + manifests
rather than reinventing. Adding a new baseline follows the
"Adding a new family" / "Adding a new trainer" rules in
[`docs/data/dataset-taxonomy.md`](data/dataset-taxonomy.md) and
[`docs/ml/training-center.md`](ml/training-center.md).

The full AI-platform architecture (five-layer model, leakage rules,
forbidden behaviors, model registry append-only invariant) lives in
[`docs/architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

### Two-VM topology (S-AI-WS9)

The "no heavy training on the Oracle live VM" non-negotiable
([`AI-TRADERS-ROADMAP.md`](AI-TRADERS-ROADMAP.md)) is now enforced
by **topology**, not just policy. Two Always Free Ampere A1 VMs
run side-by-side in the same compartment + VCN:

| VM | Role | Systemd units | Marker file |
|---|---|---|---|
| **Live trader VM** | Deterministic trade execution; FastAPI dashboard surface | `ict-trader-live.service`, `ict-web-api.service` | (none today; pre-WS9) |
| **Training-center VM** | Model training, dataset builds, registry writes, experiment runs | `ict-trainer.service` (disabled by default), `ict-trainer.timer` (disabled by default) | `/etc/ict-trainer-vm.role` → `training-center` |

The training-center VM is provisioned via
[`.github/workflows/provision-training-vm.yml`](../.github/workflows/provision-training-vm.yml)
+ [`scripts/ops/provision_training_vm.py`](../scripts/ops/provision_training_vm.py).
Cloud-init bootstraps it from
[`deploy/training-vm-cloud-init.yaml`](../deploy/training-vm-cloud-init.yaml)
with the repo cloned to `/home/ubuntu/ict-trading-bot` and the
trainer systemd unit installed but **disabled** — the operator
opts in to training cycles explicitly, so the Always Free quota
isn't consumed by idle compute.

**Cross-VM data flow** (filed for follow-up — not yet wired):
- Live VM owns `trade_journal.db`. The training center needs
  read access for label feedstock. Options: scheduled rsync from
  live VM, or read via the `/api/diag/*` surface over HTTPS. No
  decision yet.
- Training center owns the registry-store + experiment runs.
  Promoted models flow back to the live VM via
  `git pull` + the operator's deploy workflow (existing
  `operator-actions.yml::pull-and-deploy`).

**Cross-VM SSH**: both VMs accept the same `VM_SSH_KEY` (operator
chose key-reuse — same private key, simpler rotation). If the
threat model later requires isolated keys, the workflow accepts
a `TRAINER_VM_SSH_KEY` secret override.

## Evidence and Documentation Flow

Every major code change must produce or update at least one of:
- a sprint log (`docs/sprint-logs/<id>.md`),
- the roadmap (`ROADMAP.md`),
- a canonical doc (this doc, the rules doc, the AI-platform doc, or
  `docs/github-actions-workflows.md`),
- subsystem docs under `docs/claude/`, `docs/operator/`, etc.,
- evidence in `tests/`, `experiments/`, or `runtime_logs/`.

Chat memory and PR descriptions are not the system of record.

## Known Architecture Risks (verified 2026-05-10)

The following risks are observed in the current repo and tracked in the
roadmap rather than silently ignored:

- **Stale repo references**: a number of active docs and scripts still
  reference `the-lizardking/ict-trading-bot`. Inventory is maintained
  in the audit section of `docs/sprint-logs/S-CANON-1.md`.
- **Doc proliferation under `docs/claude/`**: 50+ working notes; the
  canonical-doc set above is the new authoritative apex. Older notes
  remain useful but non-authoritative on policy.
- **Sprint summary divergence**: sprint summaries and sprint prompts
  exist in two folders (`docs/sprint-summaries/`, `docs/sprint-plans/`).
  New work uses `docs/sprint-logs/` with the canonical template.
- **No GitHub Actions reference doc** (now resolved by
  [`github-actions-workflows.md`](github-actions-workflows.md)).
- **Empty / spurious sqlite-connection-named files** in the repo root
  (`<sqlite3.Connection object at 0x...>`). Diagnosed in this audit.
- **AI-scope known gaps** — see
  [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
  § Known Gaps. The current `ml/` tree is vestigial; WS3–WS10 deliver
  the target dataset / training / registry / monitoring stack.

## Architecture Update Rule

This document must be reviewed whenever a sprint changes:

- runtime flow,
- subsystem boundaries,
- deployment behavior,
- operator communication behavior,
- GitHub Actions automation,
- or any canonical file path used as part of the operating model.

When the change touches any stage of the trade pipeline (any block in
[`TRADE-PIPELINE.md`](TRADE-PIPELINE.md)), that document must be
updated in the same sprint and the dashboard's **Trade Process** tab
visually verified after merge to `main`. The dashboard fetches the
pipeline doc directly from this repo, so a stale doc means a stale
operator UI.

For AI-scope changes (data → feature → model → orchestration →
control layer boundaries, dataset families, model registry, deployment
tiers, Oracle/HF split) the corresponding doc to update is
[`architecture/ai-model-platform.md`](architecture/ai-model-platform.md).

## Verification Checklist (current state)

Confirmed against the repo on 2026-05-10:

- [x] Runtime entrypoint: `src/main.py` → `src/runtime/pipeline.py`
- [x] Risk manager: `src/units/accounts/risk.py`
- [x] Order execution: `src/runtime/orders.py` and
      `src/units/accounts/execute.py`
- [x] Strategy registry: `src/strategy_registry.py` driven by
      `config/strategies.yaml`
- [x] Telegram bot entrypoint: `src/bot/telegram_query_bot.py`
- [x] Comms directory: `comms/` with `requests/`, `archive/`, `schema/`
- [x] Deploy scripts: `scripts/deploy_diag.sh`,
      `scripts/deploy_pull_restart.sh`
- [x] systemd files: `deploy/ict-*.{service,timer}`
- [x] Existing GitHub Actions: enumerated in
      [`github-actions-workflows.md`](github-actions-workflows.md)
- [x] Trade pipeline canonical map:
      [`TRADE-PIPELINE.md`](TRADE-PIPELINE.md)
- [x] AI-scope architecture doc:
      [`architecture/ai-model-platform.md`](architecture/ai-model-platform.md)
      (S-AI-WS1, 2026-05-10)

---

## Change log

Architecture-impacting changes (per the rubric in
[`architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`](architecture/ARCHITECTURE-CHANGE-CHECKLIST.md))
land a row here. Per-PR ledger sits in
[`ROADMAP.md`](../ROADMAP.md); the table below is curated and
filtered to architecture-level deltas only.

| Date | Sprint | Change | Files touched | Operator impact |
|---|---|---|---|---|
| 2026-05-10 | S-CANON-1 | Canonical-doc adoption: this file supersedes the older `docs/architecture.md` and the architecture sections of root `CLAUDE.md`. Companion rules doc + sprint-log template + canonical workflows doc all stand. | `docs/ARCHITECTURE-CANONICAL.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`, `docs/github-actions-workflows.md` | None — informational. |
| 2026-05-10 | S-AI-WS1..WS4 | AI platform baseline: pipeline stage contracts (`docs/pipeline/stage-contracts.md`), typed dataclasses (`src/pipeline/types.py`), dataset framework (`ml/datasets/`), training center + registry + Predictor + splitters + compare (`ml/`). | `ml/`, `src/pipeline/`, `docs/pipeline/`, `docs/architecture/ai-model-platform.md` | None — research-only. |
| 2026-05-10 | S-AI-WS5-A..F | Six baseline models registered + paired manifests. None promoted past `candidate`. | `ml/configs/*.yaml`, `ml/trainers/`, `ml/datasets/families/`, `ml/registry-store/` | None — research-only. |
| 2026-05-10 | S-AI-WS7-PART-1 | Model registry gains `target_deployment_stage` + canonical stage ladder (`research_only` → `candidate` → `backtest_approved` → `shadow` → `advisory` → `limited_live` → `live_approved`). Append-only `StatusEvent` history; `promote_stage()` requires `--by` + `--reason`. | `ml/registry/`, `ml/promotion/` | None — registry unread by runtime. |
| 2026-05-10 | S-AI-WS7-PART-2..6 | Shadow harness complete. `src/runtime/shadow_adapter.py::with_shadow_pred` + `with_shadow_preds` helpers (per-predictor failure isolation). `ml/shadow/factory.py` resolves `shadow_model_ids` against the registry with a stage gate (`{shadow, advisory, limited_live, live_approved}` allowed; `{research_only, candidate, backtest_approved}` refused). Both production strategies (`vwap` + `turtle_soup`) wired. `Coordinator._shadow_predictors_cache` lifts the factory call to O(reloads). | `src/runtime/shadow_adapter.py`, `ml/shadow/*`, `src/units/strategies/vwap.py`, `src/units/strategies/turtle_soup.py`, `src/core/coordinator.py`, `config/strategies.yaml` | None unless operator sets a non-empty `shadow_model_ids`. |
| 2026-05-10 | S-AI-WS8-PART-1 | Shadow-predictions audit log gains an operator surface: `ml/shadow/inspector.py` (streaming reader + filters + per-(model_id, stage) aggregate + text formatters) + `python -m ml shadow-inspect`/`shadow-stats` CLI subcommands. | `ml/shadow/inspector.py`, `ml/cli.py` | None — diagnostic tooling, read-only. |
| 2026-05-10 | S-AI-WS10 | Architecture-doc enforcement scaffold. New `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`, `.github/PULL_REQUEST_TEMPLATE.md` with arch-impact checkboxes, advisory `.github/workflows/arch-doc-guard.yml` (soft `::warning`, never fails). | `docs/architecture/ARCHITECTURE-CHANGE-CHECKLIST.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/workflows/arch-doc-guard.yml`, `scripts/arch_doc_guard.py`, this file | None — informational. |
| 2026-05-10 | S-AI-WS9 | Two-VM topology: training-center VM provisioning via OCI Always Free Ampere A1. New `scripts/ops/provision_training_vm.py`, `.github/workflows/provision-training-vm.yml` (dispatch + issue-trigger), `deploy/training-vm-cloud-init.yaml`, operator runbook. Makes "no heavy training on the live VM" enforced by topology, not just policy. New VM bootstraps with `ict-trainer.service` DISABLED — operator opts in. | `scripts/ops/provision_training_vm.py`, `.github/workflows/provision-training-vm.yml`, `deploy/training-vm-cloud-init.yaml`, `docs/runbooks/training-vm.md`, this file | Operator triggers workflow once to spin up the trainer VM; no impact on live trader. |
| 2026-05-10 | S-AI-WS9-FU | `scripts/ops/run_training_cycle.sh` — body of `ict-trainer.service`. `Type=oneshot` cycle: pull main, lazy-provision venv, train every manifest under `ml/configs/`, JSONL events to `runtime_logs/training_cycle.jsonl`. First-failure short-circuit. Operator can now `systemctl enable --now ict-trainer.service` on the trainer VM. | `scripts/ops/run_training_cycle.sh` | Operator opt-in. |
| 2026-05-10 | S-AI-WS7-FU | Shadow-prediction audit log rotation. `scripts/ops/rotate_shadow_log.py` + `deploy/ict-shadow-log-rotate.{service,timer}` (DISABLED BY DEFAULT). Size OR age threshold (default 100 MiB or 7 days); atomic rename; optional `--gzip`; date-suffixed filenames with collision suffix. | `scripts/ops/rotate_shadow_log.py`, `deploy/ict-shadow-log-rotate.{service,timer}` | Operator opts in via `systemctl enable --now ict-shadow-log-rotate.timer`. |
| 2026-05-10 | S-AI-WS8-PART-2 | Shadow-predictions dashboard endpoints. New `GET /api/bot/shadow/predictions` + `GET /api/bot/shadow/stats` via `src/web/api/routers/shadow.py`. Both reuse `ml.shadow.inspector` from PART-1; no duplicate parsing. Envelope response distinguishes "no records" from "log absent" via `log_present`. | `src/web/api/routers/shadow.py`, `src/web/api/main.py`, `CLAUDE.md` | Live after next deploy. |
| 2026-05-10 | S-AI-WS8-PART-3 | Shadow-prediction drift detector. New `ml/shadow/drift.py` (summary stats + KS statistic + PSI + plain-English verdict buckets + `compute_drift` → `DriftReport`). New CLI subcommand `python -m ml shadow-drift` + dashboard endpoint `GET /api/bot/shadow/drift`. Window-over-window self-comparison; no external reference required. Pure stdlib. | `ml/shadow/drift.py`, `ml/shadow/__init__.py`, `ml/cli.py`, `src/web/api/routers/shadow.py`, `CLAUDE.md` | Live after next deploy. |
| 2026-05-10 | S-AI-WS10-FU (pre-commit) | Opt-in pre-commit hook. `scripts/git-hooks/pre-commit` mirrors the CI guard locally but BLOCKS the commit on warning. Installed by `bash scripts/install-hooks.sh`. CI advisory + local blocking — asymmetric by design. | `scripts/git-hooks/pre-commit`, `scripts/install-hooks.sh`, `ruff.toml` | Operator opt-in. |
| 2026-05-10 | S-AI-WS10-FU (doc-audit) | Periodic architecture-doc audit. `scripts/ops/audit_verification_checklist.py` parses this file's Verification Checklist; `.github/workflows/doc-audit-weekly.yml` runs Mondays + on-demand + via labelled issue; opens a `doc-drift`-labelled issue when paths go stale (idempotent search-before-create). | `scripts/ops/audit_verification_checklist.py`, `.github/workflows/doc-audit-weekly.yml`, `.github/workflows/bootstrap-labels.yml` | Informational issues filed when drift detected. |
| 2026-05-10 | S-AI-WS6-PART-1 | Open-source model layer scaffolding. `docs/architecture/model-inventory.md` (rules + criteria + candidate models by use case) + `ml/predictors/external.py` (`ExternalPredictor` ABC + `ProviderError`). Provider-agnostic; no HF / vendor SDK pulled in. PART-2 (first concrete integration) blocked on a real use case. | `docs/architecture/model-inventory.md`, `ml/predictors/external.py`, `ml/predictors/__init__.py` | None — framework only. |
| 2026-05-10 | S-AI-DOCS-SWEEP | End-of-session reconciliation. Updates header markers, workstream status rows, recommended-order section, and change-log tables on `ROADMAP.md`, `docs/AI-TRADERS-ROADMAP.md`, and this file. | `ROADMAP.md`, `docs/AI-TRADERS-ROADMAP.md`, this file | None — informational. |

---

## Known gaps

Deliberate omissions and queued work. An entry here is a
**contract** between the team and future maintainers: the
architecture doc does not yet reflect this state, by design,
because the work is in flight or out of scope for the current
milestone.

| Gap | Why deferred | Tracking |
|---|---|---|
| **WS5 baselines not yet promoted past `candidate`** | All six are registered and ready, but promotion to `shadow` requires a real `trade_journal.db` and the trainer VM to be running. Operator-blocked. | WS7/WS9 operator-unlock; ROADMAP.md. |
| **`shadow_model_ids` empty in production YAML** | The harness is wired but inert. Production rollout = YAML edit + a promoted model in the registry. | Same as above. |
| **No external-reference drift detection** | PART-3 ships window-over-window self-comparison (useful from day one). The "real" reference-distribution variant needs a `metrics_distribution` field on the registry entry to store training-set scores. Filed. | WS8 follow-up. |
| **`arch-doc-guard` is advisory, not blocking** | Hard-failing on CI would push the team to bypass it. Local opt-in pre-commit hook now blocks; CI upgrade still filed for the next iteration once track record exists. | Filed in S-AI-WS10 sprint log. |
| **Trainer VM ↔ live VM data flow not yet wired** | WS9 ships the trainer VM topology + `run_training_cycle.sh` but cross-VM `trade_journal.db` access (rsync vs diag-API-over-HTTPS) is filed for the operator to decide post-provision. Manual `scp` workaround is in the runbook. | WS9 follow-up. |
| **Trainer VM not yet provisioned** | Issue #791 was filed to dispatch `provision-training-vm.yml`; workflow did not post back. Operator can re-dispatch via UI. | WS9 operator-unlock. |
| **WS6 first concrete external-model integration** | PART-1 ships the inventory + ABC. PART-2 (a specific HF / OSS model) requires a real use case that meets the approval criteria in `docs/architecture/model-inventory.md`. | WS6-PART-2. |
