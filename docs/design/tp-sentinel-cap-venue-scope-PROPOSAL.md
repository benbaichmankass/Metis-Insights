# Should a Bybit-derived take-profit cap bind non-Bybit venues?

**Status: RESOLVED 2026-08-24 — option C MEASURED AND REJECTED. The clamp and
both legs' `tp_r: 6.0` are UNCHANGED; only the false YAML comments were fixed.
See § 9 for the measurement. Nothing in §§ 1-8 was applied.**
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

✅ **RESOLVED 2026-08-25 (#10248, `eebb8ebe`) — and this section undercounted
by more than half.** It said five files. A mechanical census found **13
declaration sites under THREE names**: `_TP_SENTINEL_CAP_PCT` (the 4 strategy
units + `position_telemetry.py`), `TP_VENUE_CAP_PCT` (`target_expectation.py`
+ 2 research scripts) and `LIVE_TP_CAP_PCT` (5 more scripts). All 13 held
`0.099` — they agreed **by luck**, with no import, no test and no guard binding
them. ⚠️ The first census probe reported 5, matching this section, because its
regex required a character before `TP_` and so silently missed both non-
underscore names; the real number came from cross-checking `grep`. Do not
re-quote "five".

**13 → 1.** `src/runtime/tp_venue_cap.py` is the owner; every site imports it.
Live order geometry is unchanged and asserted mechanically: the units import
under their existing local name, so all 8 clamp expression lines are
byte-identical, and every `src/` consumer resolves to the same OBJECT (`is`,
not `==`). The guard `tp-venue-cap-single-owner` now fails CI on a second
declaration — the check `m20_fleet_exit_sweep.py` correctly complained did not
exist ("NOTHING CHECKS THAT THIS STILL MATCHES THE LIVE VALUE").

⚠️ **This resolves the DUPLICATION only. The venue question in § 6 is still
OPEN** — the consolidation deliberately preserved the single value the fleet
has always used and answers nothing about whether `0.099`, a Bybit ErrCode
10001 boundary, is right for the non-Bybit legs it is applied to. Keeping the
two separate was the point: bundling a refactor with a Tier-3 geometry decision
makes both harder to judge.


---

## 9. RESOLUTION (2026-08-24) — C was measured, and it loses

The operator directed **"measure first, then decide."** Two things came out of
that, and the first one invalidates this document's own § 6.

### 9.1 ⚠️ Option C's stated rationale was FALSE

C described itself as addressing *"the documentation defect without touching
order geometry"*. **It does touch order geometry.** The effective target is

```
min(cap_r, tp_r),   cap_r = 0.099 · entry / risk
```

`cap_r` is a **percent-of-entry**; `tp_r` is a **multiple-of-risk**. They are
different functions of different variables, so **no `tp_r` reproduces the
clamp**. Lowering `tp_r` to 3.22 / 4.08 tightens the *real* target on every
trade where `cap_r > tp_r` — **half the trades by the definition of the
median**, since 3.22 and 4.08 *are* the medians quoted in § 4.

The half of § 5 that survives: `backtest_system`'s ROSTER maps both prop legs to
the live unit and imports its `order_package` — the function holding the clamp —
so the +$483 / +$883 gate **did** measure the clamped book. That was checked, not
assumed.

### 9.2 The measurement

Blocked at first: `account_compat_matrix` passed `overrides={}` **hardcoded**, so
the engine could only score a strategy at its config values — pricing a candidate
`tp_r` required first editing the Tier-3 file under evaluation. PR #10238 removed
that circularity and shipped `prop-tp-r-gate.yml`; the grid and accept criterion
were fixed in the workflow header **before any result**.

**Actions run `32781427791`, 10/10 arms, 5m base, `breakout_1` ruleset.**

| `trend_donchian_sol_prop` | trades | ev_net_usd | P(net>0) | vs control |
|---|---|---|---|---|
| **6.0** (control) | 411 | **$1,084** | 0.8863 | — |
| 5.0 | 413 | $1,090 | 0.8840 | +$5 |
| 4.0 | 413 | $1,029 | 0.8773 | −$55 |
| 3.0 | 422 | $986 | 0.8517 | −$99 |
| 2.5 | 431 | $950 | 0.8587 | −$135 |

| `trend_donchian_eth_prop` | trades | ev_net_usd | P(net>0) | vs control |
|---|---|---|---|---|
| **6.0** (control) | 977 | **$968** | 0.8063 | — |
| 5.0 | 981 | $980 | 0.8163 | +$12 |
| 4.0 | 987 | $795 | 0.7707 | −$173 |
| 3.0 | 999 | $611 | 0.7273 | −$358 |
| 2.5 | 1020 | $503 | 0.6760 | −$465 |

**C's own values land in the losing region on both legs.** EV falls
monotonically below 5.0 and `P(net>0)` falls with it; criterion clause (c) fails.
5.0-vs-6.0 is a tie (+$5 / +$12, well under 2%) and is **not** read as a win.

⚠️ **State the population.** These are this run's own numbers on its own data
vintage and window — they are **not** comparable to trainer relay #8975's
+$483 / +$883, and the control is the 6.0 arm *in this run*.

⚠️ **`eth_pullback_prop_2h` was NOT measured.** The run covered the two donchian
prop legs only; that leg is `execution: shadow`. Its comment was corrected on the
**structural** ground alone (read off the code) — it has no measured `cap_r`
median and no EV evidence, and the donchian numbers must not be borrowed for it.

### 9.3 What was applied

**Option C is REJECTED on the evidence.** `tp_r` stays 6.0 on both legs; the
clamp is untouched; no order path changed.

The **documentation defect is fixed instead**: all three prop legs carried
`# real 6R take-profit cap`, which is false — the clamp gets there first on more
than half of the two measured legs' trades. Those comments now state the real
mechanism, and the two donchian legs carry the measured evidence for why 6.0
stays. **Comment-only: the parsed YAML is byte-identical before and after**,
asserted rather than eyeballed.

### 9.4 Still open, and deliberately not closed here

- **§ 5's load-bearing unknown is UNCHANGED**: nobody has established whether
  Breakout imposes a TP limit at all. This measurement says 6.0 beats the lower
  arms *under the clamp as it exists*; it says nothing about whether the clamp
  should apply to a venue that may not need it.
- **The grid never probed ABOVE 6.0.** The surface is monotone increasing toward
  6.0 and flat between 5.0 and 6.0, so the optimum may sit at or above it —
  possibly at the 50.0 sentinel the non-prop siblings use. Answering that needs a
  second run at `{6.0, 8.0, 12.0, 50.0}`; **6.0 is therefore the best of the five
  arms tested, NOT a demonstrated optimum**, and this document does not claim it
  is one.
- **Option B remains structurally blocked** for the reason in § 6 (the clamp runs
  before the account is known).
