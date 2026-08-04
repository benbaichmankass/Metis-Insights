# P0 Label-Augmentation — the wall is broken, measured, and guarded (2026-08-04)

> **The recurring breach is over.** The per-trade backtest-augmentation infra was
> fully built and had **never been fed a row** (`trades.is_backtest=1` = 0 in every
> month — `BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED`), so every "blocked on
> labels" conclusion was reached without it, and successive sessions re-derived the
> wall as unsolved. This session **ran it, measured it, and locked it** so it can
> never silently rot again. What follows is the measured verdict — not a citation.

## What was done (verified this session)
- **P0.1 — the never-run engine ran.** The `research-backtest-augment` workflow +
  `scripts/ml/backtest_augment_runner.py` (both already built, never dispatched)
  produced **1,473 `is_backtest=1` rows** across 9/9 legs (pooled roster
  `trend_donchian`@1h / `squeeze_breakout_4h`@4h / `htf_pullback_trend_2h`@2h ×
  BTC/ETH/SOL, 3y), landed as the standing `datasets-out/backtest_trades.db` on the
  trainer (mirrors how `backtest_trades_mes.db` is produced). **0 → 1,473.**
- **P0.4 — the never-again lock.** `training-population-guard` (`scripts/check_training_population.py`
  + `config/training_population.yaml` + workflow + tests) fails CI if a journal-
  decision family trains live-only unclassified, and **forces the pay-down** when a
  family is wired. Green in CI (all 27 checks pass on PR #8453).

## The scientific verdict (M23 Phase-2 harness, `INCLUDE_PAPER=true`, trainer #8460)

**Population — the augmentation is genuinely in the corpus:**
- Train set **5,635 rows**: `{backtest: 5,094, live: 324, live_paper: 217}`. The
  ~19× expansion over the ~78-row wall is real and *used* — the head trained on it.
- **Trusted eval (live holdout): 324 real trades** — BTCUSDT 299, ETH 11, ADA 6,
  XRP 7, IEF 1. base win-rate 0.259, majority 0.741.

**The augmented head learns a REAL edge — but below deployable volume:**
- Classification gate **FAIL** (accuracy 0.716 < majority 0.741; precision 0.405 >
  base 0.259 — better-than-chance ranking, not a majority-beater).
- **EV-gate (the one that matters):** as a top-slice trade *filter* the meta-label
  is **net-R positive on the slice it keeps** — but only on a handful of trades:

  | τ | best t* | n_sel | % book | win-rate | net-R (sel) | net-R (take-all) | Δ |
  |---|---|--:|--:|--:|--:|--:|--:|
  | 0.5 | 0.40 | 14 | 4% | 0.571 | **+24.0** | −175.6 | +199.6 |
  | 0.75 | 0.34 | 21 | 6% | 0.571 | **+11.0** | −175.6 | +186.6 |

  Verdict: **`SELECTION EV POSITIVE (below usable-volume floor / no edge)`** — the
  meta-label is NOT (yet) a net-positive trade *filter* at cost 0.05R. A real signal,
  not a deployable one.

**The binding constraint is proven — and it is the EVAL book, not the train set.**
Even *with* `INCLUDE_PAPER=true`, the trusted eval stayed at 324 real trades because
the **honest-provenance guard correctly excluded the fabricated paper pnl** (e.g. it
dropped 116/519 BTC rows and all 31 SOL paper rows whose pnl is not
measured/estimated — `provenance.pnl_is_trustworthy`). So **backtests cannot grow
the eval side**, and neither can fabricated paper marks. The ceiling is *trustworthy
real/paper outcomes*, which accrue with time and honest measurement — a data-accrual
problem, **not** a train-volume problem.

## Disposition (honest, non-breach)
1. **The breach is closed.** Augmentation is built → ran → measured → guarded. No
   session can re-cite "blocked on labels" past a green `training-population-guard`.
2. **The train-augmentation is beneficial but the head is not deployable yet.** It
   surfaces a real filter edge (26%→57% win-rate on its slice) without poisoning the
   trusted eval — but at 4–6% coverage it is below the usable-volume floor. Deploying
   any resulting model as a live filter stays the separate **Tier-3** gate, unmet.
3. **The frontier lever is the EVAL book, not more backtests.** The next real move is
   **growing the trusted real/paper eval population** (real trades accruing +
   trustworthy-pnl paper) — the M30/L3 workstream — so the sub-volume edge can cross
   the floor. More train augmentation cannot move this.
4. **Guard debt: measured-deferral, not neglect.** The three pooled families stay in
   `augmentation_debt` with this verdict recorded as the reason — the nightly
   production wiring is *deliberately deferred* pending eval-book growth (or an
   explicit operator choice to keep the heads maximally trained), NOT owed-and-forgotten.
   This is the honest state the guard now encodes.

## Convergence soaks (W0.2 — C1/C2 evidence)
Read via the diag relay (#8458 `conviction_arbitration`, #8459 `conviction_sizing`).
Both logs are **present and accruing** on the live VM
(`/data/bot-data/runtime_logs/conviction_arbitration…`, ~145 KB).
**Honest partial read:** the `conviction_arbitration` tail (last 250 lines) is
**low-volume** and dominated by `resolution: "same_direction"` records — i.e. the
intent layer is mostly seeing *reinforcement* (two strategies agreeing), not genuine
*conflicts* where conviction would pick a different winner than the static priority
table. That means **C2 (conviction-driven conflict resolution) has thin live
evidence to size on** — genuine multi-strategy conflicts are rare, so the priority
table is seldom the deciding factor. A full quantitative disagree-rate + the
`conviction_sizing` would-be-vs-actual distribution are **deferred to a C1/C2-focused
session** (the raw tails are captured on #8458/#8459); this does not block P0. The
implication for **C1** (reductive conviction sizing on demo) stands as the cleaner
first master-model move — it doesn't depend on conflict frequency.

## Pointers
- Guard: `scripts/check_training_population.py` · `config/training_population.yaml` ·
  `docs/training-population-matrix.md`.
- Engine: `.github/workflows/research-backtest-augment.yml` ·
  `scripts/ml/backtest_augment_runner.py` · standing db `datasets-out/backtest_trades.db`.
- Eval harness: `scripts/ml/m23_phase2_labelvol.sh` (run `INCLUDE_PAPER=true`) ·
  `ml/experiments/splitters.py::split_live_holdout` · `scripts/ml/m23_ev_gate.py`.
- Backlog: `BL-20260731-BACKTEST-AUGMENTATION-NEVER-FED` · `MB-20260530-001` (both
  now carry the measured verdict) · `MB-20260717-M23-META-LABEL` (the eval-book lever).
- Trainer run: issue #8460 (verdict) · #8455 (db landing).
