# Proposal — reconcile a resting IB protective leg whose PRICE diverges from the journal

**Status:** PROPOSAL. Tier-3 (live order path: `src/runtime/order_monitor.py`,
`src/units/accounts/ib_client.py`). **Not implemented in this PR** — this is the
"analyse and propose the exact change" half the permission tiers require.
Nothing here has run against a broker.

**Filed against:** `BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND` ·
`BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`

---

## 1. The gap, in one line

`IBClient.protection_coverage` has been corrected twice — boolean→quantity
(`BL-20260814`), one-sided→two-sided (`BL-20260816`) — and **it still cannot see
PRICE**, so a position protected at a level no strategy chose grades as fully
covered.

## 2. Evidence (measured, `ib_paper`, 2026-08-20T22:26:01Z)

`/api/diag/ib_open_orders` + `/api/bot/positions`, `read_state: orders_read`:

| sym | trade | pos | declared SL | resting STP | Δ | declared TP | resting LMT |
|---|---|--:|---|---|---|---|---|
| MHG | 4796 | 29 | 6.221714 | 6.2215 | ✓ | 7.141302 | 7.1415 ✓ |
| MGC | 4773 | 95 | 4371.1469 | 4371.1 | ✓ | 4393.0207 | **NONE** |
| MES | 4350 | 15 | 7533.696429 | **7516.50** | **69 ticks** | 8390.59025 | **NONE** |

**MES 4350 is protected 17.196 points below its declared stop — $1,289.73 on 15
contracts at $5/pt.** It graded FULLY STOP-COVERED throughout: quantity right
(15 of 15), side right, only the price wrong. MHG and MGC match their journals
to within a tick **in the same read**, which is what makes this an outlier
rather than rounding.

**It is a direct consequence of the over-cover remediation.**
`BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS` records order **375 @
7533.75** (matches the journal within one tick) and order **338 @ 7516.5** (does
not). **375 was cancelled and 338 kept** — the leg that matched the journal is
the one that was removed, which is precisely what that row's own criterion #1
was written to prevent.

## 3. Why the structure permits it

`protection_coverage` returns
`{size, covered_qty, stop_qty, target_qty, legs, unknown_qty_legs, oca_groups, source}`.
**`legs` is an integer COUNT, not a list** — there is no price anywhere in the
verdict, and none in any consumer. Measured 2026-08-20: `auxPrice` appears in
`src/` **exactly once** (`list_open_orders`, the dump surface — no consumer);
`aux_price`/`lmt_price` appear in `scripts/` **zero** times.

So the sweep asks *"how much qty rests?"* and can never ask *"at what price?"*.

## 4. The mechanism that makes a safe repair possible

⚠️ **This corrects a claim I made earlier in the session and stated to the
operator: that `place_protective` "only places, never cancels", and therefore
that no safe one-shot replacement existed.** That is **wrong**, and the code
says so plainly.

`IBClient._locked_place_protective` **pre-cancels the symbol's resting
protective legs before arming**, scoped to the caller's OCA group when one is
supplied and symbol-wide otherwise
(`_cancel_oca_group_for_symbol` / `_cancel_resting_orders_for_symbol`,
`BL-20260624-MHG-FLIP`). `modify_protective` delegates to it and deliberately
does **not** cancel again, to avoid a double-cancel.

That matters because it rules out the two unsafe repairs I had been weighing:

| approach | why it is wrong |
|---|---|
| `cancel_ib_order --force-protective --force-client-id` on 338 | Order 338 is owned by **clientId 497 — the trader's own execution band**. `cancel_ib_order.py` refuses `< 9000` by default precisely because connecting as that id **evicts the trader's live IB session**. It also leaves the position naked until the next broker-naked sweep (up to `IB_BROKER_NAKED_CHECK_SECONDS`, default 300 s). |
| place a correct stop, then cancel the old one | Two stops of 15 on a 15 long in **disjoint OCA groups** — exactly the double-fire → naked-short hazard `BL-20260816` is about. A fall through 7533.75 fills one and flattens; a continued fall to 7516.50 then fills the other and opens a SHORT. |

**The correct repair is neither.** It is one `modify_protective` call on the
trader's own client, carrying the journal's declared levels: it cancels and
re-arms as a single operation, on the session that owns the order, with no
eviction and no disjoint-group window.

## 5. The proposed change

### 5a. `ib_client.py` — let the verdict carry price (Tier-3 file, additive)

`protection_coverage` already iterates every resting leg to classify and sum it.
Emit the prices it is already looking at, additively, so no existing consumer
changes:

```diff
         return {
             "size": size,
             "covered_qty": covered,
             "stop_qty": stop_q,
             "target_qty": target_q,
             "legs": legs,
+            # PRICES of the resting legs, by side. Additive and nullable-empty:
+            # every existing consumer reads only the qty fields. A leg whose
+            # price cannot be parsed is COUNTED in `unpriced_legs`, never
+            # dropped and never defaulted -- an unparseable price makes the
+            # divergence UNGRADEABLE, which is a third state, not "no
+            # divergence" (the collapse this whole family of bugs is made of).
+            "stop_prices": stop_prices,      # List[float]
+            "target_prices": target_prices,  # List[float]
+            "unpriced_legs": unpriced,       # int
             "unknown_qty_legs": unknown,
             "oca_groups": oca_groups,
             "source": source,
         }
```

A stop leg's price is `auxPrice` (`STP`/`STP LMT`/`TRAIL`); a target leg's is
`lmtPrice`. **Classification must keep testing the stop family FIRST** — `"STP
LMT"` contains `"LMT"`, and an LMT-first test would file every stop-limit as a
take-profit and *manufacture* target coverage.

### 5b. `order_monitor.py` — grade the price, three states, detect-only by default

Inside the existing `_check_broker_naked_ib_positions` sweep, after the coverage
read (so **no additional broker round-trip** — the account-wide
`reqAllOpenOrders` is the cost, and it has already been paid):

```
declared = trades.stop_loss                       # READ, never invented
nearest  = min(stop_prices, key=|p - declared|)   # the leg we would keep
tol      = max(1 tick from config/instruments.yaml, declared * 1e-6)

verdict:
  aligned      -> |nearest - declared| <= tol
  diverged     -> > tol
  ungradeable  -> stop_prices empty while stop_qty > 0, or unpriced_legs > 0,
                  or the instrument's tick size is unknown
```

`ungradeable` is a real state and is **never folded into `aligned`** — that fold
is the exact bug class this repo has now paid for three times on this one
accessor. It alerts (rate-limited per `(account, symbol)`) and repairs nothing.

**Repair is gated by a `*_MODE` env, not a default-off `*_ENABLED` flag** —
following the `NETTING_ATTRIBUTION_MODE` / `CONVICTION_SIZING_MODE` precedent
this repo already uses for exactly this kind of staged order-path change:

- `IB_PROTECTIVE_PRICE_MODE=annotate` **(default)** — grade, alert, write a soak
  row. Byte-for-byte no change to any order.
- `IB_PROTECTIVE_PRICE_MODE=apply` — additionally call
  `execute.modify_open_order` with **both** legs read from the journal
  (`trades.stop_loss`, `trades.take_profit_1`), because `modify_protective`
  re-arms the whole OCA pair and passing only the changed leg would drop the
  other.

**Fail-safe throughout:** a `None` coverage read is skipped (never repair on an
unconfirmed broker read); a declared level that is missing or non-positive makes
the row ungradeable rather than triggering a repair with an invented number; the
repair is attempted at most once per `(account, symbol)` per cooldown window,
mirroring `IB_CLOSE_RETRY_COOLDOWN_S`, so a venue that cannot fill does not get
its bracket cancelled and re-armed every sweep.

### 5c. Tests that fail without the fix

1. `protection_coverage` surfaces `stop_prices` for a stop-only book, and
   `unpriced_legs` counts a leg whose `auxPrice` is unparseable.
2. A stop-limit (`STP LMT`) classifies as a STOP and its `auxPrice` lands in
   `stop_prices` — the manufacture-target-coverage inversion, planted.
3. The MES payload verbatim → `diverged`; the MHG payload verbatim → `aligned`.
   **MHG is the discriminating control**: a grader that flags all three is as
   broken as one that flags none.
4. `annotate` mode places no order (assert the client is never called).
5. `apply` mode calls `modify_open_order` with **both** journal levels, never a
   computed one.
6. Empty `stop_prices` with `stop_qty > 0` → `ungradeable`, and the repair is
   **not** attempted.

## 6. Rollout

`annotate` on `ib_paper` → read the soak rows → operator decides `apply`. The
sweep is already cadence-gated (`IB_BROKER_NAKED_CHECK_SECONDS`, 300 s) to stay
clear of the IB pacing/wedge class, and this adds no new read.

## 7. What I have NOT verified

- **No code here has been run.** The diff is a proposal read off the current
  files, not a tested patch.
- **The tolerance is unchosen.** One tick is a starting point, not a measured
  value. MHG and MGC both sit inside one tick of their declared level in the
  live read, and MES sits 69 ticks out, so the two populations are far apart —
  but that is n=3 on one account on one day, and a tolerance shipped on it would
  be a value with no distribution behind it (the exposure-ceiling mistake).
  **`annotate` mode exists to produce that distribution before `apply` is
  considered.**
- **Whether the MES divergence is still live when this is read.** It was live at
  22:26:01Z; the position was ~131 points above the resting stop, so there is no
  urgency, but a repair must re-measure rather than trust this document.
- **`BL-20260816-IB-PROTECTIVE-STOPS-NEVER-SET-OUTSIDERTH` is untouched and
  interacts with this.** Every IB protective stop carries the library default
  `outsideRth=False`; whether that makes a GTC futures stop inert outside RTH is
  a broker-behaviour question this repo holds no evidence on. A re-arm through
  this path inherits that, whatever it turns out to be.
