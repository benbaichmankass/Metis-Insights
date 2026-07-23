# M28 — Signal R&D Program (design of record)

**Status:** active program plan (operator-directed 2026-07-23). The top-level,
long-horizon plan for developing tradeable signals in the valuation-snapshot
format. Governs the whole funnel — *what info we build, how we test it cheaply
BEFORE we build it, and how it translates into realized net-of-cost PnL* — so the
work is one coherent program, not jumbled bits. Composes with `research-driver` +
`RESEARCH-RIGOR-STANDARD.md`; the iteration mechanics live in
[`M28-signal-research-methodology.md`](M28-signal-research-methodology.md); the
running record in [`M28-signal-research-ledger.md`](M28-signal-research-ledger.md).

## 0. The thesis, and the failure mode this program is designed against

**Thesis:** there exist weeks-to-days-horizon signals in free/keyless
macro + positioning + derivatives + mechanistic data, expressible as
valuation-snapshot conviction, that survive real trading costs and translate into
positive net PnL.

**The failure mode we are explicitly engineering against:** we already
**over-built three times.** Each of value / COT / crypto got a full production
stack — a PIT data feed, a backfill script, an off-VM workflow, a committed store,
a test suite — *before* we knew whether the signal had any edge. Then all three
graded null. That is expensive motion for zero information gain over a cheap
prototype. **This program's central discipline: prototype-and-grade on
already-available data FIRST; invest in production wiring ONLY for a construction
that has already cleared the signal + PnL gates.** "Worth the build" is a gate that
precedes the build, not a hope that follows it.

## 1. The funnel — every candidate passes cheap→expensive gates, each a GO/NO-GO

```
 S0 Feasibility    S1 Prototype     S2 Signal grade    S3 PnL grade      S4 Productionize  S5 Express+Live
 screen (no build) construct+emit   (is there signal?) (does it pay?)    (only if S2+S3    (Tier-3,
 ─ PIT data avail? ─ toolkit over   ─ P4 calibration   ─ conviction-      cleared)          operator-gated)
 ─ crude univariate  throwaway/      + non-overlap       weighted net-of-  ─ PIT feed +      ─ expression +
   IC on a sample    in-hand data    horizon-IC +        cost portfolio     backfill +        cost model +
 ─ pre-register      → valuation-    conv-spread         PnL, walk-fwd →    workflow →         shadow→advise→
   direction+horizon snapshot rows  ─ GATE: flagged     Sharpe/PnL/maxDD/   committed store    live ladder
                                      IC AND +conv-       turnover          ─ the ONLY stage  ─ backtest-gated
                                      spread @ tradeable ─ GATE: +net PnL    that builds
                                      horizon             OOS, beats a       production infra
                                                          naive baseline
                                                          net of costs
```

**A NO-GO at any stage is a recorded result** (ledger row + reason), never a
silent drop and never a quiet retry-until-it-passes (`RESEARCH-RIGOR-STANDARD.md`
§ honest negatives). The whole point of ordering the gates cheap→expensive is that
most candidates die at S0–S2 for near-zero cost, and we only spend build effort
(S4) on something that already proved it pays (S3).

### The two gates the operator called out, made explicit

- **"Know what we're building and test it before we build" → S0 + the S4
  precondition.** S0 is a *pre-build* screen: is the data even available
  point-in-time (not revised-only — the #1 way to manufacture a fake edge)? Does
  the crudest univariate form show *any* IC on a sample? State the hypothesis
  (direction + horizon) *before* grading so we aren't fishing. **Nothing reaches
  S4 (the production build) until it has passed S2 AND S3 on prototype data.**
- **"Understand how it translates into PnL" → S3, a NEW instrument we must
  build.** A positive IC is necessary but NOT sufficient — crypto's 1d had a
  flagged IC yet its conviction spread was below fees. S3 is the harness that
  carries a graded signal all the way to **realized net-of-cost portfolio PnL**
  (§4). Signal grade (S2) answers *is there signal*; PnL grade (S3) answers *does
  it make money after costs at a tradeable size.* Both are required; S3 is the one
  that actually gates a build.

## 2. The research map — the space we are searching

Rows = input families; columns = construction dimensions. Each cell is a candidate.
The ledger tracks tested cells + verdicts.

| Input family | D1 transform | D2 conditioning | D3 cross-section | D4 composite |
|---|---|---|---|---|
| **Value** (ERP/real-yield/GSR/OAS) | ⬜ | ⬜ | ⬜ | ⬜ |
| **COT** positioning | ⬜ change/divergence | ⬜ | ⬜ | ⬜ |
| **Crypto** funding/OI/basis | ⬜ funding-change | ⬜ funding×OI | ⬜ | ⬜ (funding+OI+basis) |
| **Sysdyn mispricing** (M29) | ⬜ mispricing-as-signal | — | — | — |
| *Options-implied* (skew/term/PC) | S0-gated | — | — | — |
| *Flows / credit / seasonality / on-chain* | S0-gated | — | — | — |

Level-percentile / single-series / time-series / contrarian is the ONE cell all
three first-pass sleeves used (ledger #1–3). The unshaded cells are the search.

## 3. Phasing — the long plan, with decision checkpoints (not just the next step)

**Phase A — Instrumentation (build the tools once).**
- A1 · Construction toolkit — `scripts/macro/signal_constructions.py` (D1–D4 pure
  transforms). **DONE** (10 tests, ruff-clean).
- A2 · **PnL harness (S3)** — the conviction-weighted, net-of-cost, walk-forward
  portfolio backtest (§4). **DONE** (`scripts/macro/pnl_harness.py`, 12 tests): three
  books per run (conviction-weighted / long-short-neutral / all-long baseline), each
  with total return · Sharpe · max-drawdown · hit-rate · turnover + an out-of-sample
  split; the S3 gate `pays_oos` = the conviction book beats all-long AND the neutral
  book is positive on the OOS half. Reuses the P4 `net_return`.
- A3 · Feasibility-screen harness (S0) — a thin "PIT-availability + crude
  univariate IC on a sample" checker so a new input family is screened before any
  feed is built.

**Phase B — Exhaust the construction space on the inputs we ALREADY have.**
Data for value/COT/crypto is in hand and the sysdyn engine is built, so every cell
in the top four rows is *cheap* to prototype (S1→S3) with zero new production
wiring. Work the ledger's queued entries: COT change/divergence, crypto
funding×OI, cross-sectional value/COT, composites, and the sysdyn mispricing
signal. **Decision checkpoint B→C:** if a construction clears S2+S3 OOS → it
graduates to Phase C. If the *entire* space on our four existing inputs is
exhausted with recorded nulls → that is a decisive result: the valuation-snapshot
format does not carry tradeable edge at our costs *for these inputs*, and we pivot
to Phase D input families rather than keep re-cutting the same data.

**Phase C — Productionize a survivor (only what cleared B).**
S4 (PIT feed + backfill + workflow + committed store) + S5 expression design, for
the specific construction that passed. This is the first time in the program we
build production infra for a signal — and only because it already proved it pays.

**Phase D — New input families (each S0-gated before any build).**
Options-implied (skew / term-structure / put-call), ETF & fund flows, credit
spreads by rating, macro-surprise indices, seasonality, on-chain. Each enters at S0;
only a passing screen earns an S1 prototype.

**Phase E — Live graduation (Tier-3, operator-gated, backtest-gated).**
The M28 shadow→advise→live ladder + `c_macro` overlay (existing M28 P5/P6). Nothing
here is autonomous.

## 4. Signal → PnL — the translation path (and the instrument we still owe)

The chain every signal must survive to be real money:

```
cheap_score ──▶ value_conviction (|cheap_score−0.5|×2) ──▶ direction (≥0.70 long / ≤0.30 short)
            ──▶ position size (conviction-weighted, risk-capped) ──▶ forward return × direction
            ──▶ − costs (fees + slippage + carry, per expression) ──▶ realized net PnL
```

- **Have:** `horizon_ic_scan.py --non-overlapping` (IC + honest t) + `conv_spread`
  (the market-neutral monetizable edge) → *is there signal, and which direction/
  horizon.* And `thesis_backtest_run.py` P4 → net-of-cost calibration + naive-
  baseline beat + an equity curve.
- **Owe (A2, the S3 harness):** a **portfolio-level** net-of-cost PnL backtest —
  build a conviction-weighted long-short book per rebalance (the conv-spread turned
  into actual sized positions), carry it forward net of a realistic cost model,
  walk-forward, and report **PnL, Sharpe, max-drawdown, turnover, hit-rate** — the
  numbers that say "this makes money at a tradeable size," not just "conviction
  correlates with return." IC ≠ PnL; this harness is where the difference is
  measured. It reuses the P4 replay + the non-overlapping windows so the PnL is on
  the same leakage-safe, honest basis as the signal grade.
- **Cost model per expression:** the M28 sleeve expresses via
  `alpaca_options_paper` defined-risk spreads (weeks horizon), but a signal could
  also map to futures/perps; the cost + payoff geometry differ. The cost model
  (M28's cost-model pillar) is a first-class input to S3, and the expression choice
  (S5) is downstream of which cost structure the signal's horizon + magnitude can
  actually pay for.

## 5. Expression — how a graded signal becomes a trade

Per the existing M28 design: weeks-horizon, symbol-agnostic, **defined-risk
options** on `alpaca_options_paper` (the M22 isolated-order-path pattern; the
executor is M28 P5, not yet wired). S5 maps each *surviving* signal to a concrete
instrument + expression + its cost/payoff, and only a signal whose S3 net PnL
survives *that* expression's costs proceeds. Live is Tier-3.

## 6. Exit criteria — what "done" means for the program

- **Success:** ≥1 construction clears S2 + S3 out-of-sample → productionized (S4) →
  expressed (S5) → graduated to shadow/advise/live (Tier-3).
- **Negative-but-done:** the mapped construction space (Phase B, and any S0-passing
  Phase D family) is exhausted with recorded nulls → a real, valuable result that
  stops us building on a non-edge. Recorded, not walked past.
- **The durable deliverable regardless:** the *method* — the funnel, the toolkit,
  the two graded instruments (signal + PnL), and the compounding ledger — is
  reusable for every future input family, which is the actual point of M28.

## 6b. Contingency — back-up plans if Phase B exhausts with no survivor

If the whole construction space (D1–D4) on our four existing inputs
(value / COT / crypto / sysdyn) grades null, that does **not** end the program —
it just means the edge isn't in *directional forward return from these inputs at
this horizon with these constructions.* Ordered cheapest-to-test / most-likely-to-work:

- **BP1 · Change the OBJECT we predict (not the input).** All four sleeves predict
  *directional* return — the hardest thing to find at retail. Cheaper edges,
  reusing infra we already have:
  - **Volatility, not direction** — predict realized-vol / vol expansion; express
    via the M28 options path (straddle/strangle, defined-risk). Positioning +
    funding extremes plausibly predict *vol expansion* even when they don't predict
    direction, and options is the natural expression we already chose. *Highest-value
    fallback — the gate + toolkit port directly; only the target (vol) and the
    expression change.*
  - **Relative value / spreads** — a signal that can't pick direction can still pick
    the *cheaper of two related assets* (market-neutral). Reuses the M22 pairs
    sleeve's isolated 2-leg path + the D3 cross-sectional toolkit.
  - **Regime / risk-on-off** — a macro read that *conditions the existing book's
    sizing/gating* rather than a standalone trade (this is what the `c_macro`
    overlay was always for). A lower bar than standalone alpha.

- **BP2 · Ensemble of weak signals (breadth, not strength).** Each input null alone,
  but a diversified blend of many weak, low-correlation signals can be tradeable
  even when no single one clears the bar (Grinold–Kahn breadth). D4 composite is the
  first step; the fuller version is a cross-input meta-model graded through the same
  S2+S3. Reframes "no single survivor" as "does the ensemble survive."

- **BP3 · New input families (Phase D).** The most likely reason these four failed
  may be the *inputs*, not the construction. Bring genuinely different information,
  each S0-gated before any build: **options-implied** (skew / term-structure /
  put-call / vol-risk-premium — forward-looking, priced by sophisticated players),
  **macro-surprise indices** (built from the actuals-vs-consensus the M28 P2 event
  subsystem already stores — near-free), **credit spreads** (HY-IG / OAS change as a
  risk-appetite gauge), **fund/ETF flows + short interest**, **on-chain** (exchange
  netflows, stablecoin supply), **seasonality/calendar**.

- **BP4 · Change the HORIZON.** We tested days-to-weeks. Try **months** (valuation
  mean-reversion is a multi-month phenomenon — weeks may be too short for value), or
  **event-windowed** — trade only in the tight window around a scheduled catalyst
  (CPI/FOMC/earnings), where signal-to-noise concentrates, reusing the M28 event
  calendar rather than trading continuously.

- **BP5 · Repurpose the machinery even if no standalone sleeve ships.** The
  valuation-snapshot + gate + PnL-harness stack isn't wasted on a null: feed the
  graded signals as **features into the existing ML conviction model** (`c_macro`
  overlay — a signal too weak to trade alone can still improve the ML book's sizing),
  or as a **reductive risk/veto overlay** (don't add a trade; shrink size when
  positioning is extreme against an existing position — a strictly lower bar than
  standalone alpha, the same shape as the news-influence sizing).

- **BP6 · Honest stop (a real, valuable result).** If BP1–BP5 are also exhausted,
  the recorded conclusion — "free-data macro/positioning signals don't carry
  tradeable weeks-horizon edge at our cost structure" — is itself worth having: it
  redirects effort to where the edge demonstrably is (exit refinement, ML, the
  existing ICT + pairs book) instead of building on a non-edge
  (`RESEARCH-RIGOR-STANDARD.md` § honest negatives).

Sequence at the checkpoint: BP1 (vol/relative-value — cheapest, reuses infra) and
BP2 (ensemble) run on the data already in hand; BP3/BP4 are the next builds; BP5 is
the salvage path; BP6 is the honest terminal. The operator picks the branch with the
Phase-B evidence in front of them — the fork is surfaced, not decided unilaterally.

## 7. Governance + autonomy

Tier-1 throughout research (docs, scripts, workflows, off-VM data, trainer-VM
relay); `src/sysdyn/*` stays import-linter-pure, all IO in `scripts/`. Tier-3 only
at S5 live graduation (operator + backtest gated) — so the whole S0→S3 loop runs
autonomously. `research-driver` governs (hourly status cadence, land outcomes in
the roadmap); the rigor standard binds every grade; the ledger records every
verdict; this doc is the map that keeps the pieces coherent.
