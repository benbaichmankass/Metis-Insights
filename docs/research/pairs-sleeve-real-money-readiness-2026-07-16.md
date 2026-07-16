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

## G2 FINDINGS (2026-07-16) — the sleeve is NOT real-money viable as specified

G2 (`scripts/research/pairs_dollar_lots.py`) ran on real 1h candles (15m CSVs
resampled) for all 4 live pairs at bybit_2's real `risk_pct=0.015`
(trainer-diag #6612). **The strong R-space edge does not survive translation to
dollars-and-lots.**

| pair | R-space net_R | $ @ $500 | $ @ $5k | $ @ $50k | $ @ $166k |
|---|---|---|---|---|---|
| SOL/BTC | +806 (win 58%) | skip 100% | skip 85% −$12 | skip 17% −$547 | skip 6% **−$2178** |
| BNB/BTC | +790 (win 58%) | skip 100% | skip 85% −$24 | skip 18% −$856 | skip 5% **−$3219** |
| ETH/BTC | +877 (win 59%) | skip 100% | skip 80% −$67 | skip 12% −$1373 | skip 4% **−$4658** |
| SOL/ETH | +540 (win 56%) | skip 99.4% +$0.40 | skip 44% −$81 | skip 4% −$1237 | skip 1.5% **−$4033** |

**Two hard facts:**

1. **Un-placeable on a small real balance.** Below ~$1k the sleeve skips
   **~100%** — the BTC leg (min 0.001 ≈ $118 notional) floors sub-min at
   `risk_pct × balance` sizing. bybit_2 is low-hundreds-of-$ ⇒ it would place
   essentially nothing.
2. **Net-NEGATIVE where it places, worsening as it places more.** As balance
   rises and skip% falls, net-$ gets *more* negative (−$0.03/trade at $5k →
   −$0.84/trade at $166k for SOL/BTC). The full-participation ($166k, ~95%
   placed) figure — the closest to the "true" executable edge — is deeply
   negative for every pair.

### This reframes the build order

- **G1 (min-qty-aware scale-up) is COUNTERPRODUCTIVE here, not the fix.** Its
  premise is "place more pairs by scaling to min-viable" — but placing *more*
  of these trades **loses more money**. The G1 code is built + tested and stays
  behaviour-preserving (`max_risk_multiple=1.0` = the current skip), but raising
  its tolerance for these 4 pairs would be *wrong*. G1 only becomes useful for a
  pair whose $-economics are actually positive.
- **The real gate is the sleeve's $-economics, which are currently negative.**
  The R-space `net_R` overstated the edge. Prime suspects for the R→$ gap:
  (a) the **fixed-entry-β hold vs rolling-β spread** idealization — R-space
  re-hedges β every bar; the live executor holds a fixed entry-β, so a drifting
  true β leaves an unhedged directional residual that bleeds; (b) **stop
  slippage** — R-space exits at the theoretical `stop_spread`, the $ sim (and
  live) exits at the market bar-close past it; (c) fees on the real notionals.
  The `--ideal-no-floor` diagnostic (trainer-diag #6615) isolates (a)+(b)+(c)
  from lots: a net-negative *ideal* (no lot floor, hedge intact) proves the edge
  fails on real fixed-β execution independent of min-qty.

### Revised status

- **Real-money routing to bybit_2 is OFF THE TABLE** until the sleeve is
  net-positive in $ on real execution. G2 has done its job: it caught this
  **before** any real money was routed (the whole point of the readiness build).
- **Paper (bybit_1) should also be scrutinized** — the fixed-β-hold economics
  apply on paper too; the live paper soak is the real-world confirmation. If the
  ideal-no-floor is negative, the sleeve likely loses on paper as well, and the
  D2 validation needs revisiting (it was R-space, rolling-β).
- **Next real work is DIAGNOSIS, not routing:** confirm the R→$ gap driver
  (#6615), then decide whether the sleeve is fixable (re-hedge to track β live? a
  tighter cointegration/half-life screen? wider entry-z to cut stop-slippage
  churn?) or should be demoted. This is a Tier-3 strategy question for the
  operator, backed by the G2 numbers.

### G2 ideal-no-floor decomposition (2026-07-16, trainer-diag #6615) — it's FEES

The `--ideal-no-floor` run (no lot constraint, hedge intact) decomposes the R→$
gap cleanly. Per pair, ideal fixed-β-hold $ P&L:

| pair | R-space net_R | ideal **fee-free** $ | ideal **7.5bps** $ | ⇒ fee cost |
|---|---|---|---|---|
| SOL/BTC | +620 | **−$295** | −$3179 | ~$2884 |
| BNB/BTC | +825 | **+$357** | −$3409 | ~$3766 |
| ETH/BTC | +636 | **−$855** | −$5194 | ~$4339 |
| SOL/ETH | +513 | **+$1279** | −$3443 | ~$4722 |

**Three-layer teardown of the edge:**

1. **R-space (rolling-β) → fixed-β gross:** the rolling-β R-space `net_R`
   (+513…+825) is **optimistic** — it re-hedges β every bar. Held at a fixed
   entry-β (what the live executor does), the *gross* (fee-free) edge collapses
   to **~breakeven, mixed sign** (−$295 / +$357 / −$855 / +$1279). The rolling→
   fixed-β idealization eats most of the apparent edge.
2. **Gross → net (fees):** 7.5 bps round-trip taker × **two legs** × ~2800
   trades is ~$2.9k–4.7k of fees per pair — which tips every pair, even the
   fee-free-positive ones (BNB/BTC, SOL/ETH), **deeply net-negative**. Fees are
   the dominant killer, ~$1.1–1.8 per trade vs a ~$0.5 gross edge.
3. **Lots:** min-qty flooring is a *third*, secondary layer — it makes the sleeve
   un-placeable below ~$1k (skip ~100%) but the ideal (no-floor) is already
   net-negative, so lots are not the primary problem.

**This is the same conclusion as the whole small-TF / chop-scalp program: the
gross edge is real but thin, and round-trip taker fees sink it.** (See the
maker-fee economics thread, `docs/research/small-tf-directions-2026-07-15.md`
Phase 1.) The implications:

- **Real-money viability needs BOTH** (a) recover the gross edge lost to fixed-β
  execution — **re-hedge live to track the drifting β** (the executor currently
  holds a fixed entry-β; a periodic re-hedge or a Kalman-β would close most of
  the rolling→fixed gap), or a tighter cointegration/half-life screen that only
  trades pairs whose β is stable — AND (b) **maker execution** (post-only limit
  entries → ~0/rebate Bybit maker fees), the same deferred `maker_band_post_only`
  fix the small-TF study flagged. Neither alone suffices: fee-free is still mixed,
  and maker fees on a negative-gross pair (SOL/BTC, ETH/BTC) still lose.
- **Best gross-edge candidates** (fee-free positive, so maker-fees *could* flip
  them): **BNB/BTC (+$357)** and **SOL/ETH (+$1279)**. SOL/BTC and ETH/BTC are
  negative even fee-free → not fixable by fees alone; they need the β-tracking
  fix or a demote.
- **The current paper soak (bybit_1) will also be net-negative** on these
  economics — the sleeve loses on paper too, not just real money. The D2
  validation (R-space, rolling-β) was optimistic; it should be re-run with the
  fixed-β + maker-fee model before the sleeve advances anywhere.

**Recommendation (Tier-3, for the operator):** hold all 4 pairs at `shadow`
(3 already are; SOL/ETH is the one still `live` on paper — consider demoting it
too, since its paper economics are net-negative under real fixed-β+taker). Do
**not** route to real money. The forward path is a research question — re-hedge/
Kalman-β + maker execution — not a wiring task; G1/G4/G5/G7 are moot until the
sleeve is net-positive in $.
