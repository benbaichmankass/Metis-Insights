# Sprint Log: S-REGIME-DEBT-REC5-FOLLOWUPS-2026-07-29

## Date Range
2026-07-29

## Objective
Close the two teed-up rec #5 regime-coverage-debt follow-ups from the
equity/futures matrix + walk-forward gate (`S-REGIME-DEBT-WALKFORWARD-2026-07-29`):
1. the `qqq_pullback_1h` trending-short OFF-cell go/no-go (walk-forward-passed but
   thin, offered in #7924, awaiting operator go/no-go), and
2. a **faithful re-run** of the "approximate" matrix rows where an omitted lever
   could be the cause — starting with `trend_donchian_sol` chop-long, whose
   `exit_head_*` + `stale_exit_*` levers were omitted by the trend harness.

## Tier
Tier-1 (research tooling + evidence). No `config/regime_policy.yaml` cell authored;
any live-routing change stays a separate operator-gated Tier-3 draft.

## Starting Context
rec #5 had shipped one Tier-3 OFF cell (`gld_pullback_1h`, #7923) and left two
open threads: `qqq_pullback_1h` (offered, operator's call) and the approximate
matrix rows (re-run faithfully before any read). The regime-debt matrix
(`scripts/research/regime_debt_matrix.py`) grades a Donchian trend row through
`scripts/backtest_trend.py`, which did NOT model the `stale_exit_*` lever — so
`trend_donchian_sol`'s chop-long **−9.26R@35** was labelled `approximate` and
could be an artifact of the missing exit lever, not a real cell.

## Repo State Checked
- Branch even with `origin/main`; all prior rec #5 work (#7918–#7925) merged.
- Coordination board (issue #6927): merge slot free ("Area clear"); posted a
  `▶️ START` covering the research-tooling scope before the first push.

## Files and Systems Inspected
- `scripts/backtest_trend.py` (matrix harness) vs `scripts/research/backtest_trend.py`
  (research harness) — confirmed they are NOT geometry/fee-model identical, so
  swapping harnesses would break apples-to-apples with the published rows.
- `scripts/backtest_pullback.py` — the reference stale-exit semantics.
- `scripts/research/regime_debt_matrix.py`, `regime_cell_walkforward.py`, and the
  two `.github/workflows/regime-{debt-matrix,cell-walkforward}.yml`.
- `config/regime_policy.yaml`, `config/regime_coverage_exemptions.yaml`,
  `config/strategies.yaml` (the `trend_donchian_sol` / `qqq_pullback_1h` configs).
- Confirmed the sandbox has NO market-data egress (agent proxy 403s
  data.binance.vision), so the re-runs must execute on free GitHub runners.

## Work Completed
- **Tooling (PR #7926, merged):** ported the `--stale-exit-bars`/`--stale-exit-below-r`
  lever into `scripts/backtest_trend.py` (the *same* harness the matrix uses —
  base geometry + fee model unchanged by construction, stop-first preserved,
  byte-identical no-op when unset) and wired it into
  `regime_debt_matrix.build_harness_cmd` (`_TREND_LEVER_FLAG`: pass the flags +
  drop them from the omitted-lever list). `exit_head_*` stays `_UNREPLAYABLE`.
  New test `tests/test_trend_harness_stale_exit.py` (4 cases); existing
  live-`monitor()` twin `test_stale_stop_lever.py` unaffected.
- **qqq_pullback_1h (item 1):** operator chose **Hold — re-confirm with more
  data**. Dispatched a finer-fold walk-forward re-confirm (#7927, 6 folds; Yahoo
  60m caps history at ~730d so finer folds is the only lever). Result:
  `short_stable_drag` flips **TRUE(3/4) → FALSE(3/6)**, pooled short −2.01R@41 —
  the drag is fold-concentrated (regime-of-sample). **No cell; stays debt.**
- **trend_donchian_sol (item 2):** faithful matrix re-run with the stale-exit
  lever ON (#7928). Chop-long drag **collapses −9.26R@35 → −2.32R@39**
  (~−0.06R/trade); only `exit_head_*` remains omitted (an ML head that can only
  cut losers earlier → shrinks the residual further). The −9.26R "cell" was
  largely the omitted lever. **No cell; stays debt.**

## Validation Performed
- `pytest tests/test_trend_harness_stale_exit.py tests/test_stale_stop_lever.py`
  → 12 passed locally; `ruff check` clean; PR #7926 CI green, merged (squash).
- Smoke-tested the lever on synthetic candles: off is a deterministic no-op;
  on produces a `stale_stop` outcome and diverges from the off run only via the
  modeled lever.
- Both re-runs executed on free GitHub runners (issues #7927/#7928) and
  commented their verdicts; numbers cross-checked against the matrix baseline.

## Documentation Updated
- `docs/research/regime-cell-walkforward-2026-07-29.md` — new "Follow-up
  dispositions (continued 2026-07-29)" section with both verdicts + tables.
- `docs/research/regime-debt-matrix-equity-futures-2026-07-29.md` §5 — marked
  `trend_donchian_sol` RESOLVED (drag was the omitted lever).
- `config/regime_coverage_exemptions.yaml` — `trend_donchian_sol` +
  `qqq_pullback_1h` reasons updated to "measured debt, no cell".
- `ROADMAP.md` — `S-STRATEGY-COVERAGE-GUARD` line extended with the resolved
  follow-ups.

## Contradictions or Drift Found
None. The faithful re-run *resolved* a potential drift (a full-sample −9.26R that
looked like an OFF cell but was the omitted lever) before it could be acted on.

## Risks and Follow-Ups
- The remaining approximate trend rows carry levers the trend harness still
  doesn't model: `trail_decay_*` (iwm/qqq/scha/splg/sol_4h trend rows),
  `vol_skip_*`/`trail_vol_*` (eth), `giveback_*` (uso), `skip_hours` (xrp_4h).
  A faithful read of those is the next lever-port follow-up (same surgical
  pattern: port the lever into `scripts/backtest_trend.py`, wire into
  `_TREND_LEVER_FLAG`).
- `exit_head_*` is permanently unreplayable offline; any trend row carrying an
  ML exit head stays `approximate` by definition — the honest ceiling is
  "measured with every offline-modelable lever ON".

## Deferred Items
- The `trail_decay_*` / `vol_skip_*` / `giveback_*` / `skip_hours` lever ports
  for the other approximate trend rows (not blocking any live decision).

## Next Recommended Sprint
Port the `trail_decay_*` lever into the matrix trend harness and re-run the five
trail-decay-carrying approximate trend rows, if a future review wants those rows
measured faithfully; otherwise rec #5 is closed (1 cell shipped, all other
candidates refuted/explained).
