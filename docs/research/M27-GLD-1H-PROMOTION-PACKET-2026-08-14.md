# M27 GLD 1h — Tier-3 promotion packet (2026-08-14)

> **Tier-3. This packet proposes; it does not ship.** Wiring a new strategy leg
> to `execution: live` is operator-approved per `docs/CLAUDE-RULES-CANONICAL.md`
> § Permission Tiers. Lane 3 of [`WORKPLAN-2026-08-14.md`](./WORKPLAN-2026-08-14.md).
>
> **Recommendation: SHADOW-first, not live — and the reason is n, not the edge.**

---

## 1. What is actually being proposed

`ict_scalp` applied to **GLD at 1h**, on an **existing Alpaca account**. It does
not exist today.

⚠️ **Do not confuse this with `gld_pullback_1h`.** That is a *different*
strategy (the pullback family), it already exists, it is `enabled: true` +
`execution: live`, and it is routed to `alpaca_paper`, `alpaca_portfolio` and
`alpaca_live`. Verified in `config/strategies.yaml`, because the names are one
word apart and conflating them would make this packet argue for something already
running. The scalp family is `ict_scalp_<symbol>_<tf>`; the leg proposed here
would be **`ict_scalp_gld_1h`**, and no such key exists.

## 2. The evidence

`docs/research/M27-P0-repull-followups-2026-07-21.md`, equities 1h batch,
Yahoo `--interval 1h --period max`, 3,480 bars/symbol, 2024-07-22→2026-07-20
(~2y), `--fee-bps-roundtrip 3.0`, anchored 4-fold walk-forward.

| Symbol | Trades | Gross totalR | Kfold baseline | **Net totalR (OOS)** | **Net exp (OOS)** | Verdict |
|---|--:|--:|:--:|--:|--:|---|
| **GLD** | **18** | +5.59 | **4/4** | **+5.92** | **+0.4933** | ✅ **STRONG PASS** |
| SPY | 13 | +1.41 | 3/4 | +1.95 | +0.195 | ✅ PASS (weak) |
| TLT | 12 | +2.46 | 3/4 | +0.55 | +0.05 | ✅ PASS (weak) |
| SLV | 20 | +2.56 | 2/4 | +2.58 | +0.1613 | ⚠️ mixed |
| USO | 24 | +2.80 | 2/4 | +0.44 | +0.022 | ⚠️ mixed |
| IWM | 21 | +2.14 | 2/4 (baseline neg.) | −1.52 | −0.0894 | ⚠️ mixed |
| QQQ | 14 | −1.69 | 1/4 | −3.5 | −0.3182 | ❌ reject |
| GDX | 25 | +0.26 | 3/4 | −0.56 | −0.0243 | ❌ reject |
| IEF | 6 | +3.11 | 2/2 valid | +1.64 | +0.82 | ❌ underpowered |

GLD is the strongest non-crypto cell M27 found, and it is the only equity/ETF
symbol clearing **4/4** folds. The roadmap already flags it as "the standout
uncommitted P4 candidate".

## 3. Why the recommendation is shadow and not live

**The edge is not the problem. The denominator is.**

**18 trades over ~2 years** — roughly **9 trades/year**, and across a 4-fold
walk-forward that is **~4–5 trades per fold**. "4/4 folds positive" over 4-5
trades each is a much weaker statement than the phrase suggests: a single
adverse trade per fold could flip several of them. The headline
**+5.92R net** is the sum of 18 numbers.

This is the same failure mode the workplan flags twice elsewhere and it should
not get a pass here because the verdict is favourable:

- `ict_scalp_5m` shows 30d `expectancyR` **+0.836** — carried by **4 trades**.
- `alpaca_portfolio` has **16 closed trades in 30d** against 16 routed strategies
  (Lane 2), which is what blocks the activation decision there.

A rule applied only to unfavourable results is not a rule. **18 trades does not
license real money**, however clean the folds look.

## 4. Two things to resolve before even the shadow wiring

**(a) 1h was chosen for DATA, not for strategy logic — and this leg would be the
first scalp leg above 15m.**

The Rig section of the findings doc is explicit: Yahoo's ~60-day cap "only
applies to 5m/15m/30m/90m, not 60m/1h", so the 1h pull was the fix for
`PB-20260721-M27-EQUITIES-DATACAP`. The interval was selected because it returned
~2 years of bars, not because 1h is where this setup was expected to live.

Every scalp leg currently in `config/strategies.yaml` is 5m or 15m —
`ict_scalp_5m`, `..._sol_5m`, `..._xrp_5m`, `..._avax_5m`, `..._xrp_15m`,
`..._eth_15m`, `..._sol_15m`, `..._mgc_15m`. **A 1h scalp would be the first of
its kind in this system.** That does not invalidate the result, but it does mean
the packet cannot claim the leg is "the same strategy that works elsewhere,
applied to a new symbol" — it is the same *code* at a timeframe the family has
never run, and the 18-trade count is itself a symptom of that (a scalp firing 9
times a year is not behaving like a scalp).

**Open question the operator or a follow-up must answer:** is the GLD edge a
*scalp* edge, or is `ict_scalp` at 1h effectively a different setup that happens
to be profitable on gold? The cheapest discriminator is running GLD at 15m on a
non-Yahoo source with real history and seeing whether the edge survives at the
family's native timeframe.

**(b) The compat matrix is mandatory and I found no artifact for the ETF family.**

`scripts/prop/account_compat_matrix.py` is required by both the `backtesting` and
`new-strategy` skills before a strategy is routed to an account it was not
evaluated against. The only compat outputs on disk are the 2026-06-17
perp-validation SOLUSDT set (`runtime_logs/prop_eval/…/solusdt_compat/`).
Absence of the artifact is not proof the work was skipped — but it is not proof
it was done, and the skill requires it either way.

## 5. Proposed path (each step gated on the previous)

1. **Author the leg as `ict_scalp_gld_1h`, `execution: shadow`**, routed to
   `alpaca_paper` — an existing account trading US ETFs. Per `ROADMAP.md` § B
   this is a **routing** decision on an existing venue, explicitly *not* a new
   integration: "reuse them; do not add a broker."
2. **Run `account_compat_matrix.py`** for that leg against the target account.
   No routing without it.
3. **Derive its own regime cells from its own evidence.** The findings doc calls
   this out directly off IWM's baseline/gated split: "any future leg must derive
   its own regime cells from its own evidence, never borrow BTC's."
   `BL-20260730-DONCHIAN-COSMETIC-SHORT-CELLS` is the anti-pattern to avoid —
   do not author a cell to make a table look complete.
4. **Soak until n is defensible.** At ~9 trades/year, a shadow soak reaching
   even 40–50 trades is a **multi-year** proposition on GLD alone. **State that
   plainly rather than let a soak run indefinitely without a stated target** —
   if the answer is "the evidence will never arrive at this rate", that is a
   finding, and it argues for resolving §4(a) (find the family's native
   timeframe) instead of waiting.
5. **Only then** a Tier-3 `execution: live` proposal, and only on a paper account
   first.

## 6. What this packet does NOT recommend

- ❌ Wiring `ict_scalp_gld_1h` directly to `execution: live`.
- ❌ Routing it to `alpaca_live`. That account holds **$0.10** and is `dry_run`
  (Lane 2); routing a 17th strategy to it changes nothing.
- ❌ Borrowing BTC's regime cells, or authoring cosmetic ones.
- ❌ Adding a broker or a new integration. GLD trades on accounts that exist.

---

**Provenance:** evidence table verbatim from
`docs/research/M27-P0-repull-followups-2026-07-21.md` (2026-07-21);
strategy-existence and routing checks read from `config/strategies.yaml` +
`config/accounts.yaml` on 2026-08-14; compat-artifact search over
`runtime_logs/prop_eval/` and repo-wide `*compat*` on the same date.
