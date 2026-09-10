# Scoping the inverse-ETF route for `alpaca_live`'s dead short side

> **Doc status:** `live` · category `research` · MI-251 · written `2026-09-10` by
> `session_01Ek2KDXDeLAMrN4JH4aEXWf` · **MEASURE AND PROPOSE ONLY — this doc
> changes no config, arms nothing, and declares no instrument.**

Operator answer, 2026-09-10, on a pop-up: *"Scope inverse ETFs first."* Chosen
over leaving the short side dead and over other routes.

---

## Verdict, up front

**The lost flow is real and it is a MAJORITY, not a handful — so the question is
worth asking.** Over 56.2 days the roster produced **15 long and 19 short**
packages: the short side is **55.9%** of this roster's flow and every one of the
19 was structurally unexecutable.

**And an affordable 1x instrument exists for the symbol that matters.** The loss
is overwhelmingly one symbol — **TLT is 17 of 19 (89.5%)** — and **TBF**
(ProShares Short 20+ Year Treasury, 1x inverse, $25.48, ER 0.95%) clears the
whole-share cash wall at **7 shares / $178.36**, which is *more* granular than
going long TLT itself (2 shares / $164.94). Holding cost is negligible: **$0.021**
over the median 4.54-day hold against ~$0.98 of per-trade risk.

**But the route does not exist, and that is the finding that governs.** There is
**no inverse-translation mechanism anywhere in `src/`** — a grep for
`inverse_etf` / `invert_direction` / `proxy_symbol` / `short_proxy` across `src/`
and `config/` returns exactly one hit, and it is a *comment in `accounts.yaml`
describing the aspiration*. A strategy binds to its instrument through
`config/strategies.yaml::symbols`, so expressing "short TLT" as "long TBF" is
**either a new strategy leg keyed on TBF — a different instrument with its own
edge, not the same signal re-routed — or a new order-path translation layer.**
Both are Tier-3 and neither is a routing change.

**So the honest recommendation is: do not wire this yet.** The affordability
screen passes and the cost is trivial; what is missing is a mechanism, and
building one is a materially larger decision than "declare an instrument". The
one thing I would act on now is **§6**, which is not about inverse ETFs at all.

---

## 1. Which bearish signals are actually being lost

⚠️ **POPULATION.** The diag journal read is capped at **1000 rows** and returns
newest-first, so this is *the most recent 1000 `order_packages`*,
**2026-07-16T06:03Z → 2026-09-10T10:05Z = 56.2 days**. It is a **cap, not the
lifetime** — older rows exist and are not in it. `order_packages` carries **no
`account_id`** (a package is generated once and fans out), so "lost for
`alpaca_live`" means *a short package on a strategy in that account's roster*.

| leg | long | short | total |
|---|---:|---:|---:|
| `tlt_pullback_1h` *(incumbent — the only leg live before 2026-09-10)* | 13 | **14** | 27 |
| `tlt_pullback_1d` | 0 | **3** | 3 |
| `ief_pullback_1d` | 0 | **1** | 1 |
| `slv_pullback_1d` | 1 | **1** | 2 |
| `iaum_pullback_1d` | 1 | 0 | 1 |
| **roster total** | **15** | **19** | **34** |

**19 lost bearish packages in 56.2 days = 0.34/day ≈ 10 per 30 days**, and the
short side is **55.9%** of the roster's flow. That is not a handful.

This is consistent with, and independently reproduces, the figure already
recorded in `config/accounts.yaml` beside `side_filter` — **57.7% short over 100%
of `order_packages` (3,984 rows, 2026-05-02…2026-08-23)** — and with the
per-leg note there that *"the short 55.4% of this leg's flow is structurally
unexecutable"*. Three windows, three methods, same answer.

**By symbol, the loss is almost entirely one instrument:**

| symbol | lost shorts | share |
|---|---:|---:|
| **TLT** | **17** | **89.5%** |
| IEF | 1 | 5.3% |
| SLV | 1 | 5.3% |

### 1a. ⚠️ The incumbent has not gone quiet — it has TURNED BEARISH

This is the finding I did not expect and it reframes the problem.

The dispatch (correctly) reports the incumbent's long side as *18× Jun / 2× Jul /
13× Aug / **0× Sep***, "going quiet the day before the account was armed". The
long count is right; **"went quiet" is not what happened.**

| month | `tlt_pullback_1h` long | short |
|---|---:|---:|
| 2026-08 | 13 | 11 |
| 2026-09 | **0** | **3** |

Every September package on this leg is short:

```
2026-09-01T16:05:34  short  TLT  entry=82.12  sl=82.61571429  status=rejected
2026-09-03T13:30:27  short  TLT  entry=82.34  ...             status=closed
2026-09-08T13:30:22  short  TLT  entry=82.47  ...             status=closed
```

And upstream of packages, the signals table agrees emphatically. Over
**2026-09-03…09-09** (`/api/diag/audit_query`, capped at 1000 rows *per leg*):

| leg | bullish evals | bearish evals | no-signal |
|---|---:|---:|---:|
| `tlt_pullback_1h` | **0** | **222** (210 `sell` + 12 `short`) | 778 |
| `iaum_pullback_1d` | 403 | 0 | 597 |
| `slv_pullback_1d` | 492 | 0 | 508 |
| `tlt_pullback_1d` | 0 | 0 | 1000 |
| `ief_pullback_1d` | 0 | 0 | 1000 |

**The account is idle because its only established leg is signalling, hard, in
the one direction it cannot take** — 222 bearish evaluations and zero bullish in
a week. It is not waiting for a setup; it is being refused every time it finds
one.

### 1b. ⚠️ Correction to a claim recorded in `config/accounts.yaml`

That file states, beside `side_filter`, that four legs *"go fully inert —
`gld_pullback_1d`, `gdx_pullback_1d`, **`iaum_pullback_1d`, `slv_pullback_1d`**
are 100% short."* **Two of those are legs Option A added to real money this
morning**, so if it still held it would matter a great deal.

**It does not hold in the current window, and the direction of the error is
reassuring rather than alarming.** Over 2026-09-03…09-09 both are **100%
BULLISH** (403 and 492 bullish evaluations, **zero** bearish), and in the package
window each produced a long. **Both readings are true of their own populations**
— the recorded one covers 2026-05-02…08-23 — which is itself the finding:
**directional bias here is regime-dependent and flips.** A leg that is "100%
short" in one window is "100% long" in the next, so *"this leg is permanently
inert on a long-only account"* is not a conclusion the data supports for any leg.
I have **not** edited that comment: it is accurate about its own window, and
`accounts.yaml` is Tier-3.

### 1c. ⚠️ A finding I raised and then RETRACTED

While reading the packages I flagged that **9.8% of longs and 7.7% of shorts had
a stop on the wrong side of entry** — which would have meant live positions with
no protective stop. **It was an artefact of my probe and I retract it.**

`order_packages.sl` is the **CURRENT** stop, which **trails into profit**; it is
not the entry stop. Re-tested against that hypothesis, **89 of 89** flagged rows
are explained by trailing (88 `closed`, 1 `open`) and **zero** are unexplained.
Recorded here because it also **invalidated a number I had already computed** — I
first quoted a TLT median stop of 0.561% derived from trailed stops. Recomputed
over entry-sided rows only it is **0.552% (n=14)**, and the minimum moves
**0.014% → 0.302%**, which is the part that mattered. Every stop figure below is
the corrected one.

---

## 2. Does an affordable inverse instrument exist? (the whole-share cash wall)

`alpaca_live` is a **~$200 CASH book** (`capacity.multiplier == 1`) and
`risk.WHOLE_UNIT_QTY_EXCHANGES` includes `alpaca`, so **bracket orders reject
fractional shares**. A $400 instrument buys **zero** shares whatever its
compat-matrix verdict — and *a `ROUTE` verdict is neither a track record nor
affordability*; it scores a bootstrapped R-ledger and never runs this wall.

**Inputs, stated:** equity **$200.22** (read 2026-09-10) · `_MARGIN_SAFETY_BUFFER
= 0.9` · leverage **1.0** · `risk_pct = 0.02` ⇒ risk budget **$4.0044** (an
*upper* bound — `confidence_scalar` only shrinks it) and **max notional
$180.20**.

An inverse ETF tracks ≈ −1× the underlying's **percentage** move, so the **% stop
transfers and the dollar stop does not**. Stop % below is the observed **median
entry-stop** per underlying.

| instrument | 1x? | price | shares (cash) | notional | shares (risk) | binds | realised risk |
|---|---|---:|---:|---:|---:|---|---:|
| TLT — long, the underlying | — | 82.47 | **2** | 164.94 | 8 | **CASH** | $0.91 |
| **TBF — inverse TLT** | **1x** | **25.48** | **7** | **178.36** | 28 | **CASH** | **$0.98** |
| TBT — inverse TLT | ✗ 2x | — | — | — | — | — | *leveraged — see §3* |
| IEF — long, the underlying | — | 93.50 | 1 | 93.50 | 85 | CASH | $0.05 |
| **TBX — inverse IEF** | **1x** | **28.95** | **6** | 173.70 | 276 | **CASH** | $0.09 |
| SLV — long, the underlying | — | 55.54 | 3 | 166.62 | 1 | risk | $3.44 |
| ZSL — inverse SLV | ✗ 2x | — | — | — | — | — | **no 1x exists** |

**TBF clears the wall comfortably and improves granularity: 7 shares against
TLT's 2.** On a book this small that is a real gain — position sizing currently
quantises to halves of the account.

**SLV has no 1x inverse instrument at all.** The only inverse silver products are
leveraged (ZSL −2x; DSLV −3x, an **ETN**, which is credit-linked and a different
risk again). That short is **unroutable at 1x**, full stop — 1 of 19, so it costs
almost nothing to concede.

⚠️ **`CASH` binds on every row, and that is a pre-existing property, not
something this route introduces.** `accounts.yaml` already records it for the
long side: *"the sizer asks for ~11x the account and the 0.9 cash wall clamps it
to 2 shares… realised risk is therefore ~$0.75, NOT the $4.00 the 2% implies…
the reachable set is IDENTICAL at 2% and 5% because cash, not risk, is the
binding term."* My derivation reproduces that independently. **Do not "fix" the
gap by raising `risk_pct`** — it changes nothing.

⚠️ **n=1 on IEF and SLV is not a distribution.** Do not size anything off those
two rows.

---

## 3. What it costs that a plain short does not

| | cost | per median 4.54-day hold on $178.36 |
|---|---|---|
| **TBF / TBX expense ratio** | 0.95%/yr | **$0.021** |
| Alpaca equity commission | $0 | $0 |
| Short borrow (the alternative) | n/a — **`shorting_enabled: false` at the venue** | — |

Against ~**$0.98** of per-trade risk, the expense ratio is **~2%** of the risk
budget. **Not a blocker.** *(Hold times: n=6 closed packages, median 4.54 d, mean
8.51 d, max 30.05 d — small n, and `updated_at` is a proxy for the close, not a
broker stamp.)*

⚠️ **1x and leveraged are different questions and I am keeping them apart.**
TBF/TBX reset **daily**, so over a multi-day hold they track −1× of *compounded
daily returns*, not −1× of the period return. At 1x that path-dependence is
small; **at 2x/3x (TBT, TMV, PST, ZSL, DSLV) it is a different instrument, not a
proxy**, and its decay is a function of realised volatility over the hold — which
a 30-day max hold makes material. **I am not proposing any leveraged
instrument**, and a proposal that reached for TBT because it is cheaper per share
would be reaching for the wrong thing.

---

## 4. Is `alpaca_live` even the right account for it?

**This is the argument against, and it is stronger than the cost argument.**

`alpaca_live` is a CASH account under an **armed** T+1 settlement gate
(`ALPACA_CASH_SETTLEMENT_MODE=apply`, `ALPACA_CASH_SETTLEMENT_ACCOUNTS=alpaca_live`,
Tier-2, 2026-08-31). Sale proceeds are unspendable until settled, and buying with
unsettled funds is a good-faith violation that gets the account restricted. At
~$200 with a $178 position, **the whole balance recycles every trade** — which
`accounts.yaml` already names as the reason the gate was armed *before* the first
leg went live.

⚠️ **Opening the short side roughly DOUBLES turnover on a book that already
recycles fully.** Today the account can act on ~44% of its roster's flow; making
the other ~56% actionable means roughly twice as many round trips through a
settlement constraint that binds hardest exactly when the balance is fully
recycled. **The inverse route does not interact with T+1 neutrally — it tightens
it.** That is a reason to prefer a *paper* book for the first exercise of this
mechanism, not a reason the route is wrong.

Related, and worth stating because it is easy to miss: at $178 of a $180.20
ceiling, **a TLT long and a TBF long are mutually exclusive** — they contend for
the same cash. That happens to be harmless (the two are signalled in opposite
regimes and the observed data never shows both at once), but it means this buys
**flow diversification, not concurrency**.

---

## 5. ⚠️ The blocker: the route does not exist, and declaring the instrument breaks CI

**5a. There is no translation mechanism.** Grepping `src/` and `config/` for
`inverse_etf` / `inverse ETF` / `invert_direction` / `proxy_symbol` /
`short_proxy` returns **one** hit — a **comment** in `config/accounts.yaml`
saying *"The short side returns as long positions in inverse ETFs once those are
built (`BL-20260823-NO-INVERSE-ETF-INSTRUMENTS-DECLARED`)"*. That is prose about
intent. **Nothing reads it.**

A strategy binds to its instrument in `config/strategies.yaml`:

```yaml
tlt_pullback_1h: {symbols: ['TLT'], timeframe: '1h', enabled: true, execution: live}
```

So there are exactly two ways to build this, and **both are Tier-3**:

- **(A) A new strategy leg keyed on TBF.** Honest and low-risk, but it is a
  **different strategy** — a pullback signal on TBF's own price series, with its
  own edge, its own `insufficient_n`, and no inherited evidence from the TLT leg.
  It is not the TLT signal re-routed.
- **(B) An order-path translation layer** mapping (short TLT) → (long TBF) at
  execution time. This preserves the signal, and it touches the **live order
  path** — instrument substitution, stop/target re-derivation in the inverse's
  price space, and a provenance question (what does the journal say the trade
  *was*?). Materially more dangerous than (A).

**5b. Declaring the instrument fails CI on its own.** `alpaca_live.symbols` is
pinned by a **strict-equality** invariant in
`tests/test_paper_portfolio_accounts.py`:

```python
expected_syms = [s for s in real.get("symbols") or [] if s not in _ALPACA_PROXY_SYMBOLS]
assert portfolio.get("symbols") == expected_syms, \
    "alpaca_portfolio symbols must be alpaca_live's minus SPLG/IAUM."
```

So **adding TBF to `alpaca_live.symbols` fails CI unless `alpaca_portfolio` is
edited in the same change** — and touching `alpaca_portfolio` is explicitly out
of scope for this task. Whoever eventually wires this must plan for a
**coordinated two-account Tier-3 diff**, or for TBF to be declared a **proxy
symbol** (the `SPLG`/`IAUM` mechanism), which is itself a decision with its own
consequences.

⚠️ And note `src/main.py::_resolve_tick_symbols` builds the tick loop's fetch
union from account `symbols`, so **TBF must be in some account's `symbols` before
any strategy on it can signal at all** — but adding it there *alone* fetches
candles nothing consumes.

**5c. Candle availability is UNVERIFIED.** Whether Alpaca serves usable history
for TBF/TBX at the strategy's timeframe has **not been tested** — this session
has no market-data credential for arbitrary symbols, and TBF is in no account's
`symbols`, so the trader has never fetched it. *We did not look.* That is not the
same as "it works" and must not be recorded as such. It is cheap to settle on a
paper book first.

---

## 6. The one thing I would act on now — and it is not inverse ETFs

**`alpaca_live` currently has one leg with a live directional signal, and that
signal is 100% in the direction the account cannot take** (§1a: 222 bearish
evaluations, zero bullish, in a week). Option A landed four more legs this
morning specifically as *plumbing and utilisation*; of those, **`iaum_pullback_1d`
and `slv_pullback_1d` are the two currently signalling bullish** (403 and 492
bullish evaluations, zero bearish).

So the account's near-term ability to exercise its untested order path rests on
the **legs added this morning**, not on the incumbent — which is a materially
better justification for Option A than the one it was approved on, and it is
**observed, not projected**. It also means **the order path may get its first
real exercise within days without any inverse-ETF work at all.**

⚠️ **This does NOT clear clause (3) of
`OI-20260910-ALPACA-LIVE-OPTION-A-FOUR-LEGS-...`.** A bullish *evaluation* is not
an order package, and no `order_packages` row exists on `alpaca_live` for any of
the four. That row's positive control still applies.

**Recommendation: let the newly-armed legs run first.** They are the cheapest
available test of the untested order path, they need no new instrument, no CI
coupling, and no Tier-3 decision. Revisit the inverse route once the order path
has been proven to work at all — building a translation layer on top of a path
never observed placing a long would be arming two unknowns at once.

---

## 7. If the operator wants it built anyway — the decision, stated

| | option A · new TBF strategy leg | option B · order-path translation |
|---|---|---|
| preserves the TLT signal | ✗ new edge, `insufficient_n` from zero | ✓ |
| touches the live order path | ✗ | ⚠️ **yes** |
| needs the two-account `symbols` diff | ✓ | ✓ |
| tier | 3 | 3, and the riskier 3 |
| recoverable if wrong | leg → `execution: shadow` | needs a code rollback |

**My recommendation, if forced to choose: neither yet — and if one, A, staged on
`alpaca_paper` first.** A is the option whose failure mode is a bad strategy
rather than a bad order. And a paper book has neither the cash wall nor the T+1
constraint, so it settles the candle-availability and mechanism questions for
free.

**Scope for whoever picks this up:** it is a **one-symbol problem** (TLT, 89.5%).
Do not build a general inverse-mapping framework for 17 packages in 56 days.

---

## What this session did NOT do

- **No config change.** `config/accounts.yaml`, `config/strategies.yaml`,
  `symbols:`, `alpaca_portfolio`, risk caps and account modes are all untouched.
- **`side_filter: long` stays** — operator-decided 2026-09-10. Everything above
  works *with* it; nothing here proposes removing it.
- **No order placed, modified or cancelled on any account.** The 56 naked TLT
  shares on trade 5414 stay naked; MGC remediation stays ABORTED.
- **No leveraged instrument proposed.**
- I did **not** edit the `accounts.yaml` comment corrected in §1b — it is accurate
  about its own window, and that file is Tier-3.

## Sources for the market data in §2–3

- [ProShares TBF — Short 20+ Year Treasury](https://www.proshares.com/our-etfs/leveraged-and-inverse/tbf) · [etf.com/TBF](https://www.etf.com/TBF) — $25.48 (2026-09-03), ER 0.95%
- [ProShares TBX — Short 7-10 Year Treasury](https://www.proshares.com/our-etfs/leveraged-and-inverse/tbx) · [etf.com/TBX](https://www.etf.com/TBX) — $28.95 (2026-09-01), ER 0.95%
- [ProShares ZSL — UltraShort Silver](https://www.proshares.com/our-etfs/leveraged-and-inverse/zsl) · [etfdb — leveraged silver ETFs](https://etfdb.com/themes/leveraged-silver-etfs/) — establishes that inverse silver exposure is **leveraged-only**

⚠️ Prices are **dated quotes from a public search, not a broker read**, and are
used only for the whole-share cash wall — a decision that would be unchanged by
several dollars of drift in either direction. Anyone acting on this should re-read
them from the venue.
