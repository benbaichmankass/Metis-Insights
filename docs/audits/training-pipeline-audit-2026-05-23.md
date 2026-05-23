# Training-Pipeline Audit — S-STRAT-IMPROVE (2026-05-23)

> **Tier-1.** Operator directive 2026-05-23: while waiting on backtests,
> verify the autonomous training pipeline won't break when switched on —
> that the recurring trainer is correctly wired to strategies, feeds from
> the **synced DB / cache (not live fetches that time out)**, and stays
> current as we change strategies. Nothing here touches the live trader.

## Scope audited
- Autonomous trainer: `ict-trainer.{service,timer}` (in
  `deploy/training-vm-cloud-init.yaml`, daily, **disabled by default** —
  operator/Claude opts in via trainer-vm-diag).
- Cycle body: `scripts/ops/run_training_cycle.sh`.
- Data sync: `scripts/ops/sync_trainer_data.sh`.
- Dataset build: `scripts/ops/build_trainer_datasets.sh` (9 families).
- Manifests: `ml/configs/*.yaml`.
- Doc: `docs/ml/training-center.md`.

## What's ROBUST (no action needed)

1. **Strategy wiring auto-adapts to roster changes.** The journal-backed
   families (`trade_outcomes`, `setup_labels`, `execution_quality`,
   `account_context`, `backtest_results`, `setup_labels_audit`) build
   *per-strategy from `trade_journal.db` rows* — they do **not** hardcode
   `vwap`/`turtle_soup`/`ict_scalp`. So adding a new strategy, retiring
   vwap, or retuning ict_scalp needs **no manifest edits**: new-strategy
   rows are picked up automatically; retired-strategy rows simply stop
   accumulating. Shadow models also auto-wire to new strategies
   (2026-05-19 default-flip). ✅
2. **Manifests are auto-discovered.** `run_training_cycle.sh` defaults
   `TRAINING_MANIFESTS` to every `ml/configs/*.yaml` — new baselines are
   trained automatically; no central list to maintain. ✅
3. **Empty/missing data is a clean skip, not a break.** `train` exits 78
   (EmptyDatasetError) → cycle logs `manifest_skipped`, `overall_rc`
   unchanged. A family build failure is logged + non-fatal; the other
   families still build. So a single bad/empty feedstock can't abort the
   cycle. ✅
4. **Journal feedstock is a DB file sync, not a live exchange call.**
   `sync_trainer_data.sh` rsyncs `trade_journal.db` +
   `signal_audit.jsonl` from the live VM (`/data/bot-data/`) over SSH —
   no exchange API, no timeout exposure for the strategy-outcome models. ✅
5. **Docs match reality.** `training-center.md` accurately describes the
   cadence, shared sync, auto-iteration, and the market_raw off-VM build. ✅

## RISKS found

1. **`market_raw` / `market_features` do a LIVE Bybit klines fetch**
   (ccxt `bybit_v5_offvm` adapter, 2024-01-01 → today, growing daily) —
   the regime-classifier feedstock. This is the one live pull in the
   cycle and the operator's exact concern.
   - **Severity: low for "breaking", medium for "staleness".** It's
     gated (`ICT_OFFVM_BUILD_HOST=1`) and **non-fatal**: a fetch
     failure is logged, `market_features` is skipped, the regime
     manifests then skip (no dataset) — the other 7 families + their
     manifests train fine. So the cycle does **not** break; the regime
     models just don't retrain that day.
   - **But:** the regime models feed the **decider's regime condition**
     (which we need for the complementary roster), so chronic fetch
     failures = stale regime models. And a *hung* (not failed) fetch was
     unbounded (see fix #2).
   - **Recommendation (follow-up, deliberate):** point `market_raw` at
     the local 3-year parquet cache (`/home/ubuntu/ict-trader-data/btc_5m.parquet`,
     resampled 5m→1h) via the CSV adapter instead of the live Bybit
     fetch — eliminates the live pull, makes regime retraining
     deterministic, reuses the cache we built. Do it when the regime
     models are next retrained for the decider (source-consistency +
     re-validation required, so not a drive-by edit).

2. **Trainer service had no run timeout (FIXED).** `ict-trainer.service`
   is `Type=oneshot` (default `TimeoutStartSec=infinity`), so a hung step
   (e.g. a stalled market_raw fetch) could wedge the cycle indefinitely;
   the daily timer won't start a second instance, so training would
   silently stop until manually killed. **Fixed:** added
   `TimeoutStartSec=7200` (2h) to the cloud-init unit — a hung cycle is
   killed and the timer retries next day. (Applies to future provisions;
   for the running trainer, push the unit via trainer-vm-diag if/when the
   timer is enabled.)

## Verification / open checks
- The autonomous timer is **disabled by default** and only enabled via
  trainer-vm-diag. Before relying on autonomous training, confirm whether
  `ict-trainer.timer` is currently enabled on the trainer (trainer-diag
  `systemctl is-enabled ict-trainer.timer`) — it may be dormant.
- When the regime models are retrained for the decider, implement
  recommendation #1 (cache-backed `market_raw`) so the decider's regime
  input is reliable + live-fetch-free.

## Standing practice (operator directive 2026-05-23)
Before each commit/update: re-verify the docs touched by the change are
current (this extends the canonical Documentation-Hygiene rule). The
program docs (plan, audits, research, sprint logs) and `training-center.md`
are current as of this audit.
