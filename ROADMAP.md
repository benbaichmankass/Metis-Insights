# ICT Trading Bot — Product Roadmap

> **Last Updated:** 2026-05-10 (through S-AI-WS5-C).
>
> **Canonical authority:**
> 1. [`docs/CLAUDE-RULES-CANONICAL.md`](docs/CLAUDE-RULES-CANONICAL.md)
> 2. [`docs/ARCHITECTURE-CANONICAL.md`](docs/ARCHITECTURE-CANONICAL.md)
> 3. This file (`ROADMAP.md`)
> 4. Current sprint log in `docs/sprint-logs/`
>
> **Scope-specific master plans:**
> - M9 + M10 — [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>   AI-scope canonical [`ai-model-platform.md`](docs/architecture/ai-model-platform.md);
>   pipeline types + stage contracts;
>   data layer [`docs/data/`](docs/data/) + [`ml/datasets/`](ml/datasets/) +
>   [`docs/ml/market-raw-adapters.md`](docs/ml/market-raw-adapters.md);
>   training center [`docs/ml/`](docs/ml/) + [`ml/`](ml/);
>   first specialist baselines under `ml/configs/`.

---

## Core Principles

1. **Lean solutions.**
2. **Stability first.**
3. **Profitability focus.**

---

## M0..M10 Milestone Roadmap

| Milestone | Type | Focus | Status |
|---|---|---|---|
| **M0–M5** | auto-claude | Foundation → Strategy testing | ✅ CLOSED |
| **M6** | auto-claude | Web app UI | 🔄 IN PROGRESS (dashboard repo) |
| **M7** | pm-sprint | Strategy review gate | 📋 NOT STARTED |
| **M8** | pm-sprint | Strategy tuning | 📋 NOT STARTED |
| **M9** | auto-claude | AI / model roadmap | 🔄 IN PROGRESS — WS1+WS2+WS4+WS4-FU+WS5-A closed; WS5-B-PART-1 + PART-2 (PR 2A + PR 2B) + WS5-C closed 2026-05-10. |
| **M10** | auto-claude | HF / data pipeline | 🔄 IN PROGRESS — WS3 closed; WS5-B-PART-1 adds `market_raw`; WS5-B-PART-2 PR 2A wires Bybit off-VM fetch; PR 2B adds `market_features`; WS5-C adds `setup_labels` (fifth buildable family); WS9 continuous. |

### M9 / M10 — AI traders workstreams (WS1–WS10)

> Master plan: [`docs/AI-TRADERS-ROADMAP.md`](docs/AI-TRADERS-ROADMAP.md).
>
> Implementation order: WS1 → WS2 → WS3 → WS4 + WS4-FU → WS5
> baselines (sub-sprints A..F; WS5-B further split into PART-1 +
> PART-2) → shadow mode (WS7) → WS6 → WS8 + WS10.
> WS9 is continuous from WS3 onwards.

| WS | Title | Status | Sprint plan |
|---|---|---|---|
| **WS1** | Architecture baseline | ✅ DONE | [ws1-architecture-baseline.md](docs/sprint-plans/ai-traders/ws1-architecture-baseline.md) |
| **WS2** | Canonical trade pipeline | ✅ DONE | [ws2-canonical-pipeline.md](docs/sprint-plans/ai-traders/ws2-canonical-pipeline.md) |
| **WS3** | Data foundation | ✅ DONE | [ws3-data-foundation.md](docs/sprint-plans/ai-traders/ws3-data-foundation.md) |
| **WS4** | Training center | ✅ DONE (S-AI-WS4 + S-AI-WS4-FU) | [ws4-training-center.md](docs/sprint-plans/ai-traders/ws4-training-center.md) + [ws4-followups.md](docs/sprint-plans/ai-traders/ws4-followups.md) |
| **WS5** | Baseline models | 🔄 IN PROGRESS (S-AI-WS5-A + S-AI-WS5-B-PART-1 + PART-2 (PR 2A + PR 2B) + S-AI-WS5-C + S-AI-WS5-C-FU + S-AI-WS5-D + S-AI-WS5-E done; F queued) | [ws5-baseline-models.md](docs/sprint-plans/ai-traders/ws5-baseline-models.md) |
| **WS6** | Open-source model layer | 📋 NOT STARTED | [ws6-open-source-models.md](docs/sprint-plans/ai-traders/ws6-open-source-models.md) |
| **WS7** | Deployment tiers | 📋 NOT STARTED | [ws7-deployment-tiers.md](docs/sprint-plans/ai-traders/ws7-deployment-tiers.md) |
| **WS8** | Monitoring and feedback loops | 📋 NOT STARTED | [ws8-monitoring-feedback.md](docs/sprint-plans/ai-traders/ws8-monitoring-feedback.md) |
| **WS9** | Oracle / Hugging Face runtime split | 🔄 CONTINUOUS | [ws9-runtime-split.md](docs/sprint-plans/ai-traders/ws9-runtime-split.md) |
| **WS10** | Architecture-doc enforcement | 📋 NOT STARTED | [ws10-arch-doc-enforcement.md](docs/sprint-plans/ai-traders/ws10-arch-doc-enforcement.md) |

**Non-negotiable rules:**

- Live trading safety > feature growth.
- No heavy training on the Oracle live VM (WS9).
- No model in live strategy logic without staged promotion +
  operator approval.
- AI output cannot bypass risk caps, broker validation, or
  mission-aware account restrictions.
- Architecture-changing code updates the architecture docs in
  the same PR.
- No auto-publishing datasets to HF (S-AI-WS3).
- No editing past `StatusEvent` entries in the registry
  (S-AI-WS4).
- No promoting to `live-approved` or `champion` without operator
  approval recorded in `--by` + `--reason` (S-AI-WS4).
- No outcome columns as features against `won` on `trade_outcomes`
  (S-AI-WS5-A).
- **No `ICT_OFFVM_BUILD_HOST=1` on the Oracle live VM**
  (S-AI-WS5-B-PART-1).

### Active milestone queue (next 3)

1. **M6 — Web app UI (dashboard repo).**
2. **(M5 P4 closed 2026-05-10).**
3. **Closed-flat invariant auto-flatten promotion** — gated on ≥ 7 days clean alert-only soak.

> **AI-traders queue note:** WS1+WS2+WS3+WS4+WS5-A+WS4-FU+WS5-B-PART-1 closed
> 2026-05-10. **Next on AI-traders track is WS5-B-PART-2** — regime
> classifier + Bybit off-VM fetch wiring (operator owns the wiring;
> needs read-only Bybit V5 creds + a non-VM build host with
> `ICT_OFFVM_BUILD_HOST=1`).

### Repo and hosting boundary (MANDATORY)

Dashboard web app **lives in a separate repository** and **runs
on Vercel** — NOT on the Oracle VM.

---

## Historical Sprint Ledger

Full detail preserved in git history. Recent AI-traders sprints:

| Sprint | Title | Status | M-mapping |
|---|---|---|---|
| S-AI-ROADMAP | AI traders models roadmap adopted | ✅ Done (`#693` `1eb59f6`) | M9, M10 |
| S-AI-WS1 | Architecture baseline | ✅ Done (`#694` `f453b89`) | M9 |
| S-AI-WS2 | Canonical trade pipeline | ✅ Done (`#701` `42a1e6f`) | M9 |
| S-AI-WS3 | Data foundation | ✅ Done (`#704` `60807f4`) | M10 |
| S-AI-WS4 | Training center | ✅ Done (`#719` `b910fd3`) | M9 |
| S-AI-WS5-A | Outcome probability baseline | ✅ Done (`#730` `6a9f5a0`) | M9 |
| S-AI-WS4-FU | WS4 follow-ups | ✅ Done (`#732` `8a69e97`) | M9 |
| **S-AI-WS5-B-PART-1** | **WS5-B Part 1 — `market_raw` multi-source adapter framework.** Canonical row shape pinned. CSV adapter live; Bybit off-VM scaffold (env-gated) with the actual exchange call filed for operator wiring. WS9 enforced via `ICT_OFFVM_BUILD_HOST=1` env-gate. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-1.md`. | ✅ Done 2026-05-10 (`#733`) | M10 |
| **S-AI-WS5-B-PART-2 PR 2A** | **WS5-B Part 2 PR 2A — Bybit off-VM fetch wiring.** `BybitOffvmMarketRawAdapter._fetch_bars` wired via ccxt's `fetch_ohlcv`; paginated `since` cursor over `[start, end]`; CI mocks the exchange. Builder framework auto-forwards `symbol_scope` / `timeframe` into `iter_rows` kwargs. Env-gate retained. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2A.md`. | ✅ Done 2026-05-10 (`#742`) | M10 |
| **S-AI-WS5-B-PART-2 PR 2B** | **WS5-B Part 2 PR 2B — Regime classifier baseline.** `market_features` family (rolling vol + 3-class regime label, forward-window leakage discipline) + `RegimeClassifierTrainer` (per-bucket modal) + `MulticlassPredictor` + `MulticlassClassificationEvaluator` + `baseline-regime-classifier.yaml` manifest. New non-negotiable: no forward-window / label columns as features against `regime_label`. Logged in `docs/sprint-logs/S-AI-WS5-B-PART-2-PR-2B.md`. | ✅ Done 2026-05-10 (`#745`) | M9 |
| **S-AI-WS5-C** | **WS5-C — Setup quality scorer.** `setup_labels` family (CLOSED, non-backtest, non-empty `setup_type` trades; emits `r_multiple = pnl_percent / risk_pct` capped at `±r_cap`) + `PerStrategyWinRateTrainer` extended with `target_kind: numeric_mean` knob (per-bucket sample mean of any numeric target) + `baseline-setup-quality.yaml` manifest using `RegressionEvaluator`. Architecture-canonical doc gains an explicit "AI-traders training workflow" section anchored on the `/health-review` skill's per-trade decision grades as labelled feedstock. Training-center doc gains a "Training session workflow" + table of established manifests so future training sessions follow the documented path. Logged in `docs/sprint-logs/S-AI-WS5-C.md`. | ✅ Done 2026-05-10 (`#754`) | M9 |
| **S-AI-WS5-C-FU** | **WS5-C follow-up — setup-quality V2 (audit-joined source).** New `setup_labels_audit` family: joins `runtime_logs/signal_audit.jsonl` recorded setups with the matching CLOSED trade by composite key `(strategy, symbol, timestamp ± window)` (no stable signal_id exists). Emits the same `r_multiple` label as v1 plus audit-time features (`audit_pattern`, `audit_side`, `audit_confidence`, `audit_bars_back_of_setup`). Rejected audits (`stage_rejections` non-empty, or no `entry`/`price`) are dropped — survivorship documented. Paired manifest `baseline-setup-quality-audit.yaml` using `audit_pattern` as the feature column for direct comparison against v1's `setup_type` baseline. Logged in `docs/sprint-logs/S-AI-WS5-C-FU.md`. | ✅ Done 2026-05-10 (`#759`) | M9 |
| **S-AI-WS5-D** | **WS5-D — Execution quality scorer.** New `execution_quality` family joining `trades` ↔ `order_packages` on `linked_trade_id`. Emits `entry_slippage_bps = ((actual_entry - intended_entry) / intended_entry) * 10_000` signed by direction (positive = trader paid worse than intended), capped at `±slippage_cap_bps` (default 200 bps). Carries `fill_latency_seconds` as bookkeeping. Paired manifest `baseline-execution-quality.yaml` reuses `PerStrategyWinRateTrainer` (numeric_mean) with `RegressionEvaluator` and time-aware holdout on `trade_created_at`. Same chassis as WS5-A / WS5-C / WS5-C-FU. Logged in `docs/sprint-logs/S-AI-WS5-D.md`. | ✅ Done 2026-05-10 (`#760`) | M9 |
| **S-AI-WS5-E** | **WS5-E — Post-trade review baseline.** First WS5 baseline whose label is reviewer-derived (not P&L-derived). New `review_journal` family scans `comms/requests/REQ-*.json` + `comms/archive/REQ-*.json`, parses the embedded health-review JSON payload from `.response.answers[*].free_text`, and emits one row per `trade_decision_grades[]` entry. Letter grade A/B/C/D/F maps to `decision_grade_score` 4/3/2/1/0; unknown letters drop the row. Paired manifest `baseline-post-trade-review.yaml` predicts per-`setup` mean grade-score using the same `PerStrategyWinRateTrainer` (numeric_mean) chassis. Empty-state acceptable until operator answers prompts with the JSON template. Logged in `docs/sprint-logs/S-AI-WS5-E.md`. | ✅ Done 2026-05-10 (this PR) | M9 |
| **S-CFW-1** | **Cloudflare Worker proxy.** Stable `*.workers.dev` hostname fronting `/api/*` to the VM. **RETIRED 2026-05-10 in S-CFW-1-FU2** — Worker deployed cleanly but its outbound `fetch()` to a raw IPv4 host is rejected by Cloudflare with error 1003. Logged in `docs/sprint-logs/S-CFW-1-cloudflare-worker.md`. | 🪦 RETIRED 2026-05-10 (`#735`) | infra |
| **S-CFW-1-FU** | **cf-worker GitHub-Actions deploy.** `cf-worker-deploy` workflow that runs `wrangler deploy` from CI. Logged in `docs/sprint-logs/S-CFW-1-FU-gha-deploy.md`. **Workflow + label remain in the repo as a recipe;** unused now that the Worker layer is retired (S-CFW-1-FU2). | 🪦 RETIRED 2026-05-10 (`#740`) | infra |
| **S-CFW-1-FU2** | **Worker retired + tunnel verified.** Empirically retired the cf-worker layer after the deployed Worker hit Cloudflare error 1003 on raw-IP `fetch()`. Corrected the wrong claim in `docs/audit/vercel-edge-vs-cf-worker.md` (Workers do NOT allow raw-IPv4 targets — only DNS hostnames). Extended `pull_logs.sh` and verified the live `*.trycloudflare.com` URL (`planners-lbs-blind-trainer.trycloudflare.com`) — same as the 2026-05-10 wrap-up, so the dashboard's existing Vercel rewrite remains healthy. Logged in `docs/sprint-logs/S-CFW-1-FU2-worker-retired.md`. | ✅ Done 2026-05-10 | infra |

> **Sprint number note:** S-067 is in flight as the silent-empty
> audit; AI traders track uses themed `S-AI-*` ids with
> sub-sprint suffixes for multi-part work.

---

## Standing / Recurring Sessions

| Type | Cadence | Cap |
|---|---|---|
| Hardening & Stability Audit | Bi-daily | 3h |
| Strategy Improvement Review | Weekly | 4h |
| Model Training & Evaluation | Weekly (HF cron) | 6h (offloaded) |

---

## Items Under Consideration (Not Yet Scheduled)

- Recurring-Session Triggers + `/roadmap` Command.
- Exchange Failover / Multi-Exchange Support.
- Deployment Automation.
- Tier 2 follow-up: live-path migration onto WS2 types.
- **Cloudflare named tunnel migration** — replace the
  ephemeral `*.trycloudflare.com` quick tunnel with a named
  tunnel at `bot.<our-domain>` (now the only viable
  stable-URL path after S-CFW-1-FU2 retired the Worker layer).
  **Prereq:** operator adds a domain to Cloudflare (zone with
  nameservers pointed at CF). When met, ~30 min sprint.
- **CFI auto-flatten promotion** — if `runtime_logs/invariant_violations.jsonl`
  stays at zero through 2026-05-17 (7-day soak from the
  alert-only enable on 2026-05-10, issue #683), file the PR that
  promotes the invariant from alert-only to auto-flatten.
- **Tunnel-URL auto-refresh** — VM-side hook that pushes the
  new `*.trycloudflare.com` URL into Vercel (via API) every
  time `setup_cloudflare_tunnel.sh` produces a new URL.
  Eliminates the operator-update step on tunnel restart.
  Smaller scope than the named-tunnel migration; useful
  in-between if the named tunnel keeps slipping.
- Per-family dataset builders for `market_features`, `setup_labels`,
  `account_context`, `review_journal`.
- `python -m ml.datasets publish` HF subcommand.
- Aggregated walk-forward.
- Per-strategy detail metrics artifact.
- Registry concurrent-writer locking.
- **WS5-B-PART-1 follow-ups:** `yfinance` adapter; `binance_offvm`
  adapter; on-disk Parquet adapter; the actual Bybit off-VM
  fetch wiring (filed under WS5-B-PART-2).

---

## Sprint File Naming Convention

`docs/sprints/sprint-NNN-prompt.md`. AI-traders workstream sprint
plans live under `docs/sprint-plans/ai-traders/wsN-<slug>.md`.
Themed ids (`S-AI-WSN`, optionally `-A`/`-B`/.../`-FU` /
`-PART-N` for sub-sprints) parallel the numeric sequence.

---

## Status Key

| Symbol | Meaning |
|---|---|
| ✅ Done | Completed and merged |
| 🔜 Next | Immediate next sprint |
| 🔄 In Progress | Currently being executed |
| ⚠️ Reopened | Verification revealed drift |
| ⛔ Blocked / Scratched | Cannot proceed or cancelled |
| 📋 Backlog | Defined but not yet started |
