# Decider — single-account smart selection (S-STRAT-IMPROVE-S9)

**Date:** 2026-05-24 · **Status:** design (corrected) · **Supersedes:** the
multi-account-blend design (PR #1902, closed) · **Operator direction:**
2026-05-24.

## Architecture (corrected — the load-bearing decision)

**One account, one fund, all strategies running, and a decider layer that
picks (or combines) the single highest-probability-of-profit trade at each
moment.** Funds are NOT divided across accounts. Concretely:

- **One pot of capital, used maximally** — not split per strategy with
  returns merely averaged. The decider concentrates the fund on the best
  available opportunity each tick.
- **bybit_1 (demo) and bybit_2 (live) are mirrors** — same roster, same
  decider, same gates. bybit_1 is the paper shadow of bybit_2.
- **MES stays on its own account (IBKR)** for now — a different broker /
  asset class, not a redundant split of the crypto fund. Crypto may move
  to IBKR later to unify everything on one account.

This *replaces* the earlier (wrong) multi-account-blend idea, where each
strategy had its own funded account. That divided the fund and just
agglomerated independent streams — the opposite of the goal.

## The decider IS the intent aggregator (today crude)

The execution layer already holds **one net position per symbol per
account** (`src/runtime/intents.py::aggregate_intents`). So a single
account with several strategies already routes through a decider — it's
just **crude today: static priority** (highest-priority strategy wins
conflicts; same-direction takes max target_qty). Decider-v2 = make that
selection **smart** (highest P(profit) / regime-aware), not static.

## Decider-v2 — the finding that shapes it

Single-account simulation (`scripts/research_decider.py`, trainer #1914),
3 BTC members (trend 2h, fade 4h, squeeze 4h), 6yr:

| | net R | maxDD | ret/DD |
|---|---|---|---|
| SUM (idealized blend, all at full size) | +144.6 | 24.9 | 5.81 |
| GREEDY (one fund, one position, naive) | +63.9 | 17.0 | 3.75 |

- One fund is **viable** (ret/DD 3.75, strong) but a **naive** decider
  lets the **trend hog the book** — fills were trend 517 / squeeze 49 /
  fade 58. The high-frequency, long-held 2h trend occupies the account
  most of the time and **crowds out the diversifiers**, forfeiting ~half
  the blend's return and much of the diversification.
- **So the decider's job is genuine selection:** sometimes *skip* a
  trend trade to take a higher-probability fade/squeeze, or regime-route,
  to recover toward the +144R / 5.81 ceiling. That is the operator's
  "knows what trade to follow by highest probability of profitability."

## v2 approach (escalating; pick the winner by simulation)

Measured on the single-account curve via `research_decider.py`:
1. **Static priority** (today's crude baseline).
2. **Regime-rule selection** — follow the regime-fit member (trend in
   high-ADX, fade/squeeze in chop/post-compression); skip the off-regime
   member even if it fired, freeing the account for the right one.
3. **Selection model** — score each candidate signal's P(profit) given
   context and follow the max. This is where **models-in-the-loop**
   belongs (cross-strategy "which signal to trust now" — NOT the
   per-strategy entry filter, which failed: the edge is exit-driven).

Deploy the winner as the live aggregator's selection logic (one account;
no per-strategy capital split). Regime/selection is research-now,
deploy-once-≥2-members-are-live.

## Activation path (single account)

1. **bybit_2 runs the same full roster as bybit_1.** Today bybit_2 =
   trend only; mirror it to the full set so the decider sees all
   candidates. **Execution gates keep the negative strategies (turtle,
   vwap, ict_scalp) in `shadow`** so they never trade real money — only
   proven members (`execution: live`) can fire. (Tier-3, operator-
   approved.)
2. Strategies graduate `shadow → live` as their live data confirms the
   backtest (fade, then squeeze).
3. Decider-v2 selection logic replaces static priority once ≥2 members
   are live.

## MES / cross-asset (separate book, IBKR)

Same strategy families re-tuned per symbol (BTC params don't transfer —
crypto-specific). Clean 1m S&P 500 data sourced + cached
(`data/SPX500_1m.parquet`, 2020-2026, Dukascopy). SPX-trend is net-
positive (+29.6R) and **near-uncorrelated with the BTC book (corr
0.009)** → strong portfolio-level diversification once IBKR is live.
This is a separate book (different broker/asset), consistent with "don't
divide the *crypto* fund."
