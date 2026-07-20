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

---

## Addendum — Phase-4 prep + P1 root-cause (same session, operator-directed)

- **k-fold OOS validation** (`kfold_oos.py`, artifact `kfold_runB.json`):
  OFF-cells rule (drop chop/volatile + trending/volatile) OOS net +20.3R
  (5m label) / +29.1R (15m live-enforcement proxy), 3/4 folds positive;
  fitted min_confidence failed robustness (2/4) → NOT proposed.
- **Tier-3 packet** drafted:
  `docs/research/ict_scalp_5m-phase4-regime-gate-PROPOSAL-2026-07-20.md`
  (awaiting operator go/no-go; no Tier-3 file touched).
- **BL-20260720-ICTSCALP-PASTSTOP-EXITS ROOT-CAUSED** (forensics relay
  #7122, candles #7117/#7118): exchange brackets fired correctly
  (TP 06-22 11:37 @64729; SL 06-22 21:23 @64250; SL 06-23 06:21 @62724);
  the journal closed only the newest row per fire, and phantom rows were
  mis-resolved (closed-pnl cross-attribution 2765←2799 / 2764←2769 /
  2796←2798; `reconcile_orphan_history` mark-price stamps 62402.2 on
  2757/2762/2770). The −7..−14R live reads are measurement artifacts.
  Two real defects filed: reconciler cascade-close under netting
  (Tier-1/2) + newest-bracket-governs-all tpsl semantics (Tier-3).
  Earlier same-day "no effective bracket" interim framing corrected in
  the findings doc + backlog (honesty: my initial pnl-arithmetic check
  was fooled by misattributed-but-internally-consistent records).

## Addendum 2 — netting fixes built + validated (operator-approved Tier-2)

- **Reconciler cascade-close** (`order_monitor._cascade_close_netted_siblings`
  + call from `_close_trade_from_order_status`): at the position-flat verdict,
  ALL open same-direction rows on the netted (account,symbol) close with the
  same exit fill and qty-prorated pnl (`netted_prorated_cascade`); reduce legs
  deferred; rows newer than the record's close time skipped. Primary-row
  guard: record qty > row share → prorated (`netted_prorated`), raw record
  total preserved in notes.
- **Tests:** `tests/test_netted_cascade_close.py` (7 new, incident-shaped
  fixture) + 130 adjacent reconciler tests + 299 system-actions workflow
  tests — all green.
- **Repair path:** `scripts/ops/repair_netted_misattributed_rows.py`
  (honest-null + provenance, signature-verified, idempotent) + new Tier-2
  operator action **`repair-netted-rows`** (workflow + wrapper + docs +
  notify + tests). Validated against the trainer's synced DB copy: dry-run
  8/8 matched (#7124); `--apply` on a throwaway copy repaired 8/8, re-ran
  0/8, row 2765 verified (#7125).
- **Remaining to complete:** merge PR #7115 (deploys the runtime fix via
  ict-git-sync), then dispatch `repair-netted-rows` (dry-run → `apply: true`)
  against the live DB. Fix 2 (tpsl semantics under netting — qty-scoped
  partial tpsl vs position-level acceptance) remains a Tier-3 design
  decision, not built here.

## Addendum 3 — Phase-4 packet APPLIED + Fix 2 built (operator-approved)

- **Phase-4 packet applied (Tier-3, operator approval 2026-07-20 in chat):**
  `config/regime_policy.yaml` gains the two ict_scalp_5m OFF cells
  (trending/volatile + chop/volatile, evidence comments citing the k-fold
  OOS) and `config/strategies.yaml::ict_scalp_5m.execution` flipped
  shadow → live with the demotion note rewritten to the verified record
  (unreproducible baseline + misattribution + fee-load truth + the gate).
  dry-run-guard: promotion direction — no annotation needed. Routing
  intact (bybit_1/bybit_2/bybit_portfolio). 50 regime-policy/gate tests
  green; YAML parse verified.
- **Fix 2 built (rollout-gated):** `BYBIT_TPSL_MODE` ∈ {full (default),
  partial} in `execute.py` — partial attaches qty-scoped brackets on
  placement (`tpslMode=Partial` + sizes) and qty-scopes the monitor's SL
  amend (`modify_open_order`, caller already forwards qty). Default full
  is wire-format byte-identical (regression-tested).
  `tests/test_bybit_partial_tpsl.py` (9 tests).
- **Venue validation vehicle:** new Tier-2 action `validate-partial-tpsl`
  (workflow + wrapper + docs + notify + tests; 343 tests green) —
  demo-locked to bybit_1, places 2 tiny netted orders, verifies BOTH
  bracket pairs coexist + a qty-scoped amend survives, cleans up.
  Runs post-merge (workflow executes from main). PASS = evidence gate for
  the Tier-3 `set-env BYBIT_TPSL_MODE=partial` flip.
- **Post-merge sequence:** (1) git-sync deploys; (2) `repair-netted-rows`
  dry-run → apply; (3) `validate-partial-tpsl` → on PASS, operator OKs the
  env flip; (4) first-decision health check (M20 P7) on ict_scalp's first
  live fire.

## Addendum 4 — merge, deploy + post-merge sequence executed (2026-07-20 late)

- **PR #7115 MERGED** (squash `6937849`) after three merge races (draft→ready
  CI re-trigger; branch behind a moving main twice; a real
  `health-review-backlog.json` conflict vs #7133, resolved keeping both
  sides' entries). **Deploy verified** via relay #7138: `/api/diag/version`
  git_sha `69378490` on BOTH the web-api and the trader's runtime status,
  trader restarted (uptime 153s), heartbeat running, `ict_scalp_5m` in the
  loaded roster → the re-promotion + OFF cells + cascade fix are LIVE.
- **repair-netted-rows on the live DB:** dry-run #7139 matched all 8
  expected corrupt signatures; apply #7141 repaired 8/8 (honest-null
  pnl/pnl_percent/exit_price, `exit_reason=netted_misattributed`,
  provenance under `notes.netted_repair`).
- **validate-partial-tpsl: PASS on the fifth dispatch** (#7159). The road
  there was itself instructive: #7142 failed on missing wrapper creds
  (`load_runtime_secrets` — the #1314 class; fixed #7143); #7145 ran on
  BTCUSDT and rode the demo strategies' live 0.016 position (its cleanup
  flattened their share — demo money; fixed #7147: isolated flat LTCUSDT +
  flat-at-start guard + test-scoped cleanup); #7152 hit the 5-USDT min
  order VALUE (fixed #7154: qty from live price); #7156 then proved the
  position-info `tpslMode` attribute is display-level — it reads `Full` on
  a clean symbol with `set_tp_sl_mode(Partial)` OK and every leg
  Partial-type — so #7157 re-anchored the verdict on the functional
  leg-type invariant. Final verdicts 4/4: bracket pairs coexist as
  qty-scoped `PartialStopLoss`/`PartialTakeProfit` legs, zero Full legs,
  qty-scoped amend preserves the sibling.
- **Held for the operator (Tier-3):** the `set-env BYBIT_TPSL_MODE=partial`
  flip on the live VM. Evidence: #7159 PASS. Honest caveat: an actual
  partial-leg FIRE has not been observed on-venue (deterministic immediate
  triggers are not constructible — trigger prices must sit on the far side
  of last price); Bybit's documented Partial-order semantics + the
  structural evidence carry the case, and the M20 first-decision health
  check watches ict_scalp's first live fire either way.
