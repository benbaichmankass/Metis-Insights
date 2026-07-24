# M28 — Signal-research ledger

The compounding record of every signal **construction** tried through the honest
gate, its verdict, and the learning. One row per construction (input × transform ×
conditioning × cross-section). Append-only; a null is a completed entry, never a
non-event (`RESEARCH-RIGOR-STANDARD.md` § honest negatives). Process + backlog:
[`M28-signal-research-methodology.md`](M28-signal-research-methodology.md).

Gate = `thesis_backtest_run.py` (P4) + `horizon_ic_scan.py --non-overlapping`
(honest t) + conviction spread (cost-aware). Bar = flagged-significant IC AND a
positive, cost-surviving conviction spread at a tradeable horizon.

| # | Input | Transform | Cond. | X-sec | Orient. | Honest verdict | Learning |
|---|---|---|---|---|---|---|---|
| 1 | Value (ERP/real-yield/GSR/OAS) | level percentile | none | time-series | value-native | `no_monetizable_horizon` — best IC 0.032@7d, t=1.06 | Level-percentile of a valuation series carries no honest short/mid-horizon edge on its own. |
| 2 | CFTC-COT large-spec net | level percentile | none | time-series | contrarian | `no_monetizable_horizon` — 90d "edge" was overlap inflation (t≈3.2 overlapping → **1.16 non-overlapping**), conv_spread negative | The apparent COT signal was a *measurement artifact* of overlapping windows, not a real edge. Level of spec positioning ≠ predictive. |
| 3 | Crypto funding/OI/basis | level percentile | none | time-series | contrarian (crowding fade) | nominal `monetizable_horizon_found` @1d (IC 0.070, t=2.10) but conv_spread **negligible** (+2 bps/day gross, net-negative after fees); 7–14d spreads not significant | There *is* a real 1d statistical signal in funding/basis crowding, but its magnitude is below fees. A bigger-magnitude construction or a longer horizon is needed to monetize it. |
| 4 | Gas storage↔price (M29 sysdyn) | mechanistic calibration (storage-anchored + weather HDD) | — | — | — | `park_deeper_investment` / `no_mechanistic_edge` — price readout ~0 OOS (storage OOS R²=−0.43, price OOS R²=0.002, not identifiable) | Graded on *calibration R²*, NOT yet through the signal gate. Distinct open question: does the model-implied **mispricing**, emitted as a snapshot signal, trade through the P4/horizon gate? (Next M29 step — now built, entry 8.) |
| 5 | CFTC-COT large-spec net | **D1 sweep** — change (Δ impulse) · divergence (spec-vs-commercial rolling-z gap) · detrend (dev-from-mean) | none | time-series | contrarian | `no_edge` (divergence, detrend) / `pnl_but_no_signal` (level, change) — **none worth building** (`cot_construction_sweep.json`, #7509) | The D1 transforms do **not** rescue COT. Change/divergence/detrend all fail the S2 signal gate exactly as the level did (entry 2). The limitation is the **INPUT** (spec-positioning level/change/divergence carries no honest predictive signal on these proxies), not the construction cell — so the next lever for COT is a different input or a cross-sectional/composite frame (D3/D4), not another D1 transform. |
| 8 | Gas storage↔price (M29 sysdyn) | **model-implied mispricing** — `(market − model)/model` vs the seed model's storage→price readout (UNG) | none | time-series | contrarian (below fair = cheap) | `no_edge`, worth_building=False — S2 honest False, S3 `pays_oos` False, **conv_ret −0.79**, Sharpe −0.04 over 835 snapshots (`sysdyn_mispricing_scorecard.json`, #7512) | **The sysdyn work IS now used — graded honestly — and the mispricing does not trade.** Consistent with entry 4's calibration: the price readout has OOS R²≈0.003, so it barely tracks price, so its "mispricing" is mostly noise. A mechanistic model that can't forecast the level can't produce a tradeable mispricing off it. Parks the seed-gas signal path; the mechanistic route needs a model that clears the calibration gate FIRST (entry 4) before its mispricing is worth grading. |

## Reading the ledger

Entries 1–3 share one construction cell — **level-percentile / no-conditioning /
time-series**; entry 4 is a calibration-not-signal test; entry 5 is the first **D1
sweep** (change/divergence/detrend on COT). The entry-5 result sharpens the read:
for COT, varying the *transform* (D1) did not help — which points the search at the
*input* and at the still-untried **D3 cross-section / D4 composite** cells rather
than more D1 variants. The
[methodology backlog](M28-signal-research-methodology.md#the-construction-backlog-what-to-try-next--the-dimensions-we-have-not-varied)
lists the unexplored dimensions (D1 transform, D2 conditioning, D3 cross-section,
D4 composite) each of these inputs can still be run through.

## Next entries (queued)

- ~~**5 · COT change/divergence**~~ — **DONE** (row 5 above; the D1 sweep, none worth building).
- **6 · Crypto D1 sweep + funding-change × OI-rising** — the same `construction_sweep`
  engine on funding/OI/basis (change/detrend), then funding impulse conditioned on
  rising OI + basis premium (D1 + D2). Needs the trainer-VM relay (Bybit geo-block).
- **7 · Cross-sectional value/COT** — rank instruments against each other per date
  (D3, `cross_sectional_snapshots`), long-cheapest/short-richest basket. Needs a
  cross-comparable metric (normalized COT-index / z-score, not raw spec_net).
- ~~**8 · sysdyn mispricing as a snapshot signal**~~ — **DONE** (row 8 above; `no_edge`,
  the mispricing doesn't trade — a mechanistic model that fails the calibration gate
  can't yield a tradeable mispricing).

## The compounding read so far (entries 1–8)

Eight constructions, **zero survivors** — and that is a *result*, not a stall. The
pattern across them narrows where the edge can still be:

- **Level-percentile / D1-transform of a single raw series is exhausted** on value,
  COT, and crypto (entries 1–3, 5). Varying the transform did not rescue any input.
- **Crypto (entry 3) is the one live statistical signal** (real 1d IC) but its
  magnitude is below fees — so the lever is *magnitude*, not *existence*: a
  bigger-amplitude construction or a cost structure that fits, not another percentile.
- **The mechanistic route (entries 4, 8) is gated on calibration first** — a model
  that can't forecast the level can't misprice it.

The **still-untried cells** are therefore the priority, in order: **D3 cross-section**
(rank instruments against each other — the classic value/carry frame, never run here),
**D2 conditioning** (crypto funding-impulse × rising-OI — targets entry 3's magnitude
problem directly), and **D4 composite**. That is the queue.
