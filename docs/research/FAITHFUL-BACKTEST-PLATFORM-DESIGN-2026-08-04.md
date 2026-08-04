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
| **P1 · Close the top fidelity gap** | Make the strategy harnesses run the **actual exit path** (exit heads + trail-decay + stale levers) via `src/units/strategies`, eliminating the `approximate` omissions. Re-run P0 calibration → prove agreement improves. Consolidate the 4 cost modules behind one `trade_cost` interface the harness consults (net-of-real-cost by construction). | Backtest trades reflect the *real* exits + costs → agreement rises → more legs cross the gate. | days |
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

## 6. Why this is not another partial fix
The partial fix would be "wire the nightly build and wait for more trades." This
plan **removes the dependency on reality's clock**: it builds the instrument that
lets a decision be made on faithful, calibrated, cost-net OOS evidence in hours —
and it earns the trust that instrument needs through measured backtest↔live
agreement, not assertion. Phase 0 delivers the first calibrated number today; each
later phase raises fidelity and widens what we can trust. The endpoint is the
pipeline a real firm runs: **simulate faithfully, prove it against production,
decide fast.**
