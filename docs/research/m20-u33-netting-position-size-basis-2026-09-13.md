# MI-278 U33 — is a journal row's `position_size` observed, or ASSIGNED by netting attribution?

> **Doc status:** `unknown` · category `evidence` · last verified `never` · registered in [`docs/DOCUMENT-INDEX.md`](../../docs/DOCUMENT-INDEX.md) · **nobody has verified this document's status — do not act on it as current**

Instrument: [`scripts/research/netting_position_size_basis.py`](../../scripts/research/netting_position_size_basis.py) ·
29 pytest controls in [`tests/test_netting_position_size_basis.py`](../../tests/test_netting_position_size_basis.py) ·
subject: `src/runtime/order_monitor.py` (**Tier-2, read only, unmodified**)

<!-- input-provenance
source: /api/diag/journal?table=trades&limit=1000
n_rows: 1000
id_field: id
rowset_digest: sha256:1752f298bbd639395817aee0581c284561eef361787f4f6f6a03fd4a17058d3b
order_digest: sha256:b073ee2cd3f571022c802c446db077d5e9d2bc2a30daf695ae90574fe32f1a1f
fields_covered: account_class,account_id,bias,broker_order_id,closed_at,cost_source,created_at,direction,entry_price,entry_reason,exit_price,exit_reason,fee_maker_usd,fee_taker_usd,funding_paid_usd,id,is_backtest,is_demo,killzone,notes,order_package_id,pnl,pnl_percent,position_size,protection_repair_first_at,protection_repair_last_at,protection_repair_last_kind,protection_repair_last_verified,protection_repairs,reconcile_status,setup_type,sl_order_id,status,stop_loss,strategy_name,symbol,take_profit_1,take_profit_2,take_profit_3,timestamp,tp_order_id
id_first: 5731
id_last: 4732
n_rows_without_id: 0
n_duplicate_ids: 0
-->

Second source: `/api/diag/log_file?name=netting_attribution_soak&lines=1000` —
a **1000-line tail of a 3,879,840-byte file**, covering
`2026-08-29T08:04:13Z → 2026-09-11T14:04:44Z`.

## The candidate, and who said it was untested

`BL-20260912-THE-NETTING-ATTRIBUTED-PATH-CARRIES-THE-LARGEST-SHARE-OF-THE-WINNER-COLLAPSE-AND-IS-100-PERCENT-ESTIMATED`
names one and says in terms that establishing it is a different unit:

> a `netting_attributed` row's `position_size` is ASSIGNED by
> `_reconcile_netting_partial_closes` rather than read from a venue fill, so
> there is no reason it must correspond to any venue close or sum of them.

**CONFIRMED at the field, not inferred from the prose.** In
`order_monitor._netting_rows_to_attribute`, `take = min(qty, remaining)` where
`remaining` starts as `excess = journal_qty - backed` — a subtraction of two
**aggregates**, distributed over rows by a selection heuristic (`leg_gone`, then
FIFO). No venue fill quantity enters at any point. That explains U16's whole
unmatchable remainder (`sum_matched_unique = 0 of 12`) in one line.

## ⚠️ And the field says three things the candidate did not

### 1. The partial branch does not close the row — it REWRITES its quantity and leaves it open

`_netting_apply_close`'s `if partial:` branch writes
`position_size = qty - take` and returns. The trade stays `status='open'` and is
closed **later** by whatever ordinary exit reaches it. So `exit_reason` can read
`sl_cross`, `sl`, `reconciler_filled` — anything.

**Every analysis of this contamination scopes it by
`exit_reason == 'netting_attributed'`** — MI-277 annex A6, MI-278 U9, MI-278
U16. That filter is structurally unable to see a reduced-then-exited row.

### 2. The evidence is DROPPABLE, so a journal-only audit under-counts

`netting_attribution_basis` and `netting_attributed_qty` live in `notes`,
written through `dump_capped(notes, 500)`. **Neither key is in
`json_notes._DEFAULT_PROTECTED`** — a tuple whose own comments record **three
prior instances** of a load-bearing key being shed by exactly this cap. So a
truncated row with no stamp is ***we could not look***, never *not attributed*.

### 3. `provenance.classify_pnl` grades the PRICE and has no quantity axis

Measured with a positive control: in `src/runtime/provenance.py`,
`position_size` appears **0** times, `quantity` **0**, `size` **0** — against
**12** for `exit_price_source` and **11** for `pnl_source`. The five `qty` hits
are all in comments about prorated PnL. PnL is price × quantity, so a row can be
graded `measured` on a manufactured quantity, and two live rows are.

## What the live pull says

**Population** (`bybit*`, the only venue this path runs on): **765 rows graded**,
235 off-venue counted and not graded.

| `qty_basis` | n |
|---|--:|
| `observed` | 602 |
| **`evidence_may_be_shed`** — *we could not look* | **111** |
| `assigned_by_attribution` | 52 |

### The two populations, side by side

| | n |
|---|--:|
| rows matching `exit_reason='netting_attributed'` — **what the prior work scoped** | **50** |
| rows carrying an attribution stamp in `notes` | 52 |
| rows the `exit_reason` filter **misses** | **2** |
| rows that could not be graded either way | **111** |

### The second source settles the 111 — and triples the miss

The soak is written **before** the DB is touched and is **not** subject to the
notes cap, so it can rescue a row whose stamp was shed. Only `mode == "apply"`
rows mutated anything; `annotate` rows changed nothing and are not counted.

`corroboration`: `neither` 709 · **`stamp_only` 27** · **`stamp_and_soak` 25** ·
**`soak_only` 4**.

**4 of the 29 soak-applied trades (13.8%) carry NO stamp in the journal at
all** — the evidence was shed, exactly as the cap predicts — and **every one is
labelled with an ordinary exit reason**:

| trade | account | strategy | `exit_reason` | took | left | removed | pnl | provenance |
|--:|---|---|---|--:|--:|--:|--:|---|
| 5687 | `bybit_1` | `trend_donchian_eth` | `sl` | 1.87 | 2.92 | 39.0% | −230.91 | estimated |
| 5569 | `bybit_1` | `ict_scalp_eth_15m` | `reconciler_filled` | 25.58 | 0.47 | **98.2%** | +8.40 | estimated |
| 5479 | `bybit_1` | `ada_pullback_2h` | `reconciler_filled` | 63315.0 | 15912.0 | 79.9% | −95.47 | estimated |
| 5404 | **`bybit_portfolio`** | `eth_pullback_2h` | `sl` | 12.52 | 0.06 | **99.5%** | −4.96 | estimated |

And the 2 the journal *could* see, both graded **`measured`**:

| trade | account | strategy | `exit_reason` | took | left | removed | pnl | provenance |
|--:|---|---|---|--:|--:|--:|--:|---|
| 5663 | `bybit_1` | `ict_scalp_eth_15m` | `sl_cross` | 52.83 | 0.29 | ≥99.5% | +0.42 | **measured** |
| 5534 | `bybit_1` | `ict_scalp_sol_5m` | `sl_cross` | 1078.7 | 4.9 | ≥99.5% | −4.56 | **measured** |

> **INVISIBLE, BOTH SOURCES COMBINED: 6** (2 stamped-but-mislabelled + 4
> stamp-shed). **The journal alone sees 2** — it under-counts by 3×.

## What this changes for work already done

- **`ada_pullback_2h` is an e35 leg.** Trade 5479 sits in the **`e35` arm** of
  `e35_break_attribution` — the arm MI-278 U32 measured at n=20 rows / 14
  packages — with 79.9% of its quantity removed and no journal trace.
  `trend_donchian_eth` and `eth_pullback_2h` land in `control_same_family`; the
  two `ict_scalp_*` legs and 5534 land in `control_other`. **All three arms are
  affected.**
- **Three of the six are `ict_scalp_*`** — MI-277's own subject, and the family
  whose 1.5R target U30 built a sweep axis for.
- **`bybit_portfolio` mirrors the real-money `bybit_2` roster** by an enforced
  test invariant, so one of the six is on the mirror of a live-money book.
- ⚠️ **This does NOT invalidate any published figure and no figure is restated
  here.** Six rows out of hundreds will not move a headline. What it changes is
  the **scoping rule**: `exit_reason == 'netting_attributed'` is not a sound
  filter for this contamination, and any future exclusion should key on the
  `notes` stamp **and** the soak.

## What this does NOT establish

- **No close is claimed false.** That needs the venue-side read
  `OI-20260908` specifies. Option (b) of the parent row is **exhausted** (U9 +
  U16) and was not re-attempted.
- **The 111 are not resolved, only bounded.** The soak reaches 29 applied trades
  over 2026-08-29 → 2026-09-11; it is **itself a 1000-line tail of a 3.88 MB
  file**, so the applied set is a LOWER BOUND. A row reduced before that window
  is not absent — it is unread.
- **Nothing is fixed.** `src/runtime/order_monitor.py` and
  `src/utils/json_notes.py` are Tier-2 and untouched. Two remedies are named in
  the backlog and neither is applied.
- **The removed-share for the two stamped rows is a LOWER BOUND** —
  `netting_attributed_qty` is overwritten each pass. The four soak rows carry a
  true cumulative because the soak appends.

## The remedies, named and not applied

1. **[Tier-2, one line]** Add `netting_attribution_basis` and
   `netting_attributed_qty` to `json_notes._DEFAULT_PROTECTED`. That tuple
   already carries four keys added for precisely this failure; these are a
   fifth. It does not recover the rows already shed.
2. **[Tier-2]** Give the partial branch a durable marker outside `notes` — the
   same argument `closed_by_operator` makes in that tuple's own comment: *the
   FLAG is what a consumer branches on, so the flag is what must survive*.
3. **[Tier-1, elsewhere]** `src/runtime/provenance.py` gains a QUANTITY axis, or
   states in terms that it grades the price only. Today `is_measured()` returns
   `True` for a row whose quantity was assigned.
