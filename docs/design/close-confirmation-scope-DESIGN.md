# Close-confirmation SCOPE — a check must be scoped to the thing it gates

> **Doc status:** `unknown` · category `architecture` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

> **Status: PROPOSED — Tier-3, order path. NOT MERGED. `landing: hold`.**
>
> Unit MI-168, dispatched 2026-09-07 on an explicit operator decision.
> Specification: [`docs/claude/diagnoses/MI-167-pipeline-integrity-pass-2.md`](../claude/diagnoses/MI-167-pipeline-integrity-pass-2.md)
> (branch `mi-167-pipeline-integrity-pass-2`, PR #11274 — **not on `main` at the
> time of writing; I read it from the branch**).
>
> MI-167 deliberately wrote no code. This memo turns its prose into a concrete,
> reviewable change so the operator can approve or reject something specific.
> **It corrects one part of MI-167's proposal on evidence — see § "Alpaca is a
> different defect".**
>
> ⚠️ **COUPLED TO MI-140 IN BOTH DIRECTIONS.** MI-140 must not be remediated
> before this lands. See § "The MI-140 coupling".

---

## 1. The defect

`IBClient.close` confirms a close by requiring the whole **SYMBOL** to reach
flat. The close is of a single **TRADE**.

```python
last_qty = self._live_position_qty(sym)      # SYMBOL-level
if last_qty is not None and last_qty <= 0:   # requires the WHOLE SYMBOL flat
    flat = True
```

So whenever the account legitimately holds other lots of that symbol — a
sibling journal trade, an unjournalled venue position, the untouched remainder
of a scaled position — **the gate can never be satisfied, no matter how
perfectly the close executed**, and the failure branch is explicitly
*"leaving DB row open to re-arm protection and retry next tick"*.

**Measured consequence** (MI-167, from the systemd journal; I did not
re-derive it and I did not re-read the venue):

| when (UTC) | what |
|---|---|
| 11:45:02 | `placeOrder MarketOrder(orderId=547, SELL, totalQuantity=43.0)` |
| 11:45:06 | `orderStatus 547: status='Filled', filled=43.0, remaining=0.0, avgFillPrice=4392.9` |
| 11:45:09 | ❌ `close not confirmed flat: live_qty=11.0` → **row left open** |
| 11:55:20 | 43 lots of stop re-armed over an 11-lot position |

A realised **−$17,024.28** absent from the book, a phantom 43-lot open row, and
43 re-armed sell lots. `live_qty=11.0` was **correct** — MGC's position
genuinely was 11. It answers *"is this symbol flat?"* and was used to answer
*"did trade 5531 close?"*

**The precondition is common, not exotic:** 13 `(account, symbol)` pairs held
two or more simultaneously-open rows in the 28-day window.

### The finding MI-167 did not make: the evidence was already in hand

`_cancel_own_close_order` — called *inside this very failure branch* — already
reads the close order's `orderStatus` and returns `"already_filled"`. So the
2026-09-07 failure envelope literally carried the fact that the close had
filled, and used it only to phrase a cancel.

This is not inference. The regression test reproduces it (§ 5):

```
close not confirmed flat: live_qty=11.0 after ~0.5s — close order 1001 was
accepted but the position is still open; leaving DB row open to re-arm
protection and retry next tick (own close order: already_filled)
```

**The system read "the close filled" to decide how to give up, and never to
decide whether to.**

---

## 2. The proposed change (IB)

Confirm against evidence that is **trade-scoped**, in this order:

| | evidence | why |
|---|---|---|
| **(a)** | **the close order's own fill** — `_order_filled_qty(close_trade)` | The venue telling us what **our** order did. Trade-scoped by construction. |
| **(b)** | **the position reduction** — `qty_before − qty_now >= need` | `qty_before` is the **Step-0 clamp read already taken** before the order was placed. No extra broker call. |
| **(c)** | **flatness** | Still sufficient, still exactly right when the account holds nothing else. Nothing that used to pass stops passing. |

### Why (a) is primary, and why this improves on MI-167's prose

MI-167 proposed **only** (b) — `qty_before − qty_now >= close_qty` — and then
correctly flagged its own weakness: *"a 43-of-43 fill mis-measured by a racing
position read."*

That weakness is worse than it looks. **The precondition for this entire defect
is that other lots of the symbol exist**, so concurrent activity on that symbol
is the *expected* case, not an edge case — and a position delta can be
corrupted, in both directions, by a sibling's fill landing inside our confirm
window. A delta-based test is vulnerable to precisely the scenario it exists to
handle.

The order's own fill is not. Preferring (a) largely dissolves the race MI-167
raised, and it is **already available at zero cost** — no new broker call, no
new read, no new dependency.

### What does NOT change

* **The gate is not deleted.** It exists for `BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`,
  where treating acceptance as success let a close never fill while the trade
  was journaled `closed` with a **FABRICATED** mark-to-market PnL. Deleting it
  re-opens that. Only re-scoping fixes both directions.
* **A read failure is still not a confirmation.** MI-167's rule 3, kept
  exactly: this narrows what counts as **success**; it must not widen what
  counts as **unknown**.
* **The substring `not confirmed flat`** stays on every refusal path. It is
  **load-bearing**: `order_monitor._apply_update` string-matches it
  (`order_monitor.py:1012`) to arm the `IB_CLOSE_RETRY_COOLDOWN_S` defer, and
  MI-167 named it as the greppable marker for counting this defect's incidence.
  Rewording it would silently disarm the cooldown and destroy the audit trail.

### The rollback

`IB_CLOSE_CONFIRM_SCOPE=symbol` restores the pre-MI-168 flatness test
byte-for-byte — **one env flip + restart, no redeploy**. Pinned by a test that
deliberately asserts the defect, because that is what a rollback of this change
means. `IB_CLOSE_CONFIRM_S <= 0` still disables the gate entirely, as today.

---

## 3. ⚠️ THE OPEN DESIGN QUESTION — should a PARTIAL reduction confirm?

**MI-167 left this to the operator and I have not decided it silently.** It is
shipped as a declared knob, `IB_CLOSE_CONFIRM_PARTIAL`, with both behaviours
implemented and both pinned by tests. **This is the one thing in this PR that is
a risk judgement rather than a repair.**

The case: `close_qty` is 43 and the venue fills **40**.

| | `strict` (**the shipped default**) | `reduction` |
|---|---|---|
| **rule** | evidenced reduction must cover the whole `close_qty` | any reduction ≥ 1 contract confirms |
| **40 of 43** | refuses → row stays **open** → retry | confirms → row journaled **closed** |
| **errs toward** | a **phantom OPEN row**: 40 lots are really gone but the journal still claims 43 | a **phantom CLOSED row**: the journal says closed while **3 lots are still held** |
| **the PnL it produces** | none yet — the close is retried and the row is closed when it completes | a PnL computed over 43 lots when 40 closed — **FABRICATED** on the remainder, per `CLAUDE.md` § provenance |
| **who cleans up** | the retry, then the reconcilers, which already handle a stale open row | **nothing**: a closed row with a live position behind it is invisible to the close path, and the 3 lots are now **unprotected** (Step 1 cancelled the bracket) |
| **failure class** | the one MI-167 measured — a false **refusal** | the one `BL-20260707` exists to kill — a false **success** |
| **cost if wrong** | churn: a retry cycle, bounded by `IB_CLOSE_RETRY_COOLDOWN_S` | a wrong number in the book **and** a naked position |

**My recommendation is `strict`, and the reasoning is asymmetric rather than
cautious:** the two errors are not the same size. A phantom open row is
recoverable by machinery that already exists and already runs. A phantom closed
row fabricates a PnL *and* leaves lots naked, and nothing downstream is looking
for it. MI-167's own framing applies — *"a check whose scope is wider than the
thing it verifies will fail safe in exactly one direction and dangerously in
the other"* — and `reduction` is the dangerous direction.

The counter-argument, stated fairly: `strict` can loop on a venue that
genuinely fills in pieces, and each retry cancels and re-places the protective
bracket. That cost is real. It is bounded by `IB_CLOSE_RETRY_COOLDOWN_S` (300s
default), and it was **substantially reduced** by preferring (a) over (b),
since the order's own fill does not suffer the measurement race that would
otherwise have produced spurious partials.

⚠️ **An unparseable value falls back to `strict`** — never to the permissive
setting. A typo must not silently arm partial confirmation. Pinned by a test.

**The operator decides. If `reduction` is chosen, this memo's § 3 should be
rewritten to say so and the default flipped in one commit, so the record shows
a decision was taken rather than a default drifting.**

---

## 4. The failure branch, and the three-state discipline

### What the new branch does: **the same thing, on a correct trigger**

MI-168 changes **when** the failure branch fires, not what it does. Leaving the
DB row open so protection re-arms and the close retries is the **correct**
response to a close that genuinely did not execute — the position really is
still there, and really is unprotected, because Step 1 cancelled its bracket.

**The phantom protection MI-167 measured was not caused by this branch being
wrong. It was caused by this branch firing on closes that had SUCCEEDED.** Fix
the trigger; keep the response. Changing the response as well would break the
genuine-failure case, which is the case the branch was written for.

### The three states, never collapsed

Today, *"we looked and the lots are still there"* and *"we could not look"*
return the **same envelope** and are indistinguishable afterwards. That is the
collapse `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" names, in the one
place where the difference decides whether a retry can ever work.

| `confirm_state` | means | retCode | action |
|---|---|---|---|
| `confirmed_order_filled` | the venue says **our order** filled the lots | 0 | close the row |
| `confirmed_reduced` | the symbol fell by at least this trade's lots | 0 | close the row |
| `confirmed_flat` | the symbol is flat | 0 | close the row |
| `not_reduced` / `not_flat` | **WE LOOKED** and this trade's lots are still there. A real failure. | 1 | leave open, re-arm, retry |
| `unreadable` | **WE COULD NOT LOOK.** The instrument went blind. | 1 | leave open, re-arm, retry |
| `not_checked` | `IB_CLOSE_CONFIRM_S <= 0` — the gate is off. **Not a confirmation anybody made.** | 0 | — |

`unreadable` takes the **same action** as `not_reduced`, deliberately: if we
could not look, the position may be open and unprotected, so re-arming and
retrying is the fail-safe direction, and MI-167's rule 3 forbids widening what
counts as unknown. But it is **named**, so a reader can tell a venue that
refused from an instrument that went blind. Those have different remedies, and
only one of them means *"a retry will never work"*.

`confirm_state` is returned in the envelope on **every** path — including the
success paths and the gate-disabled path — so a consumer can never read
*"we did not look"* as *"we looked"*.

⚠️ **The failure `retMsg` now carries `filled=`.** On 2026-09-07 that field
would have read `43.0` while the envelope said the close had failed. It is the
single most diagnostic value at this site and it was previously absent from
every surface a human reads.

⚠️ **`confirm_state` currently has no consumer that BRANCHES on it** — it is
recorded, not acted on. Stated rather than hidden, because `CLAUDE.md` is
explicit that a signal written and never read is worse than a missing one, and
because `collapsed-state-guard` requires a real consumer per state before a
contract can be registered. **This is deliberately NOT registered with
`collapsed-state-guard` yet**, for the reason the `BYBIT_HEDGE_MODE_SYMBOLS`
row already gives about registering a state nothing branches on: it would
either fail the guard or invite a decorative branch. Registering it is
follow-up work once a consumer exists, and it is named here so the follow-up is
not lost.

---

## 5. The regression test, and proof it fails on today's code

`tests/test_close_confirm_trade_scope.py` — 12 tests. The headline case
reproduces the two-open-rows-one-symbol precondition: 54 MGC lots (43 for trade
5531, 11 for sibling 5353), close 43, venue fills 43, symbol settles at 11.

**A test that passes before and after proves nothing.** Run against the
unmodified `src/units/accounts/ib_client.py` at `main` (`054650e`), same
harness, same command:

```
$ git stash push -- src/units/accounts/ib_client.py
$ python3 -m pytest tests/test_close_confirm_trade_scope.py -q
...
8 failed, 4 passed in 2.13s
```

The headline assertion, verbatim:

```
E  AssertionError: the close filled 43 of 43 at the venue and must be
   confirmed; a sibling trade's lots are not evidence that OUR close failed —
   got {'retCode': 1, 'retMsg': 'close not confirmed flat: live_qty=11.0 after
   ~0.5s — close order 1001 was accepted but the position is still open;
   leaving DB row open to re-arm protection and retry next tick
   (own close order: already_filled)'}
E  assert 1 == 0
```

**`live_qty=11.0` — the production value, reproduced in the harness.** And
`already_filled`, which is § 1's finding measured rather than read.

With the change: **12 passed.**

**The 4 that pass in both runs are the Alpaca tests plus the typo-fallback
test** — they pass pre-fix because they assert behaviour this change does not
alter. That is stated rather than glossed: only 8 of the 12 are evidence of the
repair.

**Adjacent suites, with the change applied:** `test_p3_close_wiring.py`,
`test_alpaca_wiring.py`, `test_close_retry_cooldown.py` and this file →
**144 passed**. The wider IB/monitor/protection sweep → **844 passed, 15
failed**, and those 15 are **pre-existing and environmental**, not mine:
identical counts with and without the change (13 failed / 23 passed on
`test_ib_sizing_and_data.py` either way), cause `ModuleNotFoundError: No module
named 'ccxt' / 'pandas'` in this sandbox. Four further suites could not be
collected at all for the same reason. **CI runs the full suite with the real
requirements; that is where this gets its real verdict.**

---

## 6. Alpaca is a different defect — a CORRECTION to MI-167

**MI-167 § "Proposed fix" item 2 asks for "the identical change" at
`alpaca_client.py:842` and `:1049`. Applying it as written would install a
false-SUCCESS on a real-money-capable path. I have not applied it.**

MI-167 is right that the two sites are *the same symbol-scoped test in two
spellings*. But what decides whether a scope is wrong is **the operation the
test gates**, and on Alpaca that operation is also symbol-scoped:

| site | operation | scope of the operation |
|---|---|---|
| `close(symbol)` `:1049` | `DELETE /v2/positions/{sym}` | **whole symbol** — Alpaca's native liquidation |
| `_close_extended_hours(symbol)` `:842` | limit order for `qty` read from `pos.get("qty")` | **whole symbol** — the live position, not the caller's trade |
| `execute.close_open_position` | receives a per-trade `qty` and, on the alpaca branch, **discards it** | — |

`AlpacaClient.close` **takes no quantity at all** (pinned by a test). So on
Alpaca the operation and the confirmation are both symbol-scoped: **they match,
symbol-flatness is reachable, and the false refusal cannot occur.** Established
by measurement, not by reading — `test_alpaca_close_is_not_exposed_to_the_false_refusal`
puts a sibling's lots on the symbol and the close still confirms.

Re-scoping the Alpaca *confirmation* without changing the *operation* would
confirm a trade-sized reduction while the venue had in fact liquidated
everything — journalling one trade closed and leaving its sibling's row open
with no position behind it. That is `BL-20260707` re-introduced, on the path
that reaches `alpaca_live`.

### What Alpaca's defect actually is: an OVER-CLOSE

Closing trade A silently liquidates sibling trade B's shares while B's journal
row stays open with nothing behind it — **the mirror image of IB's false
refusal**. Pinned as current measured behaviour by
`test_alpaca_close_of_one_trade_liquidates_its_sibling_too`.

**Remedying it is a separate Tier-3 decision and is NOT in this PR**, because
the honest fix is to make the *operation* trade-scoped (place a qty-sized
opposing order instead of `DELETE /v2/positions/{sym}`) — a materially larger
order-path change than a confirmation re-scope, on a real-money-capable client,
with its own share-hold and `qty_available` interactions. It should be its own
unit. **Filed, not worked.**

---

## 7. The MI-140 coupling

`alpaca_live` is **REAL MONEY**. It is immune today for exactly one reason: it
cannot place orders at all (MI-140 — both signals since arming were shorts
suppressed by the operator's `side_filter: long` gate). **Remediating MI-140
arms an Alpaca close-path defect on a real-money account.**

Recorded as a `blocked_on` edge in **both directions**:

* [`WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED.yaml`](../claude/work/objects/WO-20260907-CLOSE-CONFIRMATION-IS-SYMBOL-SCOPED.yaml)
  → MI-140
* [`WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG.yaml`](../claude/work/objects/WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG.yaml)
  → this unit

⚠️ **The coupling holds, but its technical basis is NOT the one the dispatch
stated.** The dispatch's premise was that MI-140 arms *this* defect — the
symbol-scoped false refusal — on real money. § 6 establishes by measurement
that it does not: Alpaca is not exposed to the false refusal. What MI-140 arms
is the **over-close** in § 6. **The coupling is if anything stronger for it** —
an over-close silently liquidates a position nobody journalled closed, where a
false refusal merely withholds a close — but the record must say which defect
it is coupled to, or a later session will look for the wrong thing and find
nothing.

### `bybit_2` is NOT exposed, and must not be "fixed"

`bybit_2` (**REAL MONEY**) has **no close-confirmation at all**:
`grep -rn "BYBIT_CLOSE_CONFIRM" src/` → **0**, with the positive control
`grep -rn "_CLOSE_CONFIRM_S" src/` returning IB and Alpaca — so the probe
demonstrably finds confirmations that exist. It escapes on a **missing venue
gate**, not on its trade shape, and it is among the 13 pairs holding concurrent
open rows.

**Adding a confirmation to Bybit is explicitly out of scope.** It would arm a
new gate on a real-money account, which is not what was approved, and — on this
memo's own evidence — a gate whose scope is not thought through is how this
defect was created in the first place.

---

## 8. What this PR does NOT do

* **It does not touch the venue, cancel a leg, or edit a journal row.** The MGC
  remediation (MI-167 § "The live hazard") is separately operator-approved and
  is not mine.
* **It does not arm `ICT_SCALP_EXIT_HEAD_MODE`, re-grade any exit-matrix cell
  `status`, or change `TP_VENUE_CAP_PCT`.**
* **It does not measure INCIDENCE.** MI-167's pass-3 item 3 asks how many
  closes have been falsely refused across all accounts and all time, greppable
  on `close not confirmed flat`. **I did not count them** — this memo
  establishes the mechanism and the precondition (13 pairs), never the event
  count. *We did not look* is not *we looked and found none*.
* **It does not fix `no such column: op.id`** in `get_existing_position_info`,
  the live SQL error MI-167 filed as Tier-1 and deferred. Still open.
* **It does not resolve the duplicate work object.** `WO-20260906-` and
  `WO-20260907-ALPACA-LIVE-S-ROUTED-REAL-MONEY-LEG` are two objects for the
  same work. I wrote the edge on the `-0907-` one the dispatch named and left
  the duplicate alone rather than merging two objects unasked. **Filed here so
  it is not lost.**
