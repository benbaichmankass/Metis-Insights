# Sprint Log — S-ICTSCALP-PHASE0-2026-07-20

## Date Range
- Start: 2026-07-20
- End: 2026-07-20

## Objective
- **Primary:** Phase 0 of the ict_scalp_5m modernization research plan
  (`docs/research/ict_scalp_5m-modernization-research-plan-2026-07-20.md`,
  owner item `PB-20260630-ICTSCALP-DEGRADE`): rebuild the trade-level
  dataset with decision-time regime stamps (kill the M7 `unknown`-cell
  skew), re-run the config-exact multi-year BTC 5m backtest, deliver the
  per-(trend,vol) cell table, and answer the gate: is the strategy
  structurally negative or did the demotion rest on measurement?
- **Secondary:** extend the ict_scalp harness for Phase-1/2 reuse
  (MFE/MAE, live-exit simulation, regime stamping).

## Tier
Tier 1 — research/diagnosis + additive tooling only. No config, live-path,
or promotion change enacted; all forward actions proposed as Tier-3.

## Starting Context
- ict_scalp_5m is `execution: shadow` (operator-approved demotion; rationale
  "structural R:R": real-money −0.64R/trade over 15; 5y backtest −0.99R/trade
  ≈ −467R; no min_confidence floor salvages net_R).
- M7 packet (2026-06-30) hinted the loss is localized to trending+volatile
  but was skewed by an untrusted `unknown/unknown` bucket (+$141 of the
  +$142 "lifetime").
- Operator standing directive: regime-bleeders get gated off in the bad
  regime, not killed.

## Repo State Checked
- Branch `claude/ict-scalp-research-phase-0-8jvs23` off `main` @ `ceec068`.
- `config/strategies.yaml::ict_scalp_5m` read in full (incl. demotion note
  landed in #6447, 2026-07-15); `git log -S` traced for the −467R figure.
- Canonical docs: CLAUDE.md, research plan, backtesting + diag-data +
  sprint-format skills.

## Files and Systems Inspected
- `scripts/backtest_ict_scalp.py`, `src/units/strategies/ict_scalp.py`
  (order_package + monitor → `_base.monitor_breakeven_sl`),
  `src/runtime/strategy_signal_builders.py::_stamp_regime_on_meta` (+ 200-bar
  fetch), `src/runtime/regime/{detector,vol_detector}.py`,
  `ml/shadow/factory.py` (registry root), `src/web/api/routers/db_explorer.py`,
  `.github/workflows/vm-diag-snapshot.yml` (allowlist + multi-path branch),
  `scripts/ops/{run_backtest_sweep.sh,fetch_qashdev_btc_archive.py,sync_trainer_data.sh}`.
- Live VM via diag relay (issues #7100, #7101): M7 review packet,
  order-packages shape probe. Trainer VM via relay (#7102–#7112): synced
  `trade_journal.db` extraction (fresh, pulled 2026-07-20 07:57Z), registry
  vol-spec resolution, historic backtest artifacts.

## Work Completed
1. **Harness extension (additive flags, default behaviour unchanged):**
   `--stamp-regime` (decision-time ADX-14 trend + frozen-spec vol over the
   live builder's 200-bar window, same pure functions as live),
   `--vol-spec-json`, `--sim-breakeven` (mirrors `monitor_breakeven_sl`
   BE@1R + `be_offset_bps`); emit rows now carry
   `mfe_r/mae_r/bars_held/exit_time/exit_price/tp`; smoke-tested on the
   5k-bar fixture.
2. **Clean live dataset:** 52 ict_scalp order packages + 97 trade legs
   extracted compactly from the trainer's DB copy; decision-time regime/vol
   stamps from `order_packages.meta` (persisted at signal time, never
   backfilled). First 14 decisions (2026-05-18→06-01) predate stamping →
   `prestamp` bucket = the M7 `unknown` cell, confirmed as the skew.
3. **Frozen vol spec** for BTCUSDT/5m recovered from the trainer registry
   (`btc-regime-5m-baseline-v1`: edges [0.000836, 0.001401], window 20) and
   verified consistent with every live-stamped row.
4. **Config-exact multi-year backtests** on canonical qashdev BTC 5m
   (2023-01→2026-02, 332,624 bars): Run A legacy exits n=659 **+117.5R gross**
   (exp +0.178); Run B live-exit-faithful n=615 **+122.5R gross** (exp
   +0.199). Net of 7.5bps round-trip: −12.9R / −1.7R (fee load ≈ 0.20R/trade).
   Run C (`--ignore-yaml` v1-defaults reproduction attempt) launched to test
   the config-exactness hypothesis for the legacy figure.
5. **Per-cell tables** (backtest n≥36/cell + live) + confidence sweep:
   calm cells net-positive, volatile cells net-negative in both runs;
   conf≥0.7 net +20/+24R; calm∧conf≥0.7 exp +0.09/+0.13R net (in-sample).
6. **Findings doc** `docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md`
   + artifacts under `docs/research/artifacts/ict_scalp_phase0/` (datasets,
   summaries, gzipped regime-stamped per-trade JSONLs, per-cell JSON).
7. **Backlogs:** PB-20260630-ICTSCALP-DEGRADE updated with the Phase-0
   verdict + Phase-4 proposal; health-review backlog gains
   BL-20260720-ICTSCALP-PASTSTOP-EXITS (P1), BL-20260720-PAPER-PNL-CROSSWRITE,
   BL-20260720-RELAY-MULTIPATH-AMP.

## Validation Performed
- Harness smoke test (fixture): regime/vol/MFE/MAE fields verified on output.
- Vol post-stamp verified against `vol_regime_from_spec` semantics (same
  function imported; live rows' stamped calm/volatile labels re-checked
  against the frozen edges — all consistent).
- Live per-cell R computed from prices (not journal pnl) after finding the
  paper pnl cross-write corruption; real-money vs paper reported separately.
- Trainer DB freshness verified (db_pulls sync_done 2026-07-20 07:59Z).
- **Gaps not yet verified:** Run C (--ignore-yaml) may still be running at
  log time (result to be appended to the findings artifacts); calm/conf
  filters are in-sample post-hoc, no k-fold OOS yet; no backtest coverage
  of Mar–Jul 2026 (archive ends Feb 2026; Bybit/Binance-Vision are
  proxy-blocked from this sandbox); live-leg MFE/MAE deferred (needs
  May–Jul 5m candles, fetchable trainer-side).

## Documentation Updated
- New: `docs/research/ict_scalp_5m-phase0-findings-2026-07-20.md`,
  `docs/research/artifacts/ict_scalp_phase0/*`, this sprint log.
- Updated: `docs/claude/performance-review-backlog.json`,
  `docs/claude/health-review-backlog.json`.
- ROADMAP: this is a research session under the ict_scalp modernization
  plan (no milestone-status change; sprint recorded in the ledger by the
  next doc-freshness pass if required).

## Contradictions or Drift Found
- **The demotion baseline (−467R / −0.99R per trade) does not reproduce
  and no artifact of it was found** (trainer `strategy_tunes/` empty, no
  July results, both trainer re-validations positive). The
  `config/strategies.yaml` demotion comment's backtest magnitude is
  therefore unsupported by any locatable evidence. NOT changed in YAML
  (Tier-3 file) — flagged here and in the findings doc for the operator.
- The backtesting skill's ict_scalp example (`--htf-rule 1h --timeout-bars
  24`) silently differs from live exits (BE-trail, no timeout) — now
  bridged by `--sim-breakeven`; skill doc update deferred.

## Risks and Follow-Ups
- **Tier-3 proposals (operator decision, after k-fold OOS validation):**
  (a) 2-D `trend_vol` OFF cells for chop/volatile + trending/volatile;
  (b) `min_confidence: 0.7`; (c) re-promotion via the Phase-4/6 path with
  the regime gate as protection. Validation vehicle: M8
  `strategy_tune_sweep` k-fold + M20 levers for the fee-load attack.
- **P1 execution issue:** 2026-06-22 exits 7–14R past armed stops
  (BL-20260720-ICTSCALP-PASTSTOP-EXITS) — poisons per-strategy R analytics
  beyond ict_scalp if the pattern repeats elsewhere.
- Relay multi-path `&` bug breaks the recommended batched-read pattern
  (BL-20260720-RELAY-MULTIPATH-AMP).

## Deferred Items
- Run C result append; live-leg MFE/MAE (trainer-side candle fetch);
  Mar–Jul 2026 backtest extension; k-fold OOS of the calm/conf gates
  (Phase 2/6); backtesting-skill doc touch-up for `--sim-breakeven`.

## Next Recommended Sprint
- **Phase 2/4 combined:** k-fold OOS validation of {calm-only, conf≥0.7,
  both} on the emitted-trade methodology (fast — post-hoc filters on
  fresh walks per fold), then draft the exact `config/regime_policy.yaml`
  OFF-cell rows + `min_confidence` change as a Tier-3 proposal packet.
  In parallel, drive BL-20260720-ICTSCALP-PASTSTOP-EXITS to classification
  (broker fill vs journal artifact) since it contaminates the live record
  every later phase compares against.

## Wrap-Up Check
- [x] Code inspected directly (paths cited above)
- [x] Docs reviewed; findings + artifacts committed
- [x] TRADE-PIPELINE untouched (no pipeline change)
- [x] Roadmap checked (research session; no milestone flip)
- [x] Contradictions recorded (unreproducible demotion baseline; skill-doc drift)
- [x] Unknowns stated plainly (Gaps + Deferred)
