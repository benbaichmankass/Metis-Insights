# Decider — multi-account blend (S-STRAT-IMPROVE-S9)

**Date:** 2026-05-24 · **Status:** design locked, operator-approved
2026-05-24 · **Realization:** one funded account per live member (the
"multi-account blend") · **Evidence:** trainer runs #1893/#1895/#1901
(blend walk-forward) + `docs/audits/fade-breakout-complement-2026-05-24.md`.

## What the decider is

The North-Star payoff: combine several net-positive, low-correlation
strategy "members" into one book that is **smoother** (higher
return-per-drawdown) than any member alone. We validated this:
equal-weight blending of the members holds **out-of-sample** — combined
ret/DD beats every standalone with materially lower drawdown.

| Window | trend2h | fade4h | trend6h | COMBINED |
|---|---|---|---|---|
| OOS 2-way ret/DD | 1.48 | 1.07 | — | **1.73** (maxDD 12.2R vs 17.5/15.2) |
| OOS 3-way ret/DD | 1.48 | 1.07 | 2.24 | **2.01** (maxDD 8.8R) |

Realistic combined ret/DD is **~2** (the full-history ~7 was in-sample
inflation). Still a real, OOS-confirmed diversification win.

## Why multi-account (the load-bearing architecture decision)

`portfolio_combine` computes the blend by **summing two independent,
full-size trade streams**. The live execution layer does NOT do that on a
single account:

- `src/runtime/intents.py::aggregate_intents` holds **one net position
  per symbol per account**. Same-direction intents take
  `max(target_qty)` — "we do NOT sum, because summing would
  double-count exposure against the same risk budget." Opposing intents
  are resolved **priority winner-takes-all** (the loser is dropped, only
  logged).
- So routing trend + fade onto the *same* account yields a
  priority-arbitrated single position (mostly the higher-priority
  member), **not** the validated blend.

To reproduce independent streams you need **independent positions**, and
on Bybit linear perps (one-way mode = one net position per symbol per
account) that means **one account per member**. The bot already runs
multiple independent accounts (bybit_1 / bybit_2 / ib_paper), each with
its own strategy set and its own position — so the multi-account blend
needs **no execution-layer code change**; it is a config + capital + ops
pattern.

(Rejected alternatives: *single-account regime-switch* — under-delivers,
it's a time-switch not an independent blend; *aggregator size-blending* —
requires surgery to the execution-layer invariant + is still one net
position. See the 2026-05-24 decision thread.)

## Capital & risk model

The equal-weight blend = **equal capital per member account**, each at the
same per-strategy `risk_pct`. Splitting capital N ways across N member
accounts (each risking the same fraction of its slice) keeps total book
risk ≈ one strategy on the full capital — i.e. the `portfolio_combine`
"weighted (same total risk)" column, which is the curve we validated.
Regime-conditional capital weighting (tilt toward the member suited to
the current regime) is a **future upgrade**, not part of v1.

## Member → account map

| Member | Account | Status |
|---|---|---|
| `trend_donchian` (2h) | `bybit_2` | **live** (real money) |
| `fade_breakout_4h` | `bybit_3` (new) | pending: shadow-proof → fund → promote |
| (future) slow/6h-trend, … | `bybit_4` … | pending validation |

## Activation checklist (per new live member)

1. **Shadow-prove.** Member runs `execution: shadow` on the demo account
   (bybit_1) until live shadow data confirms the backtest expectancy
   (weeks). (`fade_breakout_4h` is here now.)
2. **Fund a dedicated account.** Operator creates + funds a new Bybit
   sub-account; keys land via the `rotate-account-keys` workflow
   (`BYBIT_API_KEY_3` / `BYBIT_API_SECRET_3`). *(Real-world step — the
   one thing Claude can't do; everything else is a workflow.)*
3. **`config/accounts.yaml`** — add the account (Tier-3, operator-
   approved PR). Template:
   ```yaml
   bybit_3:
     type: regular
     exchange: bybit
     api_key_env: BYBIT_API_KEY_3
     mode: live                 # real money — independent position
     market_type: linear
     strategies: [fade_breakout_4h]   # ONE member -> independent stream
     symbols: [BTCUSDT]
     risk:
       max_dd_pct: 0.05
       daily_usd: 100
       pos_size: 500
       risk_pct: 0.3            # same as bybit_2 -> equal-weight blend
       min_balance_usd: 50
       leverage: 3
   ```
4. **`config/strategies.yaml`** — flip the member `execution: shadow →
   live` (Tier-3, operator-approved PR).
5. **Deploy.** `pull-and-deploy`; confirm the member opens its own
   position on its account and the blend's two streams run independently.
6. **Monitor.** Per-account PnL + the combined-book drawdown vs the
   single-strategy baseline.

## Open items / risks

- **Capital fragmentation:** N accounts split capital → each account's
  min-notional / fee tier applies on a smaller balance. Size members so
  each clears Bybit min-order + the `min_balance_usd` gate.
- **Account ceiling:** Bybit sub-account limits + operational overhead
  grow with members; revisit beyond ~3–4 members.
- **Decider v2 (regime weighting):** once ≥2 members are live, test
  tilting capital by regime (the `btc-regime-*` classifiers already
  exist) vs static equal weight.

## Next step

`fade_breakout_4h` is the first member queued for this path — it is
collecting live shadow data on bybit_1 now. Promotion (steps 2–5) waits
on that data confirming the edge. No real-money action is taken until then.
