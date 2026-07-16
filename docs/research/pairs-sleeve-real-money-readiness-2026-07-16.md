# Pairs sleeve → real-money readiness (2026-07-16)

**Status:** planning. The paper go-live (bybit_1) validated the signal + execution
logic and surfaced the min-qty half-placement bug (fixed by the pre-placement gate,
#6591). This doc enumerates everything the sleeve needs BEFORE it can route to a
**real-money** account (bybit_2), so real-money go-live is a checklist, not another
bug-discovery loop.

## The core insight — why paper masks the real problems

The paper account `bybit_1` carries a **~$166k inflated demo balance**, so
`risk_budget = balance × risk_pct (0.015) ≈ $2,500` per pair — big enough that most
legs clear the exchange minimum lot. A **real** account holds real money (bybit_2 is
currently small, low hundreds of $). At a realistic real balance, `risk_pct × balance`
is tiny and **most pairs can't clear the venue minimum at all** — and the ones that
can are dominated by min-qty *rounding drag* the R-space backtest never modelled.

So real-money readiness = **"make the sleeve tradeable AND correct AND provably
net-positive on a SMALL real balance."** Every gap below flows from that.

## Current real-money account (bybit_2)

- `mode: live`, `account_class: real_money`, `risk_pct: 0.015`
- `symbols: [BTCUSDT, ETHUSDT, XRPUSDT, ADAUSDT]` — **no SOL, no BNB**
- risk block: `max_dd 0.05, daily_loss 0.05, daily_usd 100, leverage 3`

## Gap analysis

| # | Gap | State | Tier |
|---|---|---|---|
| **G1** | **Min-qty-aware sizer.** #6591 *refuses* a sub-min pair (safety) — but refuse-everything ≠ tradeable. Real money needs a sizer that scales the pair UP to clear both legs' min-qty *while preserving the β-hedge*, and refuses only when that scale would exceed the risk budget. | **Missing** | 3 (order-path) |
| **G2** | **$-and-lots backtest.** `backtest_pairs.py` is R-space + net-of-fee only — proves the *edge*, not survival of min-qty rounding on a small $ balance. Need a `$`-and-lots sim: real balance + `risk_pct` + venue lot table → floored qtys + real fees → net `$` and the *skip_size fraction* at that balance. This is the "will it make money on \$X real" answer. | **Missing** | 1 (research) |
| **G3** | **Symbol coverage.** bybit_2 trades BTC/ETH/XRP/ADA; the current pairs use SOL+BNB. **Only ETH/BTC fits bybit_2.** Real-money pairs are constrained to the account's symbols → either extend `bybit_2.symbols` (Tier-3) or find cointegrated pairs *within* BTC/ETH/XRP/ADA (the N5 universe-scan, scoped to this set). | **Mismatch** | 3 (config) |
| **G4** | **Account→ruleset compat matrix.** CLAUDE.md makes `scripts/prop/account_compat_matrix.py` mandatory before routing a strategy to an account (standard acct → net-of-fee perf). Not yet run for the pairs sleeve vs bybit_2. | **Not run** | 1 |
| **G5** | **Real/paper PnL isolation for a 2-leg sleeve.** Legs are journaled `pairs_<name>_a/_b`. On a real_money account confirm: both legs reconcile/close together; the sleeve's PnL doesn't corrupt real-money KPIs (`/strategy/attribution` excludes reconciler artifacts; per-leg local-compute vs bybit broker-truth netting). | **Verify** | 1 |
| **G6** | **Post-placement imbalance unwind + alert.** The gate stops PRE-placement half-placement. But if leg A fills and leg B is rejected *post-submit* (transient), `_unwind_legs` must actually FLATTEN leg A and fire a loud operator alert. **On paper the unwind did NOT flatten the BNB leg** — that's a real bug; on real money a naked leg is real risk. | **Broken (observed)** | 3 (order-path) |
| **G7** | **Risk-cap routing.** The pairs executor is an ISOLATED order path (`run_pairs_tick`, NOT `multi_account_execute`), so it sizes off `risk_pct` directly — does it also honour the account's **daily-loss / max-DD** caps? Likely **not** (it bypasses the per-account RiskManager guardrails). On real money the sleeve must respect the same daily-loss/DD stop as every other strategy. | **Likely gap** | 3 (risk) |

## Build order (highest-leverage first)

1. **G6 — fix the unwind + alert** (correctness; we already saw it fail). *The one active bug.*
2. **G1 — min-qty-aware sizer** (extends `pairs_sizing.pair_notionals`; pure fn + tests).
3. **G2 — $-and-lots backtest** → run on bybit_2's real balance + venue lots; report net-$ + skip fraction. *The go/no-go evidence.*
4. **G7 — risk-cap routing** (route the pairs path through the account daily-loss/DD guard).
5. **G5 — PnL isolation verify** (2-leg rows on a real account).
6. **G4 — compat-matrix run** (mandatory pre-routing gate).
7. **G3 + N5 — universe-scan scoped to {BTC,ETH,XRP,ADA}** → which cointegrated pairs fit bybit_2.
8. **(Tier-3, operator) wire pairs → bybit_2** in `accounts.yaml` + re-flip.

G1/G2/G4/G5 are Tier-1 (build now). G6/G7 touch the order/risk path (Tier-3 — build +
propose). The final account wiring (step 8) is the operator's go-live flip.
