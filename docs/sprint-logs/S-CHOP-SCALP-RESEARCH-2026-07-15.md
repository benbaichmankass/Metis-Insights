# Sprint Log: S-CHOP-SCALP-RESEARCH-2026-07-15

## Date Range
2026-07-15 (single research session).

## Objective
Research strategies **geared to scalp through chop** — use a multi-timeframe read
to gauge chop/range boundaries and catch bounces between them — and determine
whether such a strategy can be net-positive AND **capital-efficient**, measured as
PnL per unit of trade-time (`net_R/pos-day`) and return-per-calendar-day vs
holding longer positions (buy-and-hold) and sitting on cash through the chop.
Symbols: BTCUSDT + ETHUSDT.

## Tier
Tier 1 (research: new backtest harnesses + study tooling + tests + docs). No
`config/strategies.yaml`, `config/*`, or `src/units/` writes — live wiring is
Tier-3 and left as an operator-gated proposal (none warranted; see below).

## Starting Context
Concurrent sessions active (`claude/exit-refinement-sprint-l74k6o`,
`claude/system-health-review-f4i2v1`); merge slot free, no scope overlap.
Existing range member `fvg_range_15m` (live on BTC) is the closest incumbent;
research-only reverters `hf_vwap_revert` (frozen negative) and `fade_breakout_4h`
(shadow) exist. A capital-efficiency metric (`net_r_per_pos_day`) already exists
in `scripts/ml/train_exit_head.py::agg`.

## Repo State Checked
Branch `claude/scalping-chop-strategies-b9n05u` synced to `origin/main`
(3177697); registered on the session board. Trainer VM inventoried (issue #6477):
`btc_5m.parquet` + `ethusdt_5m.csv`, 3yr, trainer reaches Bybit.

## Files and Systems Inspected
- Docs inspected: root `CLAUDE.md`, `.claude/skills/backtesting/SKILL.md`,
  `.claude/skills/session-coordination/SKILL.md`, `docs/claude/session-board.json`.
- Code inspected: `src/units/strategies/fvg_range_15m.py`,
  `scripts/backtest_fvg_range.py`, `scripts/ml/train_exit_head.py` (net_r_per_pos_day),
  `src/runtime/regime/detector.py`, `scripts/ops/fetch_backtest_candles.py`.

## Work Completed
- **`scripts/backtest_chop_scalp.py`** — multi-TF range-bounce scalp harness. HTF
  boundaries/regime attach to LTF bars via a backward `merge_asof` (lookahead-safe);
  capital-efficiency block in `_summarize` (`net_r_per_pos_day`, hold, roundtrippers%).
- **`scripts/research/chop_scalp_study.py`** — study orchestrator (grid + incumbent
  fvg_range + buy-hold + cash + chop-tape characterization; uniform per-trade
  scoring; IS/OOS walk-forward).
- **`scripts/backtest_fvg_range.py`** — additive `hold_bars` + `mfe_r` on
  `--emit-trades` (no behaviour change).
- **`tests/test_backtest_chop_scalp.py`** — 14 tests.
- **`docs/research/chop-scalp-capital-efficiency-2026-07-15.md`** — evidence report.
- Ran the study on 3yr real BTC + ETH 5m data via the trainer relay (#6480).
- PR #6479 (draft). Backlog entry `PB-20260715-CHOP-SCALP-NEGATIVE`.

## Validation Performed
- `pytest tests/test_backtest_chop_scalp.py` — 14 pass; `tests/test_fvg_range_15m.py`
  — 12 pass (no regression from the additive emit fields). `ruff check` clean.
- Harness + orchestrator smoke-run on in-repo sample; full study on trainer 3yr
  BTC+ETH 5m, net-of-fee 7.5bps, IS (2023-24) / OOS (2025-26).
- **Result:** every tradeable-frequency multi-TF chop_scalp variant is net-NEGATIVE
  on both symbols, IS and OOS (`net_R/pos-day` −4 to −15, win 12–27%). The only
  positive cells are 1–9-trade FVG-confirmed noise that collapses OOS. The
  incumbent `fvg_range_15m` is the only capital-efficient range play (BTC +1.70
  net_R/pos-day OOS, beats cash + buy-hold in the flat OOS window; negative on ETH).

## Documentation Updated
- `docs/research/chop-scalp-capital-efficiency-2026-07-15.md` (new evidence report).
- `docs/claude/performance-review-backlog.json` (`PB-20260715-CHOP-SCALP-NEGATIVE`).
- `docs/claude/session-board.json` (session register + PR/run status).

## Contradictions or Drift Found
None. Result confirms `fvg_range_15m`'s own docstring caveats (low-frequency,
BTC-specific, recent-regime edge) — no doc correction needed.

## Risks and Follow-Ups
- **Negative result is the deliverable — no Tier-3 strategy wiring.** Do not chase
  a faster multi-TF chop-scalper.
- If range-scalping is revisited (high burden of proof): tighter higher-win target
  (partial-TP/tp1r, not full far boundary); 1m LTF data; an ETH-specific structure.
  Tracked in `PB-20260715-CHOP-SCALP-NEGATIVE`.

## Deferred Items
1m-LTF fetch + tighter-target sweep (only if the operator wants to push further
against the negative evidence).

## Next Recommended Sprint
None dependent on this. The reusable harness + study orchestrator remain for any
future chop/range experiment.

## Wrap-Up Check
- [x] Tests pass, ruff clean, harness+study verified end-to-end on real data.
- [x] Evidence report written with net-of-fee metrics, window, sample sizes, verdict.
- [x] Backlog + sprint log + board updated.
- [x] No live-path (`config/`, `src/units/`) writes. No Tier-3 change proposed.
