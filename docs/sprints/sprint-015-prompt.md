# Sprint S-015 — Strategy + model improvement pass (long autonomous)

**Mode:** Long autonomous session (~3–4 hr). PM unavailable for the duration.
**Judged on:** out-of-sample P&L improvement vs locked baseline, across all strategies (turtle_soup + vwap, and any model components they pull in).
**Created:** 2026-04-30. **Predecessors:** S-014 closed (PRs #183–#198 merged).

## Sprint goal

Run a self-contained training + backtest pass against the last 5 years of OHLCV. Lock a baseline, search for improvements, ship a P&L-comparison report. **All branches in this sprint open as drafts and stay drafts** until PM approves — including the harness, samplers, baseline, and final report. Failed experiments do not get their own PRs; they live in the summary report only.

Not a "huge model" sprint — local-compute only. Aim for grids of cheap experiments rather than one expensive run.

## Execution model — split across two sessions (added 2026-04-30)

The sandbox the planning session runs in has the egress gateway allowlisted to **pypi + GitHub only** (probe results: every market-data host returned HTTP 403 from inside this box). Real data fetches will fail here. Therefore S-015 is split:

- **Session A (this one — sandbox-bound, no market-data egress).** Build T1 harness + multi-source data fetcher + sampler + tests + a clearly-labeled synthetic fixture for the unit tests, plus the analysis-only T3 deliverables that don't need fresh data (TOD / per-symbol on existing repo fixtures). Open everything as draft PRs.
- **Session B (next networked session — operator-online VM-resident OR a sandbox with market-data egress).** Pull the harness from Session A, run T2 (lock baseline), T4/T6/T7 (parameter + regime experiments), T9 (summary). Same draft-only rule. Operator merges the stack at end.

If you find yourself in Session A and your draft for T2/T4/T6/T7 needs real OHLCV — **stop**, don't synthesize. Document in the checkpoint that the work is queued for Session B.

## Compute envelope (HARD)

- **Local only.** No Colab, no Hugging Face GPU. The whole loop runs in the autonomous session.
- **Memory budget:** keep peak RSS under 2 GB. Stream OHLCV chunks; never load 5y of 1m bars for every symbol at once.
- **Wall-clock budget per experiment:** ≤ 90 s. If a single backtest run blows past that, downsample / chunk the window rather than wait.
- **No new heavy deps** (xgboost / lightgbm / torch). Stick to numpy / pandas / scikit-learn / statsmodels — already in `requirements.txt`.

## Data contract

- **Window:** rolling **last 5 years** ending at session date (2021-04-30 → 2026-04-30).
- **Sampling:** month-bucketed, **weighted toward recency**:
  - 0–12 months: weight `1.00`
  - 13–36 months: weight `0.50`
  - 37–60 months: weight `0.25`
  Stratified shuffle so every fold sees a recency mix; no leakage across folds (no overlap inside a month).
- **Sources — open, keyless, NOT Bybit.** Training data must not come from the venue we trade on (avoids subtle leakage between training set and live execution). Order of fallthrough:
  1. **Coinbase Exchange public REST** (`api.exchange.coinbase.com/products/<sym>/candles`).
  2. **Kraken public REST** (`api.kraken.com/0/public/OHLC`).
  3. **yfinance** (Yahoo Finance crypto pairs, e.g. `BTC-USD`).
  4. **CryptoCompare keyless tier** (`min-api.cryptocompare.com/data/v2/histohour`).
  5. **HuggingFace community OHLCV datasets** (per `docs/claude/huggingface-workflows.md`).
  Each adapter must detect upstream errors (HTTP ≥ 400, DNS, timeout) and yield to the next source. If every source fails, raise — never silently substitute.
- **NEVER** Binance, **NEVER** Bybit, **NEVER** any key-gated feed (per `docs/claude/testing-policy.md` + the no-leakage rule above).
- **Slippage model:** 2 bps round-trip, applied symmetrically to entry + exit fills.
- Cache the resampled monthly buckets under `data/backtests/sprint-015/` so reruns don't re-download. Cache is keyed by `(symbol, timeframe, year, month, source)` so you can prove provenance per bucket.
- **Synthetic OHLCV is permitted *only* in unit-test fixtures**, never as a fallback for an experiment run. If real data is unavailable, the harness must fail loudly, not silently substitute.

## Read order (binding)

1. `docs/claude/checkpoints/CHECKPOINT_LOG.md` — most recent entry only.
2. `docs/claude/ml-training-policy.md` + `docs/claude/testing-policy.md`.
3. `docs/sprints/sprint-014-prompt.md` for the T0..T10 pacing pattern this mirrors.
4. `src/units/strategies/{turtle_soup,vwap}.py` + `src/runtime/pipeline.py:154` (vwap_signal_builder) + `src/runtime/pipeline.py:386` (turtle_soup builder lookup).
5. `config/strategies.yaml` (current parameters — this is the baseline).

## Tasks (in order, T0..T10)

### T0 — Resume + plan (≤ 15 min)

`git status`, latest checkpoint, sketch which experiments fit in the 3–4 hr wall-clock budget, write the plan into the first commit. **No PR opened for T0** — it's planning, not deliverables.

### T1 — Unified backtest harness (≤ 400 LOC, **draft PR**)

- New `scripts/sprint015/run_backtest.py` — takes `(strategy_name, params, data_buckets)`, returns `{trades, realised_pnl, sharpe, max_dd, win_rate, n_trades, fold_results}`.
- New `scripts/sprint015/sample_data.py` — month-bucket sampler with the recency weights above. Caches under `data/backtests/sprint-015/`.
- Pure-functions, fully unit-testable. **No** import of `src/runtime/orders.py` (live-trading code). Exchange execution is replaced by the harness's fill model (mid-price, 2 bps slippage round-trip).
- Tests: deterministic fixture run produces a stable PnL number; sampler produces non-overlapping months and the weight distribution is correct.

### T2 — Lock the baseline (≤ 200 LOC, **draft PR**)

- Run the harness on **current** `config/strategies.yaml` for both turtle_soup and vwap across **N ≥ 5 folds**.
- Write `docs/backtests/sprint-015/baseline.md` with the per-strategy baseline metrics + sample-fold seeds + commit SHA.
- This is the gate every later experiment must beat to get a PR.
- Append checkpoint.

### T3 — Strategy-agnostic improvements (≤ 400 LOC, **draft PR**)

Things that don't change a strategy's signal logic, just visibility:

- Slippage / fee sensitivity sweep (so we know which gains are real vs below the cost floor).
- Killzone / time-of-day overlay analysis: which UTC hours actually contributed to PnL.
- Per-symbol attribution: is one symbol carrying the strategy?

Output is analysis-only — written into the eventual summary report.

### T4 — VWAP parameter sweep (≤ 250 LOC, **draft PR — only if it beats threshold**)

Grid over `ENTRY_STD_THRESHOLD`, exit threshold, max hold time, optional partial-TP. Pick the dominated-Pareto frontier on (PnL, max_dd).

**Push threshold:** `Sharpe delta > 0` AND `max-DD not worse by > 10% of baseline` AND `fold-wise paired t-test p < 0.10`. If no candidate clears the threshold, **do not open a PR** — fold the negative result into T9.

### T5 — Mid-session checkpoint (always, even if T4 was a no-PR negative).

### T6 — turtle_soup parameter sweep (≤ 250 LOC, **draft PR — only if it beats threshold**)

Grid over `atr_stop_mult`, `tp1_at_r`, `tp2_at_r`, `partial_close_pct`, `trail_atr_mult`, `min_sweep_buffer_bps`. Same Pareto-frontier rule. Same threshold. Same no-PR-on-fail rule.

### T7 — Shared regime filter probe (≤ 300 LOC, **draft PR — only if it beats threshold**)

Cheap regime classifier (volatility / trend bucket using rolling ATR + 20/50 EMA cross). Tested as an *additive veto*: only block trades the baseline would have lost. Same threshold. Same no-PR-on-fail rule.

### T8 — Mid-session checkpoint.

### T9 — Final report (≤ 300 LOC, **draft PR**)

`docs/backtests/sprint-015/summary.md`:

- Baseline table (per-strategy, per-fold).
- Each candidate change tried — passed *and* failed — with delta-PnL, delta-Sharpe, delta-max-DD, p-value, trade count delta.
- Recommendation column: "merge", "review", "reject".
- One paragraph each on what didn't work and why (negative results matter).
- Cross-references to the per-experiment draft PRs that *did* clear the threshold.

### T10 — Final session checkpoint

Append to `CHECKPOINT_LOG.md`. List: drafts opened, drafts not opened (and why), recommended order to review them. Telegram fallback ping.

## Self-merge vs DRAFT (HARD — different from S-014)

| Change shape | Decision |
|---|---|
| **Everything that gets pushed in this sprint** | **draft PR — PM review** |
| Harness scripts, samplers, fixture data | draft |
| `docs/backtests/**`, sprint summary | draft |
| Strategy-source code (`src/units/strategies/*.py`, `src/runtime/pipeline.py`) | draft |
| `config/strategies.yaml` parameter values | draft |
| `config/units.yaml` regime-filter wiring | draft |
| Failed experiment (didn't beat threshold) | **no PR — describe in summary** |
| `config/accounts.yaml` | **OFF LIMITS** (S-014 already wired this) |
| `src/runtime/{orders,risk_counters,notify,signal_writer,validation}.py`, `src/main.py`, `deploy/`, `src/bot/*` order paths | **OFF LIMITS** |

**Default rule:** every push is a draft. Every PR opens with `draft: true`. Nothing self-merges. PM reviews and merges.

## Push threshold (HARD — gates whether an experiment gets a PR at all)

A strategy/parameter/filter change qualifies for a draft PR iff **all three** hold against the locked baseline:

1. `Sharpe delta > 0` (some absolute Sharpe improvement, however small).
2. `max-DD not worse by more than 10 % of baseline max-DD`.
3. `fold-wise paired t-test p < 0.10` on per-fold realised P&L.

If any of those fail, fold the result into the summary report and move on. No branch, no PR.

## Pacing (HARD)

- PR size ≤ 400 LOC for code, ≤ 300 LOC for reports, excluding cached data.
- Re-read this prompt after every 2 drafts pushed.
- Append a checkpoint after every 2 drafts pushed (or every milestone boundary, whichever comes first).
- If a CI run / harness run fails, fix on the same branch (force-push allowed on feature branches). If not obvious in 30 min, write a `BLOCKED — needs PM` checkpoint and stop.
- If you OOM or blow the wall-clock budget twice on the same experiment, downsample and document the compromise — don't escalate to bigger boxes.

## Guardrails (HARD STOPS)

1. Do **NOT** install xgboost / lightgbm / torch / tensorflow. If a model needs more than scikit-learn, design it to ride on `numpy.linalg` and document the shortcut.
2. Do **NOT** touch the live trader's order path or risk-counter code.
3. Do **NOT** modify the strategy code or `config/strategies.yaml` outside a DRAFT PR (and only one that cleared the threshold).
4. Do **NOT** pull market data from Binance or other key-gated exchanges.
5. Do **NOT** push to main. PR + draft only via GitHub MCP. **No `mcp__github__merge_pull_request` calls in this sprint.** PM merges.
6. Do **NOT** retrain anything live. Models live under `models/sprint-015/<strategy>/`; production never auto-loads them. Promotion is a separate PM-review PR.

## Files Claude may modify

- `scripts/sprint015/**` (new tree).
- `data/backtests/sprint-015/**` (new tree, gitignored except for small fixtures).
- `docs/backtests/sprint-015/**` (new tree).
- `docs/sprints/sprint-015-prompt.md` (this file).
- `docs/claude/checkpoints/CHECKPOINT_LOG.md` (per-session entries).
- `models/sprint-015/**` (only if a strategy ends up wanting a fitted model artefact; gitignored — checksum + provenance recorded in summary).
- `src/units/strategies/{turtle_soup,vwap}.py` and `config/strategies.yaml` — **only inside a draft PR that already cleared the threshold**.

## Files OFF LIMITS

See guardrail § 1, plus everything S-014's prompt put off limits.

## Definition of Done (this session)

- [ ] Backtest harness + month sampler shipped as a draft PR with passing tests.
- [ ] Baseline locked + recorded as a draft PR.
- [ ] Strategy-agnostic analysis as a draft PR.
- [ ] At least one improvement candidate per strategy *attempted* — and *either* a draft PR (if it cleared the threshold) *or* a documented negative result in the summary.
- [ ] Sprint summary report opened as a draft PR.
- [ ] All draft PRs are listed in the final checkpoint with one-line review notes.
- [ ] Final checkpoint appended.
- [ ] Telegram ping (fallback if creds missing — record it in the checkpoint).

## What success looks like

PM returns to the repo and sees a small stack of draft PRs:

1. `s015-harness` (the backtest infra).
2. `s015-baseline` (the locked baseline).
3. `s015-analysis` (the slippage / TOD / per-symbol probes).
4. *Optionally* `s015-vwap-params`, `s015-turtle-params`, `s015-regime-filter` — only the ones that cleared the threshold.
5. `s015-summary` (the report including all attempted experiments, positive and negative).

PM reviews in that order, merges or comments. No surprises in `main`.
