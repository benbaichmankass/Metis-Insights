# A1 research-backtest-augment runner — build scope (2026-08-02)

**Purpose.** Pin the design for the **new free-runner workflow** the WORK-PLAN
(`WORK-PLAN-2026-08-02.md` W1.2) requires *before* dispatching A1 (`MB-20260530-001`).
Written as the "scope that before dispatching A1" deliverable so the build is mechanical
and the ML-correctness decisions are made once, on the record — not guessed at build time.

**Status:** SCOPE only. No runner is built or dispatched by this document. Tier-1.

## What A1 actually asks

`MB-20260530-001`: does feeding **config-exact backtest trades** (as `is_backtest=1` rows)
into the pooled decision models (`setup_candidates` / `trade_outcomes` families) improve
their read on **live** trades — measured on a **live-only holdout**? The pipeline was
BUILT in S-MLOPT-S7 and RUN end-to-end for **MES only** (#8318, nightly-pinned #8326). The
genuine remainder is extending it to the **pooled/crypto** book — the original n≈78 wall.

Two halves:
- **W1.2 (this runner, free CPU):** run the live crypto strategies' config-exact harnesses
  → emit per-trade JSONL → record as `is_backtest=1` rows → publish the augmented DB.
- **W2.1 (trainer VM, FIFO lane):** build `include_backtest=True` datasets from those rows
  + retrain the pooled models + evaluate on a live-only holdout. NOT this runner.

## The runner is a COMPOSITION of two existing tools — do not re-implement

1. **Fetch + emit (per strategy):** `scripts/research/regime_debt_matrix.py` already
   resolves the feed (**Binance-vision for `*USDT` crypto**, `resolve_feed`), builds the
   config-exact harness cmd (`build_harness_cmd`, with the fidelity flag), and runs it with
   `--emit-trades` → a per-trade JSONL (`{strategy, entry_time, direction, gross_r, net_r,
   confidence}`). Reuse `run_one`'s early half (fetch → build_harness_cmd → subprocess), or
   factor a small `emit_trades_for(name, cfg, workdir, days)` helper it can share. **Use the
   corrected venue fee** (the harness already resolves 0 bps for commission-free; crypto is
   its real Bybit/Binance fee) — the augmented rows must carry realistic `net_r`.
2. **Record (per strategy):** `python scripts/ml/record_harness_trades.py --db
   <artifact>/backtest_trades.db --symbol <SYM> --trades-jsonl <jsonl>=<strategy>
   --run-tag a1-crypto-<UTC-date>`. Writes **only `is_backtest=1`** rows via the shared
   `write_backtest_trades` INSERT path (the `is_backtest=1`-only safety contract). One DB
   accumulates all strategies (multiple `--trades-jsonl` or repeated invocations against the
   same `--db`).

## Strategy list — the crypto pooled book (verify against live config at build time)

The pooled families train on the crypto book, so the target set is the **crypto**
`coverage_debt` + live crypto strategies, NOT the ETF/equity legs. From
`config/regime_coverage_exemptions.yaml::coverage_debt` (34 strategies), the crypto subset:

```
eth_pullback_2h, eth_pullback_prop_2h, sol_pullback_2h, ada_pullback_2h,
avax_pullback_2h, xrp_pullback_2h,
trend_donchian_eth, trend_donchian_eth_4h, trend_donchian_sol, trend_donchian_sol_4h,
trend_donchian_ada_4h, trend_donchian_avax_4h, trend_donchian_xrp_4h
```

**Build-time verification (mandatory):** confirm this list against the strategies that
actually feed the pooled `setup_candidates`/`trade_outcomes` datasets — read
`ml/datasets/families/setup_candidates.py` + the live `trade-outcome-lgbm-v1.yaml` /
`setup-candidates-metalabel-*` configs for the symbol/strategy scope. Do NOT augment a
strategy the pooled model never reads (wasted rows) and do NOT miss one it does (biased
holdout). `btc_*` is already covered by the MES/BTC single-symbol path (#8318) — this runner
is the *pooled remainder*, so include BTC only if the pooled config reads it.

## Artifact contract (the W1.2 → W2.1 handoff)

- **Output:** `backtest_trades.db` (SQLite, `is_backtest=1` rows only) + the per-strategy
  JSONLs + a `SUMMARY.md` (rows recorded per strategy, run-tag, fidelity per leg).
- **Publish:** upload as a workflow artifact (retention ≥ 14d). It is a **temp DB**, never
  the production money DB (the record script's safety note). The trainer-side W2.1 pulls the
  artifact (or the runner commits it under `runtime_logs/trainer_mirror/backtest_augment/`
  if the trainer ingests from the mirror — decide by how W2.1 reads it; MES precedent #8318
  used a nightly-pinned path, mirror that convention).
- **run-tag:** `a1-crypto-<UTC-date>` so the trainer step can scope the augment cohort and
  a re-run is idempotent by tag.

## Workflow shape (mirror `regime-debt-matrix.yml`)

`issues.opened` + `workflow_dispatch`, owner-guarded, label
`research-backtest-augment-request`; inputs `only` (CSV, blank = crypto pooled default),
`days` (730). Install `pyyaml pandas numpy` (+ the crypto fetch dep). Steps: emit per
strategy → record into one DB → SUMMARY → upload artifact + comment. **Read-only, authors no
cell, writes no live DB.** Add the label to `bootstrap-labels.yml`. Register the wrapper
script in `docs/research/RESEARCH-CAPABILITY-INDEX.md` (the `check_research_index` guard) and
respect the silent-empty / diagnostic-provenance guards (this session hit all of them on the
1.3 tool — the same four apply).

## Gotchas learned this session (apply to the build)

- `scripts/research/` is a **protected read-path**: any broad `except` needs an inline
  `# allow-silent: <reason>`.
- Every diagnostic must state what it computed (diagnostic-provenance guard); no inert params.
- A new `scripts/research/*.py` (or `scripts/ml/*.py` if placed there) must be registered in
  the capability index or the `pytest-run` + `artifact-validity-guard` both fail.
- Marking a draft PR ready re-queues checks; the full `pytest-run` is ~5.5 min.

## Then — A1 dispatch (NOT this runner)

Once the runner publishes the augmented crypto DB, W2.1 (trainer, FIFO lane, `🔒 VM-LANE
CLAIM` on the board first): build `include_backtest=True` pooled datasets, retrain
`setup_quality` / `trade_outcomes`, evaluate on a **live-only holdout**, and report whether
augmentation moves the live read. That verdict is the A1 answer; a promotion is Tier-3.
