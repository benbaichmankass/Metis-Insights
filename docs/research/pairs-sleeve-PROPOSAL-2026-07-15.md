# PROPOSAL (Tier-3, operator-gated) — market-neutral crypto pairs sleeve

**Status:** DRAFT / PROPOSE-ONLY · **Tier:** 3 (new live strategy + a new execution
primitive) · **Date:** 2026-07-15 · **Evidence:** `docs/research/small-tf-directions-2026-07-15.md`
§ D2 (trainer runs #6498/#6500/#6501/#6506/#6509).

> This is a **drafted proposal**, not a wiring PR. Nothing here is built or merged.
> A market-neutral pairs sleeve touches real money and needs a new 2-leg execution
> path — it ships only with explicit operator approval, via the phased rollout below.

## 1. Why — the evidence (all validated, net-of-fee, walk-forward)

The M22 program was chartered to find a **reliable small-TF trading tool**. Market-neutral
crypto **cointegration pairs at 1h** is the answer — the one candidate that clears every gate:

| Gate | Result |
|---|---|
| Profitable (3yr, net taker 7.5bps) | SOL/BTC +541R, BNB/BTC +478R, ETH/BTC +454R, SOL/ETH +350R; 55–58% win |
| **OOS-validated** (train 2023-24 → held-out 2025-26) | +158 to +177R OOS, **no expectancy decay** (0.23–0.26R/trade), 58–60% win, OOS maxDD 6–15R |
| **Capital efficiency** (net_R / position-day) | **0.8–1.2 R per position-day** (OOS 0.93–1.12); mean hold ~5.7h; deployed ~40% of calendar time |
| Param-robust | lookback 15–20 / any entry_z strong; only lb=30 breaks |
| **Cointegration-stable** | half-life ~12h; mean-reverting in **100%** of 30-day windows over 3yr; no breaks; spread bounded 100% |
| **Funding-insensitive** | net funding differential ~0.5 bps/trade ≈ 2–4% of the edge (negligible, like fees) |
| Fee-insensitive | net_R barely moves taker→zero (R normalized by the wide spread-stop dwarfs fees) |
| Not OHLCV-blind | the edge is the cointegrated-spread relationship, not directional price prediction |

It sidesteps **both** root causes that killed every small-TF scalp (fee-drag +
OHLCV-blindness) and, unlike the wave-1 funding carry, it is **alive in the current
regime**. On the operator's original capital-efficiency metric it decisively beats
holding/cash and every scalp (which are net-negative per position-day).

## 2. What — the sleeve

A **spread mean-reversion** strategy on a cointegrated crypto pair:
- Compute the log-spread `s = log(A) − β·log(B)` with a rolling hedge-β (lookback 15 bars @1h).
- Enter when `|z| ≥ entry_z` (2.0): short-spread (short A / long B) when z high, long-spread when z low.
- Exit on mean-reversion `|z| ≤ exit_z` (0.5), an adverse-divergence stop `stop_z` (2.0σ = the R-unit),
  or a `max_hold_bars` timeout.
- Candidate pairs (ranked by OOS net/maxDD): **SOL/BTC, BNB/BTC** (best), ETH/BTC, SOL/ETH.
- Harness of record: `scripts/backtest_pairs.py` (params `lookback=15, entry_z=2.0, exit_z=0.5,
  stop_z=2.0, hedge_beta=rolling`).

## 3. The architectural reality — a 2-leg execution primitive (the real lift + main risk)

**This is where a pairs sleeve differs from every existing strategy.** The execution layer
(`intent_multiplexer` → `aggregate_intents` → `compute_execution_delta` → `coordinator.multi_account_execute`)
is built on **single-symbol intents**: one strategy → one symbol → one directional position.
A pair trade is **two simultaneous, opposite-direction legs on two symbols** that must open,
be managed, and close **together** — that shape has no home in the current model. Three
implementation options, in increasing order of intrusiveness:

1. **Isolated pairs executor** (recommended for v1) — model the sleeve like the prop bridge:
   its own executor that places both legs atomically, journals a single logical pairs-trade
   (two `order_packages` linked by a `pairs_id`), and manages the spread exit — WITHOUT
   touching `aggregate_intents`/`compute_execution_delta`. Lowest blast-radius; keeps the
   single-symbol invariants intact (the new-strategy skill's "if you're editing the aggregator,
   stop" rule). Requires a per-leg fill-confirm + a leg-imbalance guard (if one leg fills and
   the other rejects, flatten the filled leg — a naked directional position is the failure mode).
2. **Paired-intent correlation-id** — emit two linked `StrategyIntent`s with a shared id and
   teach the delta dispatcher to route them atomically. More reuse, but it changes the
   strategy-agnostic core — higher risk, needs its own design review.
3. **Synthetic single-symbol** — trade only the leg with the edge and treat the hedge as
   implicit. Rejected: it's no longer market-neutral (the whole point).

**Recommendation:** option 1. The 2-leg atomicity + leg-imbalance guard + spread-exit
management is the bulk of the engineering and the primary Tier-3 risk to weigh.

## 4. Account routing + the mandatory compatibility matrix

- **Symbols:** the pairs span ETH/SOL/BNB/BTC perps. `bybit_2` (real-money linear-perp) can
  hold these, but the sleeve needs BOTH legs of a pair tradeable on ONE account — confirm the
  account's `symbols:` cover each pair's legs before routing.
- **MANDATORY before any routing** (new-strategy + prop-architecture contract): run
  `scripts/prop/account_compat_matrix.py --strategy pairs_<A>_<B> --data <feed>` per candidate
  account; route only to accounts whose verdict is **ROUTE** (standard: positive net perf).
  The pairs harness is R-based, so the matrix run must use the $-translated sizing (below).
- **Sizing (Tier-3, RiskManager):** the sleeve carries no per-strategy risk (removed 2026-06-29).
  The $ risk per pair-trade = account `risk_pct` basis × confidence, applied to the **spread-stop
  distance** — and the 2-leg notional is `qty_A` on A + `β·qty_A` on B. The concurrency cap
  (how many pairs open at once; they share the BTC leg so they are correlated) is a risk-caps
  decision. This is the "$-return-on-2-leg-margin" translation the research flagged — it is a
  **sizing choice, made in the proposal**, not a research number.

## 5. Phased rollout (shadow → demo → real; each phase operator-gated)

Mirrors the S9 shadow-first path, adapted for the 2-leg primitive:

1. **P-A — Build the isolated pairs executor + a SHADOW soak** (Tier-2/3 review): the executor
   computes and **logs** the would-be 2-leg pairs-trade each tick (like the existing soaks:
   `/api/bot/allocator/soak`, `exit_ladder_soak`) but places **no order**. Proves the live
   spread/entry/exit computation matches the backtest on live data, and exercises the
   leg-imbalance/fill-confirm paths in dry mode. Weeks of soak.
2. **P-B — Demo execution** (`bybit_1` demo, real fills, paper money): route ONE pair (SOL/BTC,
   the best OOS net/maxDD) to the demo account with real 2-leg fills. Confirms fill realism,
   slippage, and the leg-imbalance guard on a real venue at zero money risk. This is the
   execution-realism validation the R-backtest can't provide.
3. **P-C — Real money** (`bybit_2`, Tier-3, operator-approved): after the demo soak confirms
   the backtest, route the proven pair(s) to `bybit_2` with the matrix-approved sizing +
   concurrency cap. Separate draft PR, revertible via one `pull-and-deploy`.

## 6. Open decisions for the operator (this proposal does NOT assume them)

1. **Proceed to build P-A** (the isolated pairs executor + shadow soak)? This is the first
   real engineering commitment.
2. **Execution-primitive choice** — option 1 (isolated executor, recommended) vs option 2
   (paired-intent core change)?
3. **Which pairs** to carry — start with SOL/BTC + BNB/BTC (best OOS), or all four?
4. **Concurrency + sizing caps** — the pairs share the BTC leg (correlated); how many
   concurrent pairs, and what per-pair risk basis?
5. **Account** — confirm `bybit_2` covers ETH/SOL/BNB/BTC legs, or scope to the pairs it does.

## 7. What stays parked
- **Xsec momentum** — gate-failed (4-symbol universe too small); needs a much wider universe.
- **Spot-perp basis** — the untested carry cousin; build-vs-park is a separate call.
- **Maker-executed neutral funding carry** (`PB-20260715-MAKER-CARRY`) — regime-gated, parked
  until funding elevates.

---
*Tier-3 proposal. Nothing in this document is built, merged, or routed to any account. The
sleeve executes real money only after the operator approves the phased rollout above.*
