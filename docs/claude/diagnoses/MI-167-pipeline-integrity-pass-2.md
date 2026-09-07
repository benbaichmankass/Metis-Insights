# MI-167 — pipeline integrity, pass 2: the close executed, the confirmation asked the wrong question

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Unit: MI-167. Lane: standing pipeline-integrity review under `CY-20260906-TRADING-TRUTH`.
Pass 2 of N. Dispatched by the manager (`session_01HrmZ1RRNM4UnEUaFdrPEjj`) 2026-09-07.
Builds on [`MI-166-pipeline-integrity-pass-1.md`](MI-166-pipeline-integrity-pass-1.md) (merged `39ab1079`).

**All measurements below are mine, taken live 2026-09-07 18:53–19:03Z through
`scripts/ops/diag_fetch.sh` (direct HTTPS, `served by https://ict-bot.duckdns.org`).
Where I reproduce or CORRECT a pass-1 figure, I say which.**

---

## The one-line finding

**Trade 5531's 43 lots were real, they filled in full, and they were sold four hours
later by our own stop-loss monitor. The close SUCCEEDED at the venue and was then
graded a FAILURE by a confirmation that asks whether the *symbol* is flat when the
close was of a *trade*.** A sibling trade (5353) legitimately held 11 MGC lots, so the
symbol could never be flat, so the close could never confirm, so the journal row was
deliberately left open — and ten minutes later the protection layer re-armed 43 lots of
stop over a position that no longer existed.

**This inverts pass 1's mechanism.** Pass 1 concluded *"the journal recorded a 43-lot
fill it does not have."* The journal recorded a fill it **did** have and then **missed
the close**. Same divergence, opposite repair.

---

## Population and provenance

| read | endpoint | state | captured |
|---|---|---|---|
| running SHA | `/api/diag/version` | `git_sha 40ebd40b` == `git_sha_on_disk`, **`restart_pending: false`** | 18:53:42Z |
| IB positions | `/api/diag/exchange_positions?account_id=ib_paper` | 3 positions, **`error: null`** | 18:54:41Z |
| IB resting orders | `/api/diag/ib_open_orders?account_id=ib_paper` | **`read_state: "orders_read"`** — a real read, 8 orders | 18:54:42Z |
| Alpaca live positions | `/api/diag/exchange_positions?account_id=alpaca_live` | **`positions: [], error: null`** — a real read of an empty book | 19:02:30Z |
| journal `trades` | `/api/diag/journal?table=trades&limit=1000` | 1000 rows, ids **4540–5539**, 2026-08-10T13:00Z → 2026-09-07T18:04Z | 18:55Z |
| journal `order_packages` | `/api/diag/journal?table=order_packages&limit=1000` | 1000 rows | 18:55Z |
| trader journal | `/api/diag/journalctl?unit=ict-trader-live.service` | 9 windowed pulls, 2000-line cap each | 18:57–19:01Z |

⚠️ **`read_state: "orders_read"` was checked before any conclusion was drawn from the
order list**, and `error: null` before any conclusion from a position list. `_open_trades`
swallows exceptions and returns `[]`; neither read did.

⚠️ **Pass 1's `restart_pending` confound is GONE.** Pass 1 ran with `59bfa099` live against
`b596ac4f` on disk. This pass: running and on-disk SHA are both `40ebd40b`, pending false.

⚠️ **The trader restarted between the entry and the close** — PID `2704958` at 08:26,
`2798995` at 11:45. Stated because it bounds what a single process observed, not because
it changes any finding: both the fill and the close are in the systemd journal regardless.

⚠️ **Negatives below carry their positive control.** *Bybit has no close-confirmation:*
`grep -rn "BYBIT_CLOSE_CONFIRM" src/` returns **0**, while the identical probe
`grep -rn "_CLOSE_CONFIRM_S" src/` returns **IB** (`ib_client.py:2636`) and **Alpaca**
(`alpaca_client.py:842`, `:1049`) — the probe demonstrably finds confirmations that exist.

---

## Finding 1 — the complete chain of trade 5531, minute by minute (CRITICAL)

This is what pass 1 handed forward as its top item. Every row is from the trader journal.

| when (UTC) | what | evidence |
|---|---|---|
| 08:26:26 | `ict_scalp_mgc_15m` emits intent, side=long | `intent_multiplexer` |
| 08:26:32.972 | ⚠️ `get_existing_position_info: read failed for account=ib_paper symbol=MGC: **no such column: op.id**` | `src.runtime.positions` WARNING |
| 08:26:32.975 | `execute_pkg … qty=**43.0000** dry_run=False` | `src.units.accounts.execute` |
| 08:26:33.113 | `placeOrder MarketOrder(orderId=**538**, BUY, totalQuantity=**43.0**)` + children 539 (LMT 4485.0) / 540 (STP 4394.5) | `ib_insync.ib` |
| 08:26:33–08:26:52 | position climbs **11 → 13 → 15 → 17 → 18 → 20 → 22 → 24 → 26 …** | `ib_insync.wrapper position:` |
| 10:01:02 – 11:43:48 | position **54.0**, `avgCost 44230.21925925` | sampled at 10:01, 11:01, 11:03, 11:12, 11:22, 11:32, 11:43 |
| **11:45:01.649** | `order_monitor: ict_scalp_mgc_15m pkg=pkg-f71c2a7b6cf2435a symbol=MGC verdict={'action': 'close', 'reason': '**sl_cross**', 'exit_price': 4393.4}` | `src.runtime.order_monitor` |
| 11:45:02.387 | `placeOrder MarketOrder(orderId=**547**, SELL, totalQuantity=43.0)` | `ib_insync.ib` |
| 11:45:06 | `orderStatus 547: status='**Filled**', filled=**43.0**, remaining=**0.0**, avgFillPrice=**4392.9**` | `ib_insync.wrapper` |
| 11:47:36 | position **11.0** | `ib_insync.wrapper position:` |
| **11:45:09.327** | ❌ `order_monitor: exchange close failed — **leaving DB open**. pkg=pkg-f71c2a7b6cf2435a account=ib_paper symbol=MGC qty=43.0 error=**close not confirmed flat: live_qty=11.0** after ~6.0s` | `src.runtime.order_monitor` ERROR |
| 11:49:36.538 | `re-armed GTC OCA (sl=4210.70714286 tp=4807.4656) on broker-naked trade_id=**5353**` → orders 548/549 | → `oca-protect-t5353` |
| **11:55:20.017** | `RE-ASSERT both_legs_resting ib_paper/MGC trade=**5531** -> sl=4394.47142857 tp=4485.04285714` → orders 550/551, **43 lots** | → `oca-protect-t5531` |
| 11:55:20.048 | `STOP PRICE DIVERGES … journal declares 4394.47142857 but the nearest resting stop is 4210.7 … more_exposed` | ERROR, and it armed anyway |
| 11:55:20.049 | `swept 4 open IB position(s) — **covered=4 naked=0**` | 54 stop lots over 11 real lots graded fully covered |

### The four candidate shapes, graded on evidence

| shape the dispatch offered | verdict |
|---|---|
| rejected / partially filled, journal recorded requested-as-filled | ❌ **REFUTED.** `orderStatus 538` and the position series show a complete 43-lot fill. No rejection; `IB_PLACE_CONFIRM_S` caught nothing because there was nothing to catch. |
| the fill **was** 43 and something closed 32 without journalling | ✅ **CONFIRMED, with the count corrected: 43, not 32.** Order **547** sold all 43. |
| contract-multiplier / lot-size confusion | ❌ **REFUTED.** `multiplier='10'` is consistent across contract, `avgCost` and PnL; journal qty and venue qty are both in lots throughout. |
| duplicate or replayed write | ❌ **REFUTED.** One package, one `linked_trade_id`, one `broker_order_id` (538), one row. |

### Pass 1's open puzzle, resolved

Pass 1 noted the venue average (4423.02) sits closer to 5531's entry (4430.7) than to
5353's (4374.4), and inferred *"the surviving 11 lots may be 5531's partial fill."*
**That inference is wrong, and the arithmetic says why.** IB does not rewrite `avgCost`
on a REDUCTION. The book was 11 @ `43860.0509091` before the fill and 54 @
`44230.21925925` after, so the 43 lots filled at

    (54 × 4423.021926 − 11 × 4386.005091) / 43 = 4432.491349

and when the 43 were sold, the surviving 11 kept the **blended 54-lot average**,
4423.02 — which is exactly why it sits between the two entry prices. The survivors are
5353's lots carrying a blended mark, not 5531's partial fill. **`avgCost` being unchanged
across the 54 → 11 transition is itself the proof that the transition was a reduction.**

---

## Finding 2 — root cause: the confirmation is symbol-scoped, the close is trade-scoped (CRITICAL)

`src/units/accounts/ib_client.py:2636–2703`:

```python
confirm_s = _env_float("IB_CLOSE_CONFIRM_S", 6.0)
...
    last_qty = self._live_position_qty(sym)      # <-- SYMBOL-level
    if last_qty is not None and last_qty <= 0:   # <-- requires the WHOLE SYMBOL flat
        flat = True
```

`_live_position_qty(sym)` returns the account's total position in the symbol. The gate
demands it reach `<= 0`. But `close()` was asked to close **one trade's** lots.

**Whenever the account holds any other lots of that symbol, the gate can never be
satisfied, no matter how perfectly the close executed.** The other lots may be
a sibling journal trade (5353, here), an unjournalled venue position (pass 1's MES 15),
or the untouched remainder of a scaled position. The gate cannot tell the difference
between *"our close did not fill"* and *"our close filled and somebody else's lots are
still here."* It reports both as failure, and the failure branch is explicitly
`leaving DB row open to re-arm protection and retry next tick`.

### This is the pass-1 framing, recurring exactly

Pass 1 closed with: *"Each is a value that is right about a question nobody asked."*
`live_qty=11.0` is **correct**. MGC's position genuinely was 11. It is the answer to
*"is this symbol flat?"* — and it was used to answer *"did trade 5531 close?"*

### The gate's own history makes the symmetry explicit

`alpaca_client.py:875–893` records why the confirmation was added
(`BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`): treating acceptance as success let a
close never fill while the trade was *"journaled `closed` with a **FABRICATED** local
mark-to-market PnL."* The fix removed false-**success** and installed false-**failure**.
Both are the same conflation of symbol-flat with trade-closed; only the sign changed.
The earlier bug fabricated a PnL; this one withholds a real one and arms phantom
protection.

### Contributing (not causal): the position read was already broken

At 08:26:32.972, one line before `execute_pkg`,
`get_existing_position_info: read failed … **no such column: op.id**` — a live SQL
schema error on the position-info read, failing on every dispatch. It did not cause this
incident (the close path does not use it), but a second, independent instrument for
"what do we already hold on this symbol" was unavailable all day. **Filed here, not
worked this pass.**

### A second, quieter symptom of the same conflation

At 11:55:20 the re-assert compared trade 5531's declared stop (4394.47) against *the
nearest resting stop on the symbol* — which was 5353's 4210.7 — graded 5531
`more_exposed` by 1837.7 ticks, and armed. Then the sweeper graded the book
`covered=4 naked=0`. **Three separate instruments (close-confirm, stop-divergence,
coverage sweep) each read symbol-level state to answer a trade-level question, and each
returned a confident wrong answer.**

---

## Finding 3 — blast radius: `bybit_2` is NOT exposed; `alpaca_live` is exposed by code but inert today

The dispatch ruled this ahead of the MGC root cause. Answer, with the population stated.

| account | money | exposed to *this* defect? | why |
|---|---|---|---|
| `ib_paper` | paper | ✅ **YES — actively firing today** | `ib_client.py:2636`; 2 concurrent MGC rows |
| `bybit_2` | **REAL** | ❌ **NO** | Bybit's client has **no close-confirmation at all** (0 hits for `BYBIT_CLOSE_CONFIRM`; positive control returns IB + Alpaca). There is no gate to misfire. |
| `alpaca_live` | **REAL** | ⚠️ **by code path, but inert** | shares `alpaca_client.py`'s identical symbol-scoped confirm (`:1049`, `position_present(sym)` / `p["symbol"] == sym`) — but holds **nothing**: venue `positions: []` (`error: null`), and **all 44** of its journal rows in the window are `rejected`, 0 open, 0 closed. It cannot hit a close path it never reaches. |
| `ib_live` | **REAL** | ❌ no | `mode: dry_run`, `strategies: []` — out of the tick loop |
| `breakout_1` | prop | ❔ **NOT CHECKED** | routed to a `breakout` exchange; I did not read its client. Stated as not-looked, not as clear. |

**So the answer to the dispatch's ordering question is NO — this defect does not reach
`bybit_2`, and reaches `alpaca_live` only in code, not in behaviour.** The MGC root cause
therefore keeps its priority. **But the inertness is not a safety property**:
`alpaca_live`'s immunity is that it cannot place orders at all (MI-140). **Fixing MI-140
arms this defect on a real-money account.** Those two units must not be worked
independently.

The precondition itself is common, not exotic — 13 `(account, symbol)` pairs held ≥2
simultaneously-open rows in the 28-day window, `bybit_2` among them (BTCUSDT, ETHUSDT).
`bybit_2` escapes on the venue's missing gate, not on its trade shape.

---

## Finding 4 — is any CLOSED-trade PnL computed off a phantom quantity?

**This is the operator's actual question, so the population is stated first.**

**Population: all 1000 journal rows, ids 4540–5539, 2026-08-10T13:00Z → 2026-09-07T18:04Z**
— 572 `closed`, 371 `rejected`, 30 `exchange_rejected`, 27 `open`.

I graded a closed row **at risk** if, at its `closed_at`, a sibling row on the same
`(account_id, symbol)` was still open — the exact precondition under which the
flat-confirmation cannot confirm.

| result | value |
|---|---|
| at-risk closed rows | **187 of 572 (32.7%)** |
| by account | `bybit_1` 172 · `bybit_2` 8 · `bybit_portfolio` 7 |
| **on a venue that HAS the defective gate (IB / Alpaca)** | **0** |

**All 187 are Bybit, and Bybit has no close-confirmation.** So within this window
**no closed-trade PnL is corrupted by this defect** — and that is *"we looked and it is
not there"*, not *"we did not look"*.

⚠️ **Three limits on that negative, stated rather than buried:**
1. It covers 1000 rows. Older closes are outside the pull and were not examined.
2. My at-risk test needs a *sibling journal row*. A close blocked by an **unjournalled**
   venue position (pass 1's MES 15 lots) would not be caught by it. I could not test that
   shape — it needs historical venue state, which the diag surface does not serve.
3. It says nothing about the *other* PnL defects this lane has already found; it is
   scoped to this mechanism only.

### What IS fabricated, right now, is an omission

Trade 5531 is `status='open'`, `closed_at=None`, `exit_price=None`, `pnl=None`,
`position_size=43`. Its true realised result is measurable from the venue:

```
entry (43 lots, from the avgCost delta) = 4432.491349
exit  (orderStatus 547, avgFillPrice)   = 4392.9
PnL = 43 × (4392.9 − 4432.491349) × 10  = −$17,024.28
```

**A realised loss of $17,024.28 (paper money) is absent from the book**, and 43 lots that
do not exist are still counted as open exposure. Per `CLAUDE.md`'s provenance rules, any
expectancy, R or win-rate figure computed over `ict_scalp_mgc_15m` today is **FABRICATED,
not estimated** — it omits a real closed loss and carries a phantom open position. I have
**not** recomputed that strategy's aggregates here; correcting them requires the journal
repair, which is Tier-3.

---

## ⚠️ The live hazard — an OPERATOR DECISION, not an action I took

Unchanged from pass 1 and re-verified at 18:54:42Z (`read_state: orders_read`): MGC
carries **54 resting stop lots over an 11-lot position (491%)**, and the oversized group
`oca-protect-t5531` holds the **tighter** stop (4394.5 vs 4210.7) against a venue average
of 4423.02, so **it fires first**. A touch of 4394.5 sells 43 against 11 → **net short
~32 lots**, with `t5353`'s 11-lot stop still resting below. OCA cancels only within a
group.

**Pass 2 adds a second route into the same outcome.** The monitor's `sl_cross` verdict
is evaluated against the same level. It fired once at 11:45 and can fire again — and if
it does, it will place **another** 43-lot market SELL against an 11-lot book. It has not
re-fired only because price recovered above 4394.47 (verdict `None (no action)` on every
subsequent tick through 18:00Z). **Neither route is guarded by the other.**

`ib_paper` is paper — **no real capital is at risk today**, and per Finding 3 no
real-money account is exposed to this mechanism.

⚠️ **I did NOT cancel a leg, edit a journal row, or touch the venue**, per the standing
instruction and `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`.
Note that pass 1's caution is now *doubly* justified: the alarm's own remedy text says
*"cancel the leg that does NOT match trades.stop_loss"*, and **both legs match** —
but we now also know that the leg matching trade 5531 is the one covering a **closed**
trade. **A responder following the remedy text has no correct move available.**

**The decision for the operator** (I recommend the third, and it is not mine to take):

1. Cancel `oca-protect-t5531`'s two legs (548→551 group) and journal 5531 closed at
   4392.9 / −$17,024.28 — removes both hazard routes and repairs the book.
2. Journal-repair only — closes the accounting hole; leaves 43 resting sell lots.
3. **Both, in that order, as one operator-approved action** — cancel first (the hazard),
   then repair the row (the accounting), because a repair alone leaves the legs and a
   cancel alone leaves the phantom row to be re-armed by the next protection sweep.

---

## Proposed fix — **Tier-3, order path. PROPOSED ONLY, NOT APPLIED. No code written.**

The gate must compare against **the lots this close was responsible for**, not against
the symbol.

1. **`src/units/accounts/ib_client.py:2636–2703`** — pass the close's own quantity into
   `close()` and confirm a **reduction of at least that much**, not flatness: capture
   `qty_before = _live_position_qty(sym)` before placing, and confirm
   `qty_before − qty_now >= close_qty` (within a lot tolerance). Flatness remains the
   correct test in the one case where it is equivalent — the account holds nothing else.
2. **`src/units/accounts/alpaca_client.py:842` and `:1049`** — the identical change. Two
   sites; `position_present(sym)` at `:842` and the `p["symbol"] == sym` match at `:1049`
   are the same symbol-scoped test in two spellings.
3. **Preserve the refusal discipline exactly.** A read failure (`None`) must still NOT
   count as confirmed — *we could not look* stays a failure, as today. The change narrows
   what counts as success; it must not widen what counts as unknown.
4. **Do not simply delete the gate.** It exists for a real defect
   (`BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`) that fabricated PnL in the opposite
   direction. Removing it re-opens that; only re-scoping it fixes both.

**Not written as code, deliberately**, per the dispatch's Tier-3 instruction. One design
question should be settled by the operator before anyone writes it: **whether a
partial reduction should confirm.** If the close fills 40 of 43, `>= close_qty` fails and
the row correctly stays open — but so does a 43-of-43 fill mis-measured by a racing
position read. The tolerance is a risk decision, not an implementation detail.

Separately and much smaller (**Tier-1**, and I did not do it): the
`no such column: op.id` failure in `get_existing_position_info` is a live SQL error on a
read path, firing on every dispatch. It deserves its own unit.

---

## What pass 3 of this lane should check

1. **Take the operator decision on the MGC hazard first** (§ live hazard). It is the only
   item here with a clock on it, and it is a decision, not a diagnosis.
2. **The general instrument still does not exist, and pass 2 did not build it either.**
   Pass 1 asked for a `journal-open vs exchange_positions` diff across every account;
   I answered the *closed*-row question by hand and left the open-row diff undone.
   Build it read-only (Tier-1). It would have found this in one call, and it is the only
   thing that will find the next one before the venue does.
3. **Ask how many closes have been falsely refused, across all accounts and all time.**
   The string is distinctive and greppable: `close not confirmed flat`. I measured the
   *precondition* (13 pairs, 187 closed rows) but never the *incidence* — I only ever saw
   the log through nine 2000-line windows on one day. Count the actual events. That
   converts "this can happen" into "this happened N times," which is what the repair
   will be prioritised against.
4. **Couple MI-140 to this.** `alpaca_live`'s only protection is that it cannot place
   orders. Whoever fixes MI-140 arms Finding 3's defect on real money the same day. That
   coupling should be recorded on both units before either moves.
5. **Grade `ib_target_naked` against a same-instant venue read** — pass 1's Finding 5,
   handed to pass 2 and **not done here**. I now know the 11:49:36Z page fired 4 minutes
   after the 11:45 close, so its `target_qty=0.0` was probably *correct* and pass 1's
   suspicion may be unfounded — but I did not read the venue at that instant and I am not
   grading it on inference.
6. **Do not re-derive Findings 1 or 2.** The fill, the close, the fill price and the
   confirmation failure are all quoted verbatim from the systemd journal above.

### One framing to carry forward

Pass 1 found three instruments that were right about their own computation and wrong
about its meaning. Pass 2 found a fourth — and then found that the *fix* for an earlier
bug in this exact code was the thing that installed it. `BL-20260707` removed a
false-success that fabricated PnL, by adding a check whose scope did not match the
operation it was checking; the false-failure it created has now cost a real close, a real
$17k of book, and armed 43 phantom lots of protection. **The lesson is not "verify the
close" — that was already the lesson, and it was learned and shipped. It is that a
verification inherits a scope, and a check whose scope is wider than the thing it
verifies will fail safe in exactly one direction and dangerously in the other.** Pass 3
should assume every remaining confirmation gate in the order path has the same question
to answer: *what is this check's scope, and is it the scope of the thing it gates?*
