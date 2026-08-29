# `alpaca_live` go-live — where it actually stands, and the ONE decision that unblocks it

**Date:** 2026-08-29 · **Trigger:** operator, *"let's also try to get alpaca real money ready
to flip to live — I'm not sure how much more work there is there, but we seem to be going in
circles a bit there."*

**Tier:** this document is Tier-1 (measurement + analysis). **Every remaining action is Tier-2
or Tier-3 and none was taken.** Nothing was funded, flipped, sized, or routed.

---

## 0. Why it feels circular — the mechanical answer

Not drift, and not for lack of analysis. Three specific things:

1. **The go-live sequence was blocked on a reading nobody took.** `BL-20260824-ALPACA-LIVE-GOLIVE-SEQUENCE`
   STEP 1 was restated on **2026-08-25** to say its decision input is
   `status_flags.capacity.multiplier`, and the read surface for it shipped the *same day*
   (`BL-20260825-ALPACA-CAPACITY-FIELDS-FETCHED-THEN-DISCARDED`). **The reading was then never
   taken.** Four days of downstream work waited on one HTTP GET. It is taken below.
2. **~15 open rows describe one account, and at least one describes a state that no longer
   holds.** `BL-20260821-ALPACA-LIVE-REFUSES-EVERY-ORDER-127-OF-127` says the account *"is
   declared mode:live"*. It is **`dry_run`** — verified twice (the live diag read AND
   `config/accounts.yaml`). Its own stated disposition (*"fund it, or shelve it to dry_run"*)
   was **taken** — shelved — and the row was never updated. A reader working the backlog
   top-down re-derives a solved problem.
3. **The steps were believed independent and are not.** STEP 1 (exposure bound) and STEP 2
   (shorting) turn out to be **the same decision**, for a reason only the new reading exposes (§ 2).

---

## 1. The measurement (2026-08-29T00:22:41Z, `/api/diag/broker_account_status?account_id=alpaca_live`)

| field | value |
|---|---|
| `status` / `trading_blocked` / `account_blocked` | `ACTIVE` / `false` / `false` |
| **`capacity.multiplier`** | **`1`** |
| `capacity.cash` = `buying_power` = `regt_buying_power` = `equity` | **`200.10`** (all four equal) |
| `shorting_enabled` | **`false`** |
| `crypto_status` | `INACTIVE` |
| `capacity.daytrading_buying_power` / `pattern_day_trader` / `daytrade_count` | **absent** (omitted, not defaulted) |
| declared `mode` (diag **and** `config/accounts.yaml`) | **`dry_run`** |

**`multiplier = 1` means this is a CASH account**, corroborated by all four capacity figures
being identical — there is no margin line. The absent PDT keys are consistent with that: PDT is
a margin-account concept.

---

## 2. What that reading decides — STEP 1 and STEP 2 are one decision

The go-live row wrote the procedure in advance, which is why this is a conclusion and not an
opinion:

> *"`1` ⇒ cash, the multiple cannot exceed 1.0, a 2.0 ceiling is **decorative**, and the real
> bound is per-trade notional against a $200 balance under whole-share sizing — i.e.
> **`max_gross_exposure_pct` would be the WRONG instrument and Step 1 needs restating.**"*

**So STEP 1 as written is void.** And STEP 2 is worse than "flip a broker flag":

> **You cannot short in a cash account.** Reg-T short selling requires a margin account. STEP 2
> is therefore not a settings toggle — it is an **account conversion**, and that same conversion
> is what would move `multiplier` 1 → 2 and make STEP 1's exposure cap the *right* instrument
> again, with a measured basis already in hand (the paper mirrors run **1.84 min / 1.86 median /
> 2.01 max** gross-exposure multiple).

**The fork is a single operator decision, and everything else follows from it:**

### Path A — stay CASH
- Shorting stays structurally impossible. **6 legs are 100 % dead** (`gdx/iaum/gld/slv/ief/tlt _pullback_1d`)
  and **68.8 % of routed flow** cannot execute. The account goes live able to run about a third
  of what routes to it, long-only.
- STEP 1 must be **restated** as a per-trade notional bound; `max_gross_exposure_pct` is decorative at 1.0×.
- **T+1 settlement becomes live-blocking, not theoretical.** `BL-20260823-ALPACA-CASH-ACCOUNT-SETTLEMENT-UNMODELLED`
  (high) is written against exactly this case — *"on a $200 CASH account the whole balance recycles
  every trade — a good-faith violation is one unsettled buy away."* **Its premise is now measured
  true.** Settlement is modelled nowhere in code.

### Path B — convert to MARGIN
- Satisfies STEP 2 (shorting) **and** makes STEP 1's instrument correct, with the mirrors' 1.84–2.01×
  distribution as its basis.
- **Introduces PDT.** Under $25 k equity a margin account gets **3 day trades per 5 business days**,
  and `BL-20260823-NO-PDT-MODELLING-ON-ALPACA` measures the Alpaca paper book at **14.9 % same-day
  round trips**. Modelled nowhere.
- Trades an unmodelled cash constraint for an unmodelled margin constraint. Neither is free.

---

## 3. Two blockers that survive BOTH paths, and are not fixed by funding

The go-live row's own framing — *"funding is the LAST step of three, not the first"* — holds, and
these are why:

1. **Sizing exceeds whole-account notional at every funding level** (`BL-20260823-ALPACA-RISK-SIZING-EXCEEDS-ACCOUNT-NOTIONAL-AT-EVERY-FUNDING-LEVEL`,
   high): 5 of 6 measurable legs, demand measured up to **12.37×** the account on a single trade.
   **Scale-invariant — a bigger deposit does not fix it**, and no exposure cap is declared on the
   account today (verified: `alpaca_live.risk` carries `max_dd_pct`, `daily_loss_pct`, `daily_usd`,
   `risk_pct` — and **no** `max_gross_exposure_pct`).
2. **At $200 the account cannot size 4 of its 10 priced symbols at all** (`BL-20260826-ALPACA-LIVE-AT-200-USD-CANNOT-SIZE-ITS-LARGEST-SYMBOLS`):
   Alpaca requires whole shares and `risk_pct 0.05 × $200 = $10` of risk does not buy one share.

### 3.1 A new finding: the paper mirror does not mirror the RISK

Read from `config/accounts.yaml` this session, and I have not found it filed anywhere:

| account | `risk_pct` |
|---|---|
| `alpaca_live` | **0.05** |
| `alpaca_portfolio` (its portfolio mirror) | 0.02 |
| `alpaca_paper` | 0.015 |

**The live account is declared to risk 2.5–3.3× more per trade than the book that is supposed to
be its forward record.** The mirrors exist so a live decision can lean on paper evidence, and
`S-PAPER-PORTFOLIO` makes `alpaca_portfolio` the *"paper-money mirror of the actual live-traded
portfolio"* — but a track record generated at the mirrors' declared rate does not transfer to a book
that will trade at the live account's, because position sizing is the thing that differs. This compounds
`BL-20260823-ALPACA-PORTFOLIO-MIRROR-MISSING-TWO-LIVE-LEGS` (roster mismatch) with a *sizing*
mismatch on the legs it does mirror. **Filed as `BL-20260829-ALPACA-MIRROR-DOES-NOT-MIRROR-RISK-PCT`.**

### 3.2 The consequence: the ceiling's only proposed basis does not transfer

This is not a side-observation — it lands directly on STEP 1, and it is derived from the code
rather than assumed.

`src/units/accounts/risk.py::_size_unbounded`:

```
risk_usdt = balance_usdt * risk_pct
raw_qty   = risk_usdt / (risk_distance * cvu)
```

so `notional = qty × entry × cvu` and

```
exposure_multiple = notional / equity = risk_pct × entry / risk_distance
```

**Equity cancels: the multiple is LINEAR in `risk_pct`.** And that is exactly the quantity the
ceiling governs — `RiskManager.gross_exposure()` returns `(open_notional, equity, exposure_multiple)`,
with `max_gross_exposure_pct` declared against it.

The go-live row's 2026-08-25 update proposed the mirrors' **1.8422 min / 1.8569 median / 2.0062 max**
as STEP 1's basis, because the live account's own soak is flat by construction. But that
distribution was measured on books declaring `risk_pct` **0.02**, while `alpaca_live` declares
**0.05** — 2.5×. The same signals on the live account therefore *demand*

> **≈ 4.6 – 5.0×** gross exposure, not 1.85 – 2.01×.

**So a ~2.0 ceiling taken from the mirror would clamp essentially every trade on the live book** —
which [`gross-exposure-governance-DESIGN.md`](../design/gross-exposure-governance-DESIGN.md) § 6
names as *worse than no ceiling* (a ceiling below normal operation silently throttles correctly-sized
trades). The mirror's basis and the live account's `risk_pct` cannot both stand.

⚠️ **Three honest bounds on that inference.** (i) It scales *demand*; **whole-share flooring is a
hard non-linearity at $200** and floors small orders to zero, so the *filled* multiple would be
lower and lumpier than linear — which is the same finding as
`BL-20260826-ALPACA-LIVE-AT-200-USD-CANNOT-SIZE-ITS-LARGEST-SYMBOLS`, not a contradiction of it.
The linear argument is the right one for choosing a ceiling on a *funded* account, where flooring
stops binding. (ii) The mirror's roster differs by 2 of 16 legs, so aggregate concurrency is not
identical. (iii) I did **not** re-measure the live book's multiple.

⚠️ **CORRECTION, same session — an earlier draft of (iii) said the live multiple *"cannot be
measured while the account is dry_run and flat"*. That is too strong, and the
`impossibility-claim-guard` caught it.** What cannot be measured is the **realised** multiple —
the book genuinely never held positions, so `/api/bot/exposure/soak` reads 0.0 on all 91 rows
*correctly* (checked: `src/units/accounts/risk.py` — `observe_exposure` takes notional from open
journal rows, of which this account has none; corroborated by the go-live row's own 2026-08-25 soak
reading). But the **demanded**
multiple is a different quantity and it **is** computable *without the account ever trading*:

> `order_packages` persists `entry`, `sl` and `tp` per decision (verified in
> `src/web/api/routers/order_packages.py:191`), and the multiple is
> `risk_pct × entry / |entry − sl|` — the sizer's own formula, with equity cancelled. Every
> refused `alpaca_live` order package therefore already carries everything needed to compute
> what it *would* have demanded, at any candidate `risk_pct`.

**This is the better basis, and it removes the chicken-and-egg rather than working around it.**
It is `alpaca_live`'s own roster at its own `risk_pct`, instead of a different book's roster at a
different one — so STEP 1 does not actually need the mirrors at all. Checked for existing tooling
before claiming it is new: `docs/research/RESEARCH-CAPABILITY-INDEX.md` has no exposure/notional-demand
entry, and `ls scripts/research/ | grep -iE 'exposure|notional|sizing'` returns nothing.
**Not built here** — it is a concrete, cheap next step rather than a finished measurement, and
`BL-20260823-ALPACA-RISK-SIZING-EXCEEDS-ACCOUNT-NOTIONAL-AT-EVERY-FUNDING-LEVEL` already reports a
`12.37×` figure of this shape, so the arithmetic has been done once by hand and wants a tool.
⚠️ One known join problem to solve first: `/api/bot/order-packages` rows **carry no `account`**
(CLAUDE.md states this), so attributing packages to `alpaca_live` needs a route the refused-order
path actually supports — that is the part to establish before quoting any distribution.

**This collapses into the § 5 plan rather than adding to it:** row 2 (declare the bound) and row 3
(reconcile `risk_pct`) are one decision, because the only available basis for row 2 is measured at
the `risk_pct` row 3 is about. Either `alpaca_live` comes down to the mirrors' rate — and the
1.85–2.01× basis becomes valid as-is — or the ceiling must be derived at 0.05, and the mirrors
cannot supply it.

---

## 4. The concern I am obliged to state once

**The strategy set routed to `alpaca_live` is net-negative on paper except one leg**
(`BL-20260823-ALPACA-LIVE-STRATEGY-SET-IS-NET-NEGATIVE-EXCEPT-ONE-LEG`, high, Tier-3): 16 legs,
and `uso_trend_1h` — **at n=10** — carries the entire positive result.

Getting the account *mechanically able* to trade is the ask, and § 5 delivers it. But mechanical
readiness is not the same as a reason to trade, and the sequencing work above has been about
whether orders can *execute*, not whether they should. **Flipping a net-negative set to real money
is the decision worth pausing on, and it is yours, not mine.** Stated once; the plan below assumes
you want to proceed.

---

## 5. What remains, in order, with owners

| # | Action | Owner | Tier | Status |
|---|---|---|---|---|
| **0** | **Decide: cash or margin.** Everything below forks here. | **Operator** | — | ⛔ **BLOCKING — the one decision** |
| 1 | If margin: convert the Alpaca account (also enables shorting, sets `multiplier`→2) | Operator (broker-side) | — | waits on 0 |
| 2 | Declare the size bound — `max_gross_exposure_pct` (margin) **or** a per-trade notional cap (cash) | Claude prepares, operator approves | 3 | instrument now decidable; waits on 0 |
| 3 | Reconcile `risk_pct` between `alpaca_live` (0.05) and its mirrors (0.015/0.02), or state why they differ | Claude proposes | 3 | § 3.1, newly filed |
| 4 | Model the constraint the chosen path carries — T+1 settlement (cash) or PDT (margin) | Claude | 2 | waits on 0 |
| 5 | Decide the routing question in § 4 (net-negative set) | **Operator** | 3 | independent of 0 |
| 6 | Deposit, then `set-account-mode` `dry_run` → `live` | Operator | 3 | LAST |
| 7 | Verify: re-read the diag, confirm first real fills reconcile, confirm `silent_refusal_alert` grades it healthy | Claude | 1 | after 6 |

**Nothing in rows 1–7 can be honestly started before row 0**, which is why this stalled: the
sequence had four steps and no one had established which of two mutually exclusive versions of
step 1 and 2 applied. It is established now.

---

## 6. Housekeeping this measurement settles

- `BL-20260821-ALPACA-LIVE-REFUSES-EVERY-ORDER-127-OF-127` — describes `mode:live`; the account is
  `dry_run` on both the live diag and in config. Its disposition was taken. **Should be closed or
  restated**; leaving it open is a live source of the circling.
- `BL-20260823-ALPACA-CASH-ACCOUNT-SETTLEMENT-UNMODELLED` — premise **confirmed measured** (`multiplier=1`).
- `BL-20260823-NO-PDT-MODELLING-ON-ALPACA` — **conditional on Path B**; inert under Path A.
- `BL-20260824-ALPACA-LIVE-GOLIVE-SEQUENCE` — STEP 1 **void as written**; STEP 2 is an account
  conversion, not a flag. Update recorded on the row.
