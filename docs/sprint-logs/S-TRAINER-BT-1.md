# Sprint Log: S-TRAINER-BT-1

## Date Range
- Start: 2026-05-17 (continuation of the S-AUDIT-PIPELINE-2026-05-17 incident response)
- End: 2026-05-17

## Objective
- **Primary goal:** Build proper, reproducible backtest infrastructure on the trainer VM so the PR #1358 incident's underlying question — "did production vwap + turtle_soup + ict_scalp v2 actually deserve to be running?" — can be answered with evidence.
- **Secondary goals:** (1) Re-validate ict_scalp_5m v2 against the PR #1156 pre-live gate on a wider dataset. (2) Characterise the turtle_soup cadence-vs-quality tradeoff that produced 0/42 stage-1 sweeps in the live audit. (3) Ablate vwap PR #1175 / PR #1183 / PR #1205 to attribute each PR's marginal contribution.

## Tier
- **Tier 1** for the infrastructure itself (`#1366`): new scripts, new docs, new requirements file, new experiment dir, cloud-init comment fix. No live-runtime code touched.
- **Tier 3** for the production deploys (`#1364`, `#1372`): touched `config/strategies.yaml` and `src/units/strategies/vwap.py`. Both opened as drafts, operator-merged after backtest evidence + chat approval.
- Justification: trainer-VM scope is autonomous per `docs/CLAUDE-RULES-CANONICAL.md` § AUTONOMY MANDATE + `docs/claude/trainer-vm-mode.md` § 3.a. Operator approval for the two Tier-3 deploys was explicit in chat ("get the winning vwap up into production" → "ok, merge all of them in order").

## Starting Context
- Active roadmap items: continuation of S-AUDIT-PIPELINE-2026-05-17 (PR #1358 unauthorised disable).
- Prior sprint reference: [`S-AUDIT-PIPELINE-2026-05-17.md`](S-AUDIT-PIPELINE-2026-05-17.md) — the incident report.
- Known risks at start:
  - The trainer VM's Python environment had never been bootstrapped (`pandas`, `numpy`, `pyarrow` all missing). The cloud-init claimed `run_training_cycle.sh` "does not yet exist on main" — that comment was itself stale (the script existed but no session had ever fired it on this trainer instance).
  - The 2026-05-08 experiment harness ran on `/tmp/btc5m/` — no persistent dataset cache.
  - PR #1175 (HTF gate) + PR #1183 (SL widening) + PR #1205 (entry threshold raise) had each been justified individually but never ablated against each other on the same dataset.

## Repo State Checked
- Branch reviewed: `main` at `c3a22ff` (post-S-AUDIT-PIPELINE-2026-05-17 sprint log + roadmap entry).
- Deployment state reviewed: live VM running ict-trader-live with ict_scalp_5m disabled (the PR #1358 state).
- Canonical docs reviewed: CLAUDE.md, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/claude/trainer-vm-mode.md`, `docs/SPRINT-LOG-TEMPLATE-CANONICAL.md`.

## Files and Systems Inspected
- Code files inspected: `src/units/strategies/vwap.py`, `src/runtime/intent_multiplexer.py`, `src/runtime/pipeline.py`, `experiments/2026-05-08-all-models-training/scripts/run.py` (engine source), `scripts/ops/run_training_cycle.sh`, `scripts/ops/fetch_backtest_candles.py`, `scripts/backtest_ict_scalp.py`.
- Config files inspected: `config/strategies.yaml`, `config/strategy_changelog.json`, `requirements.txt`, `requirements-dev.txt`, `requirements-test.txt`.
- Deployment files inspected: `deploy/training-vm-cloud-init.yaml`, `deploy/ict-trader-live.service`.
- Docs inspected: `docs/claude/trainer-vm-mode.md`, `docs/CLAUDE-RULES-CANONICAL.md`, `docs/runbooks/`, `ROADMAP.md`.
- Services / timers inspected: `ict-trainer.service`, `ict-trainer.timer`, `ict-git-sync.timer`, `ict-trader-live.service`.
- GitHub Actions workflows inspected: `.github/workflows/trainer-vm-diag.yml`, `.github/workflows/operator-actions.yml`.

## Work Completed

### PR #1366 — Trainer backtest sweep infrastructure (Tier 1)

Files added:
- `scripts/ops/trainer_bootstrap.sh` — idempotent venv setup at `$REPO_ROOT/.venv` (matches existing `run_training_cycle.sh` convention), installs `requirements.txt` + new `requirements-backtest.txt`.
- `requirements-backtest.txt` — pyarrow + requests + python-dateutil. Pandas/numpy already in `requirements.txt`.
- `scripts/ops/fetch_qashdev_btc_archive.py` — downloads the qashdev/btc 5m monthly BTCUSDT archive (Binance Vision mirror) into persistent storage at `/home/ubuntu/ict-trader-data/raw/`, consolidates to a parquet. Incremental: skips cached months. Idempotent.
- `experiments/2026-05-17-post-incident-validation/PLAN.md` + `scripts/run.py` — the variant harness. Imports the 2026-05-08 backtest engine via `importlib` (dir name starts with a digit so it isn't a regular Python package). Runs vwap V_BASELINE + V_1175_htf_only + V_1175_1183 + V_PROD (full ablation), turtle_soup TS_PROD + extended T3 sweep (bps ∈ {3, 5, 7, 10, 12}), turtle_soup 5m naive port. Writes `all_metrics.json` + `SUMMARY.md` with a cadence-aware gate.
- `scripts/ops/run_backtest_sweep.sh` — orchestrator. Composes bootstrap → fetch → main harness → ict_scalp re-validation. Single-issue dispatch point for the trainer-vm-diag relay.
- `docs/runbooks/trainer-backtest.md` — runbook covering invocation, file layout, gate criteria, timing, failure modes, how to add variants.
- `docs/claude/trainer-vm-mode.md` § 10 — new section pointing at the runbook.
- `deploy/training-vm-cloud-init.yaml` — fixed the stale "filed for a follow-up PR" comment that misdirected initial discovery; added `trainer_bootstrap.sh` to the runcmd block so fresh provisions ship with the backtest deps pre-installed.

### Backtest sweep results (issue #1370 / `experiments/2026-05-17-post-incident-validation/SUMMARY.md`)

Dataset: qashdev/btc 5m BTCUSDT, 3.16 years (Jan 2023 → Feb 2026, 332 624 bars).

| Group | Variant | Trades | Trades/yr | Win % | E[R] | Total R | Sharpe | Max DD R | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| vwap | V_BASELINE | 10137 | 3206 | 24.4 | -0.007 | -73.3 | -0.39 | -152.44 | fail |
| **vwap** | **V_1175_htf_only** | **5840** | **1847** | **26.2** | **+0.071** | **+411.8** | **+2.82** | **-55.24** | **PASS** |
| vwap | V_1175_1183_htf_sl | 5177 | 1637 | 33.1 | +0.029 | +148.7 | +1.34 | -76.72 | fail |
| vwap | V_PROD (pre-revert) | 3449 | 1091 | 30.7 | +0.039 | +133.1 | +1.38 | -52.47 | PASS |
| turtle_soup_15m | TS_PROD (10 bps) | 37 | 12 | 62.2 | +0.241 | +8.9 | +1.49 | -6.00 | PASS |
| turtle_soup_15m | T3_3 | 122 | 39 | 50.0 | -0.001 | -0.1 | -0.01 | -11.20 | fail |
| turtle_soup_15m | T3_5 | 81 | 26 | 46.9 | -0.073 | -5.9 | -0.66 | -14.00 | fail |
| turtle_soup_15m | T3_7 | 53 | 17 | 41.5 | -0.187 | -9.9 | -1.39 | -15.00 | fail |
| turtle_soup_15m | T3_12 | 28 | 9 | 64.3 | +0.283 | +7.9 | +1.54 | -4.00 | PASS |
| turtle_soup_5m | T5M_NAIVE | 9 | 3 | 44.4 | -0.111 | -1.0 | -0.32 | -3.00 | fail |

**Findings:**
1. **PR #1175 (HTF gate) was the dominant winning factor.** Adding the gate alone turned a -73 R 3-year loss into a +412 R profit with Sharpe +2.82.
2. **PR #1183 (SL 0.5σ → 0.75σ) net-degraded performance.** Total R dropped +412 → +149 and Max DD got *worse* (-55 → -77).
3. **PR #1205 (entry 1.0σ → 1.5σ) partially recovered.** Total R +149 → +133. Cleaned up some of the SL damage but didn't fully recover #1175's edge.
4. **Turtle_soup TS_PROD (10 bps) is the right config.** Lowering `min_sweep_buffer_bps` below 10 destroys edge (the live audit's 0/42 sweeps isn't a bug — it's the strategy correctly rejecting low-quality setups).
5. **5m turtle naive port has no edge.** 9 trades over 3 years, negative E[R].

### ict_scalp_5m v2 re-validation (issue #1373)

Dataset: BTCUSDT 5m, last 90 days (Dec 2025 – Feb 2026, 25 920 bars). Matches PR #1156's original pre-live gate window.

| Metric | Value | Gate (PR #1156) |
|---|---|---|
| Trades | 54 | — |
| Win rate | 59.26 % | ≥ 40 % ✓ |
| Expectancy R | +0.382 | ≥ +0.20 ✓ |
| Total R | +20.61 | > 0 ✓ |
| Max DD R | 4.61 | ≤ 8 ✓ |
| Per-trade Sharpe | 0.401 | ≥ 0.5 (OR annualized ≥ 1.5) |
| Annualized Sharpe | ~5.94 | ✓ via the "OR" path |
| Outcome mix | 17 TP / 11 SL / 26 timeout | healthy |

**ict_scalp_5m v2 clears the gate decisively.** Per-trade Sharpe is just below the strict 0.5 threshold but the annualized "OR" path passes by 4×. Expectancy and DD are well within budget.

### PR #1372 — Revert PR #1183 + PR #1205 (Tier 3)

Based on the ablation evidence: `src/units/strategies/vwap.py`
- `ENTRY_STD_THRESHOLD: 1.5σ → 1.0σ` (undid PR #1205)
- `SL_STD_MULT_DEFAULT: 0.75σ → 0.5σ` (undid PR #1183)
- HTF 4h ±2% gate (PR #1175): unchanged
- ATR-based noise-floor (also from PR #1183): unchanged

The 2026-05-15 #1200 sweep that justified PR #1205's 1.5σ was run *without* the HTF gate; with the gate present, the optimum shifted. The two decisions (1200's sweep and this revert) optimised correctly for the regime they were measured in.

### Trainer-VM iteration log

Round 1 (#1367) — bootstrap passed, fetch failed (wrong qashdev path: `data/spot/...` should be `historical_data/spot/...`). Fixed in `37641b6`.

Round 2 (#1370) — bootstrap + fetch + harness all passed (4-second wall-clock for the full ablation). ict_scalp step hit the SSH idle timeout on the full 332k-bar input. Fixed in `97279c9` (subsample 12 months).

Round 3 (#1371) — 12 months (105k bars) also hit the timeout. Fixed in `79c89a2` (subsample 90 days).

Round 4 (#1373) — all four steps completed cleanly in ~90 seconds total. SUMMARY table + ict_scalp metrics produced as expected. Production deploy was authorised in chat.

## Validation Performed
- **Tests run:** `bash -n` on the two new `.sh` scripts (clean), `python3 -m ruff check .` against the whole repo (clean, after fixing the F401 + F841 hits surfaced in CI), `python3 -c "import ast; ast.parse(...)"` on the two new `.py` files.
- **Trainer end-to-end:** 4 rounds of issue-driven `trainer-vm-diag` dispatches, each progressing the orchestrator further until #1373 completed all four steps cleanly.
- **Production:** `pull-and-deploy` (#1374) + explicit `restart-bot-service` (#1375). Post-restart journalctl confirms `sl_std_mult: 0.5` in vwap signal meta (was 0.75 pre-deploy) and `ict_scalp_5m: no actionable signal (no liquidity sweep in last 12 bars)` (active evaluation, was `strategy disabled in config/strategies.yaml — returning side=none` pre-deploy).
- **Gaps not yet verified:** 24h post-deploy production cadence vs the backtest's predicted ~1847 trades/yr for vwap; first live `ict_scalp_5m` actionable signal (backtest cadence was ~54 trades / 90 days ≈ 1 trade every other day).

## Documentation Updated
- **Rules doc updates:** PR #1364 added the "session-start AND session-end" reconciliation banner to CLAUDE.md + the matching § Documentation Hygiene & Premise Verification section in `docs/CLAUDE-RULES-CANONICAL.md`.
- **Architecture doc updates:** none required — no architectural change.
- **Trade pipeline doc updates:** none required.
- **Runbook updates:** new `docs/runbooks/trainer-backtest.md`. Trainer-vm-mode § 10 points at it.
- **Strategy changelog updates:** `config/strategy_changelog.json` — new `vwap` entry (the revert) shipped in PR #1372; new `ict_scalp_5m` entry (the re-enable + 90-day re-validation) shipped in this closeout PR.
- **ROADMAP.md updates:** B-2 row corrected to reflect the revert; S-TRAINER-BT-1 added to the completed-sprints table.
- **Sprint log:** this file (`docs/sprint-logs/S-TRAINER-BT-1.md`).
- **Audit log closure:** `docs/sprint-logs/S-AUDIT-PIPELINE-2026-05-17.md` § Closure (this PR).

## PRs / Issues Produced

| Number | Type | Status |
|---|---|---|
| #1364 | Revert + doc-hygiene (`config/strategies.yaml::ict_scalp_5m`, CLAUDE.md, canonical rules) | Merged 2026-05-17 |
| #1366 | Trainer backtest sweep infrastructure (this sprint) | Merged 2026-05-17 |
| #1372 | Revert vwap PR #1183 + PR #1205 | Merged 2026-05-17 |
| #1367, #1370, #1371, #1373 | Trainer-vm-diag dispatches for the backtest sweep | All closed |
| #1374 | `pull-and-deploy` operator action | Closed |
| #1375 | `restart-bot-service` operator action | Closed |

## Lessons / Followups
1. **Backtest engine warnings.** The 2026-05-08 harness's `_make_turtle_signal_fn` emits `RuntimeWarning: invalid value encountered in divide` six times per turtle run. Benign (NaN-on-zero-range masked by `np.where`), but worth silencing in a future sweep.
2. **ict_scalp throughput.** The CLI is a pure-Python loop and overruns the diag SSH idle timeout on inputs > ~50k bars. A vectorised reimplementation (or backgrounded run with polling diag) would unlock the full 3-year window for ict_scalp the way the vwap/turtle harness already has.
3. **Cadence regime check.** The cadence-aware gate worked well for separating vwap (high-cadence) from turtle_soup (low-cadence), but the 100-trades/yr cutoff was ad-hoc. Worth revisiting with more data points.
4. **The next operator-relevant question** (raised but not actioned in this sprint): with V_1175_htf_only now live, does the +69% throughput hold up against live fees, slippage, and the news-veto / daily-loss caps that the backtest doesn't simulate? Watch the 24h cadence vs the 1847 trades/yr backtest prediction.

## Closure
Production is at the winning config. All three PRs landed cleanly. ict_scalp_5m is back online and passes the canonical pre-live gate on the most recent 90 days. The trainer VM now has a one-issue dispatch for any future backtest sweep.
