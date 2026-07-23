# M29 — AI-Driven System-Dynamics Modelling (design-of-record)

> **Status: 🟢 P0 SCOPE LOCKED 2026-07-23** (operator-directed: *"add a roadmap milestone —
> AI-driven system dynamics modelling"*; P0 scope confirmed same day). **The P1 build is
> unblocked** (the pure stock-flow engine + a hand-specified seed model — Tier-1/offline).
> Nothing here touches `src/`, `config/`, or any order path. Every phase before the
> graduation gate is **observe-only**; any step that lets a model condition a live order
> is **Tier-3**, operator-gated, and backtest-gated.
>
> **P0 scope (operator-confirmed 2026-07-23):**
> - **Target: A — the macro–energy complex** (primary); the fleet self-model (target B) is
>   the P6 fast-follow reusing the same engine.
> - **Seed system: EIA weekly natural-gas storage → MNG price response** (the ROADMAP_MACRO
>   M1/M2 canonical case — smallest, cheapest, weekly cadence).
> - **AI-role lead: system identification** (ML fits the model's structure/params/lags from
>   point-in-time data); LLM structure-elicitation + scenario generation follow in P2/P3.
> - **Milestone shape: distinct M29** (a reusable platform capability; target B is outside
>   M28's remit) — feeding M28's `c_macro` overlay, not folded into M28.
>
> **Anchor:** `MB-20260723-M29-AI-SYSTEM-DYNAMICS`
> **Feeds:** M28 (Macro/Value Speculation Sleeve) · ROADMAP_MACRO (Energy/carbon M1–M5)
> **Ladder position** (per [AI-TRADER-RESEARCH-PLAN](AI-TRADER-RESEARCH-PLAN-2026-07-19.md)):
> a new **observe → advise** capability; it does not skip ahead to *gate/size/select*.

## 1. What this is (and what it is not)

**System dynamics (SD)** models a domain as **stocks** (accumulations — inventory,
positioning, equity, allowance banks), **flows** (rates that change stocks — production,
demand, injections/withdrawals, capital in/out), and **feedback loops** (reinforcing and
balancing) with explicit **delays**. The output is a *simulation* you run forward under
assumptions, not a single-point regression. The value is that mispricings and regime
turns often live in the **linkages and lags** between variables, not in any one series —
precisely the framing ROADMAP_MACRO already adopts ("the gas–power–carbon complex is one
system observed at three points; mispricings live in the linkages").

**"AI-driven"** means AI does the parts hand-built SD is worst at, across three roles
this milestone treats as *phases*, not a fork:
1. **System identification** — ML infers the structure, parameters, and lags of the SD
   model from historical data (which flows drive which stocks, with what gain and delay),
   instead of an analyst hand-tuning constants.
2. **Structure elicitation** — an LLM proposes/maintains the causal-loop diagram (the
   stock-flow topology + sign of each link) from research text (filings, release
   calendars, utility resource plans), with data *calibrating and falsifying* it.
3. **Scenario generation** — the calibrated model is *run forward* under
   surprise-vs-consensus and policy-shock assumptions to produce **scenario
   distributions** whose summary statistics can condition thesis conviction / sizing.

**This is NOT:** a black-box price predictor, a bar-based single-symbol backtest lever,
or a reason to touch the live ICT order path. It is a *world/system model* whose outputs
are an **input** to the existing decision machinery, held to the same point-in-time,
calibration-first, net-of-cost discipline as everything else.

## 2. Scope — the two targets (operator to confirm priority)

The method is one; it can be pointed at two systems. Both are in scope; the design
**recommends the macro–energy complex first** (it directly unblocks M28/energy value and
reuses the most existing infra) and treats fleet self-modelling as a fast-follow that
reuses the same engine.

| # | Target system | Stocks / flows / loops (examples) | Consumes | Delivers to |
|---|---|---|---|---|
| **A (primary)** | **Macro–energy complex** | NG storage (stock) ← injection/withdrawal (flows) ← weather-driven demand + LNG-export/production; power demand ← data-center interconnection queue; carbon allowance bank (stock) ← auction supply − compliance surrender; coal↔gas switching (balancing loop) | FRED macro series, EIA storage, weather/forecast revisions, event calendar (all point-in-time) | M28 `thesis_conviction` + ROADMAP_MACRO M1/M2 (energy event study) |
| **B (fast-follow)** | **Our trading system itself** | account equity (stock) ← realized PnL flows; open gross exposure (stock) ← entries − exits; strategy-population "capital at risk" per sleeve; drawdown→sizing balancing loop; correlation-driven concurrent-loss reinforcing loop | `trade_journal.db` (trades, order_packages, daily_risk_state), allocator soak, per-strategy net-R | Risk/allocation what-if simulation; M18/M26 exposure-throttle evidence |

**Explicitly out of scope for v1:** any SD model that *directly* emits an order intent
(SD conditions conviction/sizing/exposure at most, and only after the graduation gate);
GPU/compute-contract or voice-brokered OTC markets (ROADMAP_MACRO §3 out-of-scope);
real-time/tick-latency modelling (the edge here is research latency, weeks horizon).

## 3. Reuse map — build on what exists, don't re-lay foundations

M28 already built most of the substrate this milestone needs; M29 is largely a *new
modelling layer over existing feeds*, plus one genuinely new engine.

**Reuse (on `main`):**
- **Point-in-time feeds + discipline** — M28 `valuation_feed` / `valuation_store`
  (FRED, `observed_at`/`as_of`, append-only revisions), `event_store` + `event_calendar`
  (scheduled events + realized outcomes), `macro_signals` traceability. *No-lookahead is
  already enforced infrastructure, not a new thing to get right.*
- **The backtest paradigm** — M28 `thesis_backtest` (calibration bins + Spearman rank +
  net-of-cost + beat-baseline) + `thesis_replay` (strict past-only reconstruction). An SD
  scenario output is scored by the **same** calibration instrument: *does the model's
  forward-scenario signal predict realized hit-rate, net of cost, out-of-sample?*
- **The cost-model pillar** (ROADMAP_MACRO §4) — any SD-conditioned horizon selection
  must consult the same execution-realism component (roll drag, fees, funding).
- **The `TradeThesis` object + `c_macro` overlay seam** — SD scenario summaries become
  additional `macro_context` fields on a thesis; the deferred `c_macro` conviction
  overlay is the eventual (Tier-3) graduation surface, shared with M28.
- **The ML training center** (trainer VM, `ml/` manifests, 3-stage ladder) — system
  identification trains as trainer-side, off-VM, candidate→shadow→advisory like any head.
- **The layer guard** (M0a import-linter) — M29's engine lives in the **Signals/Modelling
  layer**, pure, no Execution import; the guard keeps it honest.

**New build (the milestone's actual work):**
- A **stock-flow simulation engine** (small, dependency-light, pure): declare
  stocks/flows/loops/delays as a spec, integrate forward, run scenario ensembles. Layer-3
  Signals/Modelling, no order path.
- A **causal-model spec** (committed, point-in-time versioned) — the topology + link
  signs + fitted parameters, one per target system, authored/maintained per §1 roles.
- **System-identification harness** — fit the spec's parameters/lags from point-in-time
  data with proper walk-forward; report identifiability + stability, never a single fit.

## 4. Phased plan (each phase: gate to proceed · stop condition)

Mirrors the repo's discipline — **pre-registered thresholds written before results are
seen**, calibration + net-of-cost as the only proof, low-n honesty, observe-only until a
backtest passes and the operator approves.

- **P0 — Design-of-record + scope lock (Tier-1). ✅ DONE 2026-07-23.** *This doc.* Operator
  confirmed the scope (target A first · seed = EIA weekly NG storage → MNG price response ·
  AI-role lead = system identification · distinct milestone; see §6). **Gate met.**
  **Stop (carried into P1):** if SD adds nothing over M28's direct value/event reads on
  this seed case, do not build the engine — fold the idea back into M28.
- **P1 — The stock-flow engine + a hand-specified seed model (Tier-1, offline).** Build
  the pure simulation engine + encode ONE small hand-authored causal model for the seed
  system (storage/injection/withdrawal/weather-demand → price), calibrated on point-in-time
  history via the existing feeds. **Gate:** the engine reproduces known historical
  dynamics within a pre-registered error band on a holdout; the spec is committed +
  point-in-time versioned. **Stop:** the system is not identifiable from available
  point-in-time data (revised-only inputs, no consensus) → re-scope the data source (same
  stop as ROADMAP_MACRO M1).
- **P2 — AI system identification (Tier-1, trainer-side).** Replace hand-tuned constants
  with ML-fit parameters/lags under walk-forward; add the LLM structure-elicitation loop
  (propose links from text → data falsifies). **Gate:** the AI-fit model calibrates
  ≥ the hand-specified model out-of-sample, with stable per-fold parameters. **Stop:**
  overfitting — per-fold parameters swing wildly / OOS calibration collapses → keep the
  parsimonious hand model, park the ML fit.
- **P3 — Scenario engine + observe-only soak (Tier-1/2, isolated path).** Run forward
  scenario ensembles at each rebalance; emit a scenario-summary signal (e.g.
  P(storage-surprise > x), expected spread response) into a **new observe-only soak log**
  (`runtime_logs/sysdyn_soak.jsonl`) alongside the M28 thesis soak — **places nothing,
  changes no order.** **Gate:** the soak accrues; scenario signals are traceable +
  point-in-time. **Stop:** scenario outputs are unstable tick-to-tick (numerical
  fragility) → fix the integrator before proceeding.
- **P4 — The decisive backtest (Tier-1, the gate).** Score the SD-conditioned thesis
  against the M28 `thesis_backtest` instrument: does adding the SD scenario signal to
  `thesis_conviction` **improve calibration and net-of-cost edge vs the M28 value/event
  baseline, out-of-sample?** Pre-registered thresholds. **Gate:** PASS = SD adds
  incremental, cost-surviving, calibrated edge. **Stop/park:** FAIL = SD is a nicer story,
  not an edge → stays observe-only research; do not graduate.
- **P5 — Graduation to conviction overlay (Tier-3, operator + backtest gated).** Only on a
  P4 PASS: wire the SD scenario summary into the `c_macro` conviction overlay via a
  `*_MODE` flag (`off`/`annotate`/`apply`), **reductive/annotate first**, never a
  default-off `*_ENABLED` gate. The per-account `RiskManager` stays the final sizing
  authority (Prime Directive). **Gate:** operator approval. **Stop:** any live behaviour
  divergence from backtest → revert to `annotate`.
- **P6 (target B) — Fleet self-model (Tier-1 → Tier-3).** Point the same engine at our own
  system (equity/exposure/drawdown loops) as a risk what-if simulator; feeds M18 allocator
  + M26 exposure-throttle evidence. Same observe → backtest → Tier-3 ladder.

## 5. Risk register

- **Storytelling risk (the #1 SD failure mode).** SD models are seductive — a plausible
  causal diagram *feels* like understanding. Defence: **calibration + net-R is the only
  proof**, thresholds pre-registered, and P4 must beat the *plain M28 value/event
  baseline*, not just "look reasonable."
- **Point-in-time integrity.** Same lookahead trap as M28 (revised consensus, revised
  macro). Enforced by reusing `observed_at`/`as_of` feeds + `thesis_replay`; never fit on
  revised data.
- **Overfitting few events / low n.** Weeks-horizon → low n. Validate by calibration +
  walk-forward stability, never significance from a handful of scenario "wins."
- **Identifiability.** Many SD structures fit the same history (equifinality). Prefer the
  parsimonious model; report parameter stability across folds; an unstable fit is a park,
  not a ship.
- **Scope creep into a black box.** The moment "SD model" becomes an unexplainable deep
  net predicting price, it has left this milestone — that is M23/M24/M25's lane. M29 keeps
  the stock-flow structure legible; AI fits *parameters within a declared structure*.
- **Attentional risk to the live system.** Pure, layer-guarded, observe-only until P5;
  cannot destabilize ICT. No big-bang.
- **LLM $-budget** for structure elicitation — reuse M13's cost accounting; widen
  deliberately, keep observable.

## 6. P0 gate — scoping questions (RESOLVED 2026-07-23)

All four resolved by the operator; the answers are locked in the status header above.

1. **Target priority** → **A (macro–energy) first**; B (fleet self-model) is the P6 fast-follow.
2. **Seed system** → **EIA weekly NG storage → MNG response** (the ROADMAP_MACRO M1/M2
   canonical case).
3. **AI-role emphasis for v1** → lead with **system identification** (data-fit params); LLM
   structure-elicitation + scenario generation follow in P2/P3.
4. **Relationship to M28** → **distinct milestone** feeding M28's `c_macro` (not folded in).

## 7. Done-condition

M29 is **DONE** when: the stock-flow engine + at least one AI-identified, point-in-time
causal model exist and are layer-pure; the P4 backtest has been run with a recorded
verdict (PASS → a Tier-3 `c_macro` graduation proposal; FAIL → an honest findings doc and
the capability stays observe-only research). As with every milestone here, a rigorous
negative is a completed outcome, not a failure.
