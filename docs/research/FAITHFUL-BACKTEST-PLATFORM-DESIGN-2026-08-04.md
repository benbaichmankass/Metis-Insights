# Faithful Backtest & Evaluation Platform — design of record (2026-08-04)

> **Operator directive (2026-08-04, binding):** *"if we wait for data accrual we'll
> never make confident decisions, and there's no way in the entire universe that
> this is how real trading firms manage their pipeline. We need real infra for
> faithfully backtesting strategies, MLs, sleeves … informed decisions with a much
> quicker turnaround. Not another partial fix."*
>
> This is the plan. It replaces the implicit "you may only evaluate on real live
> trades" doctrine with an **earned-trust simulator**: a backtest engine faithful
> enough that its out-of-sample output is trusted evidence — and whose trust is
> **measured and continuously re-calibrated** against the live data we do have.

## 1. The diagnosis — why we are snail-paced

We conflated **"trustworthy evaluation"** with **"real live trades."** That was an
over-correction to a real scar (the research→results gap: backtests green-lit
strategies the live layer then lost money on; the fabricated-PnL episode). The
correction — "only trust real live trades" — capped every decision at the speed of
reality: the P0 label-augmentation verdict (2026-08-04) is the perfect example — the
augmented head learns a real edge but the **eval book is 324 real trades** and we
"wait for accrual." That is not a pipeline.

**The concrete fidelity gaps (measured this session):**
1. **Code drift.** The per-strategy harnesses (`scripts/backtest_{trend,squeeze,…}.py`)
   **re-implement** strategy logic separately from `src/units/strategies/`, so they
   drift from live and **omit real levers** — `trend_donchian`'s backtest is tagged
   `approximate`, omitting `exit_head_action`, `exit_head_model`,
   `exit_head_threshold`, `trail_decay_arm_r`, `trail_decay_tight_mult` (i.e. the
   *actual live exit path*). The system backtester (`scripts/backtest_system.py`)
   already routes through the **real `aggregate_intents`** — proof the unified
   pattern works — but re-implements account bookkeeping and signal generation.
2. **No execution realism.** The cost model is scattered across four modules
   (`runtime/trade_costs.py`, `broker_cost_attribution.py`, `broker_truth.py`,
   `prop/montecarlo.py`); slippage, funding, partial fills, and latency are not
   modeled uniformly. This is the direct cause of "green in backtest, red live."
3. **Trust is asserted, not measured.** Fidelity is a **self-declared label**
   (`faithful`/`approximate`) — there is **no backtest-vs-live agreement number**
   anywhere. We have never actually checked whether our backtests are right.

## 2. The principle — trust is EARNED and MEASURED, never assumed

A backtest earns the right to be trusted evidence through four properties, and the
fourth is the one that closes the research→results gap for good:

1. **Fidelity by construction** — the backtest runs the *same code* as live (signal
   builders, intent/coordinator layer, order monitor, exit heads, risk sizer); only
   the **data feed** (historical, point-in-time) and the **broker** (a realistic fill
   simulator) are swapped. No re-implementation → no drift.
2. **Execution realism** — one cost/fill component (fees, slippage, funding, partial
   fills, latency, roll drag) that **both** the backtest **and** the live sizer
   consult. A backtest edge is net-of-real-cost or it doesn't count.
3. **Honest OOS discipline** — purged + embargoed walk-forward, regime-stratified,
   so a backtest edge is an *out-of-sample* edge, not an overfit.
4. **Continuous calibration against live (the linchpin)** — the simulator's output
   is trusted **only to the extent it reproduces the live results on the legs where
   we have both.** We compute a **backtest↔live agreement score** per strategy×regime
   and **gate on it**. Live data stops being the sole evaluator and becomes the
   **calibrator that certifies the sim.** A validated sim then yields *unlimited fast
   OOS evidence*; live data validates the instrument, the instrument gives the speed.

This is exactly how real quant firms operate: they don't wait for live trades to
validate a strategy — they run a **high-fidelity simulator they have earned the
right to trust**, and they keep proving that trust against production.

**The payoff:** decision turnaround goes from *months* (wait for 300→1000 real
trades) to *hours* (run thousands of faithful, calibrated, cost-net, OOS backtest
trades). This unblocks strategies, MLs (the P0 eval-book wall dissolves — the head is
evaluated on faithful OOS backtest evidence whose trust is calibrated against the 324
real trades), sleeves, and the macro family — one platform, one trust gate.

## 3. The architecture

```
        ┌─────────────────── SHARED LIVE CODE (no re-implementation) ───────────────────┐
        │  signal builders (src/units/strategies) → intent/coordinator (aggregate_intents)│
        │  → risk sizer → order monitor + EXIT HEADS + trail/stale levers                 │
        └───────────────▲───────────────────────────────────────────▲───────────────────┘
                        │ swap ONLY these two seams                  │
              ┌─────────┴─────────┐                       ┌──────────┴──────────┐
   LIVE:      │ live market feed  │            BACKTEST:  │ historical PIT feed │
              │ real broker       │                       │ SIM BROKER (fills,  │
              └───────────────────┘                       │ slippage, funding,  │
                                                          │ latency) = the ONE  │
                                                          │ execution-realism    │
                                                          │ cost component       │
                                                          └──────────────────────┘
                                   │
              ┌────────────────────┴─────────────────────┐
              │  EVALUATION SERVICE (one entry point)     │
              │  evaluate(strategy|ml|sleeve, window)  →  │
              │   purged walk-forward OOS · cost-net ·    │
              │   regime-stratified · fidelity-scored ·   │
              │   CALIBRATED (backtest↔live agreement) →  │
              │   a TRUST-GRADED verdict + turnaround     │
              └───────────────────────────────────────────┘
```

Four components:
- **A · Unified engine** — extend `backtest_system.py` from "real intent layer" to
  "real *everything*": drive the actual signal builders + order monitor + exit heads;
  the only re-implemented piece becomes the sim broker (B).
- **B · Execution-realism component** — consolidate the four cost modules into one
  `SimBroker` / cost model both backtest and live consult (the ROADMAP_MACRO §4
  pillar). Models fees, slippage (spread + impact), funding, partial fills, latency,
  roll drag.
- **C · Fidelity + calibration** — per-run fidelity score (which live levers are
  modeled) **plus** the backtest↔live agreement metric (§2.4) with a gate threshold.
- **D · Evaluation service + trust gate** — one `evaluate(...)` that returns a
  trust-graded verdict, wired into the M7/M25 promotion gates so nothing promotes on
  un-calibrated backtest evidence, and nothing is *blocked* that has calibrated OOS
  evidence.

## 4. The plan — phased, Day-1 first, each phase standalone-valuable

Aggressive but honest: the full unified engine is a multi-session build. **Phase 0
ships today and immediately unblocks** by telling us *which existing backtest evidence
we can already trust* — it is the linchpin (it operationalizes §2.4).

| Phase | Deliverable | Unblocks | Effort |
|---|---|---|---|
| **P0 · TODAY — the calibration gate** | `scripts/research/backtest_fidelity_calibrate.py`: for a strategy×symbol, compare the **backtest trade distribution** (from the augment engine's `is_backtest=1` rows) against the **live trade distribution** (journal, measured-provenance only) — win-rate, R-distribution (KS), hold-time, per-regime — and emit a **backtest↔live agreement score + verdict** (`calibrated` / `drifts` / `insufficient-live`). Turns the qualitative `faithful/approximate` label into a **measured number** per strategy. First target: `trend_donchian` BTC (299 live + 204 backtest). | **Tells us TODAY how much to trust each backtest** → the ones that clear the agreement gate become trusted OOS evidence *now* (dissolves the P0 eval-book wall for those legs). | **built today** |
| **P1 · Close the top fidelity gap** | ✅ *Execution-realism component DONE + MEASURED (§ 5b, 2026-08-04):* one shared cost model (`src/runtime/execution_costs.py` — fees+slippage+funding) both the harness and live sizer consult; re-ran the calibrator → cost is real (funding-dominant) but is **not** the gap's driver (~10–12%), so the leg stays `drifts`. *Remaining P1.x:* a real stop-distance **live-R** (the KS axis is a sign-proxy artifact today) + a **wider trusted-live set**, then make the harnesses run the **actual exit path** (exit heads + trail-decay + stale levers) via `src/units/strategies`. | Backtests are net-of-real-cost by construction; the honest driver of the gap is now identified (small-sample/regime, not cost). | days |
| **P2 · The unified engine** | Extend `backtest_system.py` to drive the real signal builders + order monitor (not `generate_signal_stream`'s re-implementation); introduce `SimBroker` (B) as the one swapped seam. Fidelity becomes structural, not per-lever bookkeeping. | Any strategy/sleeve backtests on the live code path → drift → 0. | 1–2 sessions |
| **P3 · Evaluation service + trust gate** | One `evaluate(strategy\|ml\|sleeve, window)` → purged-WF OOS + cost-net + regime-stratified + calibrated verdict; wire it into the M7/M25 promotion gates (replace "live-holdout only" with "calibrated-OOS-or-live"). | Fast, uniform, trust-graded decisions for *everything* — the actual unblock. | 1–2 sessions |

**Guardrails that keep this honest (learned from the scars):**
- A backtest verdict is **never** trusted without a fidelity score **and** a
  calibration result — an un-calibrated strategy (no live overlap yet) is graded
  `insufficient-live` and its backtest is a *lead*, not a *result* (same discipline as
  the R-coverage / pnl-coverage pattern).
- The calibration set is **measured-provenance live trades only** (the provenance
  guard already enforces this — it's why the P0 eval excluded fabricated paper pnl).
- The `training-population-guard` family gets a sibling **`calibration-gate`**: a
  strategy/ML promoted on backtest evidence must carry a passing agreement score.

## 5. Immediate next actions (today)
1. **P0 calibrator built + run** on `trend_donchian` BTC (this session) → the first
   measured backtest↔live agreement number. This is the proof-of-concept of the whole
   trust model and the thing that unblocks *now*.
2. **Wire the nightly build** to feed the pooled decision heads from the augment db
   (operator-approved) — the augmentation stays operationally in-use, and its output
   becomes P0-calibratable.
3. Then P1 (close the exit-head omission) is the highest-leverage fidelity fix.

## 5a. FIRST CALIBRATION RUN (2026-08-04, trainer #8461) — the thesis, measured

The calibrator ran against real data on its first day. For the **first time ever**
we have a *measured* backtest↔live agreement number — and it confirms the diagnosis:
**our current backtests do NOT reproduce live, and now we can prove it with a number
instead of blanket-distrusting everything.**

| leg (BTC) | live n (measured-prov) | backtest n | live WR | backtest WR | KS(R) | live mean-R | backtest mean-R | verdict |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| `htf_pullback_trend_2h` | 30 | 238 | 0.233 | 0.357 | **0.395** | **−0.53** | **+0.04** | **drifts** |
| `trend_donchian` | 24 | 204 | 0.500 | 0.328 | 0.466 | 0.00 | +0.08 | insufficient-live |
| `squeeze_breakout_4h` | 3 | 52 | 0.000 | 0.404 | 0.789 | −1.00 | +0.49 | insufficient-live |

**What this proves, today:**
1. **The research→results gap is now MEASURED, not anecdotal.** `htf_pullback_trend_2h`
   is the first leg over the live-n floor: its backtest says **+0.04R (≈break-even)**
   while live is **−0.53R (losing)** — KS 0.395 > 0.30 → `drifts`. That single number
   is why "green in backtest, red live" kept happening. The calibrator catches it in
   seconds.
2. **We were right to distrust these backtests — but distrust is now a dial, not a
   wall.** The verdict tells us *which* legs to trust and *how far off* the rest are,
   so P1 (close the exit-head + cost omissions) has a **measurable target**: drive the
   KS down and watch legs flip `drifts → calibrated`. That is the fast feedback loop
   the pipeline was missing.
3. **The trusted live calibration set is scarce** (BTC trend = 24 measured-provenance
   trades, not the 299 raw — the provenance filter is strict, correctly). This is
   *why* the sim must be made faithful: we cannot afford to depend on the scarce live
   set for evaluation — we depend on it only to *calibrate* the sim (§2.4), then let
   the faithful sim carry the volume.

**Immediate P1 target (now PINPOINTED, 2026-08-04):** `htf_pullback_trend_2h` is
+0.57R too optimistic vs live. Crucially, the harness self-labels this leg
**`faithful`** (it models every declared *strategy* lever) — yet it drifts. So
**`faithful` ≠ `calibrated`**: the gap is **execution realism**, not omitted levers.
The harness (and even the live cost *estimate*) model **fees only**
(`net_r = r_multiple − fee_bps`); neither models **funding** (crypto perps pay
funding every ~8h; a 2h-hold strategy crosses several windows) nor **slippage /
real-fill** cost — which the *real* live PnL (exchange-fills / broker-truth) ate.
That is the research→results-gap mechanism, mechanically identified.
**P1 = build the execution-realism component (§3.B): add funding + slippage to the
one shared cost model, re-run the calibrator, and watch KS fall toward the gate.**
Caveat respected: live n=30 is small — P1 must also **stratify by regime / expand the
trusted-live set** to separate "cost-model gap" from "small-sample/regime bias"
before declaring the leg `calibrated`. Same-day measurable, repeated per leg.

> **⚠️ MEASURED 2026-08-04 → the hypothesis in this paragraph is REFUTED. See § 5b.**
> P1 built the cost component and re-ran the calibrator: funding+slippage is real
> (funding-dominant) but explains only **~10–12%** of the gap; the residual ~0.49R is
> small-sample/regime bias, and n=30 is too thin to stratify (per-direction n=12/18 <
> floor). The `drifts` verdict is driven by a **sign-proxy KS artifact**, not cost. The
> next lever is a real live-R + a wider trusted-live set, not a bigger cost model.

## 5b. P1 EXECUTION-REALISM MEASURED (2026-08-04, trainer #8463/#8464) — the § 5a hypothesis is REFUTED

P1 built the one shared execution-realism cost component (§ 3.B — `src/runtime/execution_costs.py`:
fees + slippage + perp funding, funding counting the 8h windows a hold crosses) and
wired it into the pullback harness (opt-in flags, default 0.0 = byte-identical). Then
we did the thing § 5a promised: **re-ran the calibrator with cost ON and measured
whether the KS falls toward the gate.** The instrument is
`scripts/research/backtest_fidelity_cost_ab.py` — one fetch, one cost-on harness run,
both arms derived from the single emit's per-component breakdown (fees don't feed back
into signal generation, so the trade set is identical → a clean cost attribution).

**Headline (`htf_pullback_trend_2h` BTCUSDT, 160 backtest trades over 2y, 30 live
measured-provenance):**

| arm | backtest WR | backtest mean-R | KS(R) vs live | verdict |
|---|--:|--:|--:|---|
| **fee-only** (the current harness) | 0.356 | **+0.022** | 0.4125 | drifts |
| **+ slippage 5bps + funding 1bps/8h** | 0.356 | **−0.037** | **0.425** | drifts |
| live (measured-prov, n=30) | 0.233 | **−0.533** | — | — |

Mean modelled cost/trade: fee **0.035R** · slippage **0.023R** · **funding 0.036R**
(the funding term is the largest — the pullback hold crosses **~9.7** 8h windows on
average, exactly the § 5a intuition that a multi-bar hold pays funding several times).

**What this MEASURES — and it refutes the § 5a guess:**

1. **The cost is real and now faithfully modelled**, and funding dominates it (0.036R
   > 0.035R fee > 0.023R slippage) — vindicating the *mechanism* § 5a identified.
2. **But cost does NOT close the gap.** Adding slippage+funding moves backtest mean-R
   by only **−0.059R** (+0.022 → −0.037). Live is **−0.533R**. The residual gap is
   **~0.49R** — *unchanged in character*. KS did not fall; it rose slightly
   (0.4125 → 0.425). Closing a 0.55R mean-R gap with cost alone would need ~10× the
   modelled cost (~140bps round-trip) — implausible. **So the "+0.57R gap is
   funding+slippage" hypothesis (§ 5a) is FALSE**: execution realism explains ~10–12%
   of it, not the bulk. Recording this honestly is the point — the calibrator caught
   our own over-claim with a number, exactly as it caught "green backtest, red live."
3. **The remaining gap is regime / small-sample bias, and n=30 is too thin to resolve
   it.** The direction stratification returns `insufficient-live` on BOTH sides (long
   n=12, short n=18 — below the 30 floor), so we *cannot* attribute the residual to a
   regime cell at this sample size. The live slice (WR 23%, mean −0.53R over 30 trades)
   is simply a small, losing sample vs the 160-trade near-break-even backtest.
4. **A calibrator-methodology limitation surfaced (P1.x follow-up).** The live R is a
   **win/loss sign proxy** (±1) while the backtest R is continuous, so **KS(R)
   structurally inflates and "calibrated" is nearly unreachable on the KS axis
   regardless of cost** — the CDF gap between a ±1 point-mass and a continuous
   distribution stays ~0.4 however you shift the continuous side. Note the WR axis
   tells a milder story: backtest 0.356 vs live 0.233 = **0.123 gap, INSIDE the 0.15
   tolerance** — the `drifts` verdict is driven entirely by the sign-proxy KS. **Next
   fidelity lever is a real stop-distance live-R** (so KS compares like with like),
   **and widening the trusted-live set** (more legs, more measured-provenance trades) —
   NOT a bigger cost model. This is the "separate cost-model gap from small-sample/
   regime bias before declaring calibrated" caveat, now discharged with evidence.

**The full trust map (fee-only baseline, every leg with both backtest AND live rows;
`--trust-map --stratify direction`, trainer #8464):**

| leg | live n (meas-prov) | bt n | live WR | bt WR | KS(R) | live mean-R | bt mean-R | verdict |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| `htf_pullback_trend_2h` BTC | 30 | 238 | 0.233 | 0.357 | **0.395** | −0.533 | +0.039 | **drifts** |
| `trend_donchian` BTC | 24 | 204 | 0.500 | 0.328 | 0.466 | 0.000 | +0.079 | insufficient-live |
| `squeeze_breakout_4h` BTC | 3 | 52 | 0.000 | 0.404 | 0.789 | −1.000 | +0.490 | insufficient-live |

Only **one** leg (`htf_pullback_trend_2h` BTC) clears the 30-trade live floor, and it
`drifts` — the trust map is *scarce by construction* (the strict measured-provenance
filter is why), which is the whole reason the sim must be made faithful rather than
leaned on the live set. **ETH/SOL pullback have live n=0** measured-provenance trades,
so they are `insufficient-live` — but the cost effect on their backtests is consistent
with BTC: cost-on shifts backtest mean-R **ETH +0.036 → −0.003** (166 trades), **SOL
+0.059 → +0.026** (173 trades) — the same ~0.03–0.06R funding+slippage drag every
symbol. (Note the BTC pullback KS reads **0.395** on the standing 238-trade
`backtest_trades.db` vs **0.4125** on the fresh 2y/160-trade fetch — the backtest
population itself depends on the fetch window, another reason the P2 unified engine's
fixed point-in-time feed matters.)


**P1 disposition:** the execution-realism component is built, wired, and MEASURED. It
is a genuine fidelity improvement (backtests are now net-of-funding+slippage by
construction and the live sizer can consult the same model), but on the pinpointed leg
it is **not** the lever that flips `drifts → calibrated`. The honest next step is P1.x
(real live-R + wider trusted-live set) and P2 (unified engine), not more cost tuning.

**Mandatory venue-aware cost (operator directive 2026-08-04 — supersedes the earlier
"opt-in" disposition).** There is no reason to run a *faithful* backtest fee-only, so
the cost is on **by default**: the pullback harness's `main()` applies the venue-aware
defaults (slippage ~5bps rt; funding **perp-only** — `funding_bps_per_window_for` is
1bps/8h for a crypto perp and **0 for futures/equity/fx**, so MES/GLD/EURUSD are never
charged a fabricated funding cost — the false-drag class the venue-fee resolver already
avoids). Every run emits **both** arms (`net_r` net-of-full-cost + `net_r_fee_only`) so
the with/without comparison is always visible. The pure `run_backtest` engine stays
byte-identical (its cost knobs default 0.0) so the lever unit tests are unaffected;
`--slippage-bps-roundtrip 0 --funding-bps-per-window 0` reproduces the fee-only arm.
**Pullback-first** — the other standalone harnesses (`trend`/`squeeze`/`fade`/
`ict_scalp`/`system`) roll onto the shared model next (a focused follow-up PR), after
which the whole roster is net-of-real-cost by construction.

## 6. Why this is not another partial fix
The partial fix would be "wire the nightly build and wait for more trades." This
plan **removes the dependency on reality's clock**: it builds the instrument that
lets a decision be made on faithful, calibrated, cost-net OOS evidence in hours —
and it earns the trust that instrument needs through measured backtest↔live
agreement, not assertion. Phase 0 delivers the first calibrated number today; each
later phase raises fidelity and widens what we can trust. The endpoint is the
pipeline a real firm runs: **simulate faithfully, prove it against production,
decide fast.**
