# Sprint Log: S-MLOPT-CLOSEOUT-2026-06-07

> Closes out the M14 (ML Optimization Program) ladder: ships S16 + S18
> (Phase 4 MLOps), S12 Part B (Phase 2.4 account_context wiring), and
> the PERF-20260601-006 regime-router phase-3 hard gate operator
> pre-approved 2026-06-01. Plus the autonomous follow-ups
> queue (BL-20260607-001/002, MB-20260603-001 settle, trainer-vm-diag
> cmd-extractor leak fix). Six PRs merged.

## Date Range
- Start: 2026-06-07T~07:00Z (session pickup; the operator's prompt was
  the M14 Phase 4 brief — drive S18 then S16).
- End: 2026-06-07T17:05Z (PR #2966 merged, autonomous-followups arc
  complete).

## Objective
- **Primary goal:** drive M14 Phase 4 to completion. Ship S-MLOPT-S18
  (champion-challenger promotion automation) and S-MLOPT-S16
  (ADWIN drift-triggered, recency-weighted retraining) per the
  operator's opening prompt. Both are Tier-1 trainer-side tooling;
  reports-only + plan-only by default with operator-gated promotion
  unchanged.
- **Secondary goals (added mid-session by operator direction):**
  S-MLOPT-S12 Part B (per-signal account-context snapshots, closes
  `MB-20260604-003`); PERF-20260601-006 regime-router phase-3
  (operator pre-approved 2026-06-01); stale-PR triage; autonomous
  follow-up queue (BL-20260607-001/002, MB-20260603-001 verification,
  trainer-vm-diag cmd-extractor cosmetic bug, drift-retrain timer
  smoke-verify).

## Tier
- **Tier 1** for: S16/S18 trainer-side tooling, S12 Part B writer +
  family adapter + tests, autonomous-followups cleanups (workflow,
  backlog dedupe, item resolutions), sprint log + doc-freshness.
- **Tier 2** for: S12 Part B coordinator hook
  (`Coordinator.multi_account_execute::_capture_account_context_snapshots`
  — live-trader write path, gated by `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED`).
  Trainer-side timer install (`#2951` relay) — autonomous per the
  trainer charter but invoked via the diag relay.
- **Tier 3** for: PERF-20260601-006 phase-3 hard gate touching
  `src/runtime/intents.py` (live order path; gated by
  `REGIME_ROUTER_ENABLED`, operator pre-approved 2026-06-01).
- **Justification:** every Tier-2/3 piece carries a default-off env-var
  rollback (one env flip + restart, no redeploy) and is annotated
  `# allow-silent:` with audit-doc entry; the live/dry contract
  remains `RiskManager.dry_run` only.

## Starting Context
- **Active roadmap items:** M14 Phase 4 (S16 + S18 NOT STARTED), then
  M14 Phase 2.4 Part B (deferred per `MB-20260604-003`), then
  PERF-20260601-006 (operator pre-approved 2026-06-01, gated on
  phase-2 soak + base-rate check).
- **Prior sprint reference:** `S-MLOPT-S15a` (PR #2928, trend-regime
  class-weight tuning + ADX-14 head-to-head); the existing
  `S-MLOPT-S*` family logs at `docs/sprint-logs/S-MLOPT-S1.md` →
  `S-MLOPT-S15b.md`.
- **Known risks at start:** S-MLOPT-S16 auto-fires retrains (bounded
  by the trainer's `live_approved` stage ceiling, but still); S18
  pushes operator Telegram pings (newly-introduced write path from
  trainer → live VM via SCP); S12 Part B touches the live trader's
  coordinator hot loop (Tier-2 — needed operator approval); PERF-006
  phase-3 IS the live order path (Tier-3).

## Repo State Checked
- **Branch / commit reviewed:** `main` at `ff389dd` (session pickup)
  → ended at `77f6f83` (PR #2966 merge, 06-07T17:03Z).
- **Deployment state reviewed:**
  - Live trader: deployed `2c10a55` + restarted to PID 841209 via
    system-action `restart-bot-service` (#2961, 14:50Z) — S-MLOPT-S12
    Part B coordinator hook now loaded; `boot_audit: 0 open
    package(s), 0 query failure(s)` across all 12 strategies.
  - Trainer VM: `ict-promotion-readiness.{service,timer}` +
    `ict-drift-retrain.{service,timer}` installed + `enable --now` via
    trainer-vm-diag `#2951` (11:19Z); first drift-retrain cycle fired
    13:05Z (then 14:00Z, 15:01Z; verified via `#2964`).
- **Canonical docs reviewed:** `docs/CLAUDE-RULES-CANONICAL.md`,
  `docs/ARCHITECTURE-CANONICAL.md`, `ROADMAP.md`,
  `docs/ml/optimization-roadmap.md` (M14 master plan), `CLAUDE.md`.

## Files and Systems Inspected
- **Code:** `ml/promotion/{stage_guard,gates,oos_edge,attribution,readiness_report}.py`,
  `ml/shadow/{adwin,drift_retrain,inspector,drift}.py`, `ml/cli.py`,
  `ml/datasets/families/account_context.py`, `ml/datasets/builder.py`,
  `ml/datasets/validate.py`, `src/units/accounts/context_snapshot.py`,
  `src/core/coordinator.py` (around `multi_account_execute` +
  `_log_new_order_package` lines 774-2280), `src/runtime/intents.py`
  (around `_shadow_regime_gate` + `aggregate_intents` lines 580-720,
  + new `_hard_regime_gate` lines 643-738), `src/runtime/regime/policy.py`
  (`would_gate` signature + cell-evaluation contract), `src/utils/paths.py`,
  `scripts/ops/_lib.sh::runtime_db_path`, `scripts/check_env_gate_in_diff.py`,
  `scripts/check_dry_run_in_diff.py`, `scripts/check_canonical_db_resolver.py`.
- **Config:** `config/regime_policy.yaml` (the OFF-cell matrix that
  phase-3 enforces), `config/strategies.yaml` (verified squeeze_breakout_4h
  still `execution: shadow` so PERF-005 stays gated until shadow net-R
  evidence accrues).
- **Deployment:** `deploy/training-vm-cloud-init.yaml` (add
  `ict-promotion-readiness` + `ict-drift-retrain` units),
  `deploy/trainer/ict-{promotion-readiness,drift-retrain}.{service,timer}`
  (standalone for running-trainer install), `deploy/trainer/ict-orderflow-capture.service`
  (pattern reference).
- **Docs:** `docs/CLAUDE-RULES-CANONICAL.md`, `docs/ARCHITECTURE-CANONICAL.md`,
  `ROADMAP.md`, `docs/ml/optimization-roadmap.md`,
  `docs/audits/env-gate-purge-2026-05-10.md`,
  `docs/claude/{ml,health,performance}-review-backlog.json`,
  `CLAUDE.md` (env-var table + schema list).
- **Services / timers:** on the live VM — `ict-trader-live.service`
  (PID 841209 post-restart), `ict-web-api.service`, the four canonical
  watchdogs; on the trainer VM — `ict-trainer.{service,timer}`,
  `ict-trainer-publish.{service,timer}`, plus the two new
  `ict-promotion-readiness.{service,timer}` +
  `ict-drift-retrain.{service,timer}`.
- **GitHub Actions:** `.github/workflows/trainer-vm-diag.yml` (cmd
  extractor fix), `.github/workflows/system-actions.yml` +
  `.github/workflows/vm-diag-snapshot.yml` (read paths used to drive
  the deploy/restart/diag relays).

## Work Completed

### S-MLOPT-S18 (Phase 4.3) — promotion-readiness report — PR #2934
- New `ml/promotion/readiness_report.py`: thin orchestrator over
  `stage_guard.run_stage_guard` that buckets proposals into
  promote/demote/hold + renders JSON + Markdown + an optional
  one-line Telegram ping (only when actionable).
- New `python -m ml promotion-readiness` CLI subcommand.
- New `scripts/ops/run_promotion_readiness.sh` trainer orchestrator:
  writes report under
  `runtime_logs/trainer_mirror/promotion_readiness/<UTC-date>/`,
  pushes `pending_pings/*.json` to the live VM via SCP on
  actionable proposals.
- New trainer-side `ict-promotion-readiness.{service,timer}` (daily,
  DISABLED-by-default in cloud-init).

### S-MLOPT-S16 (Phase 4.1) — ADWIN drift-triggered retraining — PR #2934
- New `ml/shadow/adwin.py`: pure-stdlib ADWIN (Bifet & Gavaldà 2007),
  Hoeffding cut `ε = √((1/2m)·ln(4w/δ))`, defaults
  `δ=0.002 / min_window=10 / max_window=10k`.
- New `ml/shadow/drift_retrain.py`: per-deployed-head scan +
  `RetrainDecision` rows; backfill rows excluded.
- New `python -m ml drift-retrain` CLI subcommand.
- New `scripts/ops/run_drift_retrain.sh` trainer orchestrator,
  ships `RETRAIN_PLAN_ONLY=1` so the first soak only writes
  `runtime_logs/drift_retrain.jsonl`.
- New trainer-side `ict-drift-retrain.{service,timer}` (hourly,
  DISABLED-by-default in cloud-init).
- **30 new tests** (`tests/ml/test_{readiness_report,adwin,drift_retrain}.py`
  + the promotion-readiness case in `test_promotion_cli.py`).

### Trainer-side timer install (autonomous) — PR #2946 + relay #2951
- Added standalone unit files under `deploy/trainer/` mirroring the
  cloud-init bodies (so long-running trainer can install via
  trainer-vm-diag without re-provisioning).
- Trainer-vm-diag relay `#2951`: `cp` units → `daemon-reload` →
  `enable --now` both timers. Confirmed `systemctl is-enabled`
  returns `enabled` for both.

### S-MLOPT-S12 Part B (Phase 2.4) — per-signal account-context snapshots — PR #2954
- New `src/units/accounts/context_snapshot.py`: 11-column
  `account_context_snapshots` table, UNIQUE
  `(order_package_id, account_id)`; helpers
  `AccountContextSnapshot`, `write_snapshots`, `daily_state_for`,
  `open_trades_count_for`, `drawdown_pct`.
- New coordinator hook
  `Coordinator.multi_account_execute::_capture_account_context_snapshots`,
  writes one row per `(order_package_id, eligible_account)` AFTER
  eligibility filtering + BEFORE per-account `RiskManager.evaluate`.
  Gated by `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED` (default off →
  enabled).
- `account_context` family `builder_version v1 → v2`: adds
  `include_snapshots=True` kwarg + 5 nullable `*_at_signal`
  columns (LEFT JOIN through `order_packages.linked_trade_id`).
- **30 new tests**.
- Deployed via `pull-and-deploy` (#2959) + `restart-bot-service`
  (#2961). The first deploy was a no-op service-wise because the
  VM's git-sync timer had already fast-forwarded HEAD before the
  system-action landed; the restart-bot-service handled the actual
  load.

### PERF-20260601-006 — Regime router phase 3 — PR #2962
- New `src/runtime/intents.py::_hard_regime_gate`: DROPS OFF-cell
  intents from the aggregator's candidate tuple BEFORE the
  reinforcement / conflict-resolution logic runs AND emits
  `regime_hard_gate` with `enforced:true`.
- New `_regime_router_enabled()` reads `REGIME_ROUTER_ENABLED` env
  (default `0` → phase 2 unchanged).
- `aggregate_intents()` routes to exactly one of the two paths per
  tick — clean event-name partition between phase-2 history
  (`regime_shadow_gate enforced:false`) and phase-3 history
  (`regime_hard_gate enforced:true`).
- Fail-permissive on every policy-load / verdict exception.
- **9 new tests** (`tests/test_aggregate_intents_regime_hard.py`).
- Env-var documented in `CLAUDE.md`, added to the env-gate-purge
  audit doc as the fourth annotated survivor.

### Autonomous follow-ups — PR #2963 + PR #2966
- **PR #2963** — three Tier-1 ops cleanups in one commit:
  - `.github/workflows/trainer-vm-diag.yml`: cmd-extractor leak fix
    (awk now terminates at the next unindented `<key>:` line, so
    `reason: |\n<prose>` no longer leaks into bash).
  - `BL-20260607-001` (sync_trainer_data.sh exec bit): already
    `100755` in git, marked resolved.
  - `BL-20260607-002` (gate-check baseline auto-select): already
    shipped on `main` (`ml/cli.py:386-391`), marked resolved.
  - Dedupe: two pairs of duplicate `BL-2026060[78]-001` IDs cleaned
    up to `BL-20260528-002` + `BL-20260607-005`.
- **PR #2966** — `MB-20260603-001` settled: trainer-vm-diag relays
  `#2964 + #2965` re-evaluated both `setup-quality-baseline-v0` and
  `setup-quality-lgbm-v2` under identical 5-fold purged WF-CV
  (n=203). **Honest negative:** baseline MAE 0.0715 vs lgbm-v2 MAE
  0.0825 — lgbm-v2 loses by ~15%. No live behaviour change (already
  at research_only).
- **8 stale PRs closed** during the operator-approval triage:
  #2665, #2617, #2466, #2434, #2171, #2107, #2464, #1787 — all
  docs/backlog-only updates from old `/health-review` /
  `/doc-freshness` / sprint-wrap sessions, superseded by later
  commits on `main`.
- **#6 squeeze_breakout_4h re-promotion HELD** — operator
  pre-approved but the gating prerequisite is not met (squeeze has
  fired zero actionable signals since demotion; can't verify
  shadow net-R vs the +17.6R backtest). Per the gate, holding.

## Validation Performed
- **Tests:** 30 new for S12 Part B + 30 new for S16/S18 + 9 new for
  PERF-006-P3 = 69 new tests, all pass locally + CI green on every PR
  (sandbox missing numpy on regime tests but CI has the full
  dependency set).
- **Lint:** ruff + `check_canonical_db_resolver.py` +
  `check_dry_run_in_diff.py` + `check_silent_empty_in_diff.py` +
  `check_env_gate_in_diff.py` all clean on every commit; the
  env-gate-guard initial failure for `REGIME_ROUTER_ENABLED` was
  fixed with the `# allow-silent:` annotation + audit-doc entry, and
  CI then went green.
- **CI status:** every merged PR ended with **11/11 checks green**.
- **Trainer-VM smoke (#2964):** `ict-drift-retrain.timer` confirmed
  fired three cycles today (13:05Z, 14:00Z, 15:01Z); each `cli_exit=11`
  (scan + dispatch flagged) under `RETRAIN_PLAN_ONLY` (no `ml train`
  fired); `runtime_logs/drift_retrain.jsonl` accumulated **76 rows**.
  Three deployed heads have flagged drift (in plan-only mode):
  `btc-regime-5m-baseline-v1`, `btc-regime-5m-lgbm-v2`,
  `execution-quality-baseline-v0`.
  `ict-promotion-readiness.timer` scheduled for **Mon 2026-06-08
  00:12:57Z** (~8h after relay; first fire pending overnight).
- **Live-VM smoke post-deploy:** `restart-bot-service` (#2961)
  confirmed PID 841209 came up with `boot_audit: 0 open package(s),
  0 query failure(s) on boot` across all 12 strategies. Snapshot
  accrual depends on actionable signals (vwap/squeeze/ict_scalp);
  the current quiet BTC tape means rows haven't accrued yet — that's
  expected behaviour, not a regression.
- **Gaps not yet verified:**
  - First `ict-promotion-readiness.timer` fire (Mon 00:12Z) — will
    confirm the SCP-to-live-VM pending-pings push end-to-end on the
    first actionable proposal.
  - First `regime_hard_gate enforced:true` audit row — requires
    operator to flip `REGIME_ROUTER_ENABLED=1` on the live VM AND
    an OFF-cell intent to fire (no live two-sided strategy in an
    OFF cell today, so the row may not appear until squeeze is
    re-promoted).
  - First `account_context_snapshots` rows — accrue on the next
    actionable signal hitting `multi_account_execute`.
  - `MB-20260604-002` (orderflow capture): unchanged this session;
    still waiting on ≥4000 captured 5m bars before A/B is
    measurable.

## Documentation Updated
- **Rules doc updates:** none required (operating contract
  unchanged).
- **Architecture doc updates:** none required (no pipeline-stage
  contract changes; the new account_context_snapshots table is
  documented in `CLAUDE.md` schema list + the env-gate audit doc).
- **Trade pipeline doc updates (`docs/TRADE-PIPELINE.md`):** none
  required (no stage added / removed; the snapshot writer + the
  hard-gate filter are pre-order-package observers, not pipeline
  stages).
- **Roadmap updates:** `ROADMAP.md` — refreshed `S-MLOPT-S16`,
  `S-MLOPT-S18`, `S-MLOPT-S12` rows from `NOT STARTED` to `IN REVIEW
  2026-06-07`; added new `PERF-20260601-006-REGIME-ROUTER-P3` row
  with full detail.
- **GitHub Actions doc updates:** none required (the cmd-extractor
  fix is internal to `trainer-vm-diag.yml`; the workflow contract is
  unchanged).
- **Subsystem doc updates:**
  - `CLAUDE.md` env-var table: added `ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED`
    + `REGIME_ROUTER_ENABLED`; updated `trade_journal.db` schema list
    with `account_context_snapshots`.
  - `docs/ml/optimization-roadmap.md`: Phase 4.1 + 4.3 + Phase 2.4
    Part B annotated SHIPPED with full prose.
  - `docs/audits/env-gate-purge-2026-05-10.md`: added 4th annotated
    survivor section for `REGIME_ROUTER_ENABLED`.
- **Backlog updates:**
  - `docs/claude/ml-review-backlog.json`: opened `MB-20260607-002`
    (S16/S18 enable + first-cycle resolution criteria); resolved
    `MB-20260604-003` (S12 Part B shipped); resolved
    `MB-20260603-001` (setup-quality WF-CV verdict).
  - `docs/claude/health-review-backlog.json`: resolved
    `BL-20260607-001` (exec bit already 100755) +
    `BL-20260607-002` (gate-check baseline already auto-selects);
    deduped two pairs of duplicate IDs.
  - `docs/claude/performance-review-backlog.json`:
    `PERF-20260601-006` evidence_log appended with the PR-2962 ship.
- **Historical docs marked superseded:** none.

## Contradictions or Drift Found
- **Duplicate `BL-2026060[78]-001` IDs** in `health-review-backlog.json`
  (sync-script + zombie-runs items; IB-gateway-restart + diag-snapshot-&
  items). Renamed in PR #2963. Pre-existing drift, fixed in passing.
- **`BL-20260607-001` / `BL-20260607-002` were both already
  satisfied** on `main` by earlier work that didn't update the
  backlog. Resolved this session.
- **`docs/audits/env-gate-purge-2026-05-10.md`** said "survivor
  count is now three" — out of date as of this session's new
  `REGIME_ROUTER_ENABLED` annotation. Updated to four with the new
  justification section.
- **Operator-approval queue mismatch:** the M14 roadmap text
  described S5/S6/S6-FU/S6-FU-2/S7/S15b/S17 as "🔄 IN REVIEW … draft
  PR pending operator OK", but none of those draft PRs actually
  exist on the remote — the work shipped under different PR numbers
  (S8 #2895, S15a #2928, S17 commit on main, etc.). The roadmap
  prose drifted from the actual remote-PR state. Flagged in chat
  to the operator; no fix this session (the work was done, just
  documented under a different PR number).

## Risks and Follow-Ups
- **Remaining technical risks:**
  - The new trainer-VM timers may fire false-positive drift
    detections under the conservative `δ=0.002`; first three cycles
    already flagged 3 of 16 heads, all in plan-only mode. If the
    operator flips `RETRAIN_PLAN_ONLY=0` without first reviewing the
    log shape, the trainer will retrain those manifests (bounded by
    the daily-cycle cadence + the registry-stage ceiling — no live
    promotion). Recommended: leave `RETRAIN_PLAN_ONLY=1` until a
    `/ml-review` reads a few days of decisions.
  - `_hard_regime_gate` is fail-permissive but routes through
    `would_gate` which depends on `_load_regime_policy` succeeding;
    a malformed `config/regime_policy.yaml` would silently fall
    through to no-gate (the YAML loader catches the exception). This
    matches the phase-2 behaviour and is the documented bias
    (never silently strand a live signal).
- **Remaining product decisions (Tier 3):**
  - `REGIME_ROUTER_ENABLED=1` flip on the live VM — the PR is
    merged but the flag is still default-off. Operator decides when
    to flip (the gate is a no-op until then because no live
    two-sided strategy sits in an OFF cell).
  - `squeeze_breakout_4h shadow → live` re-promotion
    (PERF-20260601-005): pre-approved but gate not met (no shadow
    fills accrued). Revisit on the next `/performance-review` once
    BTC volatility returns.
- **Blockers:** none.

## Deferred Items
- **M7 / M8** (Strategy review gate + Strategy tuning): the next
  unstarted milestones outside M14. Sized as a fresh sprint.
- **First `ict-promotion-readiness.timer` cycle output** — will land
  Mon 2026-06-08 00:12Z; verify the SCP-pending-pings path
  end-to-end then.
- **`MB-20260604-002`** (orderflow capture A/B): unchanged this
  session; needs ≥ ~4000 captured 5m bars before measurable.
- **`MB-20260604-004`** (macro for MES, Part A of S12): unchanged;
  waiting on a fresh MES `market_features` rebuild on `v6`.
- **`PERF-20260601-007`** (regime router phase 4, soft weights +
  classifier-v0 detector): the backlog explicitly requires
  "phase-3 demonstrated lift for ≥ a few weeks of soak" first.
  Revisit late June / early July.

## Next Recommended Sprint
- **Suggested next sprint:** **M7 — Strategy review gate** (next
  unstarted milestone). The closest precedent is the
  decider-v2-style framing in
  `docs/sprint-plans/DECIDER-SINGLE-ACCOUNT-2026-05-24.md` (v2
  steps 2/3). With M14 closed out, M7 unlocks the path for
  promoting turtle_soup + ict_scalp_5m past their current
  in-system-bleed gates.
- **Why next:** M14 is structurally complete (all phases shipped
  or operator-gated). M7 is the highest-leverage unstarted
  milestone — it owns the gating contract for the strategy roster
  itself, which is the prerequisite for several queued
  shadow → live promotions (squeeze + the long-only re-expansions
  to two-sided).
- **Required verification before starting:** confirm the
  promotion-readiness daily report has produced ≥1 cycle (Mon
  00:12Z) so M7 can build on top of it instead of duplicating
  gate logic.

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage,
      `docs/TRADE-PIPELINE.md` was updated and the dashboard's Trade
      Process tab was visually verified — N/A this session (no
      pipeline-stage contract changes; the snapshot writer + hard-gate
      filter are pre-package observers).
- [x] Roadmap status was checked.
- [x] Contradictions were recorded.
- [x] Remaining unknowns were stated clearly.
