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

## Strategy list — VERIFIED against the live pooled manifest (2026-08-02 correction)

> **CORRECTION (2026-08-02).** The first draft of this section GUESSED the roster as the
> crypto `coverage_debt` pullback/donchian strategies. That was WRONG — verified against the
> actual pooled manifest. Field beats comment: the manifest is the truth. The correct roster
> is below.

The live pooled decision-model manifest is
**`ml/configs/setup-candidates-metalabel-p2pool-v1.yaml`** (M23 P2, `setup_candidates`
family, `symbol_scope: all`, `split_strategy: live_holdout`). Its build command encodes the
augmentation roster explicitly:

```
python -m ml.datasets build setup_candidates --output-dir datasets-out \
  --version v020 --source market_raw --symbol-scope all --timeframe all --overwrite -- \
  "market_raw_paths=<BTC 1h>,<ETH 1h>,<SOL 1h>"  backtest_trades_db=<tmp.db>  live_trades_db=<journal>
```

So the roster the pooled model actually reads is the **3-strategy harness roster replayed
per symbol**, NOT the coverage_debt pullbacks:

| harness strategy | timeframe | symbols (per manifest) |
|---|---|---|
| `trend_donchian` | 1h | BTCUSDT, ETHUSDT, SOLUSDT |
| `squeeze_breakout_4h` | 4h | BTCUSDT, ETHUSDT, SOLUSDT |
| `htf_pullback_trend_2h` | 2h | BTCUSDT, ETHUSDT, SOLUSDT |

- TRAIN side = those `(strategy, symbol)` harness backtests → `is_backtest=1` rows in the
  `backtest_trades_db` (the artifact this runner produces). EVAL side = each pooled symbol's
  **real** closed trades appended from `live_trades_db` (the live-holdout — done trainer-side,
  W2.1, NOT this runner). `symbol` rides as a categorical feature (the S-MLOPT-S8 xsym lesson).
- The BTC-only sibling is `setup-candidates-metalabel-backtest-c2-v1.yaml`
  (`--symbol-scope BTCUSDT`, same 3-strategy roster); #8318 exercised the BTC/MES single-symbol
  path. This runner's job is the **pooled ETH+SOL extension** — the original n≈78 wall.

**Build-time re-verify (still mandatory):** before building, re-read the pinned pooled
manifest(s) for the exact `market_raw_paths` symbols + the harness roster (a manifest bump
could add a symbol/strategy). Do NOT augment a `(strategy, symbol)` the pooled model never
reads (wasted rows) and do NOT miss one it does (biased holdout). The runner produces ONE
combined `backtest_trades.db` (multiple `record_harness_trades --symbol` invocations
accumulate into the same `--db`).

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
