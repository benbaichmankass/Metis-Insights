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
| 10 | Crypto funding **LEVEL** (entry-3's signal) | **D2 conditioning** — funding level, and funding level gated on **rising OI** | **rising-OI gate** | time-series | contrarian | `pnl_but_no_signal` (level) / `pnl_but_no_signal` (level×OI), worth_building=**False** — S2 honest **False** for both; **gross/zero-fee** (`--fee-frac 0`): `funding_level` conv_ret **+0.316** / Sharpe **+0.58** / pays_oos True; `funding_level_x_oi_rising` conv_ret **−0.497** / Sharpe −0.705 (`crypto_construction_sweep.json`, #7522) | **The decisive D2-crypto result, two findings.** (1) **The rising-OI crowding gate is REFUTED for the level** — it flipped the sign, +0.316 → **−0.497**. The impulse got a marginal *help* from the gate (entry 9); the level gets *destroyed* by it. So "crowding builds as OI rises → fade harder" does not hold for the level; the level signal is strongest **unconditioned**, and neutralizing the not-rising-OI half throws away its informative bars. **D2 conditioning on OI is a dead end for crypto funding.** (2) `funding_level` is a **positive-net near-miss and the strongest result in the program so far** — and this **corrects entry 3's "below fees" framing**. The fee-aware re-grade (#7523, 10 bps round-trip) barely moved it: gross +0.316 → **net +0.293 / Sharpe +0.564 / pays_oos True**. The fee impact is small because this harness holds **30-day cohorts** (not the daily-churn a 1d-IC read implies), so 10 bps over a 30-day hold is a tiny drag — the level's conviction portfolio *survives cost*. The binding constraint is **NOT fees, it's S2 signal-confirmation** (honest non-overlapping IC still insignificant → `pnl_but_no_signal`, so `worth_building=False` is still correct — you don't productionize an unconfirmed IC). This flips the crypto read: the funding LEVEL is the **most promising OPEN thread**, not exhausted — the lever is now getting S2 to pass (a horizon/cross-section where the per-name IC is significant), not magnitude or cost. **[⚠ This optimistic read was itself over-corrected — SUPERSEDED by entry 11: the dense-horizon scan shows the S3 "pays_oos" was a short-bias benchmark artifact, not alpha; no honest monetizable horizon exists.]** |
| 11 | Crypto funding **LEVEL** | **dense-horizon S2 scan** — resolve the S2/S3 gap: scan honest non-overlapping IC + net conv_spread across 1,2,3,5,7,10,14,21,30d at 10 bps | none | time-series | contrarian | `no_edge`, worth_building=**False** — `any_honest_monetizable_horizon=False` at EVERY horizon. Best positive IC at **1d (ic 0.049, ic_t 1.658 — NOT significant)**; IC turns strongly **negative** at 7–30d (−0.09 → −0.16 → −0.20); no horizon is both significant and net-positive (`#7526`) | **The decisive resolution — corrects BOTH prior reads.** (a) **Entry 3's "1d IC t=2.10 significant" was overstated:** the honest non-overlapping 1d `ic_t` is **1.658** (insignificant). (b) **Entry 10's "pays net, strongest open thread" was over-optimistic:** the S3 `pays_oos` at 30d is a **benchmark artifact** — a contrarian/short-biased book beats all-long in a falling market — NOT alpha, because the honest IC at the monetizable horizons (7–30d) is strongly **negative** (−0.09 → −0.20): the fade is actively **wrong** there. The contrarian funding thesis has only a weak, insignificant positive tilt at 1d (sub-fee) and **inverts to momentum** at multi-day. **Crypto funding is genuinely exhausted.** *Methodology learning:* S3 conviction-vs-all-long `pays_oos` can be a directional short-bias artifact in a trending market — the honest per-horizon IC is the real arbiter, and a dense-horizon scan is what exposes it. |
| 12 | Value sleeve — **distinct drivers** (SPY←ERP, TLT←real-yield, SLV←GSR) | **D3 cross-sectional** — rank the three instruments per date on oriented own-history cheapness (`cheap_score`), avoiding the degenerate TLT/IEF/GLD-share-DFII10 trap | none | **cross-section** | value-native (cheaper = higher rank) | `no_edge`, worth_building=**False** — S2 honest **False**, S3 `pays_oos` **False**; conv_ret **+0.236** gross / Sharpe **0.142** over the 3-instrument basket on real ETF history (SPY 5423 / TLT 5423 / SLV 5091 daily closes; `m28-value.json`, #7534, graded on a GitHub-hosted US-IP runner) | **The value sleeve's D3 frame also fails — and this exhausts the THIRD real input.** The candle fetch worked cleanly this time (the trainer-VM datacenter-IP block was the reason #7529/#7531 failed, not the construction), so this is a genuine graded null. conv_ret is weakly positive (+0.236) but the honest S2 IC is insignificant and S3 doesn't beat the all-long benchmark OOS — the same `pnl_but_no_signal`-shaped shortfall as every other sleeve. **D3 cross-section has now failed on both inputs it was tried on (COT entry 7, value here); all three real inputs — COT, crypto, value — are exhausted across every construction cell.** *Note:* only 3 distinct drivers cleared the "distinct series" bar (OAS/credit needs an HY-OAS ETF proxy the seed universe doesn't carry), so the cross-section is thin (min_symbols=3, exactly at the floor) — a wider value cross-section would need more distinct-driver instruments, but the per-instrument S2 IC being insignificant makes that unlikely to rescue it. |

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
- ~~**10 · Crypto D2 — funding LEVEL × rising-OI**~~ / ~~**11 · dense-horizon S2 scan**~~
  — **DONE** (rows 10–11). Crypto funding is **genuinely exhausted**: the dense scan
  (row 11, #7526) showed no honest monetizable horizon (IC insignificant at 1d, negative
  at 7–30d) and that the S3 "pays" was a short-bias benchmark artifact. The rising-OI
  gate is refuted. Nothing more to try on the crypto-funding input.
- ~~**12 · Cross-sectional VALUE sleeve (D3)**~~ — **DONE** (row 12 above; `no_edge`,
  #7534). The distinct-driver value cross-section (SPY←ERP / TLT←real-yield / SLV←GSR)
  fails the honest gate on real ETF history — S2 IC insignificant, S3 doesn't beat all-long
  OOS. Value is now exhausted alongside COT and crypto.
- ~~**13 · D4 composite**~~ — **MOOT, not built** (honest call). D4 combines surviving-gross
  signals; it is only worth building if ≥2 sleeves show a real gross edge. **None do** —
  funding_level's gross +0.316 was refuted as a short-bias artifact (entry 11), value_xsec's
  +0.236 gross fails S2/S3 (entry 12), and every other cell is net-negative. Blending nulls
  cannot manufacture an edge; a composite of signals that individually carry no honest IC
  has no honest IC either. Recording D4 as moot rather than running it to a foregone null is
  the honest disposition (`RESEARCH-RIGOR-STANDARD.md` § don't burn compute on a foregone
  result — reason it out and record it).
- ~~**14 · D4 composite of the two VALIDATED leads (`vix_term` + `hy_oas_pct`)**~~ — **MOOT,
  not built** (honest call, 2026-07-25). Distinct from entry 13 (which blended *nulls*): entry
  13 asked "do the no-IC sleeve signals combine?", this asks "do the two signals that DID pass
  the IC gate — `vix_term` (M31, robust) and `hy_oas_pct` (M32, robust-WF) — ensemble into a
  DEPLOYABLE edge?" **Moot by a clean a-priori Sharpe ceiling:** the standalone timing Sharpes
  are ~0.0–0.18 (Track A-S5), and a 2-factor ensemble caps at `single × √2 ≈ 0.25` even in the
  *best* case (perfectly uncorrelated). Both are same-signed US-equity-*stress* gauges (VIX
  term structure + HY credit spread), so they're plausibly positively correlated → the real
  ensemble Sharpe is *below* 0.25, still far under the ≥0.5–1.0 a standalone deployable book
  needs. Two thin, likely-correlated edges cannot arithmetically combine past the deployable
  bar; running it is a foregone null. Recorded per `RESEARCH-RIGOR-STANDARD.md`. **This drains
  the construction queue: every dimension (D1 transform / D2 conditioning / D3 cross-section /
  D4 composite) has now been run or reasoned-to-moot on every free input.**

### Escalation — the free/cheap macro-input class is exhausted (2026-07-24)

All three real macro inputs (COT spec-positioning, crypto funding/OI/basis, value
ERP/real-yield/GSR) have now been run through **every** construction cell — D1 transform,
D2 conditioning, D3 cross-section — plus the mechanistic sysdyn route, and produced **zero
survivors** across 15 graded constructions. D4 composite is moot (no gross-edge sleeve to
blend). This is a *conclusive* negative on the current input class, not a stall.

**The honest next step is a different INPUT class, not more constructions on these series.**
Two concrete candidate directions, in the order I'd pursue them:

1. **Higher-frequency microstructure** — order-flow / order-book imbalance, trade-sign
   autocorrelation, realized-vol term-structure on the instruments the bot already trades
   (crypto is keyless via Bybit; futures via the IB feed). This is the *natural* next class:
   the daily-bar macro inputs are informationally thin (one obs/day, slow-moving), whereas
   microstructure carries the short-horizon predictive content the 1d-funding near-miss
   (entry 3) hinted at but couldn't reach at daily resolution. **Tier-1 feasibility probe
   first** (can we accrue a clean intraday feature panel off the existing feeds?), then the
   same S0→S3 funnel.
2. **A paid / alternative dataset** — on-chain flows, options-implied skew, positioning
   from a vendor. Higher signal potential but a real cost + procurement decision → this is
   an **operator-gated** call, surfaced not assumed.

**Surfaced for the operator (the one genuine decision point):** the free-macro-input program
has reached its honest floor. I recommend pivoting the R&D funnel to **(1) higher-frequency
microstructure off the existing feeds** as the next Tier-1 workstream — no new cost, natural
information-content step-up. Direction (2) (paid data) is available if you want to spend, but
I would exhaust the free microstructure class first. **This pivot is now underway — see the
M30 section below; the S0 feasibility probe has run and PASSED (the first positive feasibility
signal in the program).**

## M30 — microstructure input class (the pivot, 2026-07-24)

The macro table above (12 rows / 15 constructions) is a **closed chapter** — all three free
daily-bar macro inputs exhausted. M30 opens the next input class: **higher-frequency intraday
OHLCV structure** off the keyless Bybit kline feed (BTC/ETH/SOL, 1h bars). Same honest
S0→S3 funnel. **Honest scoping (recorded up front):** true tape/book order-flow microstructure
has NO free historical feed (Bybit `recent-trade` is a short rolling window, orderbook is
snapshot-only), so the tractable free route is *intrabar OHLCV shape* — a modest step-up over
daily bars, not full order-flow.

| Stage | Result | Detail |
|---|---|---|
| **S0 feasibility** (data + first-look) | **PASS** — first positive feasibility signal in the program | 1000 1h bars/symbol (41.6d), all 5 PIT features (realized_vol, rv_term_structure, ret_autocorr_lag1, range_position, volume_zscore) non-degenerate on all 3 symbols. Interesting first-look ICs: **SOL `realized_vol`→fwd-return, IC 0.070→0.089→0.128→0.177 monotone across 1/2/4/8 bars** (t 2.21→5.66); ETH `range_position@8bar` IC −0.069 (t −2.16); BTC nothing. Run on the trainer VM (#7541, Bybit reachable). |
| **S2 honest non-overlapping IC** (directional vs magnitude split) | **`no_edge` / `magnitude_only_no_direction`** — NO robust directional edge across 60 cells; the S0 headline was the predicted directional-regime artifact, **confirmed** | Non-overlapping (stride=H) Spearman IC, split into **ic_dir** (drift-demeaned signed fwd-return — tradeable direction) vs **ic_mag** (vs \|fwd-return\| — vol-forecasting, not direction). **SOL `realized_vol`** — the S0 star (first-look IC 0.070→0.177): ic_dir **insignificant at every horizon** (t 1.11/0.74/0.45/0.40) while ic_mag is strongly significant (t 6.83/3.54/3.54/2.76) ⇒ **directional-regime artifact confirmed** — the vol signal forecasts SIZE, not DIRECTION (exactly the entry-11 trap). BTC `realized_vol` identical; `volume_zscore`/`rv_term_structure` magnitude-only on both. The ONLY `directional_edge` across all 60 cells is **ETH `range_position` @H=1bar** (ic_dir −0.0676, t −2.14) — a **single marginal cell**; at t=2/5% across ~60 cells ≈ 3 false positives are expected, so this lone hit is almost certainly **multiple-comparisons noise, NOT promoted to S3**. Run on the trainer VM (#7544). |

**Caveat RESOLVED by S2 (#7544) — the S0 headline was the artifact, as predicted.** The S0
first-look IC was **in-sample + OVERLAPPING** (t inflated ~√8 → real t ≈ 2, not 5.7), on ONE
symbol over ONE 41-day window, and `realized_vol` is a **magnitude** correlated with a **signed**
forward return — the textbook **directional-regime artifact** (SOL trended up; vol clusters;
"high-vol bar" and "subsequently up" both proxy the trending-up regime). The honest
non-overlapping S2 scan (S2 row above) **confirmed exactly this**: SOL/BTC `realized_vol` carry a
strongly-significant **magnitude** IC (ic_mag t up to 6.8) but a completely **insignificant
directional** IC (ic_dir t < 1.2 at every horizon) — the vol signal forecasts SIZE, not tradeable
DIRECTION. This is the same trap that fooled the crypto-funding S3 (entry 11: overlapping t=3.2 →
non-overlapping t=1.16), caught this time *before* believing it. **1h-OHLCV shape carries no
robust directional edge** — the lone `directional_edge` (ETH `range_position` @1bar, t −2.14) is a
single marginal cell out of 60, consistent with multiple-comparisons noise, and is not promoted.

**Venue note (infra, for future sessions):** Bybit is **geo-blocked from GitHub US runners**
for the kline endpoint (both `api.bybit.com` and `api.bytick.com` returned empty sub-second on
a US runner, #7538 — refuting the "bytick is US-reachable" comment in `crypto_signals_data.py`,
now corrected). The **trainer-VM relay is the reliable Bybit path** (as the crypto sleeve
already used). The dead `m30-micro-probe` GitHub-runner workflow was removed in this same PR.

**M30 next step (honest).** The five 1h-OHLCV-shape features are magnitude-dominated at every
horizon — the *one* directional near-miss is likely noise.

**M30-2 — finer-interval (15m + 5m) S2 scan — DONE, `no_robust_edge` (#7546).** Correction to
own: the 1000-bar Bybit call caps N regardless of interval, so a finer interval gives the *same*
non-overlapping N over a *shorter* span — its value is finer intrabar *resolution*, not more N. The
result is unchanged in substance: **still no robust directional edge.** Both intervals are dominated
by `magnitude_only`/`no_edge` (realized_vol/volume_zscore carry a strong magnitude IC, ~0 direction —
the same vol-clustering artifact). The scattered `directional_edge` cells (BTC range_position @15m-H8
t=2.23; SOL range_position @5m-H1 t=−2.11; SOL/ETH volume_zscore isolated cells) sit right at the
multiple-comparisons false-positive rate (~6 cells cross \|t\|>2 of ~150 tested vs ~7–8 expected at
5%). The one mild standout worth an honest note: **ETH `volume_zscore` @5m** — two *adjacent*
horizons significant same-sign (H2 t=2.52, H4 t=3.08), less noise-like than an isolated cell, but
one feature / one symbol / in-sample. **Not promoted to S3.** **The free-microstructure-OHLCV class
is now exhausted at S2, alongside the macro class.**

**Pivot (operator-directed 2026-07-24): the implied-vol input class — see the M31 section.** Rather
than the paid dataset, the operator surfaced a Schwab account (free option-chain/IV data) and the
free CBOE/FRED vol family is gradeable *now* — a genuinely forward-looking class. M31 Track A is the
next build.

## M31 — implied-volatility / options-derived input class (operator-directed, 2026-07-24)

Operator direction: **exhaust free data before paying.** A new free-ish source is available —
the operator has a **Schwab** brokerage account, whose Market Data API gives **option chains with
Greeks (implied vol)** + price history at **no extra entitlement for individual developers**. This
opens the **implied-volatility / options-derived** input class — a genuinely *forward-looking*
class (positioning/fear priced into options), categorically different from the exhausted
backward-looking macro/OHLCV series, and it **overlaps a live instrument**: SPY/QQQ/index option
skew → S&P index-level signal → the bot's **MES** leg.

**Honest scoping constraint (recorded up front):** Schwab's option-chain endpoint (like yfinance)
returns the **current** chain — *point-in-time, no history*. So an S0→S3 grade of per-underlying
skew can't run on day one; it needs either a multi-week **soak** accruing daily snapshots, or a
historical IV dataset (the paid gap). Two-track plan:

| Track | Source | Status | Note |
|---|---|---|---|
| **A — aggregate implied-vol (free, historical, immediate)** | CBOE/FRED vol family via the existing keyless `fred_adapter` (`VIXCLS` + a `VXVCLS` 3-month term ratio, `OVX` oil; `SP500`/`DCOILWTICO` targets) | **BUILT** (`scripts/macro/implied_vol_probe.py` + 15 tests, ruff+pytest green) — grade runs on a GitHub US runner via `m31-implied-vol-grade.yml` (label `m31-implied-vol-grade-now`); **dispatch after this PR merges** (the label-trigger workflow must be on `main` first) | Features: `level_pct` (contrarian: high implied vol → forward bounce), `term_ratio` (VIX3M/VIX contango/backwardation), `vrp` (implied − realized). Honest **non-overlapping** directional Spearman IC vs forward SP500/oil returns at 5/10/21/42d. |
| **B — per-underlying live skew (Schwab, enrichment)** | Schwab Market Data API option chains + Greeks | **BLOCKED on the one operator hand-off** (app registration + creds); scaffold builds credential-free in parallel | Finer skew/term-structure the aggregate indices can't give (25Δ risk-reversal, single-name skew), accrued as a **forward soak**. |

**The one operator hand-off (Track B, credentials — everything else is mine):**
1. **developer.schwab.com → Dashboard → Apps → Create App**, request the **Trader API** product,
   HTTPS callback URL. Approval ~1–3 business days.
2. Provide the **app key + secret** (I pre-create the Actions secret slots); do the one-time
   **browser OAuth** to mint the first refresh token.
3. **Operational reality (flagged, not hidden):** the Schwab **refresh token expires every 7 days**
   → a weekly browser re-auth. Fine for periodic research pulls; a real recurring maintenance touch
   if Track B is ever productionized into a live feed. Schwab is **US equities/ETF/index/options
   only** — no crypto (crypto stays on Bybit).

Track A is the immediate, free, no-creds workstream and starts next; Track B proceeds in parallel
once the app is approved.

### M31 Track A — S2 result (#7549): the program's FIRST coherent directional edge

Honest non-overlapping directional IC of the 5 features vs forward SP500/oil at 5/10/21/42d:

| Feature (→target) | Verdict | Rows (ic_dir, ic_t) |
|---|---|---|
| `vix_level` (VIX→SP500) | `no_edge` | weak positive tilt, all t < 1.6 |
| `vix_vrp` (VIX−realized→SP500) | `no_edge` | all \|t\| < 1.5 |
| **`vix_term` (VIX3M/VIX→SP500)** | **`directional_edge`** | **5d −0.123 (t −2.76) · 10d −0.144 (t −2.30) · 21d −0.157 (t −1.72) · 42d −0.273 (t −2.14)** |
| `ovx_level` (OVX→WTI) | `no_edge` | all \|t\| < 0.8 |
| `ovx_vrp` (OVX−realized→WTI) | `no_edge` | all \|t\| < 1.2 |

**Why `vix_term` is a REAL S2 pass, not an M30-style scatter artifact:** it is **3 of 4 horizons
significant, ALL same sign (negative), with IC monotonically strengthening with horizon**
(−0.12 → −0.14 → −0.16 → −0.27). Cross-horizon coherence like that is not what multiple-comparisons
noise produces (M30's `directional_edge` cells were isolated, opposite-signed, at the FP rate). This
is the **first construction in the entire program to clear the honest non-overlapping S2 gate with a
coherent signature.**

**Economic read (sensible, and that's a caveat too):** negative IC on VIX3M/VIX means
**backwardation** (near-term fear spike, ratio<1) → forward SP500 **up**, and contango/complacency →
flat/down — the classic "buy the vol spike / vol-risk-premium" regularity. **It is a well-documented,
crowded signal.** So the honest posture is *skeptical-positive*: (a) this is **S2 (honest IC), not
S3** — no cost-aware conviction spread or OOS split yet; (b) **in-sample, one window**; (c) the IC
magnitude is modest (0.12–0.27); (d) being a known signal, the real question is whether any edge
**survives costs and is not already arbitraged** in a tradeable implementation. **Not a strategy —
a lead worth advancing to S3.** This is the first `advance_to_s3` disposition in the program.

**Next build (queued): M31 Track A-S3** — extend the probe with a **held-out OOS split** + a simple
**cost-aware long/short-by-term-ratio conviction PnL** (mirroring the `thesis_backtest_run`/conviction-
spread S3 the macro sleeves used), to test whether `vix_term` pays net of fees out-of-sample or is a
known-but-unmonetizable regularity. Also widen the vol-term family (VXN/VXD → QQQ/DJIA) to check
generalization. Track B (Schwab per-underlying skew) still parked on the operator's app registration.

### M31 Track A — S3 result (#7552): the FIRST signal to survive the OOS/cost kill-test — marginally

The cost-aware OOS conviction test on `vix_term` (VIX3M/VIX → SP500), split 60/40 (orientation
fit IS-only, 10 bps round-trip):

| H | is_ic | oos_ic (t) | net spread | pays_oos |
|---|---|---|---|---|
| 5d | −0.127 | −0.129 (−1.84) | +0.0023 | ✗ (t<2) |
| 10d | −0.109 | −0.194 (−1.97) | +0.0003 | ✗ (t<2) |
| 21d | −0.132 | −0.161 (−1.11) | +0.0150 | ✗ (t<2) |
| **42d** | **−0.172** | **−0.420 (−2.17)** | **+0.0567** | **✓** |

**What's genuinely encouraging (and NEW for the program):** the OOS IC **held its (negative) sign
at ALL 4 horizons** and the net conviction spread is **positive at every horizon** — the signal did
**not** flip out-of-sample. That is exactly where every prior "near-miss" died (entry 11: crypto
funding's OOS IC flipped positive→negative). So `vix_term` is the **first construction in the entire
program to survive the S3 kill-test.** Formal verdict: **`pays_oos_net`**.

**Why it's recorded MARGINAL, not "we found alpha" (the honest reservations):**
1. **The formal pass rests on ONE cell** — H=42d, whose OOS half is only ~23 non-overlapping
   anchors (q=0.34 tails ⇒ ~8-vs-8 in the long/short bins). t=−2.17 on n≈23 has a wide CI; the
   short-horizon cells (t=−1.84, −1.97) are just *under* significance and their net spreads are
   economically tiny (+0.02–0.03% over the hold).
2. **The edge concentrates at the long (42d) horizon** and in the most-recent OOS window — it could
   be **regime-specific** (a vol-spike + rebound episode in the held-out span is exactly what
   backwardation→bounce would capture). A single 60/40 split can't distinguish "robust" from
   "one good regime."
3. **It is a known, crowded signal** (VIX term-structure / VRP harvesting — VXX-short, SVXY, etc.).
   In-sample+OOS persistence is expected; the real question is a *tradeable* implementation that
   isn't already arbitraged.

**Disposition: a validated LEAD, not a deployable edge.** Honest next step is **robustness, not
productionization**: a **multi-fold walk-forward** (not one split) to confirm the OOS IC holds
sign/significance across *several* held-out windows, and a wider **vol-term family** cross-check
(the same VIX3M/VIX construction is the only one FRED gives cleanly for the term ratio; VXN/VXD lack
a 3-month FRED sibling, so cross-index generalization needs a different data path — likely Schwab/CBOE,
which is Track B). Only after walk-forward robustness would an S4 productionization (a real
`macro_thesis`-style snapshot signal on MES) even be proposed — and that would be **Tier-3**,
operator-gated. **This is the program's first genuine forward progress past S3 in 16 constructions.**

### M31 Track A — S4-prep result (#7556): `vix_term` passes the multi-fold walk-forward — ROBUST

The honest robustness follow-up (#7554): an **expanding-window walk-forward** (K=4 folds, orientation
fit on the past only, no lookahead) grading whether the negative OOS IC holds its **sign across
several held-out windows** — the test that separates a durable edge from the "one good split" doubt
the S3 pass rested on. Per horizon for `vix_term` (VIX3M/VIX → SP500):

| H | folds holding sign | pooled OOS IC (t) | verdict |
|---|---|---|---|
| 5d | **4/4** | **−0.114 (−2.54)** | **robust** |
| 10d | **4/4** | **−0.134 (−2.08)** | **robust** |
| 21d | 4/4 | −0.145 (−1.51) | regime_dependent (sign held, pooled t<2) |
| **42d** | **4/4** | **−0.322 (−2.33)** | **robust** |

**`vix_term` is ROBUST — the program's first construction to clear BOTH the S3 OOS/cost kill-test
AND multi-fold walk-forward robustness.** Every single one of the **16 folds across all 4 horizons
came back negative** (the IS-fit sign), and the pooled OOS IC is significant (|t|≥2) at 3 of the 4
horizons. This **removes reservation #2** from the S3 read (the "regime-specific / one lucky 60/40
split" doubt): the negative directional IC is stable across *four independent expanding windows*, not
an artifact of where one cut landed. It is also the **only** robust probe — `vix_level`, `vix_vrp`,
`ovx_level`, `ovx_vrp` are all `regime_dependent`/`not_robust`, exactly matching their S2 `no_edge`.

**The two honest reservations that SURVIVE (why this is still a LEAD, not a live edge):**
1. **Robust IC ≠ tradeable net-of-cost edge.** The walk-forward validates the *directional signal*'s
   sign-stability; it does not re-test the fee. Per S3, the net conviction spread only cleared the
   10 bps round-trip decisively at **H=42d** (+0.0567) — at 5d/10d the net was +0.002/+0.000 (cost
   eats nearly all of a real IC). So the *robust* horizons (5d/10d) are barely tradeable, and the
   *tradeable* horizon (42d) is the smallest-N (still robust here, but on ~12-anchor folds).
2. **It remains a known, crowded signal** (VIX term-structure / VRP harvesting). Sign-persistence is
   *expected* for a real risk premium; the open question is a *tradeable* implementation not already
   arbitraged — which is an execution/costs question, not an IC question.

**Disposition — upgraded from "MARGINAL" to a VALIDATED LEAD.** `vix_term` has now passed every gate
the free-FRED path can apply (S2 directional IC → S3 OOS/cost → S4-prep walk-forward). The honest next
moves are **enrichment + productionization scoping**, not more FRED grading (the daily VIX3M/VIX
construction is fully wrung out): (a) a **wider vol-term family** cross-check (VXN/VXD → NDX/DJIA)
needs a non-FRED source — **Track B / Schwab** (blocked on the operator's app registration); and
(b) an **S4 productionization** — a real `macro_thesis`-style term-structure snapshot signal on the
MES (S&P) leg — is now a defensible proposal, but is **Tier-3, operator-gated** (order-path-affecting)
and should carry the net-of-cost caveat (edge concentrates at the 42d hold). **This is the program's
first construction to reach the S4 doorstep.**

### M31 Track A-XA result (#7559): the robust vix_term signal is EQUITY-SPECIFIC, not a broad premium

The cross-asset generalization test (#7558): point the **same** robust VIX3M/VIX term-ratio feature at
**oil** and **gold** forward returns, to answer whether the edge is a broad vol-risk-premium (predicts
multiple risk assets) or is SP500-specific. Result:

| target | S2 | S3 | walk-forward | read |
|---|---|---|---|---|
| **SP500** (control) | directional_edge | **pays_oos_net** | **robust** | the validated signal |
| **WTI oil** (`DCOILWTICO`) | no_edge (all \|t\|<1) | s2_only_no_s3 | regime_dependent | **no generalization** |
| **gold** (`GOLDAMGBD228NLBM`) | no_data | no_data | no_data | **untestable on free FRED** (series empty) |

**The signal does NOT generalize to oil** — VIX term structure carries no significant, cost-surviving,
or sign-stable predictive content for WTI forward returns at any horizon (S2 t = 0.70 / −0.29 / −0.07
/ −0.54; the best walk-forward verdict is `regime_dependent`, never robust). **Gold is a data gap, not
a result:** FRED's LBMA daily gold series (`GOLDAMGBD228NLBM`) returns an empty body (the LBMA
redistribution license was revoked, so the series is frozen/unserved) — the probe honest-nulls to
`no_data` by design; a real gold cross-check needs a **non-FRED** price feed (Track B / Schwab, or a
yfinance `GC=F` path).

**Conclusion — `vix_term` is equity-specific on the free-FRED evidence.** The one cross-asset target
with data (oil) shows no edge, so the robust signal is best read as a **specific equity-vol-term
effect on the S&P**, NOT a demonstrated broad risk-on/off vol premium. Two consequences: (a) it
**tightens the S4 scope** to the MES/SP500 leg only — there is no evidence for a general vol-term
sleeve across commodities; and (b) it slightly **eases the "crowded broad premium" concern** (a
narrower, instrument-specific effect is less likely to be the exact thing the big VRP funds arb). The
free-FRED implied-vol exploration of `vix_term` is now **complete**: robust on SP500, non-generalizing
to oil, gold-blocked on data. Remaining forward motion is the two non-FRED tracks — **Track B / Schwab**
(the VXN/VXD → NDX/DJIA family cross-check + a real gold feed) and the **Tier-3 S4 productionization**
proposal (SP500-leg-scoped, net-of-cost-caveated).

## M32 — credit / rates risk-premium input class (the skipped free-FRED inputs, 2026-07-25)

**Why this sleeve exists (honest correction).** The program had declared "free macro
exhausted" after testing exactly THREE inputs — COT, crypto funding, value (ERP/real-yield/GSR).
But the keyless FRED library carries several *other* documented cross-asset risk-premium
predictors that were never run: **credit spreads** (HY/IG OAS — credit famously leads equity),
the **yield-curve slope** (10Y-2Y / 10Y-3M), and a **financial-conditions index** (Chicago Fed
NFCI). Each → SP500 forward returns, through the SAME honest funnel Track A used
(non-overlapping IC → OOS/cost → multi-fold walk-forward). Runs off-VM on a US GitHub runner
(keyless fredgraph, no relay). Grade: #7565 (`scripts/macro/credit_curve_probe.py`).

### M32 result (#7565): HY-OAS percentile is a lead, not a cost-surviving edge; curve/NFCI/IG = no edge

| probe (→SP500) | S2 (honest non-overlap IC) | S3 (OOS/cost) | walk-forward | read |
|---|---|---|---|---|
| **hy_oas_pct** (HY OAS 1y %ile) | **directional_edge** (H10 ic=0.25 t=2.20; H42 ic=0.53 t=2.35) | s2_only_no_s3 (`pays_oos=False` every H) | **robust** @ H10 (sign 0.75, pooled-oos ic=0.26 t=2.09) | **lead, not edge** |
| hy_oas_mom (HY OAS Δ21d) | no_edge | s2_only_no_s3 | regime_dependent | null |
| ig_oas_pct (IG OAS 1y %ile) | no_edge (all \|t\|<1.2) | s2_only_no_s3 | regime_dependent | null |
| curve_10y2y (2s10s level) | no_edge | s2_only_no_s3 | regime_dependent/not_robust | null |
| curve_10y3m (3m10y level) | no_edge | s2_only_no_s3 | regime_dependent/not_robust | null |
| nfci (Chicago Fed FCI level) | no_edge | s2_only_no_s3 | regime_dependent/insufficient | null |

**Read.** HY-OAS stress-percentile → SP500 is the one construction with a coherent signal:
S2-significant at 10d and 42d and **robust** on the multi-fold walk-forward at 10d — but it
**fails the S3 cost gate at every horizon** (`pays_oos=False` throughout; no net-of-fee OOS
conviction spread). That is the **exact shape of `vix_term`**: a *validated directional lead
that does not survive costs* — a real relationship (credit does lead equity), not a deployable
edge. The canonical rates predictors (curve slope, NFCI) and IG OAS show **no honest edge at
all** — insignificant S2 across every horizon.

**Meta-finding (the free-macro frontier is now genuinely closed).** With credit/rates added,
the free-keyless-FRED macro class has been swept across its documented risk-premium inputs —
COT, crypto, value, implied-vol (Track A), and now credit/rates. The class yields exactly
**two validated leads (`vix_term`, `hy_oas_pct`), zero cost-surviving edges.** The earlier
"free macro exhausted" claim was an overclaim *by input count* but **correct by conclusion**:
the boundary is now established on the full input set, not three of it. Remaining forward motion
in the macro program is NOT more free-FRED framing — it is (a) the **point-in-time data producer**
(unblocks M28-P4 valuation + the soaks that turn a *lead* into a tradable, cost-aware, regime-
conditioned signal) and (b) the **non-FRED tracks** (Track B / Schwab options-skew, credential-gated).

### M31 Track A-XI result (#7574): vix_term GENERALIZES across US equities — a 2nd cost-surviving leg (NQ)

The cross-**index** test (a follow-up to the cross-asset XA result above): point the SAME robust
VIX3M/VIX term-ratio feature at **NASDAQ-100** and **DJIA** forward returns (both keyless FRED). XA
showed the signal is not cross-*asset* (oil no-edge, gold data-blocked) — "equity-specific." XI asks the
sharper question: equity-specific to the *S&P alone*, or to *US large-cap equity vol-term broadly*?

| target | S2 (non-overlap IC) | S3 (cost-aware OOS) | walk-forward | read |
|---|---|---|---|---|
| **SP500** (control) | directional_edge (H5 t=−2.76, H10 t=−2.30, H42 t=−2.14) | **pays_oos_net** (H42) | **robust** (H5, H10, H42) | the validated lead |
| **NASDAQ-100** (`vix_term_ndx`) | directional_edge (H5 t=−2.03) | **pays_oos_net (H5 AND H21)** | **robust** (H5) | **full trifecta — a 2nd cost-surviving leg** |
| **DJIA** (`vix_term_djia`) | directional_edge (H5 t=−2.35, H10 t=−2.15) | s2_only_no_s3 (no horizon pays net) | **robust** (H5) | lead, not cost-surviving |

**All three carry the SAME negative sign** (elevated VIX3M/VIX term ratio → lower forward equity
returns), and **all three are walk-forward robust at H5**. So the effect is **NOT SP500-specific — it is
a broad US-large-cap equity vol-term effect.** This both *sharpens* and *widens* the read from XA:

- **Widens the tradeable S4 scope from one leg to two.** NASDAQ-100 clears the **full** cost-aware gate
  (`pays_oos_net` at H5 and H21, walk-forward robust at H5) — the same trifecta `vix_term`/SP500 passes.
  So the S4 productionization leg is no longer MES-only: it is **ES (MES) + NQ (MNQ/NASDAQ-100)**, a
  materially larger deployable surface than the XA-scoped "S&P leg only."
- **DJIA is a confirmed lead, not a deployable edge** — robust IC + significant S2, but no horizon
  survives the round-trip-cost OOS gate (same shape as `hy_oas_pct`/M32). **YM stays OUT** of the S4 leg.
- **The "crowded broad-premium" concern is NOT re-raised** — this is still a *within-equity-vol-term*
  effect (SP500/NDX/DJIA co-move), not a demonstrated cross-asset risk premium; XA's oil/gold nulls stand.

**Net for the program's one live lead:** `vix_term` is upgraded from *"one equity-specific robust lead"*
to *"a broad US-equity-vol-term effect with TWO cost-surviving tradeable legs (ES + NQ)."* This is the
strongest positive result in the ledger. Forward motion is unchanged in kind (Tier-3 S4 productionization,
now scoped ES+NQ; the point-in-time producer + soak to condition it) — but the deployable prize is larger.

### M31 Track A-S5 result (#7577): the vix_term edge is REAL but too THIN to deploy standalone — the sobering size read

The signal grade said *is there a robust, cost-surviving lead* → yes (A + XI). It does NOT say *how big
the tradeable edge is*. Track A-S5 expresses `vix_term` as an actual long/short/flat timing position on
each index future (a-priori direction, non-overlapping, OOS-split, net of a realistic ~1.5 bp futures
round-trip) and reports the numbers a deployment decision turns on — Sharpe, CAGR, max-drawdown:

| target | FULL Sharpe (H5/10/21/42) | FULL CAGR | FULL maxDD | OOS Sharpe (H5/10/21/42) | read |
|---|---|---|---|---|---|
| **SP500** | 0.07 / −0.02 / 0.18 / 0.05 | ~0 (−0.0002…+0.015) | −26% … −39% | 0.26 / 0.13 / 0.47 / 0.67 | barely-positive full; OOS only at small-N |
| **NASDAQ-100** | −0.06 / −0.30 / 0.04 / 0.15 | **negative on 3 of 4** | **−45% … −82%** | 0.02 / −0.33 / 0.18 / 0.37 | worst — negative + brutal DD |
| **DJIA** | 0.08 / 0.10 / 0.05 / −0.02 | ~0 | −26% … −41% | 0.30 / 0.42 / 0.29 / 0.77 | barely-positive full; OOS small-N |

**The honest verdict: `edge_real_but_thin` — NOT deployable as a standalone directional strategy.**
This does NOT contradict the robust-IC result (A/XI); it completes it. A small, real, sign-stable IC
produces a timing strategy whose **full-sample Sharpe is ~0.0–0.18** (far below the ≥0.5–1.0 a standalone
needs) with **−26% to −82% drawdowns**. The apparent OOS strength (SP500 H42 Sharpe 0.67, DJIA H42 0.77)
is **concentrated at the small-N long horizons** (n=24–48 non-overlapping periods) — exactly the fragile,
regime-dependent regime the earlier notes flagged; it is not a robust standalone edge, it is a thin tilt
that happened to work in the recent 40%. NASDAQ-100 — the "2nd cost-surviving leg" from the IC read — is
the WORST on a size basis (negative full CAGR, −82% maxDD), which sharply qualifies the XI "widens to NQ"
optimism: it widens the *statistical* lead, not a *deployable* one.

**Consequence for S4 (recommendation update).** `vix_term` should **not** be productionized as a
standalone ES/NQ timing signal — the tradeable edge is too thin and the drawdowns too large. Its only
defensible use is what was flagged from the start: a **low-weight conviction/exposure tilt** inside a
larger book (observe→advise→size), never a primary entry — and even that is optional given the size read.
This is the classic "significant IC ≠ tradeable Sharpe" outcome, recorded as an honest negative-leaning
result. **The vix_term investigation is now complete end-to-end** (S2 edge → S3 cost → walk-forward robust
→ cross-asset null → cross-index generalize → SIZE too thin): a *validated lead, not a deployable edge* —
the same landing as `hy_oas_pct`, reached with more rigor. The free-macro program's honest final tally:
**zero deployable standalone edges** across every free input class.

## The compounding read so far (entries 1–12)

Fifteen graded constructions across twelve ledger rows, **zero survivors** — and that is a
*result*, not a stall. The pattern narrows where the edge can still be:

- **COT is exhausted across construction cells.** Level (entry 2), the D1 transform
  sweep (entry 5), AND the D3 cross-market basket (entry 7) all fail the honest gate.
  Three orthogonal framings of spec-positioning, three nulls ⇒ the *input* carries no
  cost-surviving edge; only a different underlying COT signal could, not more framing.
- **Crypto funding is now also genuinely exhausted** — and it took a two-step correction
  to see clearly. The level LOOKED like a positive-net near-miss (entry 10: +0.293 net
  Sharpe 0.564), but the dense-horizon scan (entry 11, #7526) showed that was a **short-
  bias benchmark artifact**: the honest non-overlapping IC is insignificant at 1d and
  strongly **negative** at 7–30d (the contrarian fade is *wrong* at monetizable horizons).
  No honest monetizable horizon exists across level / impulse / OI-conditioned cells.
- **Level / D1 / D2 / D3 of a single raw series has produced no survivor on any input**
  (COT, crypto, value-level). Reframing one series does not manufacture an edge that the
  input doesn't carry.
- **The mechanistic route (entries 4, 8) is gated on calibration first** — a model
  that can't forecast the level can't misprice it.

**Methodology learning worth keeping** (entry 11): the S3 conviction-vs-all-long
`pays_oos` can be a **directional short-bias artifact** in a trending market — a book that
is merely *less long* than all-long "beats" it without any predictive skill. The honest
per-horizon IC is the real arbiter; a **dense-horizon IC scan** is what exposes a fake S3
"win." This is now a standing check before calling any `pnl_but_no_signal` a near-miss.

- **Value is now also exhausted** — the distinct-driver D3 cross-section (entry 12, #7534)
  fails on real ETF history, the same S2-insignificant / S3-artifact shortfall.

**The meta-finding (now conclusive):** ALL THREE real macro inputs — COT, crypto, value —
are exhausted across every tried construction cell (D1/D2/D3), and the mechanistic sysdyn
route is gated on a calibration it can't clear. **D4 composite is moot** — it needs ≥2
gross-edge sleeves to blend and there are none. Fifteen graded constructions, zero survivors.
The honest conclusion the program set out to test is reached: **these free/cheap daily-bar
macro inputs carry no cost-surviving edge in any construction we can frame.** The search now
moves to a *different input class* — see the **Escalation** section under *Next entries*: the
recommended Tier-1 pivot is **higher-frequency microstructure off the existing feeds** (no
new cost, a genuine information-content step-up), with a paid/alternative dataset as the
operator-gated alternative. This is the deliverable: not a strategy, but a *rigorously
established boundary* of where free-macro signal isn't, and a reasoned next direction.
