# The hedge-book flat read is REPAIRED and OBSERVED on the fleet

> **Doc status:** `live` · category `evidence` · created `2026-09-12` · registered in [`docs/DOCUMENT-INDEX.md`](../../DOCUMENT-INDEX.md)
>
> **Unit:** MI-281 · object [`WO-20260912-REPAIR-THE-HEDGE-BOOK-FLAT-READ-THAT`](objects/WO-20260912-REPAIR-THE-HEDGE-BOOK-FLAT-READ-THAT.yaml)
> **Session:** `session_01ALe9gTsSEMVWYyR8X8TY79` · operator-directed 2026-09-12 (dedicated ORDER-PATH lane)
> **Row:** `OI-20260908-A-HEDGE-BOOK-FLAT-READ-IS-CLOSING-LIVE-BYBIT-POSITIONS-AND-THE-FLAP-GUARD-CANNOT-SEE-IT`
> **Scope:** observation only. **No `src/`, `config/`, `deploy/` or workflow change. Every production read was a GET.**
> Predecessor: [`BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md`](BYBIT-HEDGE-BOOK-FLAT-READ-2026-09-08.md) (MI-204), which measured the defect.

---

## The headline

**The code half was already landed and deployed when this lane opened, and the
missing half was the one nobody had done: the OBSERVATION.** All three clauses
of the row's `clears_when` now carry evidence, reported separately.

⚠️ **What does NOT follow: that the nine unestablished false closes are now
known to have been correct.** They are permanently unadjudicable, and that is
recorded below as a gap in the evidence rather than a clean bill.

---

## Clause (1) — the root cause landed. VERIFIED, not taken on trust.

`46e1efb1f` — *"MI-204 D1+D2+D3: a zero-size hedge sibling must not read as a flat
symbol"* (PR #11435, 2026-09-08T18:53Z).

**It is DEPLOYED, which is a different fact from merged.** `/api/diag/version`
read `2026-09-12T05:47:40Z`:

```
git_sha: c5a0ba993   git_sha_on_disk: c5a0ba993   restart_pending: false
```

and `git merge-base --is-ancestor 46e1efb1f c5a0ba993` returns 0 — established by
the command, not by reading the shas and judging them similar.

**The two collapses ARE separated.** `src/runtime/bybit_position_book.py` is a pure
decision function with five never-collapsed states, consumed by
`_bybit_position_protection`:

| state | meaning | caller |
|---|---|---|
| `selected` | exactly one book carries size | grade it |
| `flat` | rows returned, all parse to 0 — **a measurement** | `_flat` |
| `no_rows` | empty list — ***we could not look*** | **`None` → SKIP** |
| `ambiguous_multi_book` | ≥2 live books | **`None` → SKIP** |
| `size_unreadable` | a `size` will not parse | **`None` → SKIP** |

Both refusals the row names are present: `not rows` returns `None` rather than
`_flat`, and more than one live book returns `None` rather than picking one. The
refusal is logged at WARNING, so it cannot convert a false-close defect into a
silent protection gap.

### The tests were green and had never been shown to go red. They have now.

41 tests pass (`tests/test_bybit_position_book.py`,
`tests/test_bybit_hedge_book_flat_read.py`). **A green suite that has never gone
red proves nothing**, so both load-bearing predicates were mutation-checked —
defect planted, suite run, defect reverted, baseline re-confirmed green:

| # | planted defect | result |
|---|---|--:|
| M1 | grade off `rows[0]` with no zero-size skip — **the original defect** | **13 failed** |
| M2 | `no_rows` graded `flat` instead of refusing | **5 failed** |
| M3 | two live books pick the first instead of refusing | **4 failed** |
| M4 | `netting_attributed` dropped from the flap-guard allowlist | **1 failed** |

Baseline green before and after; `git status --porcelain` clean on both files.

---

## Clause (2) — OBSERVED on the fleet. Both halves, and they are different facts.

### (2a) A soak row on a hedge-armed symbol carrying a NON-ZERO `exchange_qty`

**Population:** the 1000-line tail of `netting_attribution_soak.jsonl`
(`/api/diag/log_file`, read 2026-09-12T05:48Z; **3,879,840 bytes on disk, so this
is a tail and NOT the lifetime**), spanning `2026-08-29T08:04:13Z →
2026-09-11T14:04:44Z`. **7 rows are post-deploy.** All 7:

| ts (UTC) | account | symbol | trade | `exchange_qty` | `position_idx` | `exchange_read_source` |
|---|---|---|--:|--:|--:|---|
| 09-09T15:40:32 | bybit_1 | AVAXUSDT | 5610 | 0.0 | `None` | `flat` |
| 09-09T22:12:36 | bybit_1 | ADAUSDT | 5417 | 15912.0 | `0` | `partial_sl_legs` |
| 09-09T22:12:36 | bybit_1 | ADAUSDT | 5479 | 15912.0 | `0` | `partial_sl_legs` |
| 09-10T18:06:15 | bybit_1 | AVAXUSDT | 5647 | 14952.9 | `0` | `partial_sl_legs` |
| **09-11T01:24:03** | **bybit_1** | **ETHUSDT** | **5663** | **0.29** | **`1`** | **`partial_sl_legs`** |
| 09-11T08:32:48 | bybit_portfolio | ETHUSDT | 5645 | 0.0 | `None` | `flat` |
| **09-11T14:04:44** | **bybit_1** | **ETHUSDT** | **5687** | **2.92** | **`1`** | **`partial_sl_legs`** |

**The two bolded rows are the observation.** `position_idx: 1` is *the venue's own
statement* that the symbol is hedged — under one-way netting Bybit reports `0`.
So on a hedge-armed symbol the selector met the two-book shape, **selected the
live long book, and returned a non-zero size** — where the old code graded the
symbol off whichever row Bybit happened to list first.

**The internal control is in the same table and is what makes this a measurement
rather than an anecdote:** ADAUSDT and AVAXUSDT, traded in the same window on the
same account, read `position_idx: 0`. The field is not stamped `1` indiscriminately;
it tracks the venue's actual position mode.

⚠️ **What this does NOT establish.** I could not observe the *row ordering* Bybit
returned, because **no repo surface exposes the raw `get_positions` payload** — the
same limit MI-204 recorded. So I cannot say that on those two specific reads the
zero-size sibling was listed first and the old code *would* have failed. What is
established is that the selector handled the hedge shape and produced a live book;
whether each individual read was a coin-flip the old code would have lost is not
observable from here.

### (2b) No further `netting_attributed` → same-size-adopt pair

**Population:** newest 1000 `trades` rows (`/api/diag/journal`, read
2026-09-12T05:49Z), ids **4716–5715**, `2026-08-17T15:29:54Z → 2026-09-12T05:05:31Z`.
**Matching rule (stated so it is falsifiable):** same `account_id` + `symbol` +
`direction`, `|size delta| ≤ 1e-6`, adopt within 3600 s **after** the close.

> **POST-DEPLOY: 0 pairs.**

⚠️ **Zero is worthless without a denominator, and the row says so in terms.** The
identical probe over the pre-deploy window finds:

| close | → adopt | gap | size |
|---|---|--:|--:|
| 5515 `bybit_1` SOLUSDT short `09-06T06:35:44` | 5516 | +209 s | 604.7 |
| 5554 `bybit_1` SOLUSDT short `09-08T12:58:54` | 5555 | +224 s | 545.0 |

**The probe can find a positive.** It finds 2 of the 3 pairs MI-204 documented;
it misses 5568→5569 because 5569's `position_size` now reads **0.47**, not the
26.05 it was adopted at — the row has been reduced since, so a size-equality probe
run today cannot match it. That is a property of the probe's input, stated rather
than hidden.

⚠️ **AND (2b) IS DELIBERATELY NOT REPORTED AS THE STRONGER HALF.** The row warns
that the flap needs a second live position in the sibling book to arise at all, so
a quiet week can mean the condition did not occur. **The half I lean on is (2a)**,
which is positive evidence that the mechanism ran and graded correctly. (2b) is
corroboration with a working control, not the load-bearing claim.

### Two post-deploy adopts exist, and they are NOT this class

`5715` (SOLUSDT long 776.8, `09-12T05:05:31`) and `5712` (BTCUSDT short 0.56,
`09-12T03:03:12`). Neither follows a `netting_attributed` close. Each follows a
**`pairs_stop`** close ~105 s earlier of the **opposite direction and an unrelated
size** (5710 short 19.7; 5708 long 0.007). The flap guard is not implicated.

⚠️ **Not investigated — outside this unit's spec, and recorded rather than
absorbed.** The shape resembles the *adopt-attribution* defect (the predecessor
doc's second mechanism: an adopt naming a strategy whose own size distribution
excludes the position), which is a different open row and wants its own lane.

---

## Clause (3) — the soak is gradeable now, and here is the count

**Population:** as (2a) — the 1000-line tail, **29 rows at effective `mode: apply`**
(the rows that touched the money DB).

| | applied | of which `exchange_qty == 0.0` | gradeable | **UNGRADEABLE** |
|---|--:|--:|--:|--:|
| pre-deploy | 22 | **12** | 0 | **12** |
| post-deploy | 7 | 2 | **2** | **0** |

Both post-deploy flat reads carry `exchange_read_source: 'flat'` — *the venue
enumerated the books and none holds anything*, a real measurement, now
distinguishable from `no_rows` (*the list came back empty*) and from a wrong-book
read. That is the collapse D3 existed to break, exercised on live data.

### ⚠️ The nine are permanently unadjudicable, and this is the honest reading

**0 of 993 pre-deploy rows carry `position_idx` or `exchange_read_source`** — the
fields did not exist when they were written. Of the 12 pre-deploy flat reads, 3
are MI-204's provable false closes (5515, 5554, 5568; **+$115.8575** of
manufactured ESTIMATED pnl). **The remaining 9 cannot be graded from any repo
surface, now or ever.** Their venue state at the moment of attribution was never
recorded and cannot be reconstructed.

**So clause (3) is satisfied by its own `or` branch** — *"the soak is given
`source` + `position_idx` so they become gradeable and the count is recorded"* —
and the count is recorded above. **It is NOT satisfied by the first branch**, and
nobody should later read this doc as having cleared the nine. One of them is
trade **5461** on real-money `bybit_2` (XRPUSDT long 69.4, `09-04T12:34:25Z`), and
whether that close was wrong is **unestablished, not benign**.

---

## What I did NOT do

- **No `src/`, `config/`, `deploy/` or workflow change.** The mutation checks were
  planted, run, and reverted; `git status --porcelain` is clean on both files.
- **Nothing closed, flattened, reduced, cancelled or re-armed.** Every production
  read was a GET.
- **Trade 5569 was not re-stamped.** It is correctly `reconciled`; re-stamping
  would hide the incident.
- **I did not establish** the Bybit row ordering on the two observed reads, whether
  any of the nine pre-deploy flat reads was wrong, or the cause of adopts 5712/5715.

## What would REOPEN this

A soak row on a hedge-armed symbol reading `exchange_qty 0.0` with
`exchange_read_source: 'no_rows'`, or any `netting_attributed` close followed
within 1 h by a same-size `adopted_orphan` on the same account/symbol/direction.
Either is the defect recurring, not a new one.
