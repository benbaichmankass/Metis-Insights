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
| 7 | CFTC-COT large-spec net | **D3 cross-sectional** — rank the COT markets against each other per date on z-scored spec_net (z is the cross-comparable metric; raw spec_net isn't comparable across crude/gold/copper) | none | **cross-section** | contrarian | `no_edge`, worth_building=False — S2 honest False, S3 `pays_oos` False (conv_ret +0.43 gross but doesn't beat the all-long arm on the OOS half) over the multi-market basket (`cot_construction_sweep.json`, #7516) | Cross-section doesn't rescue COT either. **Level (entry 2), D1 transforms (entry 5), AND the cross-market basket (this) all fail** — the COT INPUT carries no honest, cost-surviving edge in *any* construction cell tried. This exhausts construction-variation on COT: the only remaining COT lever is a *different underlying signal* (not spec-positioning), not another framing of the same series. |
| 8 | Gas storage↔price (M29 sysdyn) | **model-implied mispricing** — `(market − model)/model` vs the seed model's storage→price readout (UNG) | none | time-series | contrarian (below fair = cheap) | `no_edge`, worth_building=False — S2 honest False, S3 `pays_oos` False, **conv_ret −0.79**, Sharpe −0.04 over 835 snapshots (`sysdyn_mispricing_scorecard.json`, #7512) | **The sysdyn work IS now used — graded honestly — and the mispricing does not trade.** Consistent with entry 4's calibration: the price readout has OOS R²≈0.003, so it barely tracks price, so its "mispricing" is mostly noise. A mechanistic model that can't forecast the level can't produce a tradeable mispricing off it. Parks the seed-gas signal path; the mechanistic route needs a model that clears the calibration gate FIRST (entry 4) before its mispricing is worth grading. |
| 9 | Crypto funding **impulse** (Δ funding) | **D2 conditioning** — funding impulse, and funding impulse gated on **rising OI** (crowding building) | **rising-OI gate** | time-series | contrarian | `no_edge` (both cells), worth_building=False — S2 honest **False** for both; `funding_impulse` conv_ret −0.328 / Sharpe −0.451, `funding_impulse_x_oi_rising` conv_ret −0.270 / Sharpe −0.457 (`crypto_construction_sweep.json`, #7519) | Two learnings. (1) The rising-OI gate **moved conv_ret in the right direction** (−0.328 → −0.270, ~18% less negative) — conditioning *is* a live lever on this input, the first construction dimension to shift the number rather than reproduce a null. But it's nowhere near enough, and neither cell clears the S2 signal gate. (2) **Methodological miss to own honestly:** this conditioned the funding *impulse* (Δ), whereas entry 3's real-but-below-fee 1d signal was the funding **LEVEL** percentile. So entry 9 is *not yet* the faithful D2 test of entry 3 — the impulse is a different (worse) base series that the OI gate couldn't rescue. The faithful test — funding **LEVEL** × rising-OI — is the immediate follow-up (entry 10, code landed, grade dispatched). |

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
- ~~**6/9 · Crypto D2 conditioning — funding impulse × rising-OI**~~ — **DONE** (row 9
  above; `no_edge`, both cells fail S2 — but the OI gate did move conv_ret the right
  way, and the miss is that it gated the *impulse* not entry-3's *level*).
- ~~**7 · Cross-sectional value/COT**~~ — **DONE for COT** (row 7 above; `no_edge`). The
  D3 frame is still untried on the **value** sleeve (rank ERP/real-yield/GSR/OAS
  cross-instrument) — that's the remaining D3 experiment, on a different input.
- ~~**8 · sysdyn mispricing as a snapshot signal**~~ — **DONE** (row 8 above; `no_edge`,
  the mispricing doesn't trade — a mechanistic model that fails the calibration gate
  can't yield a tradeable mispricing).
- **10 · Crypto D2 conditioning — funding LEVEL × rising-OI** — the *faithful* D2 test
  entry 9 should have been: condition entry-3's real-but-below-fee funding **level**
  percentile on rising OI, testing whether the crowding gate concentrates that live
  1d signal above fees. Code landed (`crypto_conditioning_snapshots` now emits
  `funding_level` + `funding_level_x_oi_rising` alongside the impulse pair); grade
  dispatched via the trainer-VM relay (Bybit geo-block).
- **11 · Cross-sectional VALUE sleeve** — rank ERP/real-yield/GSR/OAS cross-instrument
  (the D3 frame on the value input, distinct from the failed COT D3). Runs off-VM.

## The compounding read so far (entries 1–9)

Eleven graded constructions across nine ledger rows, **zero survivors** — and that is a
*result*, not a stall. The pattern narrows where the edge can still be:

- **COT is exhausted across construction cells.** Level (entry 2), the D1 transform
  sweep (entry 5), AND the D3 cross-market basket (entry 7) all fail the honest gate.
  Three orthogonal framings of spec-positioning, three nulls ⇒ the *input* carries no
  cost-surviving edge; only a different underlying COT signal could, not more framing.
- **Level-percentile / D1-transform of a single raw series is exhausted** on value and
  crypto too (entries 1, 3). Varying the transform did not rescue any input.
- **Crypto (entry 3) is the one live statistical signal** (real 1d IC) but its
  magnitude is below fees — so the lever is *magnitude*, not *existence*: a
  bigger-amplitude construction or a cost structure that fits, not another percentile.
- **D2 conditioning is the first lever that MOVED the number** (entry 9): the rising-OI
  gate cut `funding_impulse`'s conv_ret ~18% less negative. It didn't clear the gate,
  and it gated the wrong base series (impulse, not entry-3's level) — but "conditioning
  shifts conv_ret" is a live finding, so the faithful **level × rising-OI** test
  (entry 10) is worth running before abandoning D2.
- **The mechanistic route (entries 4, 8) is gated on calibration first** — a model
  that can't forecast the level can't misprice it.

The **queue**, in order: **entry 10 — crypto funding-LEVEL × rising-OI** (the faithful
D2 test of entry 3's below-fee signal, the single most promising open lever since (a)
crypto is the one input with a real signal and (b) conditioning already demonstrably
moves conv_ret); then **entry 11 — D3 cross-section on the VALUE sleeve** (rank
ERP/real-yield/GSR/OAS cross-instrument — the D3 frame on a different input than the
failed COT one); then **D4 composite**.
