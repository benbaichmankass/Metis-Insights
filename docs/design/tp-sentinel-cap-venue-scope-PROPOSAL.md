# Should a Bybit-derived take-profit cap bind non-Bybit venues?

**Status: PROPOSAL — Tier-3, order geometry. Nothing here is applied.**
**Date:** 2026-08-24 · **Requested by the operator** (2026-08-24) after the item
was surfaced and parked by the preceding session.

Every number below is measured from `config/strategies.yaml` +
`config/accounts.yaml` + the units on disk at `69dc0d7`. **No config was
changed, no order path was touched, and no leg was demoted.**

---

## 1. What the code does

`_TP_SENTINEL_CAP_PCT = 0.099` clamps the take-profit to ±9.9% of entry:

```python
# src/units/strategies/trend_donchian.py:388
tp = min(entry * (1 + _TP_SENTINEL_CAP_PCT), entry + tp_r * risk)
```

The constant's own comment names its origin: *"Bybit (and most exchanges)
reject TP further than ~10%"* — i.e. **Bybit's ErrCode 10001 boundary**.

**It is applied in the STRATEGY SIGNAL BUILDER, before the account is known.**
`trend_donchian.py:388` runs while building the signal; account routing happens
downstream in `Coordinator.multi_account_execute`. So the clamp is **venue-blind
by construction** — every account a signal fans out to inherits a ceiling
derived from one venue's API limit.

This is not a bug report about the clamp being *wrong*. On Bybit it is exactly
right, and it is why `execute_pkg` does not eat a 10001 rejection. The question
is only whether its **scope** should be the venue that motivated it.

## 2. Blast radius — measured, not estimated

⚠️ **State the population.** This counts **(account, leg) routings**, not legs:
one leg fanned out to four accounts is four routings, and the clamp applies per
routing. Legs are counted where noted, and the two are not interchangeable.

Scope: the 4 units that carry the constant — `trend_donchian`,
`htf_pullback_trend_2h`, `fade_breakout_4h`, `squeeze_breakout_4h` — restricted
to **enabled** legs, resolved through the **account-side** `strategies:` lists.

| venue | routings | share |
|---|---|---|
| `bybit` | 27 | 30.0% |
| `alpaca` | 52 | 57.8% |
| `interactive_brokers` | 8 | 8.9% |
| `breakout` (prop) | 3 | 3.3% |
| **total** | **90** | |

**63 of 90 routings (70.0%) carry a Bybit-derived ceiling on a non-Bybit venue.**

⚠️ **A first draft of this section reported ZERO, and it was wrong.** The probe
read a per-strategy `accounts:` key that is empty on every leg — routing is
declared **account-side**. An empty result read as a clean negative is
`diagnostic-provenance-guard` sub-class **C**, and it would have retired this
item as a non-issue. Recorded because the finding nearly died of its own probe.

## 3. What is actually LIVE, which is a much smaller claim

Routing count is not exposure. Filtering to accounts that can place an order
today (`mode: live`):

| account | venue | class | mode | verdict |
|---|---|---|---|---|
| `alpaca_live` | alpaca | real_money | **`dry_run`** | places nothing |
| `alpaca_paper` · `alpaca_portfolio` · `alpaca_options_paper` | alpaca | paper | live | paper |
| `ib_paper` | interactive_brokers | paper | live | paper |
| **`breakout_1`** | **breakout** | **prop** | **live** | **⚠️ real tickets** |

**So exactly one live, money-bearing, non-Bybit path is affected today: the
prop account.** Everything else is paper or dry. Any framing of this as "63
live routings are mis-clamped" would be false, and the difference matters for
how urgent it is (it is not urgent) versus how *sharp* it is (it is sharp).

## 4. Where it bites, and the part that is provable

`breakout_1` routes three legs, and **all three declare a real target of
`tp_r: 6.0`** — not the `50.0` sentinel. Per
`scripts/research/target_reachability_report.py` (run at `69dc0d7`):

| leg | tp_r | cap_r | verdict |
|---|---|---|---|
| `trend_donchian_sol_prop` | 6.0 | **3.22** (sibling-derived) | **COSMETIC** |
| `trend_donchian_eth_prop` | 6.0 | **4.08** (sibling-derived) | **COSMETIC** |
| `eth_pullback_prop_2h` | 6.0 | — | `no_cap_basis` — *we did not look* |

On two of the three, the declared 6.0R target is **byte-identically nothing**:
the clamp gets there first, so the YAML reads as a deliberate 6R decision and
the book behaves as if no target were declared. The third is **not** graded
reachable — it has no measured cap, which is a different state and is not
folded in.

⚠️ **`cap_r` here is a MEDIAN over the leg's trades.** ATR varies per trade, so
`COSMETIC` means *clamped on more than half the trades*, never *never reached*.

## 5. ⚠️ The load-bearing unknown — stated rather than assumed

**Nobody has established that Breakout imposes a ~10% take-profit limit, and
this proposal does not claim it does not.**

A prop "order" on this system is not an API call at all: `breakout_1` emits a
`prop_signal` **Telegram ticket** that a human places on a terminal
(`src/prop/`). There is no `set_trading_stop`, so there is no ErrCode 10001 to
avoid. Whether the *terminal* or Breakout's *rules* cap a target is a question
about that venue's contract, and I have no evidence either way.

That asymmetry is the whole proposal: **the clamp is applied to prop without
anyone having checked whether prop needs it**, and its 2 provably-cosmetic legs
are the cost of not having checked.

## 6. Options

**A — Do nothing.** Defensible: the affected live surface is one prop account,
the clamp is conservative (it only ever *tightens* a target), and a wrong
loosening places an unreachable target on a real ticket. Cost: two prop legs go
on declaring a 6R target that is nothing, and the YAML keeps lying about intent.

**B — Make the cap a declared per-venue property (recommended shape).** Move
`0.099` out of the four unit files into one owner keyed by venue, defaulting to
**0.099 for every venue** so today's behaviour is byte-for-byte unchanged, and
let a venue declare otherwise once evidence exists. This is the
`collapsed-state` shape the repo already uses: `capped(pct)` / `uncapped` /
**`unknown` — we did not look**, with `unknown` resolving to the *safe* 0.099.

⚠️ **B is structurally blocked today**, and this is the honest blocker rather
than a preference: the clamp runs **before the account is known**, so there is
no venue to key on at line 388. Making the cap venue-aware requires either
moving the clamp downstream of routing (an order-path change) or fanning the
signal per venue. **That is a real design change, not a constant edit**, and it
should not be smuggled in as one.

**C — Fix the declarations instead of the clamp.** Leave the code alone and
change the two prop legs' `tp_r` to a value inside their measured `cap_r`, so
the YAML states what the book does. Cheapest and lowest-risk, and it addresses
the *documentation* defect without touching order geometry — but it accepts the
Bybit ceiling on a venue that may not impose one. Still **Tier-3** (a `tp_r`
change is a strategy-parameter change).

## 7. Recommendation

**Take C now, and only take B if a measured answer about Breakout's actual
limit says the ceiling is wrong.** Rationale: A leaves a known-false
declaration standing; B is an order-path change whose benefit is unquantified
until someone establishes the venue's real limit, and this repo's own rule is
that a value with no measured distribution behind it does not ship.

**What would change the recommendation:** evidence that Breakout permits a
target beyond 9.9%. That is a question about the venue's terms, not about this
codebase, and it is the one input I could not obtain.

## 8. Noted in passing — a single-owner defect, not part of this proposal

`_TP_SENTINEL_CAP_PCT = 0.099` is **duplicated in five files** —
`trend_donchian.py:133`, `htf_pullback_trend_2h.py:98`,
`fade_breakout_4h.py:117`, `squeeze_breakout_4h.py:79`, and
`position_telemetry.py:72`. Four are strategy units; the fifth is the telemetry
reader that must agree with them or its `pct_of_cap` is measured against a
different ceiling than the one applied.

Five copies of one venue constant is the shape `cost-model-single-owner` and
`canonical-db-resolver` exist to prevent, and it is also **the thing that makes
option B cheap if it is ever taken** — the consolidation is worth doing on its
own merits, independently of the venue question. **Not proposed here**, because
bundling a refactor with a Tier-3 geometry decision makes both harder to judge.
