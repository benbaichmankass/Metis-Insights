# S-PAIRS-CONTROL-EXIT-RESIDUE-2026-08-22

## Date Range
2026-08-22 (single session, `s4-pairs-control`), continuing directly from
`S-COVERAGE-AND-CASCADE-CLASSIFY-2026-08-22` (`s3-exitpath`, main at `4eb7c5ba`).

## Objective
Three items, in the order the handoff set them:
1. **The owed live positive control** on item 1.6's pairs package cascade — attempted twice
   at session close 2026-08-22 and NOT YET OBSERVABLE both times.
2. **Item 1.1's three-row residue** — trades 4928 / 4733 / 4180, each crossing its own stop
   by a hair and keeping the generic reason, with one surviving hypothesis to confirm or kill.
3. **Workplan item 1.4** — AVAX scalp sizes above the venue maximum; the Tier-1 half of its
   `next_action`.

## Tier
**Tier 1 throughout. Nothing was shipped to `src/`.** All three results are measurement +
code trace against deployed code. One Tier-2 change is *prepared and explicitly not shipped*
(§ "What was NOT done"). No `execution:` change, no Tier-3 gate, no account mode, no model
promoted, no live-VM mutation, no order placed or cancelled.

## Starting Context
`docs/claude/WORKPLAN-2026-08-21.md` is the queue. Board tail **proved**:
`perPage=100, page=13` → **short page of 95**, `page=14` → `[]`; newest the predecessor's
own 🔓 release at 16:50:19Z. Sole session, slot free, no open 🔒.

## Repo State Checked
Fresh branch from `origin/main` at `4eb7c5ba` (the predecessor's three PRs are all merged,
so this is a new PR, never a stack on merged history). The live trader **and** web-api both
serve `4eb7c5ba` (`/api/diag/version`; `runtime_status.git_sha`), so every read below is
against deployed code. ⚠️ Clone **unshallowed first** (3,555 commits) before any `git log -S`
or file-age read.

## Files and Systems Inspected
- `src/runtime/order_monitor.py` — `_reconcile_open_trades`, `_close_trade_from_order_status`,
  `_classify_broker_exit`, `_sweep_pending_pnl_from_bybit`, `_sweep_local_pnl_for_unpriced`,
  `_watchdog_stuck_strategies`
- `src/units/strategies/pairs_executor.py` — `_close_pair`, `_cascade_close_pair_package`,
  the `half_open_cleanup` call site
- `src/units/accounts/qty_legalize.py` — `_resolve_venue_lot_rule`, `legalize_qty`
- `src/units/accounts/precision.py` — `_live_lot_rule`, `get_lot_bounds`, `_STATIC_LOT_RULE`
- `src/units/accounts/execute.py` — the `_submit_order` legalize pre-flight
- `src/units/accounts/risk.py` — the `max_qty_by_margin` clamp
- Live: `/api/diag/version`, `/api/diag/status`, `/api/diag/journalctl`,
  `/api/bot/pairs/soak`, `/api/bot/db/table/{trades,order_packages,tables}`

## Work Completed

### 1. Item 1.6's live positive control — **PASS**

Third attempt, and this one fired. Criteria were fixed in advance by the row: one
`order_packages` row, updated after the fix deployed, whose `close_reason` is one of
`pairs_revert` / `pairs_stop` / `pairs_half_open_cleanup`.

**POPULATION.** All 189 packages with `strategy_name LIKE 'pairs_%'`, `filter_state` and
`order_state` both asserted `applied`. Deploy boundary = `e9dbd7b0`, merged
2026-08-22T15:35:53Z.

| | n | `close_reason` distribution | pairs-native |
|---|---|---|---|
| **pre-deploy** (`updated_at < 15:35:53Z`) | 187 | `stuck_cascade_recovered` 109 · `reconciler_filled` 57 · null 21 | **0** |
| **post-deploy** | 2 | `pairs_half_open_cleanup` 1 · `reconciler_filled` 1 | **1** |

The row: package `pkg-a7041e63d2ac491b` (`pairs_sol_eth_a`, SOLUSDT),
`updated_at` **2026-08-22T17:05:17.426815Z**, `close_reason` **`pairs_half_open_cleanup`**,
`linked_trade_id` 4942. Trade 4942 carries the **same** `exit_reason`, `closed_at`
17:05:17.184801Z — **242 ms** earlier. The stuck-cascade sweep runs on the monitor cadence
(tens of seconds), so a 242 ms gap is the in-line cascade inside `_close_pair`, not the
sweep; and the sweep's reason would have been `stuck_cascade_recovered`, which this is not.
The `/api/bot/pairs/soak` `half_open` row for the same pair is stamped 17:05:17.433738Z.

**Zero of 187 before, one of one eligible after.** Pre-fix, `pairs_revert`/`pairs_stop`/
`pairs_half_open_cleanup` appeared on 99 trade rows and zero packages, so a single row is
decisive against that baseline.

**Why the first two attempts were NOT YET OBSERVABLE — diagnosed, not just repeated.** The
sleeve was **flat on all four pairs** from 12:10Z, and had evaluated only 4 bars (all
`skip_flat`) between the deploy and the first attempt. No pair was open, so no close could
occur; the unchanged distribution genuinely proved nothing. Cadence, for whoever waits on
this class again: over 2026-08-10 → 2026-08-22 the soak carries **38 `close` + 24 `half_open`
= 62 cleanup-or-close events in ~12.4 days (~5/day)**, so a few hours of silence is normal
variance, not a stalled fix. **And `half_open` is the likelier first trigger, not `close`** —
`_close_pair` is reached with `outcome="half_open_cleanup"` from `pairs_executor.py:992`, and
SOL/ETH strands on essentially every open (`BL-20260821-PAIRS-SOL-ETH-STRANDS-ON-EVERY-OPEN`),
which is exactly what fired here.

### 2. Item 1.1's residue — the surviving hypothesis REFUTED, the real mechanism found

The standing hypothesis was that `_classify_broker_exit`'s fallback to
`_resolve_protective_levels(symbol, direction)` grades a trade against **another** trade's
bracket on a netting account. **Refuted on the rows themselves:** each of 4928 / 4733 / 4180
has a linked package with the correct `linked_trade_id` and positive `sl`/`tp` **identical**
to the trade's own, so the fallback branch is unreachable — and on those levels all three
satisfy the production inequality for `'sl'`. The levels were never the problem.

**The classifier never ran.** `_close_trade_from_order_status`'s no-closed-pnl-record
fallback (`order_monitor.py:6033`) hard-codes `reconciler_filled` and leaves `exit_price`
NULL — correctly, there being no price. `_sweep_pending_pnl_from_bybit` (`:8323`) then
selects exactly those rows (`status='closed' AND pnl IS NULL`), recovers the venue record,
writes `exit_price` / `pnl` / `exit_price_source` — **and never touches `exit_reason`, never
calls the classifier.** The price becomes known; the label that depends on it is never
recomputed.

**POPULATION: 572** rows with `exit_reason='reconciler_filled'` (all closed, all
non-backtest; `filter_state` asserted `applied` on both pages), of which **395 gradeable**
(exit price > 0 **and** a linked package with ≥1 positive level; 172 no package + 5 no price
are **excluded, not counted**).

| | n | would grade `sl` | `tp` | genuinely between |
|---|---|---|---|---|
| **broker-truth price** | **155** | 83 | 8 | 64 (**41.3%**) |
| estimated or worse | 240 | 70 | 20 | 150 (62.5%) |

**91 of 155 (58.7%) broker-truth `reconciler_filled` rows actually reached a declared level.**
⚠️ Quote the **91**, not the 181 — the wider figure rests partly on `local_markprice`, the
FABRICATED class `provenance.py` exists to distrust.

Two confirmations: **181 of 181** mislabelled rows carry **no** `exit_reason_source` note key
(the marker the classifying branch stamps), a 100% signature that none reached it; and the
**14** rows that *did* reach it are all `unresolved` and **all 14 still grade `None` today**,
so *"classified then the price moved"* contributes **zero** rows — the prior session's
elimination of later price refinement stands. The 41.3% independently reproduces item 1.1's
41.0%, on a differently-scoped cut.

Evidence: [`exit-reason-frozen-at-close-2026-08-22`](../research/exit-reason-frozen-at-close-2026-08-22.md).

### 3. Item 1.4 — the Tier-1 half answered, and two corrections to its own row

The row's `next_action` Tier-1 half was *"establish which basis produced 34k AVAX — the rows
carry `notes.margin_basis`, read it."* **Read.** Two of the 21 rows are recent enough to carry
the stamp (it shipped 2026-08-13), and both say the same thing:

- `{'kind': 'venue_available', 'basis_usd': 81229.86, 'leverage': 3, 'buffer': 0.9, 'max_qty_by_margin': 33997.92498997939}` → `position_size` **33997.9**
- `{'kind': 'venue_available', 'basis_usd': 91800.28, 'leverage': 3, 'buffer': 0.9, 'max_qty_by_margin': 33265.434696282246}` → `position_size` **33265.4**

`position_size` equals `max_qty_by_margin` **to full float precision** on both. `risk.py:1232`
clamps `qty` down to that cap when the risk-based size exceeds it, so **the emitted size is
the margin ceiling itself, not a risk-derived number** — the answer the row asked for. The
basis is broker truth (`venue_available`), so the ceiling is correctly computed; the defect
is that a ceiling is being shipped as a size, with a second ceiling downstream that is inert.

**The venue-max clamp exists, is wired, and did not fire — nine days after it shipped.**
`qty_legalize.legalize_qty` clamps to `maxOrderQty` and is called with `prefer_live=True` at
`execute.py:1346`; it landed 2026-08-13T10:39:59Z. Yet at **2026-08-22T10:30:58Z** the trader
sent AVAXUSDT qty **33,265.4** and Bybit bounced it (`ErrCode 10001`,
`max_qty:2200000000000` = 22,000). The clamp's own WARNING
(`_submit_order: ... EXCEEDS venue maxOrderQty ... clamped to ...`) is **absent** from the
journal window, so the branch was not entered and `venue_max` was `None`. The venue's own
error proves a maximum exists — so `None` here means *the lookup did not resolve one*, which
is `BL-20260814-VENUE-MAX-NONE-CANNOT-SAY-WE-COULD-NOT-LOOK` firing in production again,
eight days after that row's own instance (trade 4640, 2026-08-14).

⚠️ **Which of the three paths to `None` was taken is NOT determinable from the journal**, and
that is the finding rather than a gap: `_live_lot_rule` returning `None` on an empty response
logs **nothing**; `_from_live` raising logs at **DEBUG**; and `_from_profile` succeeding
returns `venue_max=None` **by construction** and logs nothing. The success path never records
which source answered. No `lot_rule live lookup failed` WARNING appears in the window, which
rules out only the one path that *does* log.

**Two corrections to the row, both measured:**
- It says *"20 rejections between 2026-08-12 and 2026-08-14"* (and `precision.py`'s comment
  says *"18 of 22"*). Measured: **21 `exchange_rejected` rows spanning 2026-07-30 →
  2026-08-22T10:30:59Z** — a far wider window, and **still live today**.
- Its *"invisible half"* — *"the accepted orders on this leg are sized at or near a venue
  ceiling"* — is **not supported at the median.** Accepted `ict_scalp_avax_5m` orders
  (n=22): median **4,574.3**, i.e. **20.8%** of the 22,000 cap; only **2 of 22 (9.1%)** exceed
  80% of it. The claim holds for a small tail — the largest accepted order is 21,971.2, or
  99.87% of the cap — not generally. Separation is perfect: all 21 rejected > 22,000, all 22
  accepted < 22,000.

### 4. ⚠️ A correction to my own §2, caught by `doc-freshness` step 2

`src/runtime/provenance.py`'s module docstring (steps 1–3, 2026-07-30) **already describes
this chain** — the fallback pinning the reason, and the classifier being *"downstream of a
price the code deliberately refuses to fetch"*. My first draft of §2 called it a mechanism
nobody had named. **Withdrawn.** What is genuinely new: it is scoped there to **demo**
(4733 and 4180 are `bybit_2`, **real money**); it is about the **PnL**, which **was fixed**
on 2026-07-30, not the **label**, which was not; the later writer here is the **broker
record**, not a sweep-time mark; and the **scale** was never measured.

⚠️ **That docstring's step 1 is itself stale, and is fixed in this PR** — the demo branch
was **narrowed** on 2026-07-30 (#8111, `BL-20260730-BROKER-TRUTH-COLLECTED-NEVER-READ`) to
resolve the exit from the **fills store** rather than returning `None`, so it no longer
"refuses to fetch". This is the session's **only `src/` edit, and it is a docstring** — no
behaviour change, `ast.parse` clean.

**This also meets G.1 from the other side.** Of the 181 mislabelled rows `bybit_1` is
**155**, and only **73 of those (47.1%)** carry a broker-truth price. Since the narrowing,
demo resolves from fills — and `fills_pnl.py`'s 5% `QTY_TOLERANCE` rejects `bybit_1`'s
netted closes at a measured 31–44% overshoot, so the price lands as an estimate. G.1 and
this finding describe **one population**. ⚠️ Do **not** read that 47.1% as the 47.1% in
`provenance.py`/#8111 — different quantities that coincide numerically.

## Validation
- Every DB read asserted **`filter_state == "applied"`** (and `order_state` where ordered)
  before any `total` or distribution was trusted.
- The control's baseline was **re-derived in this session**, not inherited: 0 pairs-native
  reasons across 187 pre-deploy packages.
- Deploy confirmed two ways: `/api/diag/version` → `4eb7c5ba` and
  `runtime_status.git_sha` → `4eb7c5ba`, with `e9dbd7b0` an ancestor.
- The cascade was distinguished from the sweep by **timing (242 ms)** and by **reason value**
  (`pairs_half_open_cleanup`, not `stuck_cascade_recovered`) — two independent grounds.
- The 41.3%/41.0% agreement between this cut and item 1.1's is corroboration across
  **different populations**, and is reported as that rather than as one measurement.
- Bybit's `instruments-info` could **not** be queried from the sandbox (CloudFront geo-block
  on both `api.bybit.com` and `api-demo.bybit.com`) — stated rather than worked around. The
  venue's own `ErrCode 10001` message is the evidence that a 22,000 maximum exists.

## What was NOT done, deliberately
- **The `exit_reason` re-classification is PREPARED, NOT SHIPPED.** It is a money-DB
  writeback (Tier-2) and needs one operator OK. Prepared detail, including the
  `is_reduce_leg` hazard and why the 181-row historical backlog is a *separate* decision, is
  in § 5 of the research doc.
- **No behaviour change anywhere.** The single `src/` edit is a docstring correction in `provenance.py`.
- **No venue-max change.** Establishing *why* `venue_max` is `None` needs observability the
  code does not currently emit; adding it is the honest next step, not guessing at a cause.
- **The stuck-cascade sweep was not touched**, per item 1.6's standing instruction.
- **The three cascade gaps** (`order_monitor.py:2910`, `:7734`, `execute.py:1719`) and
  **G.1's matcher** were not worked — both remain one operator-gated decision each.
- **The Sunday 2026-08-23 22:30Z trigger** (`trig_014S3NAzMKy2Ac2AM2GgyRE5`, MES target attach
  + MGC flatten) was **left armed and untouched** — this session is not that session.

## Rows
- `BL-20260822-PAIRS-PACKAGES-CLOSED-BY-THE-STUCK-CASCADE-SWEEP` — control **PASS**, closed.
- `BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE` — **new**.
- `BL-20260821-AVAX-SCALP-SIZES-ABOVE-THE-VENUE-MAXIMUM` — Tier-1 half answered; count,
  window and the "invisible half" claim corrected.
- `BL-20260814-VENUE-MAX-NONE-CANNOT-SAY-WE-COULD-NOT-LOOK` — 2026-08-22 recurrence recorded;
  the three silent paths narrowed.
- `BL-20260822-EXIT-ATTRIBUTION-UNDER-REPORTS-BRACKET-HITS` — mechanism named.
