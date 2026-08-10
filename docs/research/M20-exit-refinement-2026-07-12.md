# M20 Exit Refinement — evidence memo (2026-07-12)

**Session:** S-M20-EXIT-REFINEMENT-2026-07-12 (branch
`claude/exit-refinement-sprint-l74k6o`). Tier-1 research — no live-path file
changed. Data pulled autonomously: live-VM diag relay #6157, trainer relays
#6158/#6159 (soak logs mirrored live→trainer, journal freshly synced, analysis
run trainer-side against `datasets-out/market_raw` candles).

## Verdict summary

| Question | Verdict |
|---|---|
| Graduate the ExitPlan ladder (P4, `PB-20260617-002`)? | **NO — and the soak can never answer it as instrumented.** 135 soak rows, **0 differing**: the only strategy that declares a TP1→TP2 ladder (`meta.tp2`) is `turtle_soup`, which is `execution: shadow` and never executes. Every live strategy derives a single-target plan identical to what is placed, so `differs_from_single_target` is structurally false fleet-wide. The gate is not "keep soaking" — it is "no ladder exists to test." |
| Graduate fc-scaled SL/TP geometry (`MB-20260705-FC-SLTP-GEOMETRY`)? | **NO — insufficient data.** 23 soak rows since 2026-07-06, only 7 with a served forecast (`fc_present`), because fc heads exist only for BTC/ETH/SOL 15m while the soak logs every symbol. Censoring-aware resolver output below. **Re-check ≈ 2026-08-25** (~50 fc-covered rows at current accrual). |
| Is there an exit-timing problem at all? | **YES — large and measurable.** Over the last 90d (275 path-resolved closed trades on BTC/ETH/SOL), the average real-money trade reached **+1.92R MFE** yet realized **−0.16R** — a mean giveback of **2.08R**; 26% of real-money trades touched ≥ +1R and still closed negative ("round-trippers"). The chop-hold hypothesis is confirmed and quantified. |
| Which lever fixes it? | Per-strategy, not blanket. The 5y IS/OOS harness A/B (§ 4) passes exactly one lever: a **conditional stale-stop** (`stale_exit_bars: 8`, `< 0R`) on **`trend_donchian_sol` + `trend_donchian_eth`** — better net_R AND maxDD in both windows, and the one cell where the live 90d counterfactuals and the harness agree. BTC donchian and the pullback family fail the gate; `ict_scalp_5m`'s counterfactuals are negative (its exits are already good). Proposal in § 5 — Tier-3, annotate-soak first. |

## 1. Data-sufficiency gate (M20 prompt step 1)

### exit_ladder_soak — 135 rows (112 api / 23 prop), 2026-06-18 → 2026-07-12

`differing = 0`. Root cause is structural, verified in code:
`build_exit_plan_from_legacy` only produces a rung when the order package
carries `meta.tp2`, and the only producer of `meta.tp2` is `turtle_soup`
(`execution: shadow`, so its packages never reach `execute.py`'s soak writer).
Every other strategy — the whole live fleet — uses a single far target
(`tp_r: 50` sentinel) + chandelier trail, so the "ladder" the soak materializes
is byte-identical to the flat SL/TP placed.

**Consequence for P4 (`PB-20260617-002`):** the graduation question cannot be
answered by more soaking. Either (a) strategies must *declare* real ladders
(an ExitPlan with actual rungs — a Tier-3 strategy-logic change to design
deliberately), or (b) P4 is re-scoped around exit levers that the fleet
actually needs (below). Recommend (b) first; (a) only if partial-banking shows
harness evidence.

### fc_geometry_soak — 23 rows, 7 fc-covered, 2026-07-06 → 2026-07-12

Coverage denominator is honest and low: `fc_present` only for BTC/ETH/SOL
(the symbols with fc heads); the equities/metals/alt rows are structurally
uncovered until fc heads exist for them. The trainer-side censoring-aware
resolver (`scripts/ml/fc_geometry_resolve.py`, relay #6159) on the 7 covered
rows: **6/7 counterfactuals censored (85.7%), paired uncensored n = 1**
(real_R −0.86 vs fc-scaled −1.00 — one trade, meaningless). Far below any
conclusive n. **Dated re-check: 2026-08-25**, or earlier if fc coverage
expands. Until then `MB-20260705-FC-SLTP-GEOMETRY` stays open, no proposal.
(Two infra gaps found + fixed en route: the trainer's checkout was stale at
`38ac1c04` — reset to `origin/main`; and `sync_trainer_data.sh` never mirrored
the soak logs `fc_geometry_resolve.py`'s contract assumes — both soak files
added to the sync set on this branch.)

## 2. The chop-hold problem, quantified (90d, path-resolved on 15m candles)

Universe: closed, non-backtest, non-reduce-leg, non-superseded,
non-adopted-orphan trades with resolvable risk, symbols with trainer candle
coverage (BTC/ETH/SOL — 275 of 491 closed trades; ADA/AVAX/XRP + equities +
metals lack trainer candles, logged as a coverage gap). R = pnl / (|entry−sl|
× qty × contract_value_usd). Real and paper reported separately, never
blended.

| class | n | mean R | mean hold | med. time-to-MFE | mean MFE | mean giveback | % time in ±0.25R chop | round-trippers |
|---|---|---|---|---|---|---|---|---|
| real_money | 200 | −0.163 | 4.0 h | 0.0 h | +1.92R | **2.08R** | 21% | **26.0%** |
| paper | 75 | −0.429 | 15.2 h | 2.0 h | +1.40R | 1.83R | 36% | 16.0% |

Per-strategy highlights (n ≥ 5):

| strategy·class | n | mean R | hold | t_MFE (med) | MFE | giveback | round-trip % |
|---|---|---|---|---|---|---|---|
| htf_pullback_trend_2h·paper | 16 | **−1.16** | 26.0 h | 2.2 h | 0.70 | 1.87 | 12.5 |
| htf_pullback_trend_2h·real | 5 | −0.61 | 24.0 h | 9.8 h | 0.50 | 1.10 | 0 |
| vwap·real | 169 | −0.26 | 0.9 h | 0.0 h | 2.06 | 2.31 | 30.2 |
| trend_donchian·real | 5 | +0.80 | 49.8 h | 5.0 h | 2.13 | 1.32 | 20 |
| ict_scalp_5m·real | 11 | **+1.02** | 6.1 h | 3.8 h | 1.35 | **0.33** | 0 |
| fade_breakout_4h·real | 7 | −0.16 | 17.2 h | 0.0 h | 0.70 | 0.86 | 0 |

Readings:

- **Flagship example (the operator's complaint verbatim in the data):**
  real-money `trend_donchian` trade #2535 (BTC long, opened 2026-06-11) —
  held **166 h (~7 days)**, peaked at **+3.59R** at hour 94, spent 23% of its
  life inside ±0.25R, and closed at **−1.09R** — a 4.7R round-trip through
  chop after the trend had stopped paying.
- **The peak comes early, the exit comes late.** Median time-to-MFE is a
  fraction of hold time everywhere (htf_pullback paper: peak at 2.2 h of a
  26 h hold). What follows the peak is chop the current exits (trail frozen
  at entry-ATR distance, 50R sentinel TP) don't respond to.
- **`htf_pullback_trend_2h` is the chop-hold poster child** — consistent
  negative expectancy in both classes, driven by holds through invalidated
  trends.
- **`ict_scalp_5m` real-money is the control case**: giveback 0.33R, no
  round-trippers — a strategy whose exit design already fits its hold
  horizon. Any fleet-wide exit rule must not touch it (its time-stop
  counterfactuals are *negative*).
- **`vwap` context:** the 169 real-money vwap rows dominate the 90d window but
  are largely historical — vwap is `execution: shadow` today and already got
  exit gates (`min_r_for_vwap_cross` etc.). Its numbers inform the diagnosis,
  not a proposal.
- **Caveat on `vwap` / 5m strategies:** MFE measured on 15m bars overstates
  capturable profit when the stop distance is small relative to the 15m bar
  range, so the vwap giveback/round-tripper numbers are upper bounds. The 2h/4h
  strategy numbers are robust to this (stop distance ≫ bar range).

## 3. Truncation counterfactuals (90d) — honest by construction

Unlike a barrier re-simulation (which the T0.4 evidence showed diverges ~0.6R
from reality), these counterfactuals only **truncate real trades**: exit value
= observed close at the truncation bar; trades the lever doesn't touch
contribute Δ = 0. Fees ≈ neutral (a truncated exit pays the same close-side
fee the real exit paid; funding is saved). Sign conventions: ΔR > 0 = the
lever would have improved the realized outcome.

**Time-stop (flat: exit at T if open R < 0):**

| lever | real-money ΣΔR | paper ΣΔR | dominated by |
|---|---|---|---|
| exit@4h if <0R | **+2.4** | **+21.6** | htf_pullback (+17.3 paper / +2.1 real), trend_donchian paper +5.0 |
| exit@8h if <0R | −0.1 | +14.7 | htf_pullback (+11.7 / +1.7) |
| exit@24h if <0R | 0.0 | +9.3 | htf_pullback paper only |
| exit@24h if <+0.25R | +1.3 | +11.5 | htf_pullback, trend_donchian |

**Stagnation-stop (exit after K consecutive hours inside ±0.25R):** positive
for trend_donchian real (+2.1R on 2 of 5) and htf_pullback (+7.8R paper /
+0.8R real); negative for eth_pullback_2h (n=3, −2.8R) — small-n noise both
ways.

**Cross-TF trend-flip exit (1h EMA9×21 against position ≥2h, age >8h):**

| strategy·class | n | affected | ΣΔR |
|---|---|---|---|
| htf_pullback_trend_2h·paper | 16 | 10 | **+18.0** |
| htf_pullback_trend_2h·real | 5 | 2 | +2.0 |
| trend_donchian·real | 5 | 2 | +1.6 |
| ict_scalp_5m·real | 11 | 1 | **−2.0** |
| eth_pullback_2h·paper | 3 | 2 | −2.4 |

Consistent story: **the faster-timeframe trend-flip and the early
conditional time-stop rescue the 2h trend-following family**, are mildly
positive for donchian, and are **harmful for the scalp family**. All
n_affected are small (1–10) — hypothesis-grade, which is why the full-history
harness validation below exists.

## 4. Full-history harness validation (5y, IS/OOS split 2025-07-01)

Levers were added to the *same standalone harnesses that validated these
strategies* (`scripts/backtest_pullback.py`, `scripts/research/backtest_trend.py`),
default-off so the base cell is byte-identical to the original engine
(the research_sweep delta-vs-base discipline). New flags:
`--stale-exit-bars N --stale-exit-below-r X` (conditional time-stop, fires
only when the trade is below X open-R at bar N, never pre-empting the intrabar
stop) and `--flip-exit-bars M` (close crossing the strategy's own Donchian
midline against the position for M consecutive bars — the trend-invalidation
exit, pullback harness only; the trend harness already has an opposite-signal
flip + unconditional `--timeout-bars`).

Run trainer-side (relay #6162 detached launch + #6163 collect; the first two
attempts #6160/#6161 failed on a path assumption / relay preemption — both
recorded). Split: IS = through 2025-07-01, OOS = after (~1y). Key rows
(net_R = fee-adjusted total R; full 35-line table in relay #6163):

| cell | IS n / net_R / maxDD | OOS n / net_R / maxDD | verdict |
|---|---|---|---|
| donchian **SOL** base | 556 / +4.8 / 41.0 | 145 / +17.6 / 17.6 | — |
| donchian **SOL** stale8b<0R | 624 / +5.3 / 36.8 | 160 / **+29.1** / **11.1** | **PASS** (better net_R AND maxDD, IS+OOS) |
| donchian **SOL** stale24b<.25R | 577 / +11.3 / 29.7 | 150 / +21.9 / 16.1 | pass (2nd) |
| donchian **ETH** base | 648 / −69.1 / 78.5 | 162 / −2.1 / 19.5 | — |
| donchian **ETH** stale8b<0R | 724 / −49.8 / 62.5 | 177 / **+7.2** / **17.0** | **PASS** (both windows improve) |
| donchian **BTC** base | 334 / +51.8 / 21.8 | 94 / −24.5 / 31.9 | — |
| donchian **BTC** any lever | worse or equal | −25…−30 | **FAIL — no change** |
| pullback **BTC** base | 238 / +43.6 / 11.7 | 75 / −3.7 / 10.2 | — |
| pullback **BTC** flip1/flip2 | +35.5 / +22.4, maxDD 20–25 | +2.9 / +3.6 | **FAIL gate** (OOS better but IS net_R and maxDD degrade) |
| pullback **ETH** base | 186 / +52.7 / 13.6 | 58 / +12.4 / 7.7 | base best — **no change** |

Reading notes: (a) lever cells re-enter after a lever exit (cooldown=1), so n
inflates and win-rate collapses by construction — net_R/maxDD are the
comparable axes; (b) the pullback result *disagrees* with the 90d live
truncation counterfactual (which favored levers for htf_pullback) — small live
n (5–16 trades) vs 5y harness history; the harness wins the argument until the
live sample grows, so pullback gets a **re-check, not a change**; (c) the
donchian stale-stop result is the one place the 90d live counterfactuals and
the 5y harness AGREE (live: stagnation-stop +2.1R on donchian real; harness:
better net_R + maxDD on ETH/SOL both windows).

## 5. Recommendation (Tier-3 — operator decision required)

**Propose: a strategy-declared conditional stale-stop for `trend_donchian_sol`
and `trend_donchian_eth` (1h) only.** Exact shape:

1. **Code (Tier-3 prep, behavior-inert until declared):** teach
   `trend_donchian.monitor()` two optional params read from the package meta /
   strategy config — `stale_exit_bars` (int) + `stale_exit_below_r` (float):
   at bar-close, if the position is ≥ N native bars old AND its open R <
   threshold, return `{"action": "close", "reason": "stale_stop"}`. No env
   flag — a YAML-declared, default-absent param (the sanctioned declared-config
   shape; rollback = delete the two YAML lines).
2. **Annotate soak first (Tier-2 deploy):** before any real close fires, run
   one observe-only soak cycle (same pattern as `exit_ladder_soak`): log
   "stale-stop would exit here" rows for 2–3 weeks and sanity-check them
   against this memo's cells.
3. **Then declare (Tier-3 merge):** `stale_exit_bars: 8`,
   `stale_exit_below_r: 0.0` on `trend_donchian_sol` + `trend_donchian_eth`
   in `config/strategies.yaml`. **Not** on `trend_donchian` (BTC — levers
   fail), **not** on the pullback family (harness contradicts the thin live
   sample), **not** fleet-wide (`ict_scalp_5m`'s counterfactuals are negative).

**Explicit honest negatives this sprint records:** ExitPlan ladder P4
(nothing to test — no live strategy declares a ladder); fc-scaled SL/TP
geometry (soak too thin, re-check 2026-08-25); BTC donchian + pullback exit
levers (fail the gate); any fleet-wide time-stop (harms the scalp family).

**Re-check triggers:** pullback-family levers when ≥30 closed live
htf_pullback trades post-date this memo; fc-geometry 2026-08-25; the
chop-hold analyzers (`scripts/research/m20_exit_analysis.py` + `m20_exit_sweep.py`)
are in-repo and rerunnable in one trainer relay.

## 6. Phase 2 (same day — operator directive: "far from finished")

Operator direction after § 1–5 merged (#6164): implement the approved Tier-3
stale-stop, and extend the research to **trailing-stop geometry**,
**exit-ladder (partial-TP) optimization**, and **ML supplements** (not just
hard rules).

### 6.1 Stale-stop implementation (shipped, annotate-first)

`trend_donchian.monitor()` now carries the conditional stale-stop
(`_stale_stop_verdict`), driven by YAML-declared `stale_exit_bars` /
`stale_exit_below_r` threaded through package meta. **No strategy declares
them yet** — until declared, every donchian-family package is evaluated at the
reference cell (8 bars, <0R) observe-only, writing one row per would-fire
trade to `runtime_logs/exit_lever_soak.jsonl` (diag: `log_file?name=exit_lever_soak`).
The YAML declaration for `trend_donchian_sol`/`trend_donchian_eth` follows
after the annotate window sanity-checks against § 4.

### 6.2 Trailing-stop geometry + exit-ladder banking (5y IS/OOS)

New default-off harness lever: `--bank-frac F --bank-at-r R` (bank F of the
position at +R R, remainder keeps the trail) — the ladder-optimization
evidence the live soak structurally could not produce. Grid swept with
`m20_exit_sweep.py --phase2` (trail_mult 3/4/5/7 × banking .25/.5 @ 1.0R/1.5R
× stale-stop combos; full 55-cell table in relay #6169). Key cells:

| cell | IS net_R / maxDD | OOS net_R / maxDD | read |
|---|---|---|---|
| pullback BTC base (trail5) | +43.6 / 11.7 | −3.7 / 10.2 | — |
| pullback BTC **trail4** | +48.4 / 12.6 | **+9.1 / 9.0** | **near-pass** — OOS flips positive, IS net_R better; only IS maxDD slips (11.7→12.6). Candidate pending a k-fold walk-forward. |
| pullback ETH base | +52.7 / 13.6 | +12.4 / 7.7 | base still best — no change |
| donchian BTC base | +51.8 / 21.8 | −24.5 / 31.9 | — |
| donchian BTC **trail7** | +58.2 / 22.2 | −3.6 / 26.5 | large OOS repair but still negative — BTC donchian's OOS weakness is structural, not exit-fixable; no change |
| donchian BTC trail3/trail4 | −20.7 / −1.7 | −36.8 / −25.7 | tighter trails are much worse for trend-followers |
| donchian ETH stale8b<0R | −49.8 / 62.5 | **+7.2 / 17.0** | phase-1 champion, confirmed vs all phase-2 cells |
| donchian SOL stale8b<0R | +5.3 / 36.8 | **+29.1 / 11.1** | phase-1 champion, confirmed |
| any `bank*` cell, all symbols | net_R always LOWER than its base | maxDD lower, win-rate higher | see below |

**The exit-ladder (banking) verdict:** partial-TP banking **reduced net_R in
every one of the 20 banking cells** while consistently lowering maxDD and
raising win rate — the classic tail-for-smoothness trade. For trend-following
strategies whose edge IS the fat right tail, banking early gives the edge
away; the ExitPlan-ladder graduation (old P4) stays parked as a net-PnL
lever. The one venue where this trade could still be RIGHT is the **prop
ruleset** (survival-weighted EV, daily-loss/DD breach rules — smoothness is
worth net_R there); logged as the follow-up
`PB-20260712-PROP-BANKING-EV` for a `run_ev_montecarlo` evaluation under
`config/prop_rulesets/breakout.yaml`.

**Trailing-geometry verdict:** direction matters and is per-family — looser
(trail7) helps the 1h donchian family's OOS, tighter (trail4) helps 2h
pullback BTC, and tight trails (trail3) are harmful everywhere. One
actionable candidate: **pullback BTC trail 5→4** (near-pass above) — proposed
for a k-fold walk-forward (the M8 tune-sweep harness) before any Tier-3 YAML
change; not shipped now.

### 6.3 ML-supplemented exits — probe result + the real experiment

The feasibility probe (`m20_ml_exit_probe.py`, relay #6168) asked whether the
existing vol-regime heads carry exit information (high P(volatile) during a
hold ⇒ worse subsequent R). **Honest result: unanswerable with current data,
and unpromising as-is** — (a) the synced shadow log only reaches back to
2026-07-07, overlapping exactly ONE closed trade; (b) in that window the
vol-regime heads read P(volatile) ≥ 0.6 essentially always (lo-bucket n = 0
across 18k records), i.e. no discrimination to trigger on.

The productive ML path is therefore a **dedicated exit head** —
`P(the trade recovers ≥ +0.25R from here)` over in-trade state (age, open R,
MFE/MAE so far, chop fraction, trail distance, native-TF vol/trend features),
trained on per-bar rows derived from historical trade paths (pure truncation
observables — no simulator, same honesty as § 3). Filed as
`MB-20260712-ML-EXIT-HEAD` with the full spec + gate ("must beat the shipped
hard stale-stop's delta on the same history"); the shadow-log history-horizon
issue is `MB-20260712-SHADOW-LOG-HISTORY`. A second experiment —
fc-range-scaled **trail distance** — rides the same harness-lever pattern and
the fc soak re-check (2026-08-25).

## 7. Phase 3 (same day) — go-live, config-exact re-validation, giveback-stop

Operator orders (in-chat, 2026-07-12): flip SOL/ETH live (soak waived), decide
trail4, build the exit head, keep pushing on proactive profit realization.

### 7.1 Stale-stop is LIVE (activation verified)

`stale_exit_bars: 8` / `stale_exit_below_r: 0.0` declared on
`trend_donchian_sol` + `trend_donchian_eth` (#6172, merged), activated via
`pull-and-deploy` (#6173): live VM HEAD `4ff5dd4 → 5b86e14`,
`ict-trader-live.service` restarted and active 08:54 UTC. Rollback = delete
the two YAML lines + restart.

**Config-exact re-validation run before the flip (relay #6170):** ETH (live
config) — clean PASS (IS −69.1→−49.8, OOS −2.1→+7.2). **SOL under its live
`long_only: true` is MIXED** — OOS +6.9→+15.5 (better net_R AND maxDD
13.9→8.7) but IS softens +27.1→+22.1 (maxDD still improves 24.8→17.1); part
of the phase-1 both-directions pass came from cutting shorts SOL never takes
live. Flip proceeded per the operator's explicit order with this on the
record; the realized stale_stop closes (journal `exit_reason='stale_stop'`)
are the live check.

### 7.2 Trail4 walk-forward (the requested decision input)

Per-year folds, pullback BTC 2h (relay #6170): trail4 beats trail5 in **4/6
folds** — 2021 +9.0→+13.0, 2022 +3.8→−1.4, 2023 +6.5→+11.5, 2024 +29.7→+28.4,
**2025 −0.4→+2.2, 2026 −4.3→+6.2**; 5y total 44.3→59.9 (+35%).
**Recommendation: flip `trail_mult: 5.0 → 4.0` on `htf_pullback_trend_2h`**
(Tier-3, one line, awaiting the operator's go).

### 7.3 Giveback-stop ("grab the PnL") — 44-cell config-exact grid (relay #6174)

- **Donchian (BTC/ETH/SOL, config-exact incl. long-only): FAILS.** The
  chandelier trail already implements a price-based giveback; the R-based
  overlay only cuts winners (BTC IS +51.8 → ≤ +33 in every cell). The
  stale-stop remains the donchian family's validated lever.
- **Pullback BTC: PASSES the spirit of the gate.** `gb 1.0R after MFE ≥ 1R`:
  IS net_R 43.1 vs 43.6 (flat), **OOS −3.7 → +7.4**, win rate 33→50%;
  `gb 0.75R` similar with OOS maxDD 9.0 vs 10.2. Independent confirmation of
  the trail4 read: pullback-BTC's exit is too loose OOS.
- **Pullback ETH: base still best** (giveback cuts IS badly). No change.
- The gb+stale combo stack is WORSE than either alone everywhere tested — do
  not stack levers without a fresh A/B.

**Pullback-BTC decision framing for the operator:** two validated candidates
that overlap (both tighten the exit) — `trail_mult 4.0` (walk-forward PASS,
§ 7.2) or `giveback 1.0R@MFE1R` (grid PASS). Recommend trail4 first (simpler,
param already exists live); the trail4+giveback combo needs its own A/B before
stacking.

## 8. E1 — exit-head training + policy replay (2026-07-12, same day)

E0 datasets (built #6182: donchian-1h 34,919 rows / 1,662 harness + 15 live
trades; pullback-2h 30,512 rows / 614 harness + 26 live) trained per the E1
protocol (`scripts/ml/train_exit_head.py`, #6184: LightGBM on `holding_pays`,
purged per-year walk-forward with last-bar purge + 7-day embargo, τ-policy
truncation replay vs actual and vs the swept hard levers). Raw run: issue
#6186; full reports in `datasets-out/exit_head/*/e1_report.json` on the
trainer.

### Pullback-2h (the family that cleared the coverage gate): **FAIL — honest negative**

Fold AUCs 0.47 / 0.53 / 0.48 / 0.54 / 0.47 — chance-level. No τ beats actual
net_R in any fold except 2026 (τ0.15: 17.5 vs 16.4, within noise at AUC 0.47).
The head cannot rank pullback in-trade bars on this feature set. **The hard
levers stand** (trail4 live; giveback grid-passed as the overlapping
alternative). E1→E2 gate: NOT met.

### Donchian-1h (below-gate research — live n=15 < 20): **PROMISING, not a gate pass**

- **AUC 0.56–0.62 in every fold** (0.604 / 0.564 / 0.610 / 0.559 / 0.617) —
  real, stable signal, unlike pullback.
- **τ=0.1 policy vs actual across the 5 folds:** aggregate net_R **86.3 vs
  73.7**, maxDD better in **5/5 folds** (e.g. 2022: +2.2 vs −18.3 at dd 13.0
  vs 34.6; 2026: +24.9 vs −4.2 at dd 6.8 vs 20.3), `net_R/pos-day` better in
  **5/5 folds** (mean hold ~6–11 bars vs ~20). It also beats BOTH hard levers
  in the same replays.
- **BUT** it loses raw net_R in the big trend years (2023: 22.2 vs 54.8;
  2024: 14.8 vs 24.9) — the banking tension again: early exits fund the bad
  years by giving up part of the good-year tail.
- **Live validation disagrees in sign** (n=15: τ-policies ≈ 0/negative vs
  actual +7.0, stale +11.7) — small-n and 2026-only, but the gate is the gate.

### Verdict + queued next steps

E1→E2: **no family passes today.** Queued (ml backlog
`MB-20260712-ML-EXIT-HEAD`):
1. **E1.5 — conditional policy shapes on donchian**: arm the exit head only
   in the states where the chop-hold loss lives (e.g. only when `open_r < 0.5`
   or after `age ≥ N`), so the trend tail is never truncated — targets the
   2023/2024 net_R giveback directly.
2. **Re-run the live half when donchian live trades ≥ 20** (currently 15;
   accrues naturally).
3. Pullback: no ML path on this feature set — revisit only with new features
   (e.g. regime-head scores as inputs) or after the E0 dataset grows.

## 9. E1.5 — conditional policy shapes (2026-07-12, same day): donchian PASSES the E1→E2 criteria

Four conditional arms added (#6193: the head may only exit while the trade is
in the chop-hold state) and re-run on the same purged walk-forward (run
#6194). Also: the E1 head was scored against the LIVE open BTC donchian trade
3344 (#6192) — 2d+ held ~flat after a +0.90R MFE round-trip, P(pays)
0.12–0.24 the whole tail, sitting just ABOVE the <0R stale-stop cell — the
concrete motivating case.

### Winner: `below_half_r @ τ=0.10` on donchian-1h
Head exits only when P(pays) < 0.10 AND the bar closed below +0.5R (a
proven trade is never touched — the trail owns it):

| fold | actual net_R / maxDD | below_half_r τ0.1 net_R / maxDD |
|---|---|---|
| 2022 | −18.3 / 34.6 | **−0.1 / 16.8** |
| 2023 | 54.8 / 33.6 | **59.3 / 17.9** |
| 2024 | 24.9 / 23.7 | **28.1 / 16.0** |
| 2025 | 16.5 / 14.1 | **26.9 / 12.5** |
| 2026 | −4.2 / 20.3 | **19.7 / 8.0** |

**Beats actual on net_R AND maxDD AND net_R/pos-day in 5/5 folds** — incl.
the 2023/2024 trend years the unconditional policy gave up (the E1 gap is
closed: 2023 is now BETTER than actual because losers are cut while the +0.5R
winners ride untouched). Aggregate net_R 133.9 vs 73.7 (+82%) at roughly half
the drawdown and half the hold time. It also beats both hard levers in every
fold. **Live validation (n=15) agrees in sign**: net +6.3 vs actual +7.0
(within noise), maxDD 8.7 vs 11.7 better, net_R/pos-day 0.91 vs 0.29 (3×);
τ0.2 even beats actual net (8.5) at dd 1.5.

Other arms: `pre_mfe1` similar but slightly weaker (4/5 folds);
`age8`-gated arms clearly worse (waiting 8 bars forfeits the early cut).
Pullback stays a FAIL (chance-level AUC — conditional shapes can't rescue a
head with no ranking signal).

### Gate verdict + proposal

Every E1→E2 criterion is met for donchian-1h (AUC materially > 0.55 all
folds; τ-policy beats the best hard rule on net_R AND maxDD; live sign
agreement). The one formality: the E0 coverage floor wanted ≥20 live trades
and donchian has 15. **Proposal (Tier-2): graduate the donchian exit head to
E2 live shadow** (observe-only scorer — logs "would exit here" per open
donchian trade, influences nothing) — the shadow itself accrues exactly the
live evidence the coverage floor wants, and E3 (YAML-declared live influence)
stays Tier-3 behind the shadow track record.

## Appendix — raw relay outputs

- Live-VM diag (soak tails + status + trades): issue #6157.
- Trainer analysis run (mirror + m20_exit_analysis full output): issue #6158.
- fc resolver + trainer-checkout fix + top-givebacks: issue #6159.
- Sweep attempts: #6160 (path bug), #6161 (relay-preempted), #6162 (detached
  launch), **#6163 (full 35-line IS/OOS table — the § 4 source)**.

## 10. `exit_ladder` on the ict_scalp fleet (2026-08-09) — closing a HARNESS gap, not a verdict gap

All eight live `ict_scalp` legs read `blocked:no_harness_levers` in the
coverage matrix for the `exit_ladder` column. That was accurate:
`scripts/backtest_ict_scalp.py` had no `bank_frac`/`bank_at_r` at all — the
2026-07-28 round (#7848) built stale-stop + giveback-stop on this harness and
stopped there. **The block was a missing lever, not a measured negative**, and
a `blocked` cell on a live leg is an open item in M20's done-condition.

### 10.1 The prior, stated up front — and why it does NOT settle this

**On record (§ 6.2, and ROADMAP): partial-TP banking reduced net_R in every
one of the 20 banking cells swept across the donchian and pullback families**,
while lowering maxDD and raising win rate — the classic tail-for-smoothness
trade. A negative here is therefore the EXPECTED result, is recorded as
`honest_negative`, and **is not to be re-litigated**.

But the stated *mechanism* is specific, and it is worth being precise about
whether it transfers, because a prior accepted for the wrong reason is not a
prior — it is a guess that happens to agree:

> "for trend-following strategies whose edge IS the fat right tail, banking
> early gives the edge away"

**That reasoning does not apply to `ict_scalp`.** Every live leg is a FIXED
bracket at `tp_at_r: 1.5` (verified against `config/strategies.yaml`,
2026-08-09, all eight legs). There is no fat right tail to give away — the
take-profit caps the upside at 1.5R by construction. So the fleet-wide result
does not transfer by its own logic, and this round is a real test rather than
a formality.

A *different* mechanism, specific to this family, pushes the same direction and
is the one to read the result against: `--sim-breakeven` is part of the
config-exact base (it mirrors the live `monitor_breakeven_sl` rule), so once a
bar closes ≥ 1R the stop is already at entry + `be_offset_bps` (15 bps on all
eight legs). A trade that reaches 1R is therefore **already** protected. That
predicts:

- banking at **1.0R** buys smoothness the breakeven stop already provides,
  while capping the 1.0R → 1.5R run — expected neutral-to-negative;
- banking at **0.5R** (below the BE arm) is the only rung where a real effect
  is available at all.

If the result is negative, the honest reading is "the BE stop already occupies
this lever's job on this family", not a re-derivation of the trend-follower
argument.

### 10.2 The trap this round had to avoid: half the standard grid measures nothing

The fleet grid (`m20_fleet_exit_sweep` / `m20_exit_sweep --phase2`) sweeps
`bank_at_r ∈ (1.0, 1.5)`. On a `tp_at_r = 1.5` leg, **a rung at or above the
take-profit is a provable no-op**: the TP check returns on the same bar and the
blend `f·tp + (1−f)·tp` returns `tp` exactly. Run as-is, half of every leg's
cells would have measured NOTHING and reported a confident "no effect" —
precisely the unasserted-denominator shape RULE ONE names.

Two things prevent it structurally rather than by remembering:

1. The sweep derives its rungs as **fractions of the leg's own `tp_at_r`**
   (`_RUNG_FRACS_OF_TP = (1/3, 2/3)` → 0.5R / 1.0R here) and refuses to emit a
   cell at or above it.
2. The harness summary echoes `banked_trades` / `banked_pct` — the **rung-fill
   denominator**. A cell whose rung almost never fills is INERT, which is a
   different finding from a lever that filled often and lost money, and the two
   must not read alike. Read every verdict below beside its `banked_pct`.

Proven in `tests/test_ict_scalp_exit_levers.py::test_rung_at_or_above_tp_is_a_provable_noop`.

### 10.3 Config-exactness: one leg differs, and it is not the obvious one

Verified by diffing all eight leg blocks in `config/strategies.yaml` rather
than trusting the "every ict_scalp leg is a config-exact copy" comment in the
M27 sweep: the **detection geometry is identical across all eight**, and
exactly four keys differ — `off_cells` + `vol_spec` (`ict_scalp_xrp_5m` only),
and `stale_exit_bars: 12` + `stale_exit_below_r: 0.0` (`ict_scalp_eth_15m`
only).

So `ict_scalp_eth_15m` ships an exit lever the other seven do not, and its
ladder cells must be measured **on top of** it. The sweep now passes a leg's
declared levers into its base (`declared_lever_flags`); without that, that one
leg would have been swept against a baseline it does not run live.

**Known caveat, recorded rather than papered over:** `ict_scalp_xrp_5m`
declares `off_cells` (regime gating) which the harness cannot reproduce — it
can stamp regime (`--stamp-regime`) but not gate on it. Its swept population is
therefore a SUPERSET of what trades live. The ladder A/B is still valid as a
*delta* (both arms see the same population), but the XRP-5m verdict's
population is not the live one, and the matrix ref says so.

### 10.4 Data reachability — 7 of 8 legs, and the 8th is a reproducibility problem

Probed the trainer directly (issue #8696) rather than assuming:

| leg | data | status |
|---|---|---|
| `ict_scalp_5m` (BTC 5m) | `backtest_BTCUSDT_5m.csv` (647k bars, 2020-03→2026-05) | ✅ |
| `ict_scalp_sol_5m` / `_xrp_5m` / `_avax_5m` | `{SOL,XRP,AVAX}USDT_5m.csv` (491k / 536k / 500k) | ✅ |
| `ict_scalp_eth_15m` / `_sol_15m` / `_xrp_15m` | `{ETH,SOL,XRP}USDT_15m.csv` (184k / 164k / 179k) | ✅ |
| `ict_scalp_mgc_15m` | **none** — no `MGC_*`, no `XAUUSD_*`; only `GC_F_1d` / `GC_F_1h` | ❌ |

⚠️ **The MGC leg is a live reproducibility gap, not just a skip.** The
2026-07-28 M27 round measured that leg on `data/XAUUSD_15m_deep.csv` (the
powered Dukascopy spot-XAU proxy). **That file is no longer on the trainer**, so
the leg's EXISTING `stale_stop` / `giveback_stop` verdicts currently cannot be
reproduced. It is regenerable on a free GitHub runner via
`research-symbol-p0-build.yml` (Dukascopy fetch, off the scarce trainer VM);
tracked rather than silently dropped.

### 10.5 Verdicts

*(Sweep dispatched 2026-08-09 on the trainer — config-exact per leg, IS/OOS
split 2025-07-01 + yearly walk-forward, `need = ceil(2·usable/3)` → 4/6 when
all folds are usable. Verdicts are recorded in the coverage matrix in the same
PR as the run that produced them, per the exit-refinement skill; they are NOT
pre-written here from the prior.)*

## 11. Re-evaluating the banking mechanism (2026-08-10, operator-requested)

Operator: *"we can reevaluate the banking mechanism, in any case I think that
would be worthwhile."* This section is that re-evaluation. Its conclusion is not
"banking works after all" — it is that **the fleet-wide result recorded in § 6.2
carries far less information than it appears to**, and the reason is a property
of the gate rather than of banking.

### 11.1 The gate cannot be passed by anything shaped like banking

Both lever gates require a cell to beat baseline on net_R **and** maxDD:

| harness | predicate |
|---|---|
| `m27/ict_scalp_exit_sweep.py::beats` | `cell.total_r > base.total_r` **and** `cell.max_dd_r < base.max_dd_r` |
| `m20_fleet_exit_sweep.py::beats` | `cn >= bn` **and** `cd <= bd` **and** (`cn > bn` or `cd < bd`) |

Partial-TP banking's entire mechanism is to **truncate the winner distribution
at the rung**: a fraction of the position is realised at `+bank_at_r` instead of
riding to the exit. Where the exit R exceeds the rung — which is what a winner
is — that fraction gives up return. So banking **necessarily** lowers net_R and
lowers maxDD. `net_R >= base` is therefore false **by construction**, and:

> **No banking cell can ever be a candidate under either gate, regardless of how
> good the risk-adjusted trade is. P(pass) = 0, a priori.**

That reframes § 6.2's headline. "Banking reduced net_R in every one of the 20
banking cells" is true, and the cells did lose net_R — but it is **one
structural property observed twenty times, not twenty independent negatives**,
and "it failed the gate" carries no information *about banking* because nothing
of that shape can pass. The § 6.2 prose already half-saw this ("the classic
tail-for-smoothness trade") and then recorded the outcome as though the gate had
adjudicated it.

This is the *sibling* of the collapsed-states class made canonical the same day:
there, two states shared one value; here, **two very different cells share one
verdict**. A cell that surrenders 1R of return to remove 5R of drawdown and a
cell that surrenders 1R to remove 0.2R are both stamped `honest_negative`.

### 11.2 What the gate discards, and the tool that reads it

`scripts/research/m20_banking_risk_adjusted.py` reads a sweep's existing
`verdicts.json` and reports the quantities the primary gate throws away —
**MAR** (`net_R / maxDD`), and the trade ratio **DD/R** (drawdown removed per
unit of net_R surrendered). Worked example from its self-test:

| cell | ΔnetR | ΔmaxDD | MAR base → cell | DD/R | gate |
|---|--:|--:|--:|--:|---|
| `bank0.5@1R` | −5.00 | **−11.00** | 2.00 → **3.89** | **2.50** | `honest_negative` |
| `bank0.25@0.5R` | −0.50 | −0.20 | 2.00 → 1.99 | 0.50 | `honest_negative` |

Same verdict, opposite objects. **It changes no gate and ships nothing** — the
net_R+maxDD gate stays the shipping criterion, because relaxing a gate to admit
a lever is precisely how a cosmetic lever gets shipped
(`BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS`). What it makes possible is an
*honest* negative — "banking cost net_R and bought too little drawdown to be
worth it here" — instead of a tautological one.

### 11.3 Where banking could genuinely be right (and is still unmeasured)

1. **Prop rulesets — the strongest case, logged 2026-07-12 and never run.**
   `PB-20260712-PROP-BANKING-EV` records exactly this: under a prop ruleset a
   breach of the daily-loss limit or the static-DD floor is **terminal**, so
   drawdown is not a preference, it is a survival constraint that net_R does not
   price. `src/prop/montecarlo.py::run_ev_montecarlo` + `config/prop_rulesets/
   breakout.yaml` exist for cost-aware EV **+ survival**. This is the one venue
   where the tail-for-smoothness trade is plausibly *correct*, and it has sat
   unexecuted for a month.
2. **Capital efficiency.** The exit-refinement skill already names *net_R per
   position-day* as the gate's tiebreak. Banking frees capital earlier; the
   sweeps have never reported it.
3. **Conditional banking.** The unconditional lever is crude. The E1.5 result in
   § 9 is the precedent: an unconditional policy FAILED and a *conditional* shape
   (`below_half_r @ τ=0.10`) passed on donchian-1h. Banking only when the tail is
   unlikely is the same move, and untested.

### 11.4 What this does and does not license

- It does **not** license shipping a banking cell. Nothing here changes a gate.
- It does **not** overturn § 6.2's measurements — those numbers stand.
- It **does** mean the ict_scalp ladder round (§ 10) must be read with the
  risk-adjusted tool beside the gate verdict, or it will reproduce the same
  uninformative negative on seven more legs.
- The open operator question is narrow: **is a net_R-first gate the right
  shipping criterion for a lever whose declared purpose is drawdown
  reduction — and specifically, should the prop sleeve be gated on
  survival-weighted EV instead?** Filed as
  `BL-20260810-BANKING-GATE-CANNOT-PASS`.
