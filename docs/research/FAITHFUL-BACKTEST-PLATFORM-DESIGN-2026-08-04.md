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
   *actual live exit path*). **Superseded 2026-08-08 — the trail_decay pair is
   now MODELLED** (all 15 research-only levers ported into
   `scripts/backtest_trend.py`, PR #8633), so the measured omitted set is 5 → 3
   and only the `exit_head_*` trio remains; see §5f + §5g. The system backtester (`scripts/backtest_system.py`)
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
| **P2 · The unified engine** | 🟡 *IN PROGRESS — exit-verdict seam DONE (2026-08-07), see § 5c.* Extend `backtest_system.py` to drive the real signal builders + order monitor (not `generate_signal_stream`'s re-implementation); introduce `SimBroker` (B) as the one swapped seam. Fidelity becomes structural, not per-lever bookkeeping. | Any strategy/sleeve backtests on the live code path → drift → 0. | 1–2 sessions |
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

   > **P1.x SHIPPED 2026-08-06 (axis half).** `backtest_fidelity_calibrate` now
   > computes a **real stop-distance live-R** — `pnl / (|entry−stop| · |qty| ·
   > contract_value_usd)` via the canonical
   > `src.web.api._clean_trades.r_multiple` — and it is the **default** axis
   > (`--r-basis stop_distance`). The ±1 proxy survives only as an explicit
   > `--r-basis sign_proxy` opt-in for reproducing the numbers in this section.
   > A row whose risk is not derivable is **excluded from the R sample, never
   > back-filled with the proxy** — a mixed axis under a `stop_distance` label
   > would rebuild the artifact invisibly. The exclusion is reported as
   > `live_r.r_coverage` (the `rCoverage` discipline), and a leg where trusted
   > rows exist but none is R-measurable now returns an explicit *"unmeasurable,
   > not untraded"* reason instead of the bare `live n=0 < floor` that an empty
   > sample would otherwise produce.
   >
   > **Every KS(R) figure in the table below is on the RETIRED `sign_proxy`
   > axis** and is kept as the record of the artifact — do NOT compare a
   > post-2026-08-06 KS against it. The trust map must be **re-run on the real-R
   > axis** (trainer, both DBs) before any leg's verdict is quoted again; that
   > re-run, plus the trusted-live-set widening (which is accrual, not code), is
   > the remaining P1.x work.

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

## 5c. P2 · THE EXIT-VERDICT SEAM IS DONE (2026-08-07) — and what P2 still owes

**A correction to how P2 was scoped.** The plan reads "drive the real signal
builders + order monitor (not `generate_signal_stream`'s re-implementation)",
which implies the harness never runs live exit code. Read at
`backtest_system.py:239-327` and `:880-918`, that is **wrong on both halves**:
`generate_signal_stream` calls the **REAL `order_package`** per bar, and the
exit block calls the strategy's **REAL `monitor()`**. The divergence was one
level up — in what the harness DID with the verdict those real functions
returned.

**Measured** against all 9 roster monitors, the call-site re-implementation
dropped three signals the live path acts on:

| verdict key | monitors emitting it | live | harness (before) |
|---|---|---|---|
| `exit_price` | **4 / 9** — incl. `trend_donchian`, the calibration target | exit fills AT it | ignored; **bar close** |
| `close_qty_pct` < 1 | **1 / 9** (`turtle_soup`) | partial; runner stays open | closed **100 %** |
| `next_tp` | **1 / 9** (`turtle_soup`) | rolls the package TP | ignored |

So no `turtle_soup` backtest ever contained a **runner** — the part of a
scale-out that earns the trend — and every trail-stop exit on the four
`exit_price` strategies was booked at the wrong price. Two further divergences
(an `elif` chain applying `sl` **or** `tp`; no meaningful-change tolerance) are
**latent** — 0 / 9 monitors emit both keys today — and are recorded as latent
rather than folded into the count.

**The fix is the P2 seam, applied to the exit path.**
[`src/runtime/monitor_verdict.py`](../../src/runtime/monitor_verdict.py) now owns
what a verdict *means*; each caller owns only its own **effectuation**
(DB + exchange live, in-memory position in the harness). Fidelity there is
structural, not per-lever bookkeeping. Also: a `monitor()` that **raises** is
now counted per owner and surfaced in the run summary — it was swallowed into
`verdict = None`, so a broken exit path read as a quiet one and a run could
report a clean exit profile over bars where the exit path never ran.

Verified by reverting: all 6 harness tests **fail** against the pre-change
effectuation, so they measure the change rather than restate it.

**What P2 still owes** (this is a slice, not the phase):

1. **`SimBroker` (component B)** — not started. Fills, partial fills, latency
   and roll drag are still absent; only fees/slippage/funding are modelled
   (§ 5b). This is the remaining "one swapped seam".
2. **The signal-builder CONTEXT** is still parallel-implemented — the HTF-bias
   injection, the ADX/vol regime stamping, and the account/risk state the live
   builder sees. `order_package` itself is real; what is fed to it is not.
3. **The runtime exit LAYER above `monitor()`** — `order_monitor.py`'s exit
   heads, trail-decay and stale-stop levers, and the `ExitPlan` ladder — is
   still absent from the harness. The verdict seam makes wiring it tractable;
   it does not wire it.
4. ~~**Re-run the calibrator** for `trend_donchian` and `turtle_soup`. Their
   backtest trade distributions have changed…~~ **WITHDRAWN 2026-08-07 — this
   was wrong.** See § 5d: the calibrator's backtest population is produced by
   `scripts/backtest_{trend,pullback,squeeze}.py`, **not** `backtest_system.py`.
   Those harnesses were untouched by P2, so their distributions did **not**
   change and a re-run would have measured nothing about the exit-verdict seam
   while being reported as if it had. The real finding is § 5d.

## 5d. THE TRUST GATE WAS CERTIFYING EVIDENCE ITS OWN PRODUCER DISOWNED (2026-08-07)

Found while scoping § 5c item 4. **Two harness families, not one**, and the
plan conflated them:

| | produces | P2'd? | feeds |
|---|---|---|---|
| `backtest_system.py` | the portfolio/intent-layer runs | **yes** (§ 5c) | roster A/Bs, cell walk-forwards |
| `scripts/backtest_{trend,pullback,squeeze}.py` | `backtest_trades.db` | **no** | **the calibrator → the P0/P3 trust gate** |

Every fidelity/agreement number in § 5a/5b comes from the **second** family,
which `backtest_trend.py` shows is a full parallel re-implementation — it
computes the Donchian channel inline (`df["high"].rolling(donchian).max().shift(1)`),
copies the confidence formula, and imports nothing from `src/units/strategies`.

**The honesty machinery already worked. The consumer did not exist.**
`regime_debt_matrix.build_harness_cmd` computes a fidelity verdict per leg, and
measured against the live config today:

| strategy | faithful | omitted levers |
|---|---|---|
| **`trend_donchian`** | **False** | `exit_head_{action,model,threshold}`, ~~`trail_decay_{arm_r,tight_mult}`~~ *(struck 2026-08-08 — now modelled, PR #8633; omitted set is 5 → 3)* |
| `htf_pullback_trend_2h` | True | — |
| `squeeze_breakout_4h` | True | — |

All five omissions on the **primary calibration target** are **exit** levers —
a third candidate driver for the § 5b gap, alongside the small-sample/regime
one, that the doc had not considered.

`backtest_augment_runner` printed that label into a human summary and **never
persisted it**, so `backtest_fidelity_calibrate` could not read it *even in
principle* — and would return **`calibrated`**, i.e. *"TRUSTED OOS evidence
now"*, on rows whose producing harness declares itself incomplete. Written and
never read, sitting directly beneath the P3 gate meant to replace
"live-holdout only": the exact class `provenance-consumer-guard` exists for.

**Fixed in this change.** The label rides on the row (`notes`, JSON; a bare
`run_tag` still means *unknown* — never coerced to faithful, the
`UNVERIFIED != MEASURED` rule), and `agreement()` takes it as a gate input so a
leg with an approximate producer returns a distinct **`approximate-harness`**
verdict naming the missing levers. A `drifts` leg reports the claim too, so a
reader never has to ask whether a drift was measured against a complete model.

**Population note:** 1 of 3 calibrated legs is affected today. This does not
retroactively invalidate the § 5a/5b `trend_donchian` numbers — those legs read
`drifts`/`insufficient-live`, so the new gate would not have changed their
verdict. What it removes is the *future* path where a lever change nudges the
metrics over the line and the gate certifies on an admittedly-incomplete model.

## 5e. THE FIDELITY DEFICIT IS MOSTLY A WIRING FACT — THERE ARE TWO `backtest_trend.py` (2026-08-08)

Found while starting § 5d's follow-up ("port the calibrated harnesses onto real
strategy code"). Before writing any port, the levers turned out to already exist
— **in a second copy of the same harness that the pipeline does not run.**

| | lines | flags | invoked by |
|---|--:|--:|---|
| `scripts/backtest_trend.py` | 624 | 30 | **`build_harness_cmd` → `backtest_trades.db` → the calibrator** |
| `scripts/research/backtest_trend.py` | 636 | 38 | the M20 exit sweeps; cited as the **reference implementation** by `src/runtime/trail_decay.py` |

Same git history (#8467 rolled venue-aware cost onto **both**, so both are
maintained), 1048 changed lines in **4 hunks**, and **neither is a superset**:

- **only `research/`** (15): `--trail-decay-{arm-r,stall-bars,tight-mult}`,
  `--giveback-{r,min-mfe-r}`, `--bank-{at-r,frac}`, `--confirm-bars`,
  `--skip-hours`, `--vol-skip-{above,below}-pctl`, `--vol-pctl-window`,
  `--trail-vol-{above,below}-pctl`, `--trail-vol-tight-mult`
- **only `scripts/`** (7): `--adx-{min,max,period}`, `--side-filter`,
  `--cooldown-bars`, `--direction-filter`, `--confidence-sweep`

**This reframes § 5d.** `trend_donchian`'s five omitted levers are not five
unbuilt capabilities: the **`trail_decay` pair is implemented in the sibling
copy**, and so are all five entry params § 5d listed as having "no harness flag"
(`confirm_bars`, `skip_hours`, `vol_pctl_window`, `vol_skip_{above,below}_pctl`).
The deficit is substantially **wiring**, not capability.

**And it runs the other way too.** `src/runtime/trail_decay.py` and
`src/runtime/trail_vol.py` — modules behind a **live Tier-3 order-affecting
lever** — name `scripts/research/backtest_trend.py` as their harness reference.
So the evidence base for a live lever comes from a harness the fidelity pipeline
never runs, and the harness it *does* run cannot express that lever at all.

**Not fixed here, deliberately.** The obvious move — repoint `build_harness_cmd`
at the research copy — is wrong as-is: it would silently drop `--adx-*` (which
`build_harness_cmd` actively forwards) and `--side-filter` (a declared
`_TREND_LEVER_FLAG`), trading one fidelity hole for another. Convergence is the
fix, it **changes backtest numbers**, and it cannot be validated in a web sandbox
(the committed `data/backtest_candles.csv` is ~3.5 days → 0 trades; the
`binance_vision` fetch is network-blocked). It needs the trainer or a GH runner
with a per-leg before/after comparison. Filed as
**`BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`** with the full
convergence plan.

**The `exit_head_*` trio does NOT converge away** and is a separate decision.
Scoring it needs the model registry at inference, and
`research-backtest-augment.yml` runs on `ubuntu-latest` — a free runner with no
registry. So either that leg moves to the trainer, or `trend_donchian` stays
approximate, which post-§ 5d means **its backtest can never be stamped
`calibrated`**. That is arguably correct — a backtest omitting a live *advisory*
exit head is not reproducing live — but it should be a **stated decision rather
than an accident**, because it makes the earned-trust path unreachable for every
strategy that graduates an ML exit head.

## 5f. THEY ARE TWO ENGINES, NOT TWO FLAG SETS — AND LIVE MATCHES THE PIPELINE COPY (2026-08-08)

§5e framed the fork as an inventory problem (30 vs 38 flags, "neither is a
superset") and proposed converging in whichever direction a before/after showed
was behaviour-preserving. **Measured, that framing is wrong in a way that
reverses the proposed fix.**

Instrument: [`scripts/research/trend_harness_divergence.py`](../../scripts/research/trend_harness_divergence.py)
(committed, re-runnable, `--json`). Population below: **BTCUSDT
2022-07-23→2022-07-27, the committed `data/backtest_candles.csv` resampled to
5-minute bars, 1001 bars, n = 21–35 trades per configuration, every optional
lever OFF on both sides.** Small — deliberately enough to establish the axis is
first-order, nowhere near enough to size it. Point `--data` at real history for
a decision-grade figure.

### They disagree about which trades exist

| config (levers OFF) | pipeline `cooldown=1` | pipeline `cooldown=0` | research |
|---|--:|--:|--:|
| donchian 20, timeout 200 | 28 trades, −11.735 net R | 29, −13.187 | **35, −13.385** |
| donchian 30, timeout 200 | 21 trades, −8.666 net R | 22, −9.822 | **28, −12.635** |

A 20–35% difference in trade *count* with identical inputs and no levers is not
a configuration gap. The engines differ on: **the trail's ATR basis** (frozen
entry-bar ATR vs the current bar's rolling ATR), an **opposite-signal flip
exit** (research only), a **post-exit cooldown** (pipeline only), the **fee
basis** (avg of entry/exit price vs entry price), warm-up length, `timeout_bars`
semantics, and the win-rate denominator (gross vs net-of-fee). `by_outcome`
shows it plainly: the pipeline reports `stop`/`trail_stop`/`timeout`, the
research engine reports `trail_stop`/`flip`.

Isolating the ATR basis alone — the research engine with only that one axis
changed — moves gross R by **−34.0%** (5m, donchian 20), **−23.0%** (5m,
donchian 30) and **+41.2%** (15m, donchian 20). Material, and **sign-unstable
across configurations**, which is itself the reason not to guess it.

### Which one is live-faithful — the fact that decides the direction

`src/units/strategies/trend_donchian.py` freezes the entry ATR into
`meta["atr"]`, and `monitor()` trails off that frozen value (the rolling
recompute there is the legacy fallback for packages missing the key). The code
says why, at the write site:

> *"Entry-time ATR is FROZEN here and used by the monitor for the trail
> distance, matching the backtest's fixed-ATR trail (`scripts/backtest_trend.py`
> uses the entry bar's ATR for the whole trade). **Without this the live trail
> would drift with a rolling ATR and diverge from what was validated.**"*

For a strategy whose *only* profit exit is the trail, that is the load-bearing
exit semantic — and on it, **live matches `scripts/backtest_trend.py`**. The
research copy is the one that does exactly what the live code documents itself
as guarding against.

**So the convergence direction is the opposite of what §5e and the backlog row
proposed.** Do NOT repoint `build_harness_cmd` at the research copy: that would
make the fidelity pipeline *less* live-faithful while appearing to fix fidelity.
Port the 15 research-only lever flags **into** `scripts/backtest_trend.py`,
keeping its engine, and retire the research copy behind it.

### The live unit is a hybrid of both — and one citation is simply wrong

`trend_donchian.py` cites **both** copies as its reference, per feature:
`scripts/backtest_trend.py` for the port, `_atr`, pending-entry and the frozen
trail; `scripts/research/backtest_trend.py` for `skip_hours`, `vol_skip_*_pctl`
and the giveback lever. `src/runtime/trail_decay.py` and `trail_vol.py` — modules
behind a **live Tier-3 order-affecting lever, and `trend_donchian` declares
`trail_decay_arm_r: 6.49` / `trail_decay_tight_mult: 2.5` today** — cite the
research copy. So an armed live lever's threshold was tuned on a harness whose
baseline trail distance moves with a rolling ATR while live's is frozen. Filed
as `BL-20260808-TRAIL-LEVER-TUNED-ON-NON-LIVE-FAITHFUL-TRAIL`.

Mechanically checked, the `confirm_bars` citation was flatly wrong: the live
comment named `scripts/backtest_trend.py --confirm-bars`, and that file declares
no such flag — it exists only in the research copy. Comment corrected here
(behaviour untouched; field beats comment).

### The `exit_head_*` blocker was misdiagnosed — it is the ARTIFACT LOCATION

§5e said the trio "needs the model registry at inference". **It does not.** The
exit head is a self-contained JSON with the booster inline (`booster_txt`),
loaded by `exit_head_shadow._load_artifacts` from
`runtime_logs/trainer_mirror/exit_head/`. It is not committed, so a
GitHub-hosted runner has no copy; the trainer and live VM do. That is a *file
distribution* problem, not a registry port — and stating it as the latter made
the fix sound bigger than it is. (Recorded as a correction because an
overstated impossibility closes off work, per `CLAUDE-RULES-CANONICAL` §
"Green is not evidence" obligation 3.)

**Operator decision (2026-08-08): move the leg to the trainer.** Shipped as
[`scripts/ml/exit_head_replay.py`](../../scripts/ml/exit_head_replay.py) — runs
the live-faithful harness, walks each trade's in-trade closed bars, builds the
feature row with the *same* `exit_head_shadow._feature_row` the monitor calls,
and re-resolves the trade at the first bar the head fires. The take/hold
predicate was **extracted** to `exit_head_shadow.would_exit_for` and is imported
by both live and replay: a second copy of that predicate would be this section's
own defect class, one level down. Absent artifact / absent LightGBM / no
servable head is **exit 2 with a named reason**, never a silent pass-through
that would emit unchanged trades under an "exit-head-applied" label.

## 6. Why this is not another partial fix
The partial fix would be "wire the nightly build and wait for more trades." This
plan **removes the dependency on reality's clock**: it builds the instrument that
lets a decision be made on faithful, calibrated, cost-net OOS evidence in hours —
and it earns the trust that instrument needs through measured backtest↔live
agreement, not assertion. Phase 0 delivers the first calibrated number today; each
later phase raises fidelity and widens what we can trust. The endpoint is the
pipeline a real firm runs: **simulate faithfully, prove it against production,
decide fast.**
