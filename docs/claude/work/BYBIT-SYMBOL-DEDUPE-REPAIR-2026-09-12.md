# The Bybit position dedupe keys on the SYMBOL, and it closed live positions

> **Doc status:** `live` · category `evidence` · created `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
>
> MI-283 · session `session_01YEdjrYi5Kpp9dLw3yQNyNm`

Repairs `BL-20260909-ACCOUNT-OPEN-POSITIONS-DEDUPES-BYBIT-POSITIONS-BY-SYMBOL-SO-A-HEDGE-BOOK-IS-DROPPED-AND-THE-ORDER-STATUS-RECONCILER-CLOSES-A-LIVE-ROW`.
Handed on by MI-281 (`WO-20260912-REPAIR-THE-HEDGE-BOOK-FLAT-READ-THAT`), which repaired the
SIBLING reader (`_bybit_position_protection`) and verified this one still broken.

**Tier-2 — order path. Prepared, validated, and NOT merged on a session's judgement.**

---

## 1. The defect

`src/units/accounts/clients.py::account_open_positions`, bybit branch, gated on
`if sym in seen` — **SYMBOL alone**, consulting no account and no `positionIdx`. Since
`BYBIT_HEDGE_MODE_SYMBOLS` was armed (2026-08-30) a hedge symbol returns one row per
book, so the second LIVE book was discarded.

**Why it reached money.** The returned list feeds `order_monitor._exchange_position_set`,
which keys on `(symbol, normalised_side)`. The two books of a hedge symbol are **opposite
sides**, so dropping one removed a whole `(symbol, side)` pair from that set — and the
journal row sitting on the dropped side read FLAT at the close test
(`order_monitor.py:4494`), so `_reconcile_open_trades` closed a live position.

## 2. MEASURED — incidence

**Population:** the 1000 newest rows of `position_read_state_soak.jsonl`, read
2026-09-12 via `/api/diag/log_file?name=position_read_state_soak&lines=1000`. A **TAIL**
of a 14,722,608-byte file, **not the lifetime**. Window `2026-09-12T03:20:49.524Z →
07:36:53.644Z` (4.27h). 1000 parsed, 0 unparseable.

| account | reads | reads dropping a NON-ZERO book |
|---|--:|--:|
| `bybit_1` | 376 | **376 (100.0%)** |
| `bybit_2` (REAL MONEY) | 368 | 0 (0.0%) |
| `bybit_portfolio` | 256 | 0 (0.0%) |

Dropped on `bybit_1`: `SOLUSDT idx=1 Buy` ×291, `BTCUSDT idx=2 Sell` ×241.

This **independently reproduces** #11867 (which measured 378/378 over an earlier window);
it is a second measurement on a different window, not a re-quote.

### ⚠️ The real-money zero is NOT safety — and the mechanism says why

The `size <= 0` skip runs **before** the dedupe, and a zero-size row **never enters
`seen`**. So the dedupe bites only when **TWO books are BOTH non-zero**.

Over the same window `bybit_2` **enumerated both hedge books on every single read** —
`BTCUSDT` idx1+idx2 and `ADAUSDT` idx1+idx2, **368 reads each** — every one zero-size.
That is the positive control: the hedge enumeration is **live on real money right now**;
the account simply held no two-sided book in the window.

**The exposure is structural and one two-sided position away, not absent.** The dedupe
consults no account id.

## 3. MEASURED — the attributed harm, reproduced

**Population:** `/api/diag/journal?table=trades&limit=1000`, read 2026-09-12, ids
**4719–5718**.

| # | closed | re-adopted | gap | `order_package_id` | manufactured pnl |
|---|---|---|---|---|--:|
| SOL | 5704 `02:07:14Z` `reconciler_filled` | 5715 `05:05:31Z` `adopted_orphan` | 2h58m | `pkg-4f8140865a9a4441` **(identical)** | **−706.888** |
| BTC | 5705 `01:08:04Z` `reconciler_filled` | 5712 `03:03:12Z` `adopted_orphan` | 1h55m | `pkg-85265901ef3948ef` **(identical)** | **−55.608** |

Sum **−762.496**, matching #11867 exactly.

⚠️ **`exit_reason` on both closes is `reconciler_filled`, NOT `netting_attributed`.** That
is `_reconcile_open_trades` — the order-status reconciler, i.e. precisely the consumer of
`_exchange_position_set`. The backlog row's title names that path, and this confirms it
against the journal rather than by reading the code.

⚠️ **The discriminator is the IDENTICAL `order_package_id`, not size coincidence.** An
`adopted_orphan` is by definition a venue position no journal row matched. A re-adopt
landing on the *same package as the row just closed* is direct evidence the venue never
closed it.

## 4. U2 — RESOLVED, and it does NOT need its own lane

MI-281 declined to absorb a finding: both post-deploy adopts (5715, 5712) follow **no**
`netting_attributed` close, and each follows a `pairs_stop` close ~105s earlier of the
**opposite** direction and an unrelated size. It looked like the adopt-ATTRIBUTION defect.

**MEASURED** (same journal population; closes in the 15 min before each adopt — there were
exactly two, and they are one pairs pair):

| adopt | Δ | the close before it |
|---|--:|---|
| 5715 (SOL **long**) | −106.0s | 5710 `pairs_sol_eth_a` **SOLUSDT short** 19.7 `pairs_stop` |
| 5712 (BTC **short**) | −105.3s | 5708 `pairs_bnb_btc_b` **BTCUSDT long** 0.007 `pairs_stop` |

**It is the SAME defect, observed from the other side. The pairs close is the TRIGGER that
made the hidden book visible.** The chain, and every step is evidenced above:

1. On `bybit_1`/SOLUSDT the **emitted** book was idx=2 (short); idx=1 (long) was the one
   dropped 291×. The pairs sleeve and the directional legs **share the book** on this
   account.
2. `_reconcile_open_trades` could not see `(SOLUSDT, long)`, so it false-closed 5704.
3. `pairs_sol_eth_a`'s **short** leg closed at `05:03:45Z` → the **short book went to
   zero**.
4. A zero-size row is dropped by the `size <= 0` gate **before** the dedupe, so it never
   enters `seen` — **the long book stopped being deduped away and became visible for the
   first time**.
5. The reverse reconciler's adopt pass saw a live `(SOLUSDT, long)` with no open journal
   row (5704 having been false-closed in step 2) and adopted it — onto 5704's own package.

BTC is the mirror image: the emitted book was idx=1 (long, the pairs leg), idx=2 (short)
was dropped 241×, and the pairs **long** closing exposed the short.

⚠️ **This is not a separate adopt-attribution defect, and filing it as one would have sent
a lane chasing a mechanism that is not there.** It also explains why the defect *looked*
intermittent: the drop is continuous (100% of reads), but it only becomes *visible* when
the other book flattens.

**The fix severs the chain at step 2**, so steps 3–5 cannot arise.

### Two things I checked and deliberately did NOT file

**No new backlog row is filed from U2.** Both candidates were checked against the repo and
neither is a defect; filing them would be the noise the backlog-governance rule exists to
refuse.

1. **`5715`/`5712` closed by `stuck_strategy_watchdog` with `exit_price: None` and
   `pnl: None`.** Checked, not assumed: a NULL exit price on an adopted-orphan close is
   **deliberate and documented in the code** — `order_monitor.py:3193`, *"exit_price stays
   NULL — we don't have a Bybit-side fill record for an order we never placed."* The rows
   are a CONSEQUENCE of the false-close chain, not an independent fault, and severing the
   chain at step 2 means they do not arise. (`backlog_search` surfaced
   `BL-20260819-TRADE-4164-CLOSED-WITH-NULL-PNL` at 0.71 lexical overlap; it is a
   different mechanism — `entry_order_avg_price_unreliable` — and this is not a
   recurrence of it.)
2. **The pairs sleeve shares `bybit_1`'s book with the directional legs**, which is what
   couples the two and made the defect look intermittent. This is not new: the standing
   plan to move the sleeve to its own account is already **deferred, not cancelled**
   (CLAUDE.md, `BYBIT_HEDGE_MODE_SYMBOLS`). Recorded here as a second concrete reason for
   that existing decision, not re-filed as a finding.

## 5. The fix

Dedupe on `(symbol, position_idx)` via a new pure `_bybit_book_key`, and emit
`position_idx` on every returned row.

**Two sets, deliberately.** `seen_books` keys the EMIT dedupe; `seen` stays keyed on
**SYMBOL** and gates only the per-symbol cross-check, whose question ("did the settleCoin
page surface this symbol at all") is genuinely symbol-shaped. Re-keying *that* would fire
a fresh `get_positions` for **every configured symbol on every read** — an unbounded
per-tick broker round-trip, which is the shape of both June 2026 wedges. Pinned by
`test_the_cross_check_fires_no_extra_venue_call`, with
`test_an_unsurfaced_symbol_is_still_cross_checked` as its non-vacuity control.

### `bybit_position_book.py` is deliberately NOT reused

It was considered and rejected, and the reason is the point: `select_position_row` answers
*"which ONE book is this symbol-scoped read about"* and **REFUSES on
`ambiguous_multi_book`** — by design, because its caller grades protection for one book.
`account_open_positions` is an account-wide **LISTING** that must return **every** live
book; a refusal there would drop *both*. **A protection grader and a position lister ask
different questions, and collapsing them would be this same defect one level up.**

The two modules' polarities on an unreadable value are therefore opposite **on purpose**,
and that is documented at both sites: a SELECTION errs toward refusing, a LISTING errs
toward emitting — because a DROP is what closes live positions, while an extra row is
idempotent for every set-keyed consumer downstream (asserted, not asserted-by-prose).

## 6. Blast radius — every consumer, read not assumed

The change can only ever **ADD** a row that was previously discarded. It never removes or
reorders one.

| consumer | shape | effect |
|---|---|---|
| `_exchange_position_set` → `_reconcile_open_trades` (4494) | **set** of `(sym, side)` | gains the missing pair ⇒ **CLOSE → DEFER**. The fix. |
| `_exchange_position_set` → `_watchdog_stuck_strategies` (5543) | same set | `position_alive` True ⇒ defer. Same direction. |
| `_reconcile_orphan_exchange_positions` close-on-disappear (3129) | its own inline set | fewer `adopted_orphan_disappeared` closes. Same direction. |
| `_reconcile_orphan_exchange_positions` **adopt pass** (3567) | walks `positions` | ⚠️ **the one genuinely new decision** — see below |
| `closed_flat_invariant._residual_from_positions` | filters sym **AND** side, then sums | opposite-side row filtered out ⇒ **no change** |
| `hourly_report` | `len(positions)` | count rises, and becomes correct. No decision. |
| `account_reachability_alert` | `None` vs list | unaffected |

**Because every money-path consumer reads a SET, a same-side duplicate is idempotent and
the set is a strict superset of what it was.** The reconciler can only move from *close*
toward *defer*, never the other way. Asserted directly in
`test_the_set_can_only_gain_pairs_never_lose_them`.

### ⚠️ The one new behaviour, stated rather than buried

The **adopt pass** walks `positions` directly and adopts any `(symbol, side)` absent from
the open journal rows. A live hedge book that is currently **invisible** will become
visible, so it **can now be adopted** — which **creates a `trades` row**, on a live
account.

This is the repo's own declared intent (`_orphan_position_policy` defaults to `adopt`:
*"an orphan is a problem to RESOLVE, never a status to rest in"*), and the path is guarded
by the re-adopt flap guard and the MI-255 orphan-attribution size gate. **It is still a
new write on a live account, which is why this is Tier-2 and is being asked rather than
merged.**

## 7. What is NOT established

- **A unit test clears nothing here, and this change does not claim otherwise.** A fixture
  cannot reproduce which row Bybit lists first, and that ordering IS the mechanism. The
  fleet observation is owed AFTER deploy: a `position_read_state_soak` row on `bybit_1`
  with `dropped_symbol_dedupe_count: 0` while `emitted` reflects both books, plus no new
  `reconciler_filled → same-package adopt` pair.
- **The Bybit row ORDERING on any specific read** — no repo surface exposes the raw
  `get_positions` payload (MI-281 recorded the same limit).
- **The nine pre-deploy flat reads remain permanently unadjudicable** (0 of 993 pre-deploy
  rows carry `position_idx` or `exchange_read_source`), one of them trade **5461 on
  real-money `bybit_2`**. *"We cannot establish this"* is the correct final answer and is
  not revisited here.

## 8. Second site — found, not named by the backlog row

`account_bybit_open_orders` (`clients.py`) carried the **identical** symbol-only dedupe —
and it **already computed `position_idx`** while still collapsing two books. Found by
grepping the fix site's own pattern across the file; the backlog row describes only
`account_open_positions`.

**Not the order path** — its sole consumer is `/api/diag/bybit_open_orders`
(`src/web/api/routers/diag.py:2935`), read-only. But that route is the instrument
`OI-20260909-INTENT-REDUCE-LEG-RESIZE-...`'s own `clears_when` tells a session to verify a
resized protective leg against, so a dropped book means a **VERIFICATION made against the
wrong book**. Fixed in the same PR because it is the same defect in the same file.

## 9. A retired alarm, and an honest label change

The `position_read_state` / `hedge_book_dropped` WARN fired on **every** dropped book —
**506 occurrences** in `docs/claude/ERROR-FEED-DIGEST.md`, and `position_read_state` is
**168 of the 1000 rows** in the (capped, therefore truncated) warn feed measured
2026-09-11. That finding calls it **feed occlusion** rather than pager fatigue, because
WARN is persist-only; retiring this class widens the reviewable history of every other
warn class.

After the fix the only reachable path to that branch is **the venue listing the same book
twice**, so the label `hedge_book_dropped` would name a cause no code path can reach —
`diagnostic-provenance` sub-class **A**, in our own alarm. It is renamed to
`duplicate_book_dropped` and the event goes from routine to anomalous. **Checked before
renaming:** nothing BRANCHES on the string (producer, one test, two generated digests).

## 10. Validation

- **80 passed** across the five directly-affected suites; **18 new** assertions.
- **7 mutants, 7 caught, 0 survived** — every load-bearing predicate shown to go red:
  symbol-only dedupe restored · `position_idx` dropped from the row · `_bybit_book_key`
  collapsed to `None` · unparseable id collapsed to `None` · cross-check gate re-keyed
  (the wedge regression) · diag surface reverted · zero-size skip moved after the dedupe.
  Baseline green either side, file restored byte-identical (md5 verified).
- Three assertions in the existing suites were **INVERTED rather than deleted**, each
  naming what it used to say. MI-222 pinned this defect *deliberately* — its scope was
  observation-only and its docstring reads *"The defect is COUNTED, not fixed. Fixing it
  is the Tier-3 change."* This is that change, and the file still records when it moved.
