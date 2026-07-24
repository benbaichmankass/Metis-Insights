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
I would exhaust the free microstructure class first. Absent a steer I'll begin the Tier-1
microstructure feasibility probe on the next fire (build the intraday feature-panel accrual
+ an S0 feasibility grade), keeping the same honest-negative discipline.

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
