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
| 10 | Crypto funding **LEVEL** (entry-3's signal) | **D2 conditioning** — funding level, and funding level gated on **rising OI** | **rising-OI gate** | time-series | contrarian | `pnl_but_no_signal` (level) / `pnl_but_no_signal` (level×OI), worth_building=**False** — S2 honest **False** for both; **gross/zero-fee** (`--fee-frac 0`): `funding_level` conv_ret **+0.316** / Sharpe **+0.58** / pays_oos True; `funding_level_x_oi_rising` conv_ret **−0.497** / Sharpe −0.705 (`crypto_construction_sweep.json`, #7522) | **The decisive D2-crypto result, two findings.** (1) **The rising-OI crowding gate is REFUTED for the level** — it flipped the sign, +0.316 → **−0.497**. The impulse got a marginal *help* from the gate (entry 9); the level gets *destroyed* by it. So "crowding builds as OI rises → fade harder" does not hold for the level; the level signal is strongest **unconditioned**, and neutralizing the not-rising-OI half throws away its informative bars. **D2 conditioning on OI is a dead end for crypto funding.** (2) `funding_level` is a **positive-net near-miss and the strongest result in the program so far** — and this **corrects entry 3's "below fees" framing**. The fee-aware re-grade (#7523, 10 bps round-trip) barely moved it: gross +0.316 → **net +0.293 / Sharpe +0.564 / pays_oos True**. The fee impact is small because this harness holds **30-day cohorts** (not the daily-churn a 1d-IC read implies), so 10 bps over a 30-day hold is a tiny drag — the level's conviction portfolio *survives cost*. The binding constraint is **NOT fees, it's S2 signal-confirmation** (honest non-overlapping IC still insignificant → `pnl_but_no_signal`, so `worth_building=False` is still correct — you don't productionize an unconfirmed IC). This flips the crypto read: the funding LEVEL is the **most promising OPEN thread**, not exhausted — the lever is now getting S2 to pass (a horizon/cross-section where the per-name IC is significant), not magnitude or cost. |

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
- ~~**10 · Crypto D2 conditioning — funding LEVEL × rising-OI**~~ — **DONE** (row 10
  above). No survivor, but the fee-aware re-grade (#7523) **corrected the "below fees"
  read**: `funding_level` nets **+0.293 / Sharpe 0.564 at 10 bps** (30-day holds, so
  cost barely bites). The rising-OI gate is **refuted** for the level (flips the sign to
  −0.50). The binding constraint is **S2 signal-confirmation, not cost** — so the level
  is the strongest OPEN thread, NOT closed. D2-conditioning-on-OI *is* closed.
- **11 · Cross-sectional VALUE sleeve** — rank the value instruments cross-instrument
  on own-history cheapness (the D3 frame on the value input, distinct from the failed
  COT D3). **Design note:** the seed universe (`config/macro_valuation.yaml`) has
  TLT/IEF/GLD all keyed off the *same* DFII10 real-yield series → a naive per-instrument
  cross-section is degenerate (three near-identical constituents). The honest D3-value
  frame is over the **distinct valuation drivers**: SPY←ERP, TLT←real-yield,
  SLV/GLD←gold-silver-ratio, credit←OAS — each an oriented own-history cheapness, ranked
  per date. Runs off-VM (FRED + a couple of ETF closes).
- **12 · D4 composite** — combine the surviving-gross signals (funding level + a value
  driver) into one conviction score, testing whether diversification lifts the blended
  Sharpe above any single sleeve. Only worth it if ≥2 sleeves show a real gross edge.

## The compounding read so far (entries 1–10)

Thirteen graded constructions across ten ledger rows, **zero survivors** — and that is a
*result*, not a stall. The pattern narrows where the edge can still be:

- **COT is exhausted across construction cells.** Level (entry 2), the D1 transform
  sweep (entry 5), AND the D3 cross-market basket (entry 7) all fail the honest gate.
  Three orthogonal framings of spec-positioning, three nulls ⇒ the *input* carries no
  cost-surviving edge; only a different underlying COT signal could, not more framing.
- **Crypto funding LEVEL is the strongest OPEN thread — NOT exhausted** (correcting an
  earlier "below fees" read). The fee-aware grade (entry 10, #7523) shows the level's
  conviction portfolio nets **+0.293 / Sharpe 0.564 at 10 bps** — it *survives cost*
  (30-day holds, low turnover). What it fails is **S2 signal-confirmation** (the honest
  non-overlapping per-name IC is insignificant), so it stays `pnl_but_no_signal`. The
  lever is therefore **IC-confirmation, not cost or magnitude**: find a horizon /
  cross-section / cleaner label where the level's per-name signal is statistically
  flagged. What IS exhausted for crypto: the **impulse** (worse) and **D2 conditioning
  on rising-OI** (refuted — it destroys the level).
- **Level-percentile / D1-transform / D2-conditioning of a single raw series has not
  produced a full survivor** on any tried input (COT, crypto, value-level entry 1) — but
  crypto's level is a genuine near-miss (positive net Sharpe, only S2 unconfirmed), which
  distinguishes it from COT (negative everywhere).
- **The mechanistic route (entries 4, 8) is gated on calibration first** — a model
  that can't forecast the level can't misprice it.

**The sharper meta-finding:** COT is exhausted (negative in every cell); crypto's funding
LEVEL is a positive-net near-miss blocked *only* on S2 IC-confirmation. Two live threads
remain, in priority order: **(a) close crypto's S2 gap** — re-test the funding level for a
horizon/label where the per-name IC is significant (the most promising, since the PnL side
already pays net); and **(b) combine signals across inputs** — **D3 cross-section on the
VALUE sleeve** (entry 11 — rank the distinct value drivers ERP/real-yield/GSR/OAS) and
**D4 composite** (entry 12). That is the queue.
If both fail, the honest conclusion is that these free/cheap macro inputs carry no
cost-surviving edge in any construction, and the search should move to a *different input
class* (higher-frequency microstructure, or a paid/alternative dataset) rather than more
constructions on the same series.
