# Sprint Log: S-REGIME-DEBT-WALKFORWARD-2026-07-29

## Date Range
- Start: 2026-07-29
- End: 2026-07-29

## Objective
- Primary goal: **Continue rec #5 (regime-coverage debt) past the data-reachability
  block.** The 2026-07-17 crypto-plain matrix (#7912) covered only the
  Binance-reachable subset; the equity/ETF/futures debt majority was blocked
  because the sandbox firewalls Yahoo. Run that majority on a free GitHub runner,
  build a reusable **walk-forward OOS-stability gate** so a full-sample losing
  cell can't ship un-validated (the #7915 lesson), and disposition each candidate
  strictly (evidence-only; any live cell is a Tier-3 draft merged only on operator
  approval).
- Secondary goals: land the findings durably (ROADMAP row + this log + research
  docs); fix any CI landmine encountered along the way.

## Tier
- Tier 1 for all research tooling, workflows, docs, and tests (no live-VM, no
  order path, no schema/API change) — committed autonomously.
- **Tier 3 for the one config change** (`config/regime_policy.yaml` gld OFF cell,
  #7923): it is a live order-routing change (`Coordinator.aggregate_intents`
  drops the gated intent). Opened as a DRAFT, merged **only after explicit
  operator approval**.

## Starting Context
- Active roadmap item: **S-STRATEGY-COVERAGE-GUARD** (governance/M7) — the
  regime-coverage debt register. rec #5 of the 2026-07-29 roadmap-toolbox
  assessment.
- Prior sprint reference: `S-STRATEGY-COVERAGE-GUARD-2026-07-17` (guard + crypto
  matrix, debt now *measured*) and `S-PB-…` / #7915 (2yr directional walk-forward
  that refuted the 2h-pullback long-drag as regime-of-sample — the methodology
  reused here).
- The regime hard gate has been **baseline-on (LIVE) since 2026-06-28**, so any
  authored OFF cell takes effect on the live book at the next `ict-git-sync`.

## Repo State Checked
- `config/regime_policy.yaml` (1-D trend cells + 2-D `trend_vol`), `config/
  regime_coverage_exemptions.yaml` (`coverage_debt` roster + `debt_ceiling`),
  `config/strategies.yaml` (exact live params), `docs/strategy-coverage-matrix.md`.
- `scripts/check_strategy_coverage.py` (the guard; regenerates + git-diff-checks
  the matrix) and `src/runtime/regime/policy.py::would_gate`.

## Files and Systems Inspected
- `scripts/research/regime_debt_matrix.py` (#7916 engine — feed resolution,
  `--emit-trades` harness + `regime_tag_emitted.py` ADX tagging).
- `scripts/research/regime_tag_emitted.py` (added `annotate_trades_with_regime`
  + `--emit-tagged`/`--only-regime`).
- `src/news/news_events.py` (`event_risk_for_symbol` contract — for the CI-landmine
  fix; confirmed "never raises", risk ∈ [0,1], shipped calendar now populated).

## Work Completed
- **Matrix engine (#7916, merged).** `scripts/research/regime_debt_matrix.py` +
  `.github/workflows/regime-debt-matrix.yml` — per-(trend_regime, direction)
  net-R breakdown per debt strategy from exact live params + ADX regime tagging.
  Feed resolution: Binance-vision for `*USDT`, Yahoo `ES=F`/`GC=F`/`HG=F` for
  MES/MGC/MHG, Yahoo passthrough for equities/ETFs.
- **Matrix run (#7917) + findings (#7918, merged).** Ran the equity/ETF/futures
  roster on a free GitHub runner: **35 rows — 16 faithful · 19 approximate · 0
  errored · 0 skipped**. The Yahoo equity/ETF + continuous-futures path is now
  first-run-verified. `docs/research/regime-debt-matrix-equity-futures-2026-07-29.md`.
- **Walk-forward gate (#7919, merged).** `scripts/research/regime_cell_walkforward.py`
  + `.github/workflows/regime-cell-walkforward.yml` + `tests/test_regime_cell_walkforward.py`
  (5 offline tests). Folds the target-regime trades into N contiguous time-folds
  and reports `short_stable_drag` (short < 0 in a strict fold majority AND pooled
  short-R < 0) — the #7915 `stable_drag` test applied to one direction within one
  regime. `regime-cell-walkforward-request` label added to `bootstrap-labels.yml`.
- **Verdicts (#7920/#7921/#7922 → doc #7924, merged).**
  `docs/research/regime-cell-walkforward-2026-07-29.md`:
  - `gld_pullback_1h` (trending, short): short −ve **4/4 folds**, pooled −15.68R
    (36); long +32.98R kept → **SURVIVES** → Tier-3 draft.
  - `qqq_pullback_1h` (trending, short): short −ve 3/4, pooled −2.84R — clears the
    mechanical gate but thin (~−0.07R/trade, concentrated in one fold) → **offered**,
    not auto-authored.
  - `slv_trend_1h` (trending, short): short −ve only 2/4 folds → **REFUTED**
    (regime-of-sample) → stays `coverage_debt`.
- **Tier-3 OFF cell shipped (#7923, operator-approved, merged `7f2f978`).**
  `config/regime_policy.yaml` `trending.gld_pullback_1h {long:on, short:off}`;
  removed gld from `coverage_debt` and ratcheted `debt_ceiling` 35→34; regenerated
  `docs/strategy-coverage-matrix.md` to satisfy the coverage guard.
- **CI-landmine fix (in #7923).** `tests/test_news_events.py::test_real_config_loads_and_is_inert`
  asserted `event_risk == 0.0` against the shipped `economic_calendar.yaml`, which
  is no longer empty (macro/energy-events populated it) — a wall-clock time-bomb
  that went red whenever "now" fell inside a real event's pre/post window (observed
  passing at 16:25 UTC, failing at 17:52 with no code change). Renamed to
  `test_real_config_loads_and_never_raises` and replaced the value assertion with
  the module's real contract (well-formed `[0,1]` float + dict, never raises).

## Validation Performed
- All harness/matrix/walk-forward runs executed on free GitHub runners (Yahoo
  firewalled from the sandbox); `results.json` uploaded per run.
- `tests/test_regime_cell_walkforward.py` — 5/5 pass offline.
- `tests/test_news_events.py` — 8/8 pass after the fix (was 1 red on `main`).
- #7923 CI: all **18 checks green** (incl. `pytest-run`, `strategy-coverage-guard`,
  `ruff-lint`, `env-gate-guard`) before merge. No red CI was bypassed.

## Documentation Updated
- `docs/research/regime-debt-matrix-equity-futures-2026-07-29.md` (new, #7918).
- `docs/research/regime-cell-walkforward-2026-07-29.md` (new, #7924).
- `docs/strategy-coverage-matrix.md` (regenerated, #7923).
- `ROADMAP.md` — extended the S-STRATEGY-COVERAGE-GUARD row + status cell with the
  rec #5 continuation (this session).
- This sprint log.

## Contradictions or Drift Found
- **ROADMAP drift (fixed):** the row's earlier rec #5 note said the equity/futures
  majority was "blocked on data reachability" — this session cleared that block and
  shipped the first cell, but the row hadn't been updated. Fixed here.
- `canonical-doc-coherence` CI checker: **PASS** (dead-VM-IP single-source,
  removed-gates, 3-stage ladder, instruction-hierarchy mirror all green).
- No doc-vs-doc or precedence contradictions found across the canonical set.

## Risks and Follow-Ups
- **gld OFF cell is now live-bound.** `config/regime_policy.yaml` is consumed by
  the live trader and `main` auto-deploys via `ict-git-sync`; the gate starts
  dropping `gld_pullback_1h` **trending-regime SHORT** intents on the live book at
  the next sync (longs + transitional/chop untouched — the approved behavior). A
  first-decision live check (a `regime_hard_gate` audit row on a gld trending-short)
  is worth confirming when a matching signal fires.
- **`qqq_pullback_1h` trending-short OFF cell** — walk-forward-passed but thin;
  offered, awaiting operator go/no-go.
- **Approximate matrix rows** (19) await a *faithful* re-run before any
  disposition — start with `trend_donchian_sol` chop-long (omitted `exit_head_*`
  / `stale_exit_*` levers could be the cause).

## Deferred Items
- qqq OFF cell (operator decision) and the faithful re-runs above — deferred to a
  future session, not dropped (recorded in the ROADMAP row + the walk-forward doc's
  Outcome).

## Next Recommended Sprint
- Operator go/no-go on the qqq cell, then faithful re-runs of the powered
  approximate rows (`trend_donchian_sol` chop-long first). Reuse the walk-forward
  gate for any survivor (`regime-cell-walkforward-request` with `strategy:`/`regime:`).
