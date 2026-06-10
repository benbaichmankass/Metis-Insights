# Sprint Log: S-M8-CLOSEOUT-2026-06-10

## Date Range
- Start: 2026-06-09
- End:   2026-06-10

## Objective
- Close out **M8 (Strategy Tuning)**: ship the canonical parameter-sweep harness
  that makes the M7 gate's `tune` action executable, validate a real tuning
  decision end-to-end, and **apply the first tune to the live trader**.

## Outcome — M8 tooling COMPLETE + first tune applied live
M8 went from NOT STARTED to a working, validated tuning instrument and one
applied real-money change in a single rotation (S0–S3 + Tier-3 application).

### Merged to `main` (ict-trading-bot)
- **S0 + S1** (#3140) — `scripts/ml/strategy_tune_sweep.py`: ingests a M7
  `tune_recipe`, expands `search_space` (log-uniform / uniform / grid / colon),
  dispatches to the existing backtesters via a `(harness, param)` registry,
  normalizes net-of-fee metrics, emits `strategy_tune_result/v1` with an advisory
  Tier-3 proposal. First real run hardened it: `fixed_args` (pin live params),
  top-level-JSON extraction (two real-run bugs), `backtest_trend.py --long-only`.
- **S2** (#3169) — walk-forward / OOS split (`--oos-start`); picks gated on OOS,
  in-sample runs flagged not-validated.
- **S3** (#3180) — k-fold anchored walk-forward (`--wf-folds`, `robust` gate),
  registry expansion to trend's structural params, and the read route
  `GET /api/bot/strategies/{name}/tune`.
- **Tier-3 application** (#3181, operator-approved) —
  `config/strategies.yaml::trend_donchian.min_confidence` **0.30 → 0.60**,
  deployed to the live trader (system-action `pull-and-deploy`, issue #3182).

### Merged to `main` (ict-trader-dashboard)
- **#87** — Strategies-tab tune-results panel (`_render_strategy_tune`) over the
  new `/tune` route; preview branch re-synced onto main post-merge.

## The decision the harness drove
`trend_donchian.min_confidence`, BTC 1h 2021→2026, live params, 3 anchored folds:

| min_conf | OOS net Σ | OOS exp μ | OOS maxDD | folds+ |
|---|---|---|---|---|
| **0.30 (was live)** | +34.9 | 0.111 | 22.3 | **2/3** |
| **0.60 (now live)** | +53.6 | 0.215 | 12.9 | **3/3** |
| 0.90 | +55.3 | 0.266 | 12.9 | 3/3 |

The OOS gate surfaced what an in-sample sweep had masked: the old `0.30` floor
was net-negative in the recent (2025–26) regime — positive in only 2 of 3 folds.
`0.60` is the conservative onset of the uniformly-robust 0.60–0.90 band
(expectancy ~2×, drawdown −42%). This is the whole point of M8: tuning decisions
are now made on OOS + k-fold evidence at the live params, not in-sample optima.

## Verification
- 53 tests across `tests/test_strategy_tune_sweep.py` + `test_strategy_tune_route.py`;
  ruff clean; all CI guards green on each merge.
- Real sweeps on the trainer VM (relays #3168 single-split, #3179 3-fold), EXIT=0.
- Live deploy verified: `config/strategies.yaml` on `main` and on the live VM
  reads `min_confidence: 0.60` (deploy issue #3182).

## Documentation
- `docs/strategy-tuning.md` (canonical M8 doc) — recipe contract, search-space
  grammar, dispatch registry + extension seam, WF/k-fold, Tier boundary.
- Sprint logs S0 / S1 / S2 / S3 + this closeout.
- `CLAUDE.md` API table: the `/tune` route. ROADMAP M8 row: applied + deployed.
- `config/strategy_changelog.json`: trend_donchian min_confidence entry.

## Risks and Follow-Ups
- Single symbol (BTC) / single param. The registry now supports trend's
  structural params + k-fold for future sweeps (MES, trail_mult, etc.).
- Watch the live trend_donchian fill rate / PnL post-deploy — the higher floor
  is more selective (fewer entries); the next `/performance-review` should
  confirm the OOS-projected improvement shows up live.
- `0.60` is the conservative onset; the 0.60–0.90 band is all robust if a future
  review wants to go higher.

## Wrap-Up Check
- [x] Code inspected directly, not inferred from summaries.
- [x] Documentation reviewed + updated (doc, sprint logs, CLAUDE.md, ROADMAP, changelog).
- [x] Pipeline stage: config-only change to a live strategy param, deployed via
      the sanctioned `pull-and-deploy` system-action + verified.
- [x] Roadmap updated (M8 applied + deployed).
- [x] Contradictions recorded (stale 0.30/2h comment rewritten to the k-fold evidence).
- [x] Remaining unknowns stated (live fill-rate impact → next performance-review).
