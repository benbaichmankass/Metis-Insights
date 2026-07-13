# Sprint Log: S-M20-EXIT-REFINEMENT-2026-07-12

## Date Range
- **Start:** 2026-07-12
- **End:** 2026-07-12

## Objective
- **Primary:** Run the M20 Exit Refinement session (operator-directed #1
  next-strategy priority + the operator's chop-hold observation: "a lot of
  trades are held through long chop periods where it isn't clear the trend we
  entered on is still relevant"). Read the two exit shadow-soaks, resolve
  counterfactual exits against realized prices, quantify the chop-hold
  problem, test time-stop / trend-invalidation / cross-timeframe exit levers,
  and propose the Tier-3 change only if the evidence clears the gate.
- **Secondary:** leave the exit-research tooling reusable (harness levers,
  sweep driver, trainer soak mirroring) so the next exit session doesn't
  re-derive it.

## Tier
- **Tier 1.** All analysis is reads + research tooling (`scripts/research/`,
  `scripts/ops/sync_trainer_data.sh` trainer-side sync, additive default-off
  flags on two standalone research harnesses). No live-path file touched. Any
  live exit change is proposed only (Tier-3, operator-gated).

## Starting Context
- ROADMAP M20 row (planned 2026-07-11) + `docs/sprint-plans/M20-EXIT-REFINEMENT-SESSION-PROMPT.md`.
- Inputs soaking: `exit_ladder_soak` (ExitPlan P3, `PB-20260617-002`),
  `fc_geometry_soak` (M19 D1, `MB-20260705-FC-SLTP-GEOMETRY`).
- Known honest-negative: the offline fc backtest failed its own
  reality-calibration check (T0.4 evidence memo) — live exits close for
  non-barrier reasons, so re-simulation alone can't validate an exit change.

## Repo State Checked
- Branch `claude/exit-refinement-sprint-l74k6o` off `main` @ `c4068a4`.
- Canonical docs read: CLAUDE-RULES-CANONICAL, root CLAUDE.md, ROADMAP (M20 +
  Next-plan), session-coordination/diag-data/sprint-format/backtesting skills,
  live-trade-management contract, S-DTP-EXITPLAN log, T0.4 evidence memo.
- Session board: registered; no open PRs (no workstream collision).

## Files and Systems Inspected
- `src/runtime/exit_ladder_soak.py`, `src/runtime/exit_plan.py`
  (`build_exit_plan_from_legacy`), `src/runtime/exit_plan_materializer.py`,
  `src/runtime/fc_geometry_soak.py`, `scripts/ml/fc_geometry_resolve.py`,
  `scripts/ml/fc_sltp_geometry_backtest.py`.
- `src/units/strategies/trend_donchian.py::monitor` (chandelier-trail-only
  exit design), `src/units/strategies/turtle_soup.py` (the only `meta.tp2`
  producer), `config/strategies.yaml`, `config/instruments.yaml`.
- `src/web/api/_clean_trades.py` (reduce-leg/phantom exclusions),
  `src/utils/closed_at.py` (epoch-ms close times).
- `scripts/backtest_pullback.py`, `scripts/research/backtest_trend.py`,
  `scripts/ops/research_sweep.py`, `scripts/ops/sync_trainer_data.sh`.
- Live VM (diag relay #6157): soak tails, status, recent trades. Trainer VM
  (relays #6158/#6159/#6160/#6161): soak mirror, journal sync, analysis runs.

## Work Completed
- **Data-sufficiency gate (both soaks FAIL — with a structural finding):**
  - `exit_ladder_soak`: 135 rows (112 api / 23 prop), **0 differing** — only
    `turtle_soup` declares `meta.tp2` and it is `execution: shadow`, so no
    live strategy produces a ladder that differs from the flat SL/TP placed.
    P4 cannot be answered by more soaking; re-scoped (see memo § 5).
  - `fc_geometry_soak`: 23 rows / 7 fc-covered; censoring-aware resolver:
    85.7% censored, paired n=1. Re-check dated 2026-08-25.
- **Chop-hold deep research (90d, 275 path-resolved closed trades,
  BTC/ETH/SOL 15m candles, trainer-side):** real-money mean MFE +1.92R vs
  realized −0.16R (giveback 2.08R), 26% round-trippers; per-strategy tables +
  truncation counterfactuals (time-stops, stagnation-stops, 1h-EMA-flip
  cross-TF exits) in the memo. Signal concentrated in the 2h trend-following
  family; `ict_scalp_5m` counterfactuals negative (no blanket rule).
- **Full-history validation sweep** (5y BTC/ETH/SOL, IS/OOS split 2025-07-01)
  through the same standalone harnesses that validated the strategies, with
  new default-off exit levers: `--stale-exit-bars/--stale-exit-below-r`
  (conditional time-stop) on both harnesses, `--flip-exit-bars`
  (Donchian-midline trend-invalidation exit) on the pullback harness.
  **Result:** exactly one lever passes the gate (better net_R AND maxDD, IS
  and OOS): stale-stop (8 bars, <0R) on `trend_donchian_sol` +
  `trend_donchian_eth` — also the one cell where the live counterfactuals and
  the harness agree. BTC donchian + pullback levers fail (honest negatives).
  Full table + Tier-3 proposal: the memo
  (`docs/research/M20-exit-refinement-2026-07-12.md` § 4–5).
- **Tooling shipped (reusable):** `scripts/research/m20_exit_analysis.py`
  (soak + chop-hold + truncation-counterfactual analyzer, stdlib-only,
  trainer-runnable), `scripts/research/m20_exit_sweep.py` (compact IS/OOS
  lever sweep driver), `scripts/research/market_raw_to_csv.py` (candle
  side-stream → harness CSV).
- **Infra fixes:** trainer checkout was stale at `38ac1c04` (its git-sync had
  silently stopped rolling forward — reset to `origin/main`, relay #6159);
  `sync_trainer_data.sh` now mirrors both exit soak logs (the
  `fc_geometry_resolve.py` docstring contract was unimplemented until now).

## Validation Performed
- Analysis script validated on a synthetic fixture locally (candles + journal
  + soak rows; S1/S2/S3/S4 sections all execute), then run against the real
  synced journal + candles on the trainer (relay #6158).
- Harness lever edits smoke-tested locally on synthetic CSVs: base cells run
  with levers off (default None/0 keeps the original code path); lever cells
  produce the new `stale_stop`/`trend_flip` exit reasons. `py_compile` +
  `bash -n` clean on all touched files.
- Full-history sweep run on the trainer against real 5y candles (relay
  #6161; first two attempts failed on a path assumption and the system-python
  pandas gap — both fixed, recorded honestly).
- **Gaps not yet verified:** path metrics only cover BTC/ETH/SOL (no trainer
  candles for ADA/AVAX/XRP, equities, metals — coverage gap logged);
  15m-bar MFE overstates capturable profit for 5m strategies (stated caveat);
  counterfactual ΔR are hypothesis-grade at current live n (1–10 affected
  trades per cell) — that is exactly why the full-history sweep gates the
  proposal.

## Phase 2 (same session — operator directive after #6164 merged)

Operator: merge the research, implement the Tier-3 proposal, and keep going —
"deep research, extensive backtesting on variations, ML supplements, trailing
stops, exit-ladder optimization." Delivered (PR #6166):

- **Stale-stop implementation, annotate-first** (memo § 6.1):
  `trend_donchian.monitor()` conditional stale-stop, YAML-declared params via
  package meta (NO strategy declares yet — live behavior unchanged);
  undeclared packages annotate the reference cell observe-only to
  `runtime_logs/exit_lever_soak.jsonl` (diag `exit_lever_soak`). 8 new tests.
  **YAML declare for `trend_donchian_sol`/`_eth` follows after the annotate
  window sanity-checks (or on operator instruction).**
- **Trailing-geometry + exit-ladder sweep** (memo § 6.2, 55 cells, relay
  #6169): banking loses net_R in ALL 20 cells (tail-for-smoothness — parked
  for standard accounts, prop-EV follow-up `PB-20260712-PROP-BANKING-EV`);
  trail geometry is per-family (trail7 repairs donchian-BTC OOS but not to
  positive; **pullback BTC trail 5→4 is a near-pass** → walk-forward
  follow-up `PB-20260712-PULLBACK-TRAIL4-WALKFORWARD`); ETH/SOL stale-stop
  remains the champion vs every phase-2 cell.
- **ML-supplemented exits** (memo § 6.3, relay #6168): probe honest-negative
  on current data (shadow log reaches only 2026-07-07; vol heads read ≥0.6
  always — no exit discrimination as-is); dedicated exit-head experiment
  spec'd (`MB-20260712-ML-EXIT-HEAD`) + shadow-log-history gap
  (`MB-20260712-SHADOW-LOG-HISTORY`).
- Relay-preemption lesson: long trainer sweeps now run detached (`nohup` +
  collect pass) — the trainer-vm-diag concurrency group cancels in-progress
  runs when a newer request arrives (#6161 was preempted).

## Phase 3 (same session — operator go-live orders)

- **Stale-stop LIVE**: YAML declared on `trend_donchian_sol`/`_eth` (#6172),
  activated via `pull-and-deploy` #6173 — live HEAD `5b86e14`, trader
  restarted 08:54 UTC, verified active. SOL long-only re-validation was MIXED
  (OOS strongly better incl. maxDD, IS softer) — flagged to the operator
  pre-merge; the flip proceeded on explicit order. ETH config-exact clean pass.
- **Trail4 walk-forward**: 4/6 yearly folds incl. 2025+2026, 5y +35% —
  recommendation delivered (`trail_mult 5→4` on `htf_pullback_trend_2h`),
  **operator-approved same day ("let's move forward on the pullback btc") and
  SHIPPED LIVE** (#6176, `pull-and-deploy` #6177 — live HEAD `15becc0`, trader
  restarted 09:29 UTC). `eth_pullback_2h` stays 5.0 (folds failed). The stale
  BTC↔ETH trail_mult equality assertion in
  `tests/test_m15_eth_pullback_wiring.py` was updated to per-leg pins.
- **Giveback-stop lever** (memo § 7.3, 44 config-exact cells): FAILS for
  donchian (trail already covers it), **PASSES for pullback BTC**
  (gb1.0R@MFE1R: OOS −3.7→+7.4 at flat IS) — confirms trail4's read; combos
  worse than either lever alone. **SUPERSEDED same day (walk-forward at
  config-exact trail4, trainer #6215): HONEST NEGATIVE — 1/6 folds.** The
  sweep pass was measured at the then-live trail5; once trail4 shipped, the
  giveback edge vanished (trail4 already captures the protection; the lever
  just churns ~25% more trades at lower net_R). Not shipped; matrix updated.
  Lesson on record: a lever validated against one exit config must be
  RE-validated when a sibling exit lever changes — config-exact means
  today's YAML, not the sweep-day's.
- **Exit-head program**: full E0–E3 plan committed
  (`docs/research/M20-exit-head-PROGRAM.md`); **E0 FIRST BUILD DONE same day**
  (#6176 builder + #6181 journal-loader schema fix; trainer runs #6179/#6182).
  `scripts/ml/build_exit_head_dataset.py` + `--emit-trades` on both harnesses;
  5y config-exact harness trades (don BTC 428 / ETH 901 / SOL 402, pb BTC 375
  / ETH 244) + live journal → per-bar datasets on the trainer under
  `datasets-out/exit_head/{1h,2h}/`:
  - **donchian 1h**: 34,919 rows (1,662 harness + 15 live trades),
    `holding_pays` 20.7% — i.e. at ~4 of 5 in-trade bars, holding did NOT add
    ≥ +0.25R (the chop-hold thesis, now as a label distribution).
  - **pullback 2h**: 30,512 rows (614 harness + 26 live trades),
    `holding_pays` 28.9%.
  - **E1 coverage gate** (≥300 harness AND ≥20 live): **pullback-2h ENTERS
    E1**; donchian-1h is at 15/20 live trades (harness side far over) — waits
    for a handful more resolvable live closes. Other families are live-only
    (no harness generator yet) and wait per the program doc.
  - Caveat on record: `dist_to_stop_r` is measured vs the INITIAL stop; the
    live trailing-stop path is not replayed (documented in the builder).
- **E1 trained same day** (operator go "let's continue to e1"; trainer
  `scripts/ml/train_exit_head.py` #6184, run #6186): **pullback-2h FAIL**
  (fold AUCs 0.47–0.54, chance-level — honest negative; hard levers stand).
  **Donchian-1h promising but below-gate** (live n=15<20): AUC 0.56–0.62 in
  every fold; τ=0.1 policy beats actual on maxDD + net_R/pos-day in 5/5 folds
  and on aggregate net_R (86.3 vs 73.7), but gives up the big trend years and
  the tiny live set disagrees in sign. **No unconditional E1→E2 pass** (memo § 8).
- **E1.5 (same day, #6193, run #6194): donchian PASSES the E1→E2 criteria.**
  Conditional shape `below_half_r @ τ=0.10` (head exits only below +0.5R —
  proven trades are never touched) beats actual on net_R AND maxDD AND
  net_R/pos-day in **5/5 folds incl. the 2023/2024 trend years** (aggregate
  net_R 133.9 vs 73.7 at ~half the drawdown/hold), beats both hard levers
  everywhere, and the n=15 live set agrees in sign. Pullback remains a fail.
  Concrete demo on live trade 3344 (#6192): the head flagged the 2d+ ~flat
  BTC donchian hold at P(pays) 0.12–0.24 — a state the <0R stale-stop cell
  can't see. **E2 live-shadow graduation proposed to the operator (Tier-2,
  observe-only)** — memo § 9.
- **E2 LIVE (same day, operator "ok continue"; #6196 + guard fix #6201).**
  `src/runtime/exit_head_shadow.py` (observe-only scorer, hooked into
  `trend_donchian.monitor()` § 2.6) + `scripts/ml/export_exit_head.py`
  (self-contained artifact: booster + shape `below_half_r@τ0.10`, stage
  `shadow`) + trainer-mirror channel (`publish_trainer_mirror.sh` →
  `trainer_mirror/exit_head/`). Artifact `exit-head-donchian-1h-v1` (34,338
  rows / 1,662 trades) exported on the trainer, delivered over the standard
  mirror, and **verified scoring live** — first record 13:01:50 UTC on the
  open BTC trend_donchian trade (age 60 bars, open_r 0.24, P(pays)=0.158,
  `would_exit:false` — a correct hold call). Records land in
  `shadow_predictions.jsonl` (`event_source:"exit_head"`) + would-exits in
  `exit_lever_soak.jsonl` (`lever:"exit_head"`).
- **E2 incident, fixed forward (#6201):** all donchian-family strategies
  share `trend_donchian.monitor()`, so the crypto-1h head also scored
  `iwm_trend_long_1d` (IWM, 1d equities) — 2 out-of-distribution soak rows
  before the fix. `maybe_score_exit_head` now has a **fail-closed
  in-distribution guard** (meta `timeframe` must equal artifact `tf`;
  symbol must be in the artifact's `symbols` allowlist when present), and
  the exporter embeds `symbols`. The 2 pre-guard IWM rows remain in the log
  as harmless artifacts — **exclude them when analyzing the E2 soak**.
- **Fast-gate doctrine (operator directive, same day; #6207).** The 2–4 week
  observe-only soak was replaced: offline validation (purged walk-forward +
  truncation-honest replay) IS the confidence gate; the live shadow phase is
  a MECHANICAL verification in hours — (1) live-vs-offline feature parity,
  (2) first-decision sanity — then the head soaks ONLINE post-flip under the
  standing health-review/ml-review monitoring. Program doc § E2/E3 rewritten.
- **Parity check: three real skews found + fixed in ~90 minutes** (the fast
  gate paying for itself):
  1. **Partial-bar scoring** (#6207) — the monitor frame ends in the current
     forming bar; training rows are closed bars. Scorer now trims un-elapsed
     bars (`_TF_SECONDS`).
  2. **Entry-anchor off-by-one** (#6207) — `ts >= entry_time` included the
     signal/fill bar the E0 builder excludes (bisect_right), leaking
     pre-entry price into mfe/mae (live mae −0.77R vs true −0.15R). Now
     strictly-after.
  3. **Candle-market mismatch in the parity script itself** — the live feed
     (and the journal's fills) are Bybit SPOT (`defaultType: "spot"`); the
     first offline recompute used LINEAR klines → a ~30-pt basis offset that
     also flipped chop/stagnation around the 0.25R band. Lesson on record:
     parity recomputes MUST use the live feed's market (spot). Second-order
     caveat: the E0 harness candles are perp-proxy sources while live is
     spot — features are R-relative (basis-invariant to first order) and
     each (candles, entry) pairing is internally consistent, so this is
     accepted and documented, not a blocker.
- **Parity VERIFIED EXACT (post-#6207 deploy, 13:57Z restart):** live records
  for bars 12:00 + 13:00 on trade 3344 match the offline spot recompute on
  ALL 13 features to 4 decimals (issues #6212/#6213/#6214) — incl. atr/vol
  ratios (Wilder warmup concern moot at equal 200-bar windows).
- **E3 built (PR #6211, DRAFT — Tier-3 pending operator approval).**
  `_exit_head_verdict` apply path behind a triple gate (YAML declare +
  artifact stage `advisory` + policy fires); `run_monitor_tick` now defaults
  cfg to the LIVE strategies.yaml (mtime-cached) so declared levers reach
  already-open packages; exporter `--stage`; YAML declares on the three 1h
  donchian legs + changelog + matrix refs; 5 new gate tests. Merging alone
  changes nothing — the mirrored artifact stays `shadow` until the operator
  approves, then the trainer re-exports `--stage advisory`.
- **Live head read post-fix:** the open BTC donchian trade (3344, +0.26R,
  59 bars) scores P(pays) 0.030–0.036 with `would_exit:true` — under E3 the
  head would bank it at the next bar close.
- **E3 LIVE + FIRST EXIT VERIFIED (operator approved — "approved, merge #6211
  and run the sequence").** #6211 merged 14:44Z, deployed (HEAD `5d5adbdf`),
  artifact promoted shadow→advisory on the trainer (#6217). **First ML
  head-driven close on real money at 14:46:45Z:** BTC trend_donchian trade
  3344 (bybit_2), entry 63754.2 → exit 64128.1 × 0.004, PnL +$1.50 (+0.59%,
  ≈+0.36R) on a 2d13h chop-hold the head read at P(pays)=0.03,
  `exit_reason: exit_head`. Mechanics check PASS (fast-gate doctrine):
  exchange flat (boot_reconcile ghost=0/untracked=0), journal closed with
  pnl, no retry loop, all other legs no_change, zero out-of-family verdicts
  (#6223/#6224/#6225). The head's would-exit had flagged this exact hold as
  dead capital — the "bank it" behaviour the milestone mandate asked for.
- **Fleet-wide sweep RUNNING** (task #27): `m20_fleet_exit_sweep.py` merged
  (#6219) + equities/futures candle gaps fetched (#6220) — 25+ legs runnable,
  detached run on the trainer (issues #6221/#6222). Early cross-validation:
  SOL stale8 re-confirms PASS 4/6 independently; **SOL stale12 PASS 6/6**
  (Tier-3 tweak candidate, queued for operator); BTC-live + htf_pullback
  cells fail (matches memo). Giveback pullback-BTC honest-negative at
  config-exact trail4 recorded (#6218).
- **E2→online-soak plan:** post-E3-flip the head soaks LIVE; the first
  head-driven exit is a mandatory health-review mechanics check; realized
  `future_r_delta` accrues in the standard soak logs for /ml-review.

## Fleet-wide sweep results (42 legs, task #27 — 2026-07-12 evening)

The `m20_fleet_exit_sweep.py` engine (#6219) ran every donchian/pullback-family
leg config-exact on the trainer (equities/futures candle gaps filled via
yfinance, #6220; ES=F/GC=F/HG=F proxies for MES/XAUUSD-MGC/MHG). 36 legs
runnable + 6 skipped (`no_harness_levers`: squeeze/fade/vwap/scalp/fvg/turtle
— matrix-blocked pending harness lever support). **Six cells pass the full
IS/OOS + walk-forward gate** (consolidated Tier-3 package: PR #6229, draft):
SOL stale12 (6/6), XRP-4h stale8 (5/6), MES trail3 (4/6, proxy), MHG trail3
(5/6, proxy), TLT-1h trail3, MGC-1h trail4 (6/6, proxy, shadow leg). Two
passes deliberately NOT shipped: USO-1h giveback (live monitors lack a
giveback lever — implementation queued) and XAUUSD trail4 6/6 (leg disabled).
**Everything else is an honest negative** — notably the ENTIRE equities 1d
fleet passes nothing (their existing trails already capture the exit value),
and all 2h alt pullbacks fail. Full verdicts:
`runtime_logs/m20_fleet/2026-07-12/` (trainer) + the coverage matrix.

## Giveback-stop monitor lever + USO-1h declare (Tier-3 draft, late evening)

The one fleet-sweep PASS blocked on implementation is unblocked:
`trend_donchian._giveback_verdict` ports the harness's giveback lever
(`scripts/research/backtest_trend.py` `gb`: fire at bar close when
`peak_r >= giveback_min_mfe_r AND (peak_r − r_close) >= giveback_r`, peak =
since-entry favourable extreme in R) into the live monitor with the exact
`_stale_stop_verdict` contract — YAML-declared (BOTH params positive) ⇒ real
`giveback_stop` close; undeclared ⇒ reference-cell (1R/1R) annotate row into
`exit_lever_soak.jsonl`; fail-safe skips on missing entry/risk/entry_time and
on the unrestrictable-window ambiguity (a pre-entry extreme must never fake a
peak). Checked AFTER stale-stop, matching the harness's exit precedence.
`order_package` threads `giveback_min_mfe_r`/`giveback_r` into meta; the
monitor's live-cfg default (#6211) covers already-open packages. 11 new tests
(`tests/test_giveback_stop_lever.py`) incl. short-side, pre-entry-peak
exclusion, stale-precedence, and annotate-dedup; full donchian suite 62 pass.

Evidence for the `uso_trend_1h` declare (trainer relay #6232, cell
`gb1R_afterMFE1R` config-exact at trail4 long-only): IS 50.33R / maxDD 12.78
vs base 48.68R / 13.87; OOS 9.04R / 4.28 vs 5.48R / 4.81 (both better on both
axes); walk-forward 4/6 folds no-worse on net_R AND maxDD (2021/2022/2025/2026
better — 2022 flips −1.0R→+1.8R; 2023/2024 marginally lower net_R). Shipped as
a draft Tier-3 PR (lever code + YAML declare together), **awaiting operator
approval — not merged**.

## Round-2 sweep: squeeze/fvg harness levers (the last two blocked LIVE legs)

The only live legs the 42-leg sweep skipped as `no_harness_levers` were
`squeeze_breakout_4h` and `fvg_range_15m`. Round 2 unblocked them:
`backtest_squeeze.py` + `backtest_fvg_range.py` grew the same stale/giveback
levers (default 0 = off, byte-identical — verified on the sample data:
lever-off output byte-equal to base; giveback-on changes the trade set), and
`m20_fleet_exit_sweep.py` learned the two families (`FAMILY_HARNESS`,
config-exact arg mapping). BTC 15m history didn't exist as a harness CSV on
the trainer — converted from the E0 dataset side-stream
(`market_raw_to_csv.py` → `data/BTCUSDT_15m.csv`, 175k rows 2021-07→2026-07,
relay #6234). Verdicts (relays #6233/#6234/#6235, config-exact):

- **squeeze_breakout_4h — ALL 6 cells FAIL IS/OOS** (stale8/stale12/gb@MFE1/
  gb@MFE2/trail2.5/trail4.5): a clean honest negative; the live trail3.5
  already captures the exit value.
- **fvg_range_15m — ALL 4 cells FAIL IS/OOS** (stale8/stale12/gb@MFE1/
  gb@MFE2; no trail lever — the strategy exits on stop/target/timeout):
  honest negative on native (non-proxy) data.

With these, **every LIVE leg in the fleet has now been exit-processed** —
the remaining matrix gaps are shadow/disabled legs pending harness levers
(turtle/fade/vwap/scalp) and future exit-head E0/E1 rounds. Also this round:
the mid-run CI failure on PR #6229 (config-pin tests contradicting the
Tier-3 cells) was fixed by moving the tlt/mgc/xauusd pins WITH the proposal —
including flipping the disabled `xauusd_trend_1h` to trail4 (its own cell
passed 6/6) so the mgc/xauusd sibling-parity contract stays true. PR #6229
green on all 18 checks at head `35a6690`.

## Package merged + activated; exit-head rounds 2-3 verdicts (evening wrap)

**PR #6229 MERGED (operator: "yes, merge whatever is ready and continue") and
ACTIVATED** — squash `5015ebb`, merge-protocol slot claimed/released,
`pull-and-deploy` #6237 confirmed live HEAD `d042223 → 5015ebb` with
`ict-trader-live.service` active after restart. Live effect: 8 exit cells
(sol stale12, xrp-4h stale8, mes/mhg/tlt trail3, mgc/xauusd trail4, uso
giveback 1R@MFE1R) + the giveback monitor lever (annotate-only where
undeclared).

**Exit-head rounds 2-3 (m20_exit_head_round.py driver, merged same PR):**
E0→E1 on the trainer for the 4h donchians (ETH/SOL/XRP/ADA/AVAX, 953 harness
trades) and the 2h alt pullbacks (SOL/XRP/ADA/AVAX, 934 trades) — relays
#6236/#6238/#6239/#6240. **Both GATE FAIL — honest negatives:**

- **donchian/4h:** AUC mean ~0.55 with the two big trend years <0.50; the
  naive τ-policy wins chop years (2022: −29.3R→+20.2R) but destroys trend
  years (2023: +67.8→−7.5; 2024: +111.2→+24.0). The E1.5 conditional shape
  (below_half_r@τ0.15) rescues 4/5 folds on both axes but still gives back
  2023 (+3.0R vs actual +67.8R — even the plain stale-8 hard rule gets
  +37.2R there). The 1h head's success does not transfer to 4h.
- **pullback/2h:** AUC ~0.54; no naive or conditional shape beats actual on
  net_R AND maxDD in ≥3/5 folds (2023: +95.6R actual vs +72.2R best arm).

Consistent with the fleet-sweep hard-lever negatives for the same legs:
these families' existing exits already capture the exit value. Matrix
updated (exit_head_ml → honest_negative for both rows). Remaining exit-head
gaps: equities E0 rounds + MES/MGC/MHG pending native IBKR history.

## Phase 4 planned: momentum-exhaustion exits (operator-acked, late evening)

Operator direction: "can we tell when the momentum's over and where we should
start exiting ... make sure we have a full design doc and it's added to the
roadmap ... then start according to the priorities." Design of record committed:
`docs/research/M20-momentum-exhaustion-DESIGN.md` — six work items (trail-decay
lever first, then the peak-is-in head + exhaustion features, then percentile /
regime-flip / conditioned-banking variants), each with mechanics, gates,
tiering, and guardrails (one lever per leg; live-parity twins in the same PR;
no proxy-data heads). ROADMAP M20 row carries the phase-4 plan so the program
survives session loss. Execution starts with P4.1 (harness lever + fleet decay
sweep) and the P4.2+P4.3 E0/E1 round on donchian/1h.

## Phase 4 execution state (late night — session hand-off point)

P4.1 lever + P4.2/P4.3 head retarget MERGED (#6244, squash `f67be26`). On the
trainer: the fleet trail-decay sweep + two E1 rounds (peak_is_in+extended,
holding_pays+extended ablation on donchian/1h) were launched CONCURRENTLY —
which starved the 1-OCPU box (SSH timeouts, mirror publish stale ~18 min;
BL-20260712-TRAINER-JOB-SERIALIZATION — serialize future rounds). Partial
decay verdicts before the starvation: a 2h pullback leg PASSES stall-armed
decay (stall6→tight1.8 wf 5/6; stall10 wf 4/6); the 4h donchians fail their
decay cells. FULL verdicts + both round reports are ON the trainer
(`runtime_logs/m20_fleet_decay/<date>/`, `runtime_logs/m20_exit_head/
p4_{peak,hp_ext}_1h/`) — pull once the jobs drain, apply the design § 3
gates (one-lever-per-leg: any decay PASS on a leg with a shipped lever needs
a combo A/B), fold into the matrix (`trail_decay` column) + this log, and
batch Tier-3 proposals. A GitHub MCP auth drop (~20:50Z, persisting past the
transient-blip threshold) blocked new relay dispatches at hand-off; existing
public issue pages remain scrape-readable (that is how the mirror age was
confirmed: diag #6249).

## Documentation Updated
- `docs/research/M20-exit-refinement-2026-07-12.md` (the evidence memo).
- ROADMAP.md M20 row → status update (this session's outcome + next gate).
- Backlogs: `PB-20260617-002` (ladder P4) re-scoped; `MB-20260705-FC-SLTP-GEOMETRY`
  annotated with the dated re-check; new follow-ups logged (see below).
- This sprint log.

## Contradictions or Drift Found
- `scripts/ml/fc_geometry_resolve.py` docstring claims the soak log is
  mirrored by `sync_trainer_data.sh` — it was not. Fixed in this PR (field
  beats comment: the *mirror* was the missing half, the docstring described
  the intended contract from the M19 D1 design).
- Trainer VM worktree was behind `origin/main` (at `38ac1c04`) — its
  self-update had not rolled forward; reset via relay. Logged as a follow-up
  to watch (below).

## Risks and Follow-Ups
- **Tier-3 proposal** (see memo § 6): per-strategy exit levers for the 2h
  trend-following family, shipped behind a `*_MODE` graduation flag —
  operator decision required; no live file changed this sprint.
- Trainer checkout staleness: if `ict-git-sync` (trainer side) is expected to
  keep the worktree current, it silently wasn't — logged to health backlog
  (`BL-20260712-TRAINER-CHECKOUT-STALE`).
- Candle-coverage gap for non-BTC/ETH/SOL symbols blocks the same analysis
  for the 4h alt-donchian + equities fleets — logged to ml backlog
  (`MB-20260712-EXIT-ANALYSIS-COVERAGE`).

## Deferred Items
- fc-geometry verdict (insufficient data until ~2026-08-25).
- Ladder P4 as originally scoped (no differing population exists to test);
  any real partial-TP ladder needs a strategy to declare one first.
- Equities/metals/alt-symbol chop-hold analysis (candle coverage).

## Next Recommended Sprint
- If the operator approves the Tier-3 direction: implement the approved exit
  lever(s) behind the graduation flag with a live shadow-annotate phase
  (soak logs the would-be lever exit vs the actual), then apply after the
  annotate soak confirms the backtest deltas. Otherwise: re-check the fc soak
  2026-08-25 and rerun this session's analyzers (all tooling now in-repo).

## Wrap-Up Check
- [x] Code inspected directly (paths above; exit paths mapped before analysis).
- [x] Docs reviewed + updated (memo, roadmap, backlogs, this log).
- [x] TRADE-PIPELINE — no pipeline stage changed (research-only sprint).
- [x] Roadmap updated (M20 row).
- [x] Contradictions recorded (resolver-docstring mirror gap; stale trainer checkout).
- [x] Unknowns stated plainly (coverage gaps, small-n caveats, 5m MFE inflation).

## P4 verdicts land: decay batch + the peak-is-in breakthrough (late night 2)

**Fleet decay sweep COMPLETE** (44 legs × 4 cells, trainer,
`runtime_logs/m20_fleet_decay/2026-07-12`, relay #6253). 13 legs had PASS
cells; after the one-lever-per-leg screen the **Tier-3 batch is 9 live legs**
(scha arm2R 6/6; sol_pullback_2h, slv_1d, mhg_1d, tlt_1h at 5/6; iwm, gld_1d,
splg, iaum at 4/6) — YAML declares staged on draft **PR #6251** with the live
trail-decay lever + evidence table. Excluded honestly: `uso_trend_1h`'s pass
evaporated on a **corrected-base combo A/B** (base_args now threads declared
stale/giveback levers, commit 6c54000; all 4 uso decay cells fail with the
shipped giveback in the base — relay #6255). sol/xrp donchian negatives
re-confirmed on their corrected stale bases. Shadow-leg passes
(trend_donchian_1h 5/6, both prop donchians) recorded in the matrix, not
declared. Coverage matrix grew the `trail_decay` column (all 15 rows).

**P4.2/P4.3 rounds A/B (serialized re-run, relays #6252/#6255):** the
serialization + 6h timeouts fixed the starvation deaths — both rounds
completed in minutes on the idle box.

- **Round A — peak_is_in + exhaustion features (donchian/1h): the
  retargeted label is genuinely learnable — AUC 0.70/0.69/0.73/0.70/0.73**
  (holding_pays never beat ~0.56). Fixed-arm `peak_full_tau0.8` beats actual
  AND both hard levers on net_R+maxDD in 3/5 folds; the two misses are trend
  years where it retains 96% (fold 1: 49.5 vs 51.7 actual at dd 22 vs 35)
  and ~46% (fold 4, 130-trade partial year) — the trend-year-giveback
  failure mode that killed every holding_pays head is substantially gone.
- **Round B — holding_pays + the same exhaustion features: AUC still ~0.55**
  (0.57/0.54/0.61/0.56/0.50). Ablation answered: the LABEL retarget drives
  the signal, not the feature block alone.

**E2 shipped for the peak head:** `exit_head_shadow` generalized to a
multi-artifact channel (scans `trainer_mirror/exit_head/*.json`, scores each
in-family artifact per bar under its own model_id; ADVISORY record still wins
the E3 return, so the live head is byte-unchanged — 13 scorer tests incl. the
new multi-artifact case). Exporter grew --target/--features/--policy;
`exit-head-donchian-peak-1h-v1` (peak_is_in, extended, shape peak_full τ0.8,
stage **shadow**) exported to the mirror (relay #6256). It starts scoring live
once #6251 merges + deploys; promotion past shadow stays the operator gate.

**Open asks (operator):** PR #6251 approval — the P4.1 lever + 9 decay-cell
declares + the multi-artifact scorer, one batch. Post-merge: pull-and-deploy +
restart, then first-fire mechanics checks (BL-20260712-M20-CELL-FIRST-FIRE).

## Merge + activation + P4.4 kick (2026-07-13 morning)

**PR #6251 MERGED (operator: "Hope you have a good merge") and ACTIVATED** —
squash `62509a0`; merge protocol followed (slot claimed/released on the board,
CI 18/18 green on the merge head, only open PR). `pull-and-deploy` #6258 rolled
the live VM `f67be26 → 62509a0`; `/api/diag/version` confirms `62509a02`;
heartbeat running. Live now: the trail-decay lever (both family monitors), the
9 decay-cell declares, the multi-artifact exit-head channel.

**Post-activation verification (relays #6261-#6267):** trader ticking
normally; shadow log alive (regime + setup-quality heads writing per bar).
Exit-head scoring is CONDITION-GATED right now — no open 1h donchian-family
trade exists (yesterday's BTC long closed; the open real-money XRP short is
4h, correctly excluded by the tf in-distribution guard), so the peak head's
first live score + the decay cells' first fire await the next qualifying
entry/stall — tracked under BL-20260712-M20-CELL-FIRST-FIRE for the next
health-review. Relay lesson: ampersand-bearing paths must ride the issue BODY
(the title parser mangles `&`), and prose bodies break the path parser (paths
only).

**P4.4 launched:** harnesses emit per-trade `mfe_r`; the sweep computes each
leg's P80 winner-MFE from the IS window and gates the dynamic
`decay_p80arm<X>R` cell (--p80-only re-run mode; base now also threads the
SHIPPED trail_decay declares so p80 cells on declared legs test the additive
refinement). Running detached on the trainer (relay #6263, nice 19).

## Batch 2 merged + activated; P4.5 verdict (2026-07-13 mid-morning)

**PR #6273 MERGED (operator: "you can merge whats ready and contune") and
ACTIVATED** — squash `c4b103b`; merge protocol followed (slot claimed/released,
CI green 21/21 on merge head). `pull-and-deploy` #6274 rolled the live VM
`62509a0 → c4b103b`; `/api/diag/version` confirms. Live now: the 6 p80
winner-MFE decay arms (trend_donchian BTC 6.49R, xrp 4.49R, avax 4.86R,
sol_4h 5.57R, qqq 3.56R, gld +5.06R combo on stall10).

**P4.5 regime-flip exits: HONEST NEGATIVE fleet-wide** (m20_flip_replay_sweep,
relay #6276). Wherever the frozen ADX-14 flip fires (pullback chop-OFF cells,
72-89% of trades) it guts the trend tail: eth_pullback_2h +67.7R→−11.8R,
tlt_pullback_1h +77.1→−26.6, gld_pullback_1h +115.5→+29.0; wf 0-2/6 typical.
The apparent PASSes are degenerate ties (long-only donchian legs no 1-D cell
ever gates → flip never fires) or improvements on money-losing shadow legs.
Consistent with the M20 through-line: the frozen label reads "chop" inside
live trends, and premature exits destroy the tail — the peak-is-in head
(AUC ~0.71, soaking in shadow) addresses the same question correctly. The
frozen-label variant is CLOSED; an ML-label variant would need per-symbol
advisory heads (BTC-only today) and is not queued. Matrix gained the
`regime_flip_exit` column (honest_negative, all rows).

P4 remaining: peak-head shadow soak → parity check → E3 proposal (operator
gate); P4.6 conditioned banking only if it can beat BOTH run-the-winner and
the shipped tighten arms; P4.7 GPU sequence model only if the tree head
plateaus (it has not — it cleared the gate).

## Session wrap: doc-freshness sweep + M21 handoff (2026-07-13)

Doc-freshness run (skill procedure): `check_canonical_doc_coherence.py` PASS;
mechanical scans clean (all micro-IP / removed-gate / 7-stage hits are
flagged-historical). ROADMAP M20 row updated to the P4 EXECUTED state
(batches #6251 + #6273 live, P4.5 honest negative, peak head soaking);
**M21 — Entry Refinement added as PLANNED** (operator-acked 2026-07-13) with
design of record `docs/research/M21-entry-refinement-DESIGN.md` (evidence
pass → hard entry-filter cells → P_win head for the parked M18 allocator →
regime-at-entry extensions; same fast-gate pipeline). Zero-regret precursor
dispatched: peak-is-in retarget rounds for the 4h-donchian + 2h-pullback
exit-head families. Follow-ups filed: ml backlog (peak-head E3 parity +
promotion decision), health backlog note (first-fire scope now includes the
6 p80 cells).
