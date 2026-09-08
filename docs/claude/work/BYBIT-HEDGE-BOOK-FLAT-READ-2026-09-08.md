# The `bybit_1` ETHUSDT orphan (trade 5569) — root cause: a hedge-book-blind venue read

> **Doc status:** `live` · category `evidence` · created `2026-09-08` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
>
> **Unit:** MI-204 · object `WO-20260908-RECONCILE-THE-BYBIT-1-ETHUSDT-ORPHAN-TRADE`
> **Session:** `session_016KUFTHGYyFDerbnXDSA6Wb` · operator-directed 2026-09-08
> **Scope:** measurement and investigation only. Nothing was closed, flattened, cancelled or re-armed.

---

## The headline

**The orphan is not an orphan. It is the same position the system closed by mistake four minutes earlier.**

`_reconcile_netting_partial_closes` read the `bybit_1` ETHUSDT **long** book as **flat**
while the venue held `Buy 26.05`, attributed the whole journal row as closed, and booked
a fabricated `+$63.82`. The reverse reconciler then found the still-live venue position
with no matching open journal row and re-adopted it as a fresh `adopted_orphan`.

The venue read is wrong because **`_bybit_position_protection` grades a symbol off
`rows[0]` of a symbol-scoped `get_positions`, and under HEDGE mode Bybit returns one row
per book.** A zero-size sibling book listed first makes the function return `_flat` for a
symbol that is not flat.

⚠️ **`bybit_1` is demo, but the defect is not demo-scoped.** The same flat-read fired on
real-money **`bybit_2`** on 2026-09-04 (trade 5461, XRPUSDT long 69.4).

---

## The done-condition, answered

> *Trade 5569 ends carrying `reconcile_status: 'reconciled'` naming the order package that
> actually opened that venue position, OR an explicit terminal saying it is unreconcilable
> with the reason written down.*

**It already carried `reconcile_status: 'reconciled'` when I arrived** — stamped by the
reconciler itself at adopt time (14:25:47Z), six minutes before the dispatch was written.
It names `pkg-5c339e25d5144c32`, and **that is genuinely the package that opened the venue
position**, verified three ways rather than taken from the field:

| check | evidence |
|---|---|
| the package is trade 5568's | `trades.order_package_id` on 5568 == `pkg-5c339e25d5144c32` |
| 5568's order is what filled | 5568's entry-time Partial SL/TP legs (`7b5af72f-…`, `e3b793f1-…`) were created `1788877006909` = **2026-09-08T14:16:46.909Z**, 240 ms before 5568's row timestamp `14:16:47.149Z`, at qty **26.05** — the exact live position size |
| those legs are still resting | read live at `2026-09-08T14:37:02Z`, both `order_status: Untriggered`, `qty 26.05` — so nothing ever closed that position |

So the literal criterion is **satisfied, and satisfying it is not the finding.**
**The row should never have existed.** Reporting "5569 is reconciled" as the outcome
would be true and useless — the state to fix is one hop upstream.

---

## The timeline (all times UTC, all MEASURED)

| time | event | source |
|---|---|---|
| `14:15:32.398` | pairs leg 5567 (ETH long 0.54) closes `pairs_half_open_cleanup` | `trades` |
| `14:16:46.909` | Bybit creates 5568's Partial SL + TP legs, qty 26.05 | `/api/diag/bybit_open_orders`, `created_time` |
| `14:16:47.149` | trade **5568** opens — `ict_scalp_eth_15m`, long 26.05 @ 2477.54, `pkg-5c339e25d5144c32` | `trades` |
| `14:21:54.709` | netting reconciler **first observes** the "divergence"; this becomes 5568's `closed_at` and its anchor time | `netting_attribution_soak` `anchored_at` |
| `14:24:09.325` | after the 2-observation confirm, it **applies**: `exchange_qty: 0.0`, `excess_qty: 26.05`, `attributed_qty: 26.05`, `attribution_basis: leg_gone` → 5568 closed, `exit_price 2479.99`, `pnl +63.8225` | `netting_attribution_soak` |
| `14:25:47.925` | reverse reconciler adopts the still-live position as trade **5569**, re-attached to `ict_scalp_eth_15m` via the *same* package | `trades.notes` |
| `14:25:48.218` | the adopt path's naked re-arm places a **Full-mode** SL+TP over the top | `bybit_open_orders` `created_time` |
| `14:31:27` → `14:43:35` | venue continuously reports `Buy 26.05 @ 2476.6`; unrealised +29.18 → +93.52 → +121.91 | `/api/diag/exchange_positions` ×4 |

**Deployed code at the time of reading:** `/api/diag/version` at `14:43:36Z` →
`git_sha bdccf226`, `git_sha_on_disk 0afe261f`, **`restart_pending: true`**. The running
trader is behind the on-disk checkout. Noted, not investigated — not this unit's.

---

## Root cause D1 — `pos = rows[0]` is hedge-book-blind

[`src/runtime/order_monitor.py:8821`](../../../src/runtime/order_monitor.py)

```python
    pos_resp = client.get_positions(category=category, symbol=symbol)
    rows = ((pos_resp or {}).get("result") or {}).get("list") or []
    ...
    if not rows:
        return _flat                      # flat — nothing to protect
    pos = rows[0]                         # <-- ONE row, arbitrarily chosen
    size = abs(float(pos.get("size") or 0) or 0.0)
    if size <= 0:
        return _flat                      # <-- a zero-size SIBLING BOOK reads as "symbol flat"
```

Under `BYBIT_HEDGE_MODE_SYMBOLS` (armed on `bybit_1`/`bybit_2`/`bybit_portfolio`
**2026-08-30**) a symbol carries two books, `positionIdx` 1 (long) and 2 (short). Bybit
returns a row for each, including the empty one. `rows[0]` therefore grades the whole
symbol off whichever book Bybit happened to list first.

`_flat` returns `{"size": 0.0, "side": "", "covered_qty": 0.0, "sl_leg_ids": set()}`. The
caller then computes:

```python
backed  = size if (size > 0 and exch_side == direction) else 0.0   # -> 0.0
excess  = journal_qty - backed                                     # -> the WHOLE row
```

**The repo already knows this and gets it right one file over.** `account_bybit_open_orders`
([`src/units/accounts/clients.py:1888-1896`](../../../src/units/accounts/clients.py) and
again at `:1910-1912`) skips `size <= 0` rows on **both** the account-wide and the
symbol-scoped call. That symbol-scoped loop — `for p in (...list...)` with a zero-size
`continue` — is direct evidence that a symbol-scoped `get_positions` **does** return
multiple rows, some empty. `_bybit_position_protection` does neither.

### How I know it was `_flat` and not the side-mismatch path

The soak row records `attribution_basis: "leg_gone"`, and
[`_netting_rows_to_attribute`](../../../src/runtime/order_monitor.py) only emits that
label when the row's `sl_order_id` is **absent from `live_leg_ids`**:

```python
    leg_gone = [r for r in rows
                if str(r["sl_order_id"] or "") and str(r["sl_order_id"]) not in live_leg_ids]
```

Trade 5568's `sl_order_id` is `7b5af72f-aa0a-4317-b796-3df599f68030`, and that leg was
**still resting on the venue**. So `live_leg_ids` was empty. The only branches returning
an empty `sl_leg_ids` are `_flat` and `full_position_stop`; `full_position_stop` returns
`covered_qty == size` with `size = 26.05`, which produces **no divergence at all**.
∴ `_flat`.

⚠️ **NOT established:** which of `_flat`'s two triggers fired — `not rows` (Bybit returned
an empty list) or `rows[0]["size"] <= 0` (a zero-size sibling book). Both are the same
defect class and the proposed fix covers both, but I did not observe the raw
`get_positions` payload; no repo surface exposes it unfiltered. Saying which would be a
guess. The zero-size-book trigger is the better-supported of the two — see the
before/after arithmetic below — but *better-supported* is not *measured*.

---

## The arithmetic cross-check — adopts appear exactly at hedge-mode arming

**Population:** the newest **1000** `trades` rows, ids **4570–5569**, timestamps
**2026-08-11T10:48:22Z → 2026-09-08T14:25:47Z** (`/api/diag/journal?table=trades&limit=1000`,
read 2026-09-08). That is **19 days before** the 2026-08-30 arming and **10 days after**.

| | before 2026-08-30 | on/after 2026-08-30 |
|---|--:|--:|
| `setup_type='adopted_orphan'` on a **bybit** account | **0** | **13** |
| `exit_reason='netting_attributed'` closes | 30 | 19 |

The netting reconciler was already applying throughout the pre-window (30 closes in 19
days), so **its activity is not what changed** — what changed is that its closes started
being wrong. Zero bybit adopts in nineteen days, thirteen in the ten days after hedge mode
was armed.

⚠️ **This is INFERRED, from those two measured counts.** It is strong and it is not proof:
the pre-window is bounded by the 1000-row cap, so "0 before" means *0 in those 19 days*,
not *0 ever*. A confounder I looked for and did not find: the `apply` mode was already
live before 08-30 (soak row `2026-08-30T13:18:45`, `apply_scope: allowlisted`).

### The three provably-wrong closes

An `adopted_orphan` of the **same account, symbol, direction and exact size** appearing
minutes later is positive proof the position was live when it was declared closed:

| netting close | → adopt | gap | size |
|---|---|--:|---|
| 5515 `bybit_1` SOLUSDT short, closed `2026-09-06T06:35:44` | 5516 @ `06:39:13` | 209 s | 604.7 = 604.7 |
| 5554 `bybit_1` SOLUSDT short, closed `2026-09-08T12:58:54` | 5555 @ `13:02:37` | 223 s | 545.0 = 545.0 |
| 5568 `bybit_1` ETHUSDT long, closed `2026-09-08T14:21:54` | 5569 @ `14:25:47` | 233 s | 26.05 = 26.05 |

**Manufactured PnL on those three rows: `+$115.8575`**, every one of them
`exit_price_source: candle_at_close` / `pnl_source: local_compute` — i.e. **ESTIMATED**,
booked against a position that never closed. 5568 alone is `+$63.8225`.

⚠️ **Three is a LOWER BOUND, not the count.** It is the subset where the flap left a
same-size `adopted_orphan` fingerprint inside a 1-hour window. The soak's applied rows
show **12 of 21 read `exchange_qty == 0.0`** (population: the 1000-line tail of
`netting_attribution_soak.jsonl`, spanning `2026-08-29T07:50Z → 2026-09-08T14:24Z`; the
file is 3,875,205 bytes, so this is a tail and not the whole file). A flat read is *not
automatically wrong* — a genuinely-flat venue with a stale open row is the case this
reconciler exists for — and I did not establish which of the other nine were correct.

### The cycle repeats

`5262` and `5438` are `adopted_orphan` rows that were **themselves** later closed by
`netting_attributed`. So the loop is
`netting_attributed → adopt → netting_attributed → adopt`, and each turn mints a phantom
closed trade with an estimated PnL, **rewrites the position's cost basis** (5569 carries
the venue's `2476.6`, where 5568 declared `2477.54`), and drops the original row's tracked
leg ids (5569 has `sl_order_id: NULL`).

This is the shape of `BL-20260618-RECONCILE-DUP` — one MGC position becoming 18 closed
trades and −$20,127 — reached through a new cause.

---

## Root cause D2 — the re-adopt flap guard cannot see this flap class

[`_recently_closed_adopted_orphan`](../../../src/runtime/order_monitor.py) selects:

```sql
AND (setup_type='adopted_orphan'
     OR exit_reason IN ('exchange_flat_reconciled', 'exit_coverage_no_strategy'))
```

`netting_attributed` is not in that list, so a strategy row false-closed by the netting
reconciler **falls straight through the guard with zero flap protection**.

That is verbatim the gap the function's own docstring records being closed for the other
two reasons by `BL-20260707-ALPACA-CLOSE-NOT-CONFIRMED-FLAT`; `netting_attributed` was
introduced later and nobody extended the list. And the docstring's stated reason for
*excluding* other close reasons —

> *a real, broker-confirmed close … is a genuine flatten and must never suppress a
> legitimate new position*

— **does not describe `netting_attributed`**, which is by construction not
broker-confirmed. Its own docstring calls its evidence *"evidence, not proof"* and its
close is an ESTIMATE anchored to a bar.

⚠️ `RECONCILER_READOPT_GUARD_SECONDS` (1800 s) is **working as designed** here. It did not
suppress because its *key* does not match, not because its window is short. Widening the
window would change nothing.

---

## Root cause D3 — why nobody caught it: `exchange_qty: 0.0` is a collapsed state

The soak row carries `ts, mode, global_mode, apply_scope, account_id, symbol, direction,
trade_id, strategy, row_qty, attributed_qty, attribution_basis, journal_qty, exchange_qty,
excess_qty, anchor_status, anchor_price, anchored_at, anchor_basis`.

It carries **no `position_idx` and no `source`.** So `exchange_qty: 0.0` cannot distinguish:

1. **the venue is genuinely flat** — the case the reconciler exists for;
2. **we read the wrong hedge book** — this defect;
3. **the position list came back empty** — *we did not look*.

A reviewer reading the soak cannot tell a correct attribution from a catastrophic one.
`_bybit_position_protection` already computes `position_idx` — but only on the
`partial_sl_legs` branch, and `_flat` (the branch that matters here) does not carry it.

This is § "Collapsed states" applied to the instrument rather than the field, and it is
why twelve flat reads accrued unexamined.

---

## A fourth thing, created by this flap and currently live

The adopt path's naked re-arm placed a **Full-mode** position-level bracket at
`14:25:48.218Z` on top of trade 5568's **Partial** legs, which were never cancelled.
Read live at `2026-09-08T14:37:02Z`, `bybit_1` ETHUSDT holds **four** protective legs
against one 26.05 position:

| leg | mode | qty | trigger | created |
|---|---|--:|--:|---|
| `StopLoss` | Full | 26.05 | 2465.01 | 14:25:48 (the re-arm) |
| `TakeProfit` | Full | 26.05 | 2496.32 | 14:25:48 (the re-arm) |
| `PartialStopLoss` | Partial | 26.05 | 2465.02 | 14:16:46 (**closed** trade 5568's) |
| `PartialTakeProfit` | Partial | 26.05 | 2496.33 | 14:16:46 (**closed** trade 5568's) |

Stop side: **52.10 against a 26.05 position = 200%.**

⚠️ **The over-cover page is structurally blind to it.** The trip requires
`state["source"] == "partial_sl_legs"` (`order_monitor.py:9920-9922`), and the position
now carries a non-empty position-level `stopLoss`, so `_bybit_position_protection` returns
via `full_position_stop` and **never calls `get_open_orders` at all**. A Full-mode re-arm
laid over Partial legs is therefore invisible to the mechanism built to catch leg pile-up.

All four legs are `reduce_only` and all four `Sell`, so they act on the correct book and
cannot reverse the position. The cost is **cap headroom** — Bybit refuses at 20 combined
TP+SL legs per symbol (`BL-20260721-BYBIT2-XRP-TPSL-LEGCAP`, 23 stranded legs measured on
`bybit_2` XRPUSDT), at which point a genuine protective tightening fails silently.

---

## The four dispatch leads, answered

**1. `bybit_1` is demo — does that bound it?** It bounds *this instance*, not the defect.
Trade 5569 is `account_class: paper`, `is_demo: 1`. But **7 of the 49** `netting_attributed`
closes in the population are on real-money `bybit_2` (BTCUSDT ×3, ETHUSDT ×3, XRPUSDT ×1),
and one of them — 5461, `2026-09-04T12:34:25Z`, XRPUSDT long 69.4 — is an
`exchange_qty: 0.0` flat read on an account where XRP is hedge-armed.

**2. Is the "no matching open journal row" test position-side aware?** The lead named the
right *class* and the wrong *function*, and the distinction matters:

- The **reverse reconciler's** match **is** side-aware — it keys `(sym, canonical_side)`
  against `known` (`order_monitor.py:3475-3485`). It is not the bug.
- The **netting reconciler's venue read** is *side*-aware (`exch_side == direction`) but
  **not book-aware**. That is the bug, and it is upstream of the adopt.

**3. Is the pairs sleeve the parent?** No. 5569's parent is `ict_scalp_eth_15m` via
`pkg-5c339e25d5144c32` — trade 5568's own package. Pairs rows are explicitly excluded from
the netting pass by `_is_pairs_sleeve_row`, and the pairs ETH leg 5567 closed at
`14:15:32`, **before** 5568 opened at `14:16:47`.

**4. Is 26.05 one fill or an accumulation?** One. 5568 opened with `position_size 26.05`
and its entry-time legs were created at qty 26.05 240 ms earlier.
⚠️ **One thing I could not explain:** the venue's `avgPrice` is `2476.6` while 5568
declared entry `2477.54` — a 0.94 gap (0.038%). Slippage and a second partial fill both
fit; I did not establish which, and it is not load-bearing for the finding.

---

## Proposed fixes — NOT APPLIED (Tier-2/3, order path)

Both touch `src/runtime/order_monitor.py`, which decides closes on live positions. They
are written out so the operator can judge the exact change, not described.

### D1 — select the book, and never grade a symbol off an arbitrary row

```python
    # BEFORE (order_monitor.py:8819-8827)
    if not rows:
        return _flat
    pos = rows[0]
    size = abs(float(pos.get("size") or 0) or 0.0)
    if size <= 0:
        return _flat

    # AFTER — mirror account_bybit_open_orders' zero-size skip (clients.py:1888-1896),
    # which is the reader that already gets hedge mode right.
    if not rows:
        return None          # <-- see the note below: NOT _flat
    live = [p for p in rows if _f(p.get("size")) > 0]
    if not live:
        return _flat         # every book empty == genuinely flat, and we LOOKED
    if len(live) > 1:
        # Two live books on one symbol. We cannot grade the symbol as one
        # position; the caller must not attribute on it.
        return None
    pos = live[0]
```

Two deliberate choices in that patch:

- **`not rows` returns `None`, not `_flat`.** An empty list from a symbol-scoped call is
  *we could not look* — the caller already treats `None` as "skip, never attribute on an
  unconfirmed read". Today it is folded into "flat", which is the collapse that lets a
  read failure close a live position.
- **Two live books returns `None` rather than picking one.** Guessing which book backs a
  journal row is exactly the guess that produced this incident.

### D2 — teach the flap guard about this close reason

```sql
-- _recently_closed_adopted_orphan
AND (setup_type='adopted_orphan'
     OR exit_reason IN ('exchange_flat_reconciled',
                        'exit_coverage_no_strategy',
                        'netting_attributed'))          -- <-- add
```

Narrow and testable. `netting_attributed` is never broker-confirmed, so it does not
violate the docstring's stated exclusion rule; a genuine new position on the same
symbol/direction within 1800 s of an *estimated* close is far more likely to be the flap
than a fresh entry.

**D2 alone would have contained this incident without fixing it** — the position would
have stayed adopted-suppressed and alerted rather than looping. **D1 alone would have
prevented it.** They are worth shipping together and in that order.

### D3 — make the soak able to say which state it saw

Add `position_idx` and `source` to `_netting_soak_row`. Cheap, Tier-1-shaped in isolation,
and it is what converts "12 of 21 read flat" from an ambiguity into a measurement.

---

## What I did NOT do

- **Nothing was closed, flattened, reduced, cancelled or re-armed.** The 26.05 ETH
  position, the four resting legs, and trade 5569 are exactly as I found them.
- **No `src/`, `config/`, `deploy/` or workflow change.** The patches above are proposals.
- **I did not "fix" trade 5569.** It is correctly `reconciled` against a correctly-named
  package. Re-stamping it would hide the incident, not resolve it.
- **I did not establish** which `_flat` trigger fired, whether the other nine flat reads
  were correct, or the source of the 0.94 entry-price gap.

## Live state at hand-off

`2026-09-08T14:43:35Z` — `bybit_1` ETHUSDT `Buy 26.05 @ 2476.6`, unrealised **+121.914**,
trade 5569 `status: open`, `reconcile_status: reconciled`. The position did **not** close
during this investigation.

---

# ADDENDUM (same session, 15:20Z) — a SECOND mechanism, and it is worse

The manager sent a check-in at 14:38Z proposing that the **pairs sleeve** is the lead:
*"does the reverse reconciler know about pairs-sleeve positions at all?"* — on the
correlation that 5 of 6 `bybit_1` orphan/unclassified events are on SOLUSDT or ETHUSDT,
the two symbols the live sleeve trades.

**I checked it from the code rather than inferring it, and the answer is layered.
Part of my own earlier reporting was wrong and is corrected here.**

## The literal hypothesis is REFUTED — the reverse reconciler does NOT exclude pairs rows

`_reconcile_orphan_exchange_positions` builds its `known` set from **every** open
non-backtest row on the account:

```sql
SELECT id, symbol, direction, strategy_name, account_id, position_size, entry_price,
       notes, order_package_id, timestamp, created_at
  FROM trades
 WHERE status='open' AND COALESCE(is_backtest,0)=0 AND account_id=?
```

No `setup_type` filter, no strategy filter, no `_is_pairs_sleeve_row` call.
**Positive control that this is a real absence and not a blind grep:**
`_is_pairs_sleeve_row` exists (`order_monitor.py:9164`) and has exactly two call sites —
`_reconcile_netting_partial_closes:9276` and `_check_broker_naked_bybit_positions:9785`.
Neither is the reverse reconciler.

So a pairs leg **whose journal row is open** is in `known` and cannot be adopted.
Live confirmation: trade **5571** (`pairs_sol_eth_b`, ETHUSDT) is open right now and has
not been adopted.

⚠️ **This is where I must correct myself.** I first reported this as *"the reconciler
cannot adopt a pairs leg"*, full stop. That is too strong. It cannot adopt a pairs leg
**whose row is still open** — and it says nothing about a pairs position whose row has
already closed, nor about which strategy an adopt gets ATTRIBUTED to. The second of those
is where the real defect is.

## The symbol correlation is real, and hedge mode explains it — with the sleeve one hop upstream

**MEASURED** (population: all 13 `adopted_orphan` rows on a bybit account in ids
4570–5569): **13 of 13 are on a hedge-armed symbol** — SOLUSDT, ETHUSDT or BTCUSDT.

The discriminator is what got **zero** adopts despite being actively traded on `bybit_1`
in the same window:

| symbol | trade rows | hedge-armed | adopts |
|---|--:|---|--:|
| SOLUSDT | 197 | yes | 8 |
| ETHUSDT | 136 | yes | 2 |
| BTCUSDT | 98 | yes | 1 |
| BNBUSDT | 60 | yes | 0 |
| **AVAXUSDT** | **121** | **no** | **0** |
| **XRPUSDT** | **42** | **no** | **0** |
| **ADAUSDT** | **12** | **no** | **0** |

175 rows of traded, non-hedge-armed denominator, zero adopts. Hedge mode is the
precondition, because it is what makes `rows[0]` able to read the wrong book.

⚠️ **INFERRED, and the confound is stated:** SOL/ETH/BNB/BTC are *both* hedge-armed *and*
the pairs symbols, so they cannot separate the two hypotheses on their own. What separates
them is the code read above plus the direct parentage evidence below. And BNBUSDT is
hedge-armed with 60 rows and zero adopts, which neither hypothesis predicts — most likely
the triggering condition simply never arose there, and I did not establish that.

**The sleeve IS causally upstream, one hop further back than the check-in placed it:**
hedge mode was armed on `bybit_1` SOL/ETH **because of the pairs sleeve**
(`BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`). The sleeve created the condition
that makes the reconciler misread the book. It does not own the adopted positions.

## 🚩 THE ACTUAL SECOND DEFECT: the adopt path can attribute a position to a strategy that did not open it

The re-attach writes `recovered_strategy` into `trades.strategy_name`. **It can name the
wrong strategy, and when it does, the wrongly-named strategy then ACTS on the row.**

**MEASURED** — scored each adopt's claimed strategy against **that strategy's own size
distribution on the same symbol**, built from non-adopted `bybit_1` rows in the same
window. Of the 5 adopts that claimed a strategy and could be judged (6 claimed none — the
honest bare-orphan state; 2 had a **truncated** `recovered_strategy` in `notes`, so no
reference existed):

| adopt | symbol | size | claimed strategy | that strategy's own range | verdict |
|---|---|--:|---|---|---|
| 5288 | SOLUSDT | 11.90 | `pairs_sol_eth_a` | 2 … 57.5 (n=80) | INSIDE |
| 5555 | SOLUSDT | 545.00 | `ict_scalp_sol_15m` | 9.1 … 2511.7 (n=15) | INSIDE |
| 5569 | ETHUSDT | 26.05 | `ict_scalp_eth_15m` | 5.31 … 114.47 (n=15) | INSIDE |
| **5448** | SOLUSDT | **1236.30** | `pairs_sol_eth_a` | 2 … **57.5** (n=80) | **OUTSIDE — 21.5× its max** |
| **5453** | ETHUSDT | **17.67** | `pairs_sol_eth_b` | 0.01 … **2.10** (n=80) | **OUTSIDE — 8.4× its max** |

**2 of 5 judgeable re-attributions named a strategy whose own size distribution excludes
the position, and BOTH wrong ones named a PAIRS strategy.**

⚠️ n = 5 judgeable, and the ±(0.5×, 2×) band is a **chosen** threshold, not a derived one.
The verdict is robust to that choice — 8.4× and 21.5× are outside any defensible band —
but the band is chosen and the sample is small.

### Trade 5453 is the worked example, and it is airtight on arithmetic

- Adopted `bybit_1` ETHUSDT **long 17.67** at `09:02:08Z`, attributed to **`pairs_sol_eth_b`**.
- **17.67 is an exact match for trade 5420, `trend_donchian_eth` long 17.67**, closed
  `reconciler_filled` 3,264 s earlier.
- `pairs_sol_eth_b` has **80 legs** on ETHUSDT in this window; the **largest is 2.10** and
  the typical leg is ~0.37. `trend_donchian_eth`'s sizes are 17.08–37.76. **17.67 sits
  inside trend_donchian's distribution and 8.4× outside the pairs sleeve's entire range.**
- The row carries `strategy_name: pairs_sol_eth_b` and **`pnl: +612.705681`**
  (`exit_price_source: candle_at_close` — ESTIMATED).
- **14 seconds after the adopt it closed with `exit_reason: pairs_half_open_cleanup`** —
  the **pairs executor acted on it**, because the journal now told it this was its leg. Its
  exit price `2525.85` is byte-identical to pairs leg 5447's exit price.

**So the pairs sleeve flattened a `trend_donchian_eth` position and booked +$612.71 of
another strategy's PnL.** For scale, that sleeve's own legs in the same window book
**−$10.46** and **−$1.31**: a single +$612.71 row is ~60× its typical magnitude and will
dominate any per-strategy aggregate computed for it.

### Three harms, and they are different facts

1. **A false strategy attribution written into the journal** — every per-strategy
   aggregate, grade and expectancy for `pairs_sol_eth_b` is contaminated by it.
2. **Cross-strategy PnL contamination of +$612.71** on a sleeve whose real legs are ±$10.
3. **A live order-path consequence** — the pairs executor closed a position it did not
   open, on a row it was handed by a reconciler.

⚠️ **NOT established, and I am not asserting it.** The same 17.67 quantity produced **two**
closed rows at two different exit prices — 5420 at `+$315.41` and 5453 at `+$612.71`,
`+$928.12` combined. That is either double-counting (5420's `reconciler_filled` close was
*also* false) or a genuine off-journal re-entry. `reconciler_filled` is a broker-confirmed
close by this repo's own definition, and 5420's close does **not** appear in the netting
soak, so I have no evidence it was false. Resolving it needs venue fill history for
`2026-09-04T08:07Z`, which no repo surface exposes. **Recorded as the open question, not
as a finding.**

### A small sibling, worth one line

Two of the 13 adopts carry a **truncated** `recovered_strategy` in `notes` (`"ict_scal…"`,
and 5516 carries an explicit `"_truncated": true`). The attribution evidence is being
clipped in the record that is supposed to preserve it, which is why those two are
unjudgeable rather than judged.

## What this does NOT change

The `rows[0]` root cause and both proposed patches stand exactly as written above —
5555 and 5569 are still netting false-closes with plausible attributions. This addendum
adds a **second, independent** defect on the same path, and it needs its own fix: the
adopt path must not write a `strategy_name` it cannot support, and a *bare* orphan
(`recovered_strategy` empty, which 6 of 13 correctly are) is the honest state when the
owner is not established. **A quantity check against the claimed strategy's own history is
the cheapest available discriminator** and would have refused both wrong attributions.

Still Tier-2/3 — proposed, not applied.

---

# ADDENDUM 2 (15:40Z) — the real-money answer, the size of it, and a defect I called benign 20 minutes before it fired

A manager check-in at 15:30Z asked the sizing question — *does the defect reach REAL MONEY?* —
after its own probe's positive control failed (it read `bybit_2` as 0 orders, but `bybit_1`,
which IS armed, also returned 0 orders, so the silence was worth nothing).

## YES. Real money is armed, and it has a flat-read close on the record

**The probe was reading the wrong collection.** `position_idx` rides on
`result.positions[]`, not on `result.orders[]` — and orders are absent whenever nothing
rests, which is why an empty book defeated the control. Read the positions instead.

**MEASURED 2026-09-08T15:33Z, `/api/diag/bybit_open_orders`, `read_state: orders_read` on
all three accounts:**

| account | position | `position_idx` | verdict |
|---|---|--:|---|
| **`bybit_2`** (mode live, **real_money**) | XRPUSDT Buy 58.5 | **1** | **HEDGE ARMED** |
| `bybit_portfolio` | XRPUSDT Buy 11903.8 | **1** | HEDGE ARMED |
| `bybit_1` | ETHUSDT Buy 0.47 | 1 | hedge |
| `bybit_1` | SOLUSDT Sell 11.7 | 2 | hedge |
| `bybit_1` | XRPUSDT Buy 46020.4 | **0** | **one-way** |
| `bybit_1` | ADAUSDT Buy 79855.0 | **0** | **one-way** |

**THE POSITIVE CONTROL IS IN THE SAME PAYLOAD, which is what makes this trustworthy:**
`bybit_1` returns `0` for XRPUSDT and ADAUSDT alongside `1`/`2` for ETH and SOL. The field
genuinely discriminates, and a `1` is not a default or a fill-in.

⚠️ **What this read CANNOT establish:** `position_idx` is only observable for a symbol that
currently *holds* a position, so the armed state of every flat symbol is unmeasured. The
declared set on `/proc/<MainPID>/environ` is the only authoritative source for that, and
`BYBIT_HEDGE_MODE_SYMBOLS` **is** in `get_env.py::ALLOWED_KEYS` (verified — 70 keys, it is
one of them), so it is readable. Dispatched as issue **#11413**. CLAUDE.md's own row must
not be used: it states outright that its value went stale twice on 2026-08-30 alone.

## The size of it, with bounds rather than an estimate

**POPULATION: the 19 `netting_attributed` closes with `closed_at >= 2026-08-30`** (the
arming date), from the newest-1000 trades window (ids 4570–5569, spanning 2026-08-11 →
2026-09-08). Cross-joined to the applied `netting_attribution_soak` rows.

| account | total | flat read | non-flat | armed | one-way | hedge unknown |
|---|--:|--:|--:|--:|--:|--:|
| `bybit_1` | 14 | **9** | 5 | 12 | 2 | 0 |
| **`bybit_2`** (real money) | 1 | **1** | 0 | 1 | 0 | 0 |
| `bybit_portfolio` | 4 | **2** | 2 | 1 | 0 | 3 |

**The contingency is the finding, and it carries its own negative control:**

| MEASURED hedge state | flat | non-flat | total | % flat |
|---|--:|--:|--:|--:|
| **armed** | **11** | 3 | 14 | **78.6%** |
| **one-way** | **0** | 2 | 2 | **0.0%** |
| unknown | 1 | 2 | 3 | 33.3% |

A one-way symbol has **one** book, so `rows[0]` cannot pick the wrong one — the prediction
is exactly 0% flat, and that is what the two closes on measured-one-way symbols (5488, 5527,
both `bybit_1` XRPUSDT) show. ⚠️ **n = 2 on that control is SMALL and is stated as such**;
it is the only control the data affords.

**ESTIMATED PnL booked on the 12 flat-read closes since arming: `−$5,033.4356`** — every
one `exit_price_source: candle_at_close`, i.e. not broker truth.
`bybit_1` −$1,882.65 (n=9) · **`bybit_2` −$2.98 (n=1)** · `bybit_portfolio` −$3,147.81
(n=2, of which trade 5451 alone is −$2,250.34).

### The bounds, and what closes the gap

| | n | basis |
|---|--:|---|
| **UPPER BOUND** | **12** | every flat read since arming. A flat read is **not automatically wrong** — a genuinely-flat venue with a stale open row is the case this reconciler exists for. |
| **LOWER BOUND** | **3** | 5515, 5554, 5568 — a same-size `adopted_orphan` followed within 4 minutes, which is positive proof the position was live when declared closed. All `bybit_1`. |
| **ungradeable** | **9** | neither provable nor refutable from any repo surface. |

⚠️ **I am not estimating within that gap, and here is precisely why I cannot:** the soak
carries no `position_idx` and no `source`, so *venue genuinely flat*, *we read the wrong
book* and *the list came back empty* share the single value `exchange_qty: 0.0`. **Adding
those two fields is what converts 9 ungradeable rows into a measurement** — which is
exactly what `BL-20260908-NETTING-SOAK-EXCHANGE-QTY-ZERO-COLLAPSES-GENUINELY-FLAT-WRONG-BOOK-AND-COULD-NOT-LOOK`
asks for, and it is now the cheapest high-value item in this whole set.

**Real-money exposure, stated exactly:** `bybit_2` is armed and has **one** flat-read close
on the record (trade 5461, XRPUSDT long 69.4, 2026-09-04T12:32:16Z, ESTIMATED −$2.98). It
is in the ungradeable nine — no adopt followed it, so I cannot say it was false. The
**precondition** for the defect was present. The dollar figure is trivial; **the
contamination of the closed-trade population is not**, because that population is what
per-strategy expectancy and every promote/demote verdict are computed from.

## 🚩 A NEW defect, and I called it benign 20 minutes before it fired

In my 15:22Z board comment I wrote that two journal rows sharing one hedge book was
*"benign in this direction"*. **It was not, and it materialised on this very position
within half an hour.** Recording the correction because a reader would otherwise inherit it.

**The netting reconciler's comparison is ASYMMETRIC.** It excludes pairs-sleeve rows from
`journal_qty` (`_is_pairs_sleeve_row`, by design — that executor owns its own state) but
`backed` is the **whole netted venue position, which includes the pairs legs' quantity**.
So it compares *non-pairs journal* against *all-venue*.

**MEASURED, from the soak row at `2026-09-08T15:26:55.295832Z`:**

```
trade_id 5569  journal_qty 26.05  exchange_qty 0.47  excess 25.58
attributed_qty 25.58  basis "fifo"     ->  residual left on the row = 0.47
```

`26.05 − 0.47 = 25.58` ✓. And **the 0.47 it left is the pairs leg's quantity** — trade
**5571** (`pairs_sol_eth_b`, ETHUSDT long **0.47**, opened ~15:16Z) is the row that
actually owns it. So after the pass:

- journal open ETH long on `bybit_1` = **5569 (0.47) + 5571 (0.47) = 0.94**
- venue = **0.47**
- **the journal over-claims by 0.47, and 5569's residual is exactly 5571's size.**

This is **systematic, not a rounding artifact**: the residual always equals the excluded
pairs legs' total. It under-attributes by that amount every time a pairs leg is open on the
same book. It scales with pairs leg size — `pairs_sol_eth_a` legs reach **57.5** on SOLUSDT,
so the phantom residual can be 57.5 SOL rather than 0.47 ETH.

⚠️ **The exclusion itself is right and must not be removed** — closing a pairs row behind
the executor's back would desync it, which is why the exclusion exists. The fix is to make
the two sides of the comparison agree: subtract the excluded rows' quantity from `backed`,
or refuse to grade a book that carries an excluded row. Either is Tier-2.

## Trade 5569's disposition, restated on current facts

**It is `status: open`, `reconcile_status: reconciled`, `position_size: 0.47`** (reduced
from 26.05 by the pass above), and its `notes` now carry `netting_attribution_basis: fifo`.

The manager's observation is right: **"orphan" was never the correct label for it.** It was
never an orphan — it was a live `ict_scalp_eth_15m` position whose journal row had been
false-closed 4 minutes earlier. And its residual 0.47 is now a **phantom** that belongs to
5571. So the honest disposition is:

- the row is **`reconciled` to a genuine parent package** (`pkg-5c339e25d5144c32`, verified
  three ways) — that clause of the done-condition stands;
- its **`setup_type: adopted_orphan` is a misnomer**, and its **0.47 residual is not
  backed by the venue** once 5571 is accounted for;
- **neither is fixable from here** — both are `src/` behaviours and Tier-2, and hand-editing
  a live open row's quantity is exactly the kind of remediation
  `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG` exists to forbid.

Recorded rather than repaired.
