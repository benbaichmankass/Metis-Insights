# Backtest-harness validation + training-cycle check — 2026-05-30

Operator directive: *"when we make a new backtest we should check it works,
along with the other backtests"* + kick a training cycle and verify the
merged 5yr-window / mes-regime-1d / demotion changes (PR #2399, merged
`65ad955`).

## 1. Standalone backtest harnesses — all RUN clean

Ran each standalone harness in a fresh sandbox venv (pandas 3.0.3, numpy)
against the repo's local sample (`data/backtest_candles.csv`):

| Harness | Exit | Notes |
|---|---|---|
| `scripts/backtest_trend.py` | 0 | runs; `--resample 2h` ⇒ 0 trades on the 3.5-day sample (correct — a 2h Donchian needs far more bars) |
| `scripts/backtest_fade.py` | 0 | runs; produces trades on raw 1m |
| `scripts/backtest_squeeze.py` | 0 | runs; full summary emitted |
| `scripts/backtest_ict_scalp.py` | 0 | runs; by-outcome breakdown emitted |

**Conclusion:** the harnesses are healthy and CLI-stable. The **numbers are
not meaningful here** — the committed sample is only ~3.5 days of 1m candles
(`2022-07-23 → 2022-07-27`), far too short for the 2h/4h strategies. Real
evaluation requires the trainer VM's deep `market_raw` history (now 5y for
BTCUSDT), not the sandbox sample. This is itself the lesson: a backtest that
"runs" can still be evidentially empty if the data window is wrong — always
check span + trade count, not just exit code.

**Scope note:** the new `fvg_range` backtest (`scripts/backtest_fvg_range.py`)
lives on the separate FVG strategy session's branch (PR #2410), NOT on
`main`, so it could not be run from this tree. Its results are reported in
PR #2410 (5.2y BTCUSDT 15m: OOS 2024–2026 +21.76R / 54.8% WR). Per operator,
that PR is owned by its own session.

## 2. Training cycle — kicked + GREEN

Kicked `ict-trainer.service` via the relay after the #2399 merge. Cycle
`git reset --hard origin/main` to `65ad955` (our merged code) and ran:

- **`cycle_end overall_rc=0`** + `publish_post_ok` (dashboard mirror updated).
- **`mes-regime-1d-lgbm-v2` → `manifest_ok`** — the NEW daily MES regime
  model trained. Dataset build confirms `build_mes_1d` worked:
  `market_features MES timeframe=1d vol_threshold=0.0062818` built (the
  wiring that had silently failed twice and was fixed in `aebfce5`).
- **`setup-quality-lgbm-v2` → `manifest_ok` at `research_only`** — demotion
  honoured; still trains for the A/B record, no longer shadow-wired.
- All `btc-regime-*` → `manifest_ok` (now training on the 5y window).
- MES trade-data manifests (`mes-{setup-quality,execution-quality,
  trade-outcome-winrate}`) → `manifest_skipped(empty_dataset)` — expected
  (no MES trades yet).

## 3. What we'd have caught earlier WITH an integrated test

Both #1 and #2 are *isolation* checks — they prove each piece runs, not that
the pieces work *together*. The gaps they cannot see (and the motivation for
the integrated-sim harness, `docs/sprint-plans/ROADMAP-INTEGRATED-SIM-2026-05-30.md`):

- A strategy's solo backtest can't show how many of its trades survive the
  **intent multiplexer + risk gates** when it competes for an account.
- A model's holdout `macro_f1` can't show how few live decisions it actually
  influences deep in the funnel (decision-attrition).
- Neither shows **portfolio-level** behaviour of strategies + models running
  together over history, in variations.

That harness is specced (design-first, operator-approved 2026-05-30) and is
the next build.
