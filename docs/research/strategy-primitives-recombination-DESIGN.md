# Strategy-primitives recombination — DESIGN (2026-06-18)

> **Tier-1 research/backtest tooling.** Nothing in this design touches the live
> order path, `config/strategies.yaml`, `config/accounts.yaml`, or any unit the
> live VM consumes. It is an *offline* combinatorial sweep over decomposed
> strategy primitives, gated by the existing k-fold + readiness-ladder tooling,
> that *proposes* new cells; any survivor wires to **demo** through the normal
> Tier-3 PR, exactly like the WS-C alt cells (PR #3941).
>
> Status: **DESIGN — for operator review before build.** First probes are
> trainer-gated and queued behind the running WS-C sweep (no rush on the VM).
>
> Origin: operator direction 2026-06-18 — *"mixing and matching different
> signals and different time frames from different strategies … a slightly
> lower-cost way to refine and repurpose existing research to see if we can get
> it to fit in different setups."*

## 1. Why this exists

The strategy book is grown so far by two moves: (a) **fan a proven family
across new symbols** (WS-C alt cells — same logic, new instrument), and (b)
**tune one family's params** (M8 sweeps). Both keep the *whole* strategy
intact. They never ask the cheaper question:

> A strategy is an **entry trigger + a regime filter + an exit manager + a
> timeframe**. Most of our research cost was spent *finding the good
> primitives*. Which **recombinations** of primitives we already validated —
> one family's entry with another's exit, on a third's timeframe — clear the
> readiness ladder?

This is lower-cost than net-new research because every primitive in the pool is
already coded, already candle-fed, and already has a fee/risk model. We are
re-using sunk research, not generating new geometry. It is also the natural
next step after the readiness ladder (`docs/strategy-readiness-ladder.md`): the
ladder tells us *which cells are paper_ready and why they missed live* — and the
"why" is almost always **one primitive** (e.g. trend_4h alts miss live only on
the recent chop regime → swap in a regime filter; ict_scalp 5m is fee-bleed →
swap in a maker exit). Recombination is the systematic version of those
per-cell refinement hypotheses already sitting in
`docs/claude/strategy-refinement-queue.json`.

## 2. What already exists (reuse, don't rebuild)

The substrate is entirely in the repo — this design adds an *orchestrator*, not
new strategy code.

- **The unit contract is already a clean seam.** Every strategy in
  `src/units/strategies/*.py` exposes the same pure function
  `order_package(cfg: dict, candles_df) -> {direction, entry, sl, tp,
  confidence, meta}` (`_base.py`). "Strategies are pure signal generators" with
  no execution coupling — so an entry geometry and an exit/stop policy are
  *already* separable concerns inside each unit; recombination is mostly a
  matter of exposing the levers each unit hard-codes.
- **The primitives already span the families.** Entry geometries:
  donchian-breakout (`trend_donchian`), HTF-pullback-continuation
  (`htf_pullback_trend_2h`), sweep→displacement→FVG (`ict_scalp`,
  `hf_displacement_cont`), VWAP-band reversion (`vwap`, `hf_vwap_revert`),
  range-fade (`fade_breakout_4h`, `turtle_soup`), squeeze-expansion
  (`squeeze_breakout_4h`), FVG-range (`fvg_range_15m`). Regime filters:
  ADX/regime head (`regime.py`, `config/regime_policy.yaml`), HTF-EMA bias
  (inside ict_scalp + the hf candidates), killzone/session gates. Exit
  managers: fixed R-multiple TP, ATR-trailing (`atr_stop_mult`/`trail_mult`,
  shared across trend + pullback), chandelier (mes/metals dailies).
- **The hf-prop research already did this by hand for one family.**
  `docs/research/hf-prop-strategy-research-plan-2026-06-16.md` +
  `src/units/strategies/hf_displacement_cont.py` / `hf_vwap_revert.py` took
  ict_scalp's geometry and **pruned/swapped its primitives** (hard HTF gate,
  killzone-only, ATR-scaled exits) into two research-only ROSTER candidates.
  Recombination generalizes that one-off into a *swept* process and folds those
  candidates in as pool members rather than bespoke modules.
- **The gate is already built.** `scripts/ops/m15_ws_b_fold_report.py`
  (anchored k-fold, net-of-fee 7.5/15 bps) + `scripts/ops/classify_strategy_tier.py`
  (reject / paper_ready / live_ready) already turn a `--emit-trades` log into a
  tier. A recombination is just *another* cell to tier — no new evaluation
  rubric. Survivors land in `strategy-refinement-queue.json` like any other
  paper_ready cell.
- **The portfolio engine already replays a ROSTER.**
  `scripts/backtest_system.py` replays many strategies over one history through
  the real `intents.py::aggregate_intents` netting — the harness in which a
  combined/confluence cell is evaluated against the live arbitration.

## 3. The decomposition model

A cell is a tuple of **four orthogonal primitives** plus a direction policy:

| Slot | What it decides | Pool (initial) |
|---|---|---|
| **Entry trigger** | *When* to enter + raw direction | donchian-breakout · htf-pullback · sweep→displacement→FVG · vwap-band-revert · range-fade · squeeze-expansion · fvg-range |
| **Regime filter** | *Whether* to take the trigger this bar | none · ADX-min (trend-only) · ADX-max (chop-only) · HTF-EMA-bias · killzone/session |
| **Exit manager** | *How* to leave (drives fee drag + R) | fixed-R TP · ATR-trail · chandelier · partial-ladder (ExitPlan) · **maker-band exit** |
| **Timeframe** | Bar cadence of the entry feed | 5m · 15m · 1h · 2h · 4h · 1d |
| Direction policy | side handling | both-sides · long-only · short-only |

Not every tuple is meaningful (a 5m donchian-breakout with a 1d chandelier is
incoherent), so the sweep is **constrained**, not a raw Cartesian blow-up — see
§4. The two highest-value axes, motivated directly by the current queue:

- **Regime-filter swaps** attack the *paper_ready→live* gap. Every trend/
  pullback alt in `SRQ-20260618-001/-002` misses live for the *same* reason —
  the recent chop fold — and the *same* fix — an ADX/regime entry gate. That is
  one new (entry, **regime-filter**, exit, tf) tuple per cell, swept in one pass.
- **Exit-manager swaps** attack *fee bleed*. `SRQ-20260618-003` rejected
  ict_scalp 5m because per-trade R sits inside the round-trip cost band. Holding
  the entry geometry fixed and swapping the exit (larger-R trail, partial-ladder,
  or a **maker-band post-only exit** that earns the rebate) is a direct,
  enumerable attack on the fee term rather than the signal.

## 4. The combinatorial sweep plan

A four-stage funnel that reuses the WS-C/M15 machinery end-to-end. Runs on the
**trainer VM** (autonomous), writes nothing to live.

1. **Pool + coherence mask.** Declare the primitive pool (§3) in a small YAML
   (`config/research/recombination_pool.yaml`, research-only). A static
   coherence mask drops incoherent tuples (timeframe/exit mismatches, a regime
   filter that nullifies its own entry). Expected live set after masking: low
   hundreds, not the raw product.
2. **Screening pass (single train/OOS split), like WS-C.** Replay each masked
   tuple on the BTC + validated-alt panel at 7.5 bps, one train/OOS cut. Keep
   only cells positive in *both* windows. This is the cheap filter — most tuples
   die here. Mirrors `m15-ws-c-alt-sweep` Method exactly (screening, **not**
   promotion evidence).
3. **K-fold gate + tier** on the screen survivors. Run the existing
   `m15_ws_b_fold_report.py` (5-fold anchored, 7.5/15 bps) → `classify_strategy_tier.py`.
   Each survivor gets a tier. `reject` is dropped with evidence; `paper_ready`
   and `live_ready` advance.
4. **Confluence/portfolio check** for any combined-signal cell (§5) via
   `scripts/backtest_system.py` so a recombination is graded against the **real**
   `aggregate_intents` netting + shared-account risk, not standalone — the same
   bar that the prop-firm tool applies. Then: a `paper_ready` survivor opens a
   new `SRQ-…` row and wires to **bybit_1 demo** through a Tier-3 PR (the PR
   #3941 pattern); a `live_ready` survivor is an operator-gated real-money
   proposal.

Orchestrator: `scripts/ops/recombination_sweep.sh` (to build) — a thin loop over
the pool that emits one `--emit-trades` log per tuple and pipes through the
existing fold-report + tier scripts. No new statistics; it is glue over proven
parts.

## 5. Two payoffs beyond "more cells"

- **Fee reduction is a first-class output, not a side effect.** The exit-manager
  axis makes "which exit minimizes round-trip drag for this entry" a *swept*
  question. The ExitPlan partial-ladder (already soaking, `/api/bot/exit-ladder/soak`)
  and a maker-band post-only exit enter the pool as exit primitives — so the
  ict_scalp fee-bleed reject becomes a search over exits rather than a dead end.
- **It feeds the AI conviction layer.** A "combined" cell — two entry triggers
  that must *agree* (confluence) before firing — is exactly a multi-signal
  conviction feature. The unified-confidence soak (`conviction_sizing` /
  `conviction_arbitration` logs) wants precisely this signal: *when N primitives
  concur, is the per-trade edge higher?* Recombination generates labelled
  confluence cohorts the shadow/decision models can learn from, tying the
  strategy-book expansion back to the AI-role expansion the audit is driving.

## 6. Risks / guardrails

- **Multiple-comparisons / overfitting.** A few-hundred-tuple sweep will throw
  false positives. Mitigations: the every-fold k-fold gate (not a single split),
  the 2×-fee headroom bar, a minimum-trade-count floor per fold (reuse the
  WS-C "thin" tagging), and — for any cell promoted past demo — an *out-of-pool*
  symbol/period holdout before a real-money proposal.
- **Correlation, not diversification.** Recombined crypto cells correlate with
  the existing book (the same caveat stamped on the alt cells). Account-level
  caps assume concurrent drawdown; the portfolio pass (§4.4) is where that is
  measured, not assumed.
- **Pool drift vs. live units.** The pool references live unit geometries; if a
  unit's logic changes, the pool YAML must track it. Keep the pool a thin
  *reference* to the units (import the same `order_package`), never a fork.

## 7. First concrete probes (trainer-gated, queued behind WS-C)

Smallest useful slice — does **not** wait on the full orchestrator:

1. **Regime-filter swap on the 10 paper_ready alt cells.** One new tuple per
   cell (their existing entry/exit/tf + an ADX/regime gate). Directly closes the
   `SRQ-20260618-001/-002` refinement hypotheses; re-tier with
   `classify_strategy_tier.py`. A clean every-fold pass = a real-money proposal.
2. **Exit-manager swap on ict_scalp's geometry.** ict_scalp/`hf_displacement_cont`
   entry × {larger-R trail, ExitPlan partial-ladder, maker-band exit} on the alt
   panel — the `SRQ-20260618-003` alternatives, run as a 3-exit mini-sweep.
3. **One cross-family probe.** htf-pullback *entry* × ADX-chop *filter* ×
   vwap-band *exit* — an explicit "pullback that scales out at the band" — as a
   proof the orchestrator+gate produces a tierable cell end-to-end.

Each probe pings the operator at its result (per the standing cadence), opens or
updates an `SRQ-…` row, and — on a paper_ready survivor — proposes the demo wire
as a Tier-3 PR.
