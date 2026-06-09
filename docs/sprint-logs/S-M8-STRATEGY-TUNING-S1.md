# Sprint Log: S-M8-STRATEGY-TUNING-S1

## Date Range
- Start: 2026-06-09
- End:   2026-06-09

## Objective
- Primary goal: produce the **first real tune result** from the M8 canonical
  sweep harness — run it against actual candle history on the trainer VM (S0
  proved it only against a fake-harness runner) and harden whatever the first
  real run surfaces. Target: a live strategy with a registry-supported param —
  **`trend_donchian.min_confidence`** (live on `bybit_2`, real money; current
  `0.30`).
- Secondary goal: close the harness gaps a valid sweep needs (pin live params;
  match the live direction config).

## Tier
- Tier 1. The M8 harness + the `backtest_trend.py` `--long-only` flag are
  research tooling (additive flag, default off; no live-path / config change).
  The sweep RESULT proposes a Tier-3 value but writes nothing — the operator
  applies any change.

## Starting Context
- Active roadmap items: M8 IN PROGRESS (S0 shipped the harness + doc + 32 tests,
  draft PR #3140). S1 = first real run, per S0's "Next Recommended Sprint".
- Prior sprint reference: `S-M8-STRATEGY-TUNING-S0` (the harness); the
  `backtesting` skill (net-of-fee + "match the live params exactly" rule).
- Known risks at start: (a) the sandbox has no candle data — the real run must
  happen on the trainer VM via the `trainer-vm-diag` relay; (b) a sweep is only
  valid evidence at the live params, incl. the live **direction** config.

## Repo State Checked
- Branch: `claude/m8-milestone-setup-ggr7d6`.
- Trainer VM (`trainer-vm-diag` relay, issues #3144/#3147/#3149/#3152/#3154):
  repo at `/home/ubuntu/ict-trading-bot`, `.venv` python (pandas 3.0.3), candle
  files present incl. `data/btc_1h_multiyear.csv` (47,460 rows ≈ 5.4 yr BTC 1h).
- `config/strategies.yaml::trend_donchian` — live params confirmed by direct
  read: timeframe `1h`, donchian `20`, atr_period `14`, atr_stop_mult `2.5`,
  trail_mult `5.0`, **LONG-ONLY** (2026-06-01, Tier-3), `min_confidence: 0.30`.

## Files and Systems Inspected
- `scripts/backtest_trend.py` — single-run summary keys (`net_total_r`,
  `net_expectancy_r`, `win_rate_pct`, `max_drawdown_r`, `total_trades`) +
  nested `by_year`/`by_outcome`; entry/direction logic (drove `--long-only`).
- `scripts/ml/strategy_tune_sweep.py` — the S0 harness (extended this sprint).

## Work Completed
- **`scripts/ml/strategy_tune_sweep.py` — `fixed_args` passthrough.** Recipes
  (and a `--fixed-args` CLI flag) now forward extra backtester flags verbatim to
  every run, so a sweep pins the strategy's live params. Without it the harness
  ran the backtester at CLI defaults — off the live config. (commit f452914)
- **`scripts/ml/strategy_tune_sweep.py` — robust JSON extraction.** Two real-run
  failures fixed: (1) the harnesses print a human table containing a Python-dict
  repr (`{'donchian': 20}`, single quotes) before the JSON payload — the naive
  first-`{`/last-`}` span captured table junk (commit 71a2454); (2) the payload
  nests `by_year`/`by_outcome`, and "keep the last valid object" grabbed an inner
  sub-object (had `trades`, no net metrics — the exact "all net columns blank"
  symptom). Now scans **top-level** objects only and keeps the last (commit
  01b3313). +5 tests (38 total).
- **`scripts/backtest_trend.py` — `--long-only` flag.** Additive (default off);
  skips short entries so a sweep matches the live LONG-ONLY config. Threaded
  through both the single-run and confidence-sweep paths. This closes the
  harness-coverage gap the M8 doc names. (commit 888b44c)

## Validation Performed — the first real tune results
Run on the trainer VM over `data/btc_1h_multiyear.csv` (≈5.4 yr BTC 1h), at the
exact live params, 13-point `min_confidence` grid `uniform [0.0, 0.6]`, fee 7.5
bps round-trip.

**Live-parity (LONG-ONLY) — the valid packet (relay #3154):**

| min_conf | trades | win% | net_total R | net_exp | maxDD R |
|---|---|---|---|---|---|
| 0.000 | 548 | 30.47 | 41.82 | 0.076 | 30.32 |
| 0.200 | 509 | 31.63 | 49.38 | 0.097 | 28.51 |
| 0.250 | 487 | 32.44 | 63.37 | 0.130 | 25.02 |
| **0.300 (live)** | 471 | 32.70 | 65.92 | 0.140 | 22.27 |
| 0.350 | 460 | 32.83 | 67.91 | 0.148 | 21.76 |
| 0.400 | 442 | 33.03 | 69.25 | 0.157 | 20.35 |
| 0.450 | 427 | 33.26 | 73.11 | 0.171 | 17.36 |
| 0.500 | 415 | 33.25 | 63.28 | 0.152 | 15.38 |
| 0.550 | 395 | 34.18 | 69.04 | 0.175 | 14.32 |
| **0.600** | 380 | 33.68 | **82.71** | **0.218** | **12.94** |

- Harness recommendation: `propose_value 0.60` (best net_total AND best
  net_expectancy; beats the 0.30 baseline). vs live 0.30: net R +25%,
  expectancy +56% (0.140→0.218), maxDD −42% (22.3→12.9 R).
- **All-directions run (relay #3152, NOT live-parity)** recommended 0.55 — kept
  as the before/after that motivated `--long-only`; the optimum shifted once
  shorts (which live never takes) were excluded, exactly the "match live params"
  rule in action.
- The harness itself: ran end-to-end EXIT=0, grid + picks + advisory
  recommendation all populated and coherent.

## Documentation Updated
- `docs/strategy-tuning.md` — `fixed_args` field documented + the live-params /
  harness-coverage-gap rule (done in the S1 commits).
- This sprint log records the first real results (the `runtime_logs/` packet is
  ephemeral on the trainer; this log is the durable record).

## Contradictions or Drift Found
- None. The all-directions-vs-long-only divergence is expected, not a drift.

## Risks and Follow-Ups
- **The 0.60 optimum sits at the grid boundary** — the true peak is likely
  ≥0.60. Re-run with the grid widened upward (e.g. `uniform [0.3, 0.9]`) before
  any proposal firms up.
- **In-sample, full-history.** This is NOT yet a merge-ready Tier-3 packet: the
  live 0.30 was set via 3-fold robust walk-forward (config comment), and the M8
  harness does not yet drive an OOS `--start/--end` split. **S2 follow-up: add a
  walk-forward / fold passthrough to the harness** so a tune result carries an
  OOS column, then re-run. Until then this result is a strong *directional*
  signal, not a go-live recommendation.
- Tier-3 decision (deferred to the operator, post-OOS): whether to lift
  `trend_donchian.min_confidence` above 0.30.

## Deferred Items
- Walk-forward / OOS split in the M8 harness (the validity gate for a Tier-3
  packet) — **the top S2 item.**
- Widen the trend grid upward to bracket the true optimum.
- Surface `runtime_logs/strategy_tunes/` on the dashboard read API + Streamlit
  (pairs with the M7 dashboard-wiring follow-up).
- Extend the registry to more `(harness, param)` pairs as gate packets demand.

## Next Recommended Sprint
- **S-M8-STRATEGY-TUNING-S2** — add walk-forward / OOS-fold support to the sweep
  harness (drive each backtester's `--start/--end`, report train vs OOS per grid
  value), then re-run the widened `trend_donchian.min_confidence` sweep to turn
  this directional signal into a merge-ready Tier-3 packet.
- Why next: the harness now produces real, live-parity results, but without an
  OOS split they aren't valid go-live evidence — that gate is the remaining gap
  between "interesting" and "actionable".

## Wrap-Up Check
- [x] Code was inspected directly, not inferred only from summaries.
- [x] Documentation was reviewed and updated as part of the sprint.
- [x] If this sprint touched any pipeline stage — n/a, research tooling only.
- [x] Roadmap status was checked and updated.
- [x] Contradictions were recorded (none; the all-dir/long-only divergence is
      expected).
- [x] Remaining unknowns were stated clearly (boundary optimum; no OOS split
      yet — both routed to S2).
