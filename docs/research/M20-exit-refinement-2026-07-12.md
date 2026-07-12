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

## Appendix — raw relay outputs

- Live-VM diag (soak tails + status + trades): issue #6157.
- Trainer analysis run (mirror + m20_exit_analysis full output): issue #6158.
- fc resolver + trainer-checkout fix + top-givebacks: issue #6159.
- Sweep attempts: #6160 (path bug), #6161 (relay-preempted), #6162 (detached
  launch), **#6163 (full 35-line IS/OOS table — the § 4 source)**.
